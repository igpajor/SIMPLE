#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE EXERCÍCIOS
# Responsável pelo catálogo e enunciados
# ==========================================

EXERCISE_STORAGE_DIR="${EXERCISE_STORAGE_DIR:-$SCRIPT_DIR/storage/exercises}"

exercise_render_content() {
  local name="$1"
  local description="$2"
  local mission="$3"
  local example="$4"
  local delivery_directory="$5"
  local delivery_files="$6"
  local allowed_functions="$7"
  local terminal_model="$8"
  local hints="$9"
  local pdf_url="${10:-}"
  local hint
  local width

  width=$(ui_width)
  ui_color_content
  ui_wrap_text "$(ui_sanitize "$name")" "$width" 2
  ui_color_reset

  if [[ -n "$description" ]]; then
    ui_exercise_section "DESCRIÇÃO"
    ui_color_content
    ui_wrap_text "$description" "$width" 2
    ui_color_reset
  fi

  if [[ -n "$delivery_directory" || -n "$delivery_files" || -n "$allowed_functions" ]]; then
    ui_exercise_section "ESPECIFICAÇÕES"
    ui_color_content
    [[ -n "$delivery_directory" ]] &&
      ui_labeled_value "Diretório de entrega" "$delivery_directory" "$width"
    [[ -n "$delivery_files" ]] &&
      ui_labeled_value "Arquivos a entregar" "$delivery_files" "$width"
    [[ -n "$allowed_functions" ]] &&
      ui_labeled_value "Funções permitidas" "$allowed_functions" "$width"
    ui_color_reset
  fi

  ui_exercise_section "MISSÃO"
  ui_color_content
  ui_wrap_text "$mission" "$width" 2
  ui_color_reset

  if [[ -n "$example" ]]; then
    ui_exercise_section "EXEMPLO DE EXECUÇÃO"
    exercise_render_example "$example" "$width"
  fi
  if [[ -n "$terminal_model" ]]; then
    ui_exercise_section "MODELO DO TERMINAL"
    ui_code_block "$terminal_model"
  fi
  if [[ -n "$hints" ]]; then
    ui_exercise_section "DICAS"
    ui_color_content
    while IFS= read -r hint || [[ -n "$hint" ]]; do
      ui_wrap_text "- $hint" "$width" 2
    done <<<"$hints"
    ui_color_reset
  fi
  if [[ -n "$pdf_url" ]]; then
    ui_exercise_section "PDF ORIGINAL"
    ui_color_content
    ui_wrap_text "$pdf_url" "$width" 2
    ui_color_reset
  fi
}

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
  local pdf_url_hex
  local name
  local description
  local mission
  local example
  local delivery_directory
  local delivery_files
  local allowed_functions
  local terminal_model
  local hints
  local pdf_url
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
           hex(COALESCE(hints, '')) || '|' ||
           hex(COALESCE(pdf_url, ''))
    FROM exercises
    WHERE code = $code_sql AND status = 'available';
  ") || return 1
  if [[ -z "$fields" ]]; then
    ui_message "Exercício indisponível."
    return 1
  fi

  IFS='|' read -r name_hex description_hex mission_hex example_hex \
    directory_hex files_hex functions_hex terminal_hex hints_hex pdf_url_hex <<<"$fields"
  name=$(db_hex_decode "$name_hex") || return 1
  description=$(db_hex_decode "$description_hex") || return 1
  mission=$(db_hex_decode "$mission_hex") || return 1
  example=$(db_hex_decode "$example_hex") || return 1
  delivery_directory=$(db_hex_decode "$directory_hex") || return 1
  delivery_files=$(db_hex_decode "$files_hex") || return 1
  allowed_functions=$(db_hex_decode "$functions_hex") || return 1
  terminal_model=$(db_hex_decode "$terminal_hex") || return 1
  hints=$(db_hex_decode "$hints_hex") || return 1
  pdf_url=$(db_hex_decode "$pdf_url_hex") || return 1
  width=$(ui_width)

  ui_screen_begin "EXERCÍCIO $code"
  exercise_render_content "$name" "$description" "$mission" "$example" \
    "$delivery_directory" "$delivery_files" "$allowed_functions" \
    "$terminal_model" "$hints" "$pdf_url"
  printf '\n'
  ui_statusline
  ui_menu_choice action "EXERCÍCIO $(ui_sanitize "$code")" \
    "1|Enviar solução para avaliação" \
    "0|Voltar ao catálogo" || return 0
  if [[ "$action" == "1" ]]; then
    exercise_submit_prompt "$code"
  fi
}

