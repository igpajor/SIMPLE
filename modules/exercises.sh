#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE EXERCÍCIOS
# Responsável pelo catálogo e enunciados
# ==========================================

EXERCISE_STORAGE_DIR="${EXERCISE_STORAGE_DIR:-$SCRIPT_DIR/storage/exercises}"

exercise_render_structured() {
  local code="$1"
  local code_sql
  local fields
  local name_hex
  local description_hex
  local mission_hex
  local example_hex
  local directory_hex
  local files_hex
  local functions_hex
  local terminal_hex
  local hints_hex
  local name
  local description
  local mission
  local example
  local delivery_directory
  local delivery_files
  local allowed_functions
  local terminal_model
  local hints
  local action
  local width

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_catalog_render_exercise "$code"
    return $?
  fi

  code_sql=$(db_sql_quote "$code")
  fields=$(db_query "
    SELECT hex(COALESCE(name, '')) || '|' ||
           hex(COALESCE(description, '')) || '|' ||
           hex(COALESCE(statement, '')) || '|' ||
           hex(COALESCE(example, '')) || '|' ||
           hex(COALESCE(delivery_directory, '')) || '|' ||
           hex(COALESCE(delivery_files, '')) || '|' ||
           hex(COALESCE(allowed_functions, '')) || '|' ||
           hex(COALESCE(terminal_model, '')) || '|' ||
           hex(COALESCE(hints, ''))
    FROM exercises
    WHERE code = $code_sql AND status = 'available';
  ") || return 1
  if [[ -z "$fields" ]]; then
    ui_message "Exercício indisponível."
    return 1
  fi

  IFS='|' read -r name_hex description_hex mission_hex example_hex \
    directory_hex files_hex functions_hex terminal_hex hints_hex <<<"$fields"
  name=$(db_hex_decode "$name_hex") || return 1
  description=$(db_hex_decode "$description_hex") || return 1
  mission=$(db_hex_decode "$mission_hex") || return 1
  example=$(db_hex_decode "$example_hex") || return 1
  delivery_directory=$(db_hex_decode "$directory_hex") || return 1
  delivery_files=$(db_hex_decode "$files_hex") || return 1
  allowed_functions=$(db_hex_decode "$functions_hex") || return 1
  terminal_model=$(db_hex_decode "$terminal_hex") || return 1
  hints=$(db_hex_decode "$hints_hex") || return 1
  width=$(ui_width)

  ui_clear
  printf '\n  EXERCÍCIO %s\n\n' "$code"
  ui_wrap_text "$name" "$width" 2
  if [[ -n "$description" ]]; then
    printf '\n  DESCRIÇÃO\n'
    ui_separator
    ui_wrap_text "$description" "$width" 2
  fi
  printf '\n  MISSÃO\n'
  ui_separator
  ui_wrap_text "$mission" "$width" 2

  if [[ -n "$delivery_directory" || -n "$delivery_files" || -n "$allowed_functions" ]]; then
    printf '\n  ESPECIFICAÇÕES\n'
    ui_separator
    if [[ -n "$delivery_directory" ]]; then
      printf '  Diretório de entrega:\n'
      ui_wrap_text "$delivery_directory" "$width" 4
    fi
    if [[ -n "$delivery_files" ]]; then
      printf '  Arquivos esperados:\n'
      ui_wrap_text "$delivery_files" "$width" 4
    fi
    if [[ -n "$allowed_functions" ]]; then
      printf '  Funções permitidas:\n'
      ui_wrap_text "$allowed_functions" "$width" 4
    fi
  fi

  if [[ -n "$example" ]]; then
    printf '\n  EXEMPLO DE EXECUÇÃO\n'
    ui_separator
    exercise_render_example "$example" "$width"
  fi
  if [[ -n "$terminal_model" ]]; then
    printf '\n  MODELO DO TERMINAL\n'
    ui_separator
    ui_code_block "$terminal_model"
  fi
  if [[ -n "$hints" ]]; then
    printf '\n  DICAS\n'
    ui_separator
    while IFS= read -r hint || [[ -n "$hint" ]]; do
      ui_wrap_text "- $hint" "$width" 2
    done <<<"$hints"
  fi
  printf '\n'
  ui_separator
  printf '  [1] Registrar entrega para avaliação\n'
  printf '  [0] Voltar ao catálogo\n\n'
  ui_read_line action "  EXERCÍCIO > " || return 0
  if [[ "$action" == "1" ]]; then
    exercise_submit_prompt "$code"
  fi
}

exercises_menu() {
  local escolha
  local exercises
  local -a exercise_list

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_catalog_menu
    return $?
  fi

  while true; do
    ui_clear
    ui_header

    printf '\n'
    printf '  EXERCÍCIOS\n'
    ui_separator
    printf '\n'

    exercises=$(db_query "
      SELECT code || '|' || name
      FROM exercises
      WHERE status = 'available'
      ORDER BY code;
    ")

    if [[ -z "$exercises" ]]; then
      ui_message "Nenhum exercício disponível."
      ui_pause
      return
    fi

    mapfile -t exercise_list <<<"$exercises"

    local i=1

    for exercise in "${exercise_list[@]}"; do
      local item_code="${exercise%%|*}"
      local name="${exercise#*|}"

      printf '  [%d] %s - %s\n' "$i" "$item_code" "$name"
      ((i++))
    done

    printf '\n'
    ui_separator
    printf '\n'
    printf '  [0] Voltar\n\n'

    ui_read_line escolha "  EXERCÍCIOS > " || return 0

    if [[ "$escolha" == "0" ]]; then
      return
    fi

    if [[ ! "$escolha" =~ ^[0-9]+$ ]] ||
      ((escolha < 1 || escolha > ${#exercise_list[@]})); then
      ui_message "Opção inválida."
      ui_pause
      continue
    fi

    local selected="${exercise_list[$((escolha - 1))]}"
    local selected_code="${selected%%|*}"

    exercise_show "$selected_code"
  done
}

# ==========================================
# EXIBIÇÃO DO ENUNCIADO
# ==========================================

exercise_show() {
  local code="$1"
  local statement_path
  local full_path
  local width
  local terminal_model
  local hints
  local hint
  local code_sql
  local title=""
  local line
  local section=""
  local example=""
  local inside_example=0
  local first_line=1
  local pending_spec=""
  local action

  code_sql=$(db_sql_quote "$code")
  if [[ -n "$(db_query "
    SELECT statement FROM exercises
    WHERE code = $code_sql AND status = 'available' AND statement != ''
    LIMIT 1;
  ")" ]]; then
    exercise_render_structured "$code"
    return
  fi
  statement_path=$(db_query "
    SELECT statement_path
    FROM exercises
    WHERE code = $code_sql
    AND status = 'available'
    LIMIT 1;
  ")

  if [[ -z "$statement_path" ]]; then
    ui_message "Enunciado não encontrado."
    ui_pause
    return
  fi

  full_path="$SCRIPT_DIR/$statement_path"
  if [[ ! -f "$full_path" && -f "$EXERCISE_STORAGE_DIR/${statement_path##*/}" ]]; then
    full_path="$EXERCISE_STORAGE_DIR/${statement_path##*/}"
  fi

  if [[ ! -f "$full_path" ]]; then
    ui_message "Arquivo do enunciado não encontrado."
    ui_pause
    return
  fi

  width=$(ui_width)

  # Lê o título da segunda linha original.
  title=$(sed -n '2p' "$full_path")

  ui_clear

  printf '\n'
  printf '  EXERCÍCIO  %s\n' "$code"
  printf '\n'

  ui_wrap_text "$title" "$width" 2

  printf '\n'
  ui_separator

  # Lê as demais partes do enunciado.
  while IFS= read -r line || [[ -n "$line" ]]; do

    if ((first_line)); then
      first_line=0
      continue
    fi

    # Ignora a linha usada como título.
    if [[ "$line" == "$title" ]]; then
      title=""
      continue
    fi

    # Preserva linhas do exemplo, inclusive vazias.
    if ((inside_example)); then
      if [[ "$line" == "ESPECIFICAÇÕES" ]]; then
        exercise_render_example "$example" "$width"
        example=""
        inside_example=0
      else
        example+="$line"$'\n'
        continue
      fi
    fi

    [[ -z "$line" ]] && continue

    if [[ "$line" == "EXEMPLO DE EXECUÇÃO" ]]; then
      printf '\n'
      ui_separator
      printf '\n'
      printf '  EXEMPLO DE EXECUÇÃO\n\n'

      inside_example=1
      continue
    fi

    case "$line" in

    "MISSÃO" | "ESPECIFICAÇÕES")
      section="$line"

      printf '\n'
      ui_separator
      printf '\n'
      printf '  %s\n' "$section"
      printf '\n'
      ;;

    "DIRETÓRIO DE ENTREGA" | \
      "ARQUIVOS A ENTREGAR" | \
      "FUNÇÕES PERMITIDAS")

      pending_spec="$line"
      printf '  %s\n' "$line"
      ;;

    *)
      if [[ -n "$pending_spec" ]]; then
        ui_wrap_text "└── $line" "$width" 2
        pending_spec=""
      else
        ui_wrap_text "$line" "$width" 2
      fi
      ;;
    esac

  done <"$full_path"

  code_sql=$(db_sql_quote "$code")
  terminal_model=$(db_query "
    SELECT terminal_model
    FROM exercises
    WHERE code = $code_sql;
  ") || return 1
  hints=$(db_query "
    SELECT hints
    FROM exercises
    WHERE code = $code_sql;
  ") || return 1

  if ((inside_example)); then
    exercise_render_example "$example" "$width"
  fi

  if [[ -n "$terminal_model" ]]; then
    printf '\n'
    ui_separator
    printf '\n  MODELO DO TERMINAL\n\n'
    ui_code_block "$terminal_model"
  fi

  if [[ -n "$hints" ]]; then
    printf '\n'
    ui_separator
    printf '\n  DICAS\n\n'
    while IFS= read -r hint || [[ -n "$hint" ]]; do
      ui_wrap_text "- $hint" "$width" 2
    done <<<"$hints"
  fi

  printf '\n'
  ui_separator
  printf '\n'
  printf '  [1] Enviar solução para avaliação\n'
  printf '  [0] Voltar aos exercícios\n'
  printf '\n'

  ui_read_line action "  EXERCÍCIO > " || return 0

  if [[ "$action" == "1" ]]; then
    exercise_submit_prompt "$code"
  fi
}

exercise_submit() {
  local code="$1"
  local commit_url="$2"
  local delivery_directory="$3"
  local code_sql
  local commit_url_sql
  local delivery_directory_sql
  local inserted

  if [[ ! "${CURRENT_USER_ID:-}" =~ ^[0-9]+$ ]]; then
    printf '  Entre em uma conta antes de enviar uma solução.\n'
    return 1
  fi

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0%s\0%s\0%s\0' \
      "$CURRENT_SESSION_TOKEN" "$code" "$commit_url" "$delivery_directory" |
      broker_request submissions.create token exercise_code commit_url delivery_directory) || return 1
    if ! broker_check_response "$response"; then
      return 1
    fi
    printf '  Solução enviada. Slot %s aberto; saldo atual: %s ponto(s).\n' \
      "$(broker_response_field "$response" slot_id)" \
      "$(broker_response_field "$response" points)"
    return 0
  fi

  if [[ ! "$commit_url" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/commit/[a-fA-F0-9]{7,64}$ ]]; then
    printf '  Informe uma URL direta de commit GitHub (SHA de 7 a 64 caracteres hexadecimais).\n'
    return 1
  fi
  if [[ -z "$delivery_directory" || ${#delivery_directory} -gt 256 ||
    "$delivery_directory" == /* || "$delivery_directory" == *\\* ||
    "$delivery_directory" == *'|'* || "$delivery_directory" == *$'\n'* ||
    "$delivery_directory" == *$'\r'* ]]; then
    printf '  Informe um diretório relativo válido dentro do repositório.\n'
    return 1
  fi
  local path_component
  local -a path_components
  if [[ "$delivery_directory" =~ [[:cntrl:]] ]]; then
    printf '  O diretório contém caracteres de controle inválidos.\n'
    return 1
  fi
  IFS='/' read -r -a path_components <<<"$delivery_directory"
  for path_component in "${path_components[@]}"; do
    if [[ "$path_component" == ".." ]]; then
      printf '  O diretório não pode sair da raiz do repositório.\n'
      return 1
    fi
  done

  code_sql=$(db_sql_quote "$code")
  commit_url_sql=$(db_sql_quote "$commit_url")
  delivery_directory_sql=$(db_sql_quote "$delivery_directory")

  if ! inserted=$(db_query "
    INSERT INTO submissions (
      user_id, exercise_id, content, content_kind, commit_url, delivery_directory
    )
    SELECT $CURRENT_USER_ID, id, 'Entrega referenciada no GitHub',
           'github_commit', $commit_url_sql, $delivery_directory_sql
    FROM exercises
    WHERE code = $code_sql AND status = 'available';
    SELECT changes();
  "); then
    printf '  Não foi possível registrar a submissão; consulte a mensagem de erro acima.\n'
    return 1
  fi

  if [[ "$inserted" != "1" ]]; then
    printf '  Exercício indisponível ou já aprovado por você.\n'
    return 1
  fi

  printf '  Solução enviada. Um ponto foi usado para abrir o slot de avaliação.\n'
}

exercise_submit_prompt() {
  local code="$1"
  local commit_url
  local delivery_directory

  printf '\n  A submissão registra referências; o SIMPLE não recebe nem executa arquivos de código.\n'
  ui_read_line commit_url "  URL direta do commit GitHub > " || return 1
  ui_read_line delivery_directory "  Diretório no repositório (use . para a raiz) > " || return 1
  if ! exercise_submit "$code" "$commit_url" "$delivery_directory"; then
    ui_pause
  fi
}

# ==========================================
# FORMATAÇÃO DO EXEMPLO
# ==========================================

exercise_render_example() {
  local content="$1"
  local width="$2"
  local inner_width
  local line
  local border

  inner_width=$((width - 6))

  if ((inner_width < 10)); then
    inner_width=10
  fi

  printf -v border '%*s' "$inner_width" ''
  border=${border// /─}

  printf '  ┌%s┐\n' "$border"

  while IFS= read -r line || [[ -n "$line" ]]; do
    printf '  │ %s\n' "$line"
  done <<<"$content"

  printf '  └%s┘\n' "$border"
}