exercise_catalog_tui() {
  local filter="all"
  local query=""
  local selected_code=""
  local response
  local rows
  local points
  local config
  local result
  local status
  local action
  local code
  local returned_filter
  local returned_query
  local returned_code
  local commit_url
  local delivery_directory
  local tui_path

  tui_path="$SCRIPT_DIR/modules/exercise_catalog_tui.py"
  while true; do
    if [[ "${BROKER_MODE:-0}" == "1" ]]; then
      response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
        broker_request catalog.dashboard token) || return 1
      if ! broker_check_response "$response"; then
        ui_pause
        return 1
      fi
      config=$(printf '%s' "$response" | python3 -c '
import json
import sys

response = json.load(sys.stdin)
items = response.get("exercises")
points = response.get("points")
if not isinstance(items, list) or type(points) is not int or points < 0:
    raise SystemExit("Resposta do catálogo inválida")
required = (
    "code", "name", "description", "statement", "example",
    "delivery_directory", "delivery_files", "allowed_functions",
    "terminal_model", "hints", "pdf_url", "status",
)
for item in items:
    if not isinstance(item, dict) or any(
        not isinstance(item.get(field), str) for field in required
    ):
        raise SystemExit("Dados de exercício inválidos")
print(json.dumps({
    "username": sys.argv[1],
    "points": points,
    "items": items,
    "filter": sys.argv[2],
    "query": sys.argv[3],
    "selected_code": sys.argv[4],
}, ensure_ascii=False))
' "$CURRENT_USERNAME" "$filter" "$query" "$selected_code") || return 1
    else
      points=$(points_balance "$CURRENT_USER_ID") || return 1
      rows=$(db_query "
        SELECT hex(e.code) || '|' || hex(e.name) || '|' ||
               hex(e.description) || '|' || hex(COALESCE(e.statement, '')) || '|' ||
               hex(COALESCE(e.example, '')) || '|' ||
               hex(COALESCE(e.delivery_directory, '')) || '|' ||
               hex(COALESCE(e.delivery_files, '')) || '|' ||
               hex(COALESCE(e.allowed_functions, '')) || '|' ||
               hex(COALESCE(e.terminal_model, '')) || '|' ||
               hex(COALESCE(e.hints, '')) || '|' ||
               hex(COALESCE(e.pdf_url, '')) || '|' ||
               hex(COALESCE(e.pdf_url, '')) || '|' ||
               COALESCE((
                 SELECT s.status FROM submissions s
                 WHERE s.user_id = $CURRENT_USER_ID AND s.exercise_id = e.id
                 ORDER BY s.id DESC LIMIT 1
               ), '')
        FROM exercises e
        WHERE e.status = 'available'
        ORDER BY e.code;
      ") || return 1
      config=$(printf '%s' "$rows" | python3 -c '
import json
import sys

username, points, filter_name, query, selected_code = sys.argv[1:]
items = []
for line in sys.stdin:
    fields = line.rstrip("\n").split("|")
    if len(fields) != 12:
        raise SystemExit("Registro do catálogo inválido")
    values = [bytes.fromhex(value).decode("utf-8") for value in fields[:11]]
    submission_status = fields[11]
    status = (
        submission_status
        if submission_status in {"pending", "approved"}
        else "available" if int(points) > 0 else "no_points"
    )
    items.append(dict(zip((
        "code", "name", "description", "statement", "example",
        "delivery_directory", "delivery_files", "allowed_functions",
        "terminal_model", "hints", "pdf_url",
    ), values), status=status))
print(json.dumps({
    "username": username,
    "points": int(points),
    "items": items,
    "filter": filter_name,
    "query": query,
    "selected_code": selected_code,
}, ensure_ascii=False))
' "$CURRENT_USERNAME" "$points" "$filter" "$query" "$selected_code") || return 1
    fi

    exec 4>&1
    if result=$(
      EXERCISE_CATALOG_CONFIG="$config" \
        EXERCISE_CATALOG_RESULT_FD=3 \
        python3 "$tui_path" 3>&1 1>&4
    ); then
      exec 4>&-
    else
      status=$?
      exec 4>&-
      return "$status"
    fi
    IFS=$'\t' read -r action code returned_filter returned_query returned_code \
      commit_url delivery_directory <<<"$result"
    [[ -n "$returned_filter" ]] && filter="$returned_filter"
    query="$returned_query"
    [[ -n "$returned_code" ]] && selected_code="$returned_code"

    case "$action" in
    back)
      return 0
      ;;
    submit)
      [[ -n "$code" && -n "$commit_url" && -n "$delivery_directory" ]] || return 1
      if ! exercise_submit "$code" "$commit_url" "$delivery_directory"; then
        ui_pause
      fi
      ;;
    chat)
      [[ -n "$code" ]] || return 1
      # shellcheck disable=SC2034 # Consumed by chat_menu in the sourced chat module.
      CHAT_INITIAL_CHANNEL="exercise:$code"
      # shellcheck disable=SC2034
      CHAT_INITIAL_ROOM_NAME="SALA · $code"
      chat_menu || ui_error "Não foi possível acessar a sala deste exercício."
      ;;
    *)
      ui_error "A tela do catálogo retornou uma ação inválida."
      return 1
      ;;
    esac
  done
}

exercises_menu() {
  local escolha
  local exercises
  local -a exercise_list
  local -a menu_options

  if ui_home_tui_available; then
    exercise_catalog_tui
    return $?
  fi

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_catalog_menu
    return $?
  fi

  nav_push "EXERCÍCIOS"

  while true; do
    ui_screen_begin "EXERCÍCIOS"

    exercises=$(db_query "
      SELECT hex(code) || '|' || hex(name)
      FROM exercises
      WHERE status = 'available'
      ORDER BY code;
    ") || {
      nav_pop
      return 1
    }

    if [[ -z "$exercises" ]]; then
      ui_empty_state "Nenhum exercício disponível." \
        "Quando novos exercícios forem publicados, eles aparecerão aqui."
      ui_pause
      nav_pop
      return 0
    fi

    mapfile -t exercise_list <<<"$exercises"

    menu_options=()
    local i=1
    for exercise in "${exercise_list[@]}"; do
      local item_code_hex="${exercise%%|*}"
      local name_hex="${exercise#*|}"
      local item_code
      local name
      item_code=$(db_hex_decode "$item_code_hex") || {
        nav_pop
        return 1
      }
      name=$(db_hex_decode "$name_hex") || {
        nav_pop
        return 1
      }
      menu_options+=("$i|$item_code - $name")
      ((i++))
    done

    menu_options+=("0|Voltar")
    if ! ui_menu_choice escolha "EXERCÍCIOS" "${menu_options[@]}"; then
      nav_pop
      return 0
    fi

    if [[ "$escolha" == "0" ]]; then
      nav_pop
      return 0
    fi

    if [[ ! "$escolha" =~ ^[0-9]+$ ]] ||
      ((escolha < 1 || escolha > ${#exercise_list[@]})); then
      ui_error "Opção inválida."
      ui_pause
      continue
    fi

    local selected="${exercise_list[$((escolha - 1))]}"
    local selected_code
    selected_code=$(db_hex_decode "${selected%%|*}") || {
      nav_pop
      return 1
    }

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
  local description_content=""
  local mission_content=""
  local example=""
  local inside_example=0
  local first_line=1
  local current_spec=-1
  local spec_label
  local -a spec_labels=()
  local -a spec_values=()
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

  # Lê as partes para poder apresentar especificações antes da missão,
  # mesmo quando o arquivo legado usa a ordem oposta.
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

    # Preserva as linhas do exemplo, inclusive as vazias.
    if ((inside_example)); then
      if [[ "$line" == "ESPECIFICAÇÕES" ]]; then
        inside_example=0
        section="ESPECIFICAÇÕES"
      else
        example+="$line"$'\n'
        continue
      fi
    fi

    [[ -z "$line" ]] && continue

    if [[ "$line" == "EXEMPLO DE EXECUÇÃO" ]]; then
      ui_exercise_section "EXEMPLO DE EXECUÇÃO"

      inside_example=1
      continue
    fi

    case "$line" in

    "MISSÃO")
      section="$line"
      current_spec=-1
      ;;

    "ESPECIFICAÇÕES")
      section="$line"
      current_spec=-1
      ;;

    "DIRETÓRIO DE ENTREGA" | "ARQUIVOS A ENTREGAR" | "FUNÇÕES PERMITIDAS")
      case "$line" in
        "DIRETÓRIO DE ENTREGA") spec_label="Diretório de entrega" ;;
        "ARQUIVOS A ENTREGAR") spec_label="Arquivos a entregar" ;;
        "FUNÇÕES PERMITIDAS") spec_label="Funções permitidas" ;;
      esac
      spec_labels+=("$spec_label")
      spec_values+=("")
      current_spec=$((${#spec_labels[@]} - 1))
      section="ESPECIFICAÇÕES"
      ;;

    *)
      case "$section" in
        "MISSÃO")
          mission_content+="$line"$'\n'
          ;;
        "ESPECIFICAÇÕES")
          if ((current_spec >= 0)); then
            [[ -n "${spec_values[$current_spec]}" ]] &&
              spec_values[current_spec]+=$'\n'
            spec_values[current_spec]+="$line"
          fi
          ;;
        *)
          description_content+="$line"$'\n'
          ;;
      esac
      ;;
    esac

  done <"$full_path"

  description_content="${description_content%$'\n'}"
  mission_content="${mission_content%$'\n'}"
  example="${example%$'\n'}"

  ui_screen_begin "EXERCÍCIO $code"
  ui_color_bold
  ui_wrap_text "$title" "$width" 2
  ui_color_reset
  ui_separator
  if [[ -n "$description_content" ]]; then
    ui_wrap_text "$description_content" "$width" 2
  fi
  if ((${#spec_labels[@]})); then
    ui_exercise_section "ESPECIFICAÇÕES"
    for index in "${!spec_labels[@]}"; do
      ui_labeled_value "${spec_labels[$index]}" "${spec_values[$index]}" "$width"
    done
  fi
  if [[ -n "$mission_content" ]]; then
    ui_exercise_section "MISSÃO"
    ui_wrap_text "$mission_content" "$width" 2
  fi
  if [[ -n "$example" ]]; then
    ui_exercise_section "EXEMPLO DE EXECUÇÃO"
    exercise_render_example "$example" "$width"
  fi

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

  if [[ -n "$terminal_model" ]]; then
    ui_exercise_section "MODELO DO TERMINAL"
    ui_code_block "$terminal_model"
  fi

  if [[ -n "$hints" ]]; then
    ui_exercise_section "DICAS"
    while IFS= read -r hint || [[ -n "$hint" ]]; do
      ui_wrap_text "- $hint" "$width" 2
    done <<<"$hints"
  fi

  printf '\n'
  ui_statusline
  ui_menu_choice action "EXERCÍCIO $(ui_sanitize "$code")" \
    "1|Enviar solução para avaliação" \
    "0|Voltar aos exercícios" || return 0

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

  commit_url="${commit_url%%#*}"
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

  printf '\n  A submissão registra apenas a referência; o SIMPLE não recebe nem executa arquivos de código.\n'
  printf '  Um ponto será usado para abrir uma avaliação por outra pessoa.\n'
  ui_section "COMO ENCONTRAR O LINK DO COMMIT"
  ui_wrap_text \
    "Envie sua solução ao GitHub. No repositório, abra Commits/History, selecione o commit que contém sua solução e copie o endereço da página." \
    "$(ui_width)" 2
  ui_wrap_text \
    "O link deve conter /commit/SHA; links /blob/ (arquivo) e /tree/ (pasta ou branch) não servem." \
    "$(ui_width)" 2
  ui_wrap_text \
    "Exemplo: https://github.com/USUARIO/REPOSITORIO/commit/abcdef0" \
    "$(ui_width)" 2
  ui_wrap_text \
    "No diretório, informe a pasta da entrega (ex.: ex00) ou . se os arquivos estiverem na raiz." \
    "$(ui_width)" 2
  ui_read_line commit_url "  URL direta do commit GitHub > " || return 1
  ui_read_line delivery_directory "  Diretório no repositório (use . para a raiz) > " || return 1
  if ! ui_confirm "Enviar esta referência para avaliação?"; then
    ui_warn "Submissão cancelada; nenhum ponto foi usado."
    return 0
  fi
  if ! exercise_submit "$code" "$commit_url" "$delivery_directory"; then
    ui_pause
  fi
}

# ==========================================
# FORMATAÇÃO DO EXEMPLO
# ==========================================

exercise_render_example() {
  local content="$1"
  ui_code_block "$content"
}
