#!/usr/bin/env bash

ADMIN_SELECTED_EXERCISE=""

admin_write_statement() {
  local code="$1"
  local name="$2"
  local description="$3"
  local statement="$4"
  local delivery_directory="$5"
  local delivery_files="$6"
  local allowed_functions="$7"
  local example="${8:-}"
  local final_path="$EXERCISE_STORAGE_DIR/$code.txt"
  local temporary_path

  mkdir -p -- "$EXERCISE_STORAGE_DIR" || {
    printf '  Não foi possível preparar o diretório de exercícios.\n' >&2
    return 1
  }
  temporary_path=$(mktemp "$EXERCISE_STORAGE_DIR/.${code}.XXXXXX") || {
    printf '  Não foi possível preparar o arquivo do enunciado.\n' >&2
    return 1
  }

  {
    printf 'EXERCÍCIO %s\n%s\n\n%s\n\nMISSÃO\n\n%s\n' \
      "$code" "$name" "$description" "$statement"
    if [[ -n "$delivery_directory" || -n "$delivery_files" || -n "$allowed_functions" ]]; then
      printf '\nESPECIFICAÇÕES\n'
      if [[ -n "$delivery_directory" ]]; then
        printf '\nDIRETÓRIO DE ENTREGA\n%s\n' "$delivery_directory"
      fi
      if [[ -n "$delivery_files" ]]; then
        printf '\nARQUIVOS A ENTREGAR\n%s\n' "$delivery_files"
      fi
      if [[ -n "$allowed_functions" ]]; then
        printf '\nFUNÇÕES PERMITIDAS\n%s\n' "$allowed_functions"
      fi
    fi
    if [[ -n "$example" ]]; then
      printf '\nEXEMPLO DE EXECUÇÃO\n%s\n' "$example"
    fi
  } >"$temporary_path" || {
    rm -f -- "$temporary_path"
    printf '  Não foi possível salvar o enunciado do exercício.\n' >&2
    return 1
  }

  if ! chmod 644 "$temporary_path" || ! mv -- "$temporary_path" "$final_path"; then
    rm -f -- "$temporary_path"
    printf '  Não foi possível salvar o enunciado do exercício.\n' >&2
    return 1
  fi
}

admin_current_example() {
  local code="$1"
  local code_sql
  local example
  local statement_path
  local full_path

  code_sql=$(db_sql_quote "$code")
  example=$(db_query "SELECT example FROM exercises WHERE code = $code_sql;") || return 1
  if [[ -n "$example" ]]; then
    printf '%s' "$example"
    return 0
  fi

  statement_path=$(db_query "
    SELECT statement_path FROM exercises WHERE code = $code_sql;
  ") || return 1
  [[ -n "$statement_path" ]] || return 0
  full_path="$SCRIPT_DIR/$statement_path"
  if [[ ! -f "$full_path" && -f "$EXERCISE_STORAGE_DIR/${statement_path##*/}" ]]; then
    full_path="$EXERCISE_STORAGE_DIR/${statement_path##*/}"
  fi
  [[ -f "$full_path" ]] || return 0
  awk '
    $0 == "EXEMPLO DE EXECUÇÃO" { reading = 1; next }
    reading { lines[++count] = $0 }
    END {
      last = count
      while (last > 0 && lines[last] == "") last--
      for (i = 1; i <= last; i++) {
        printf "%s%s", lines[i], (i < last ? ORS : "")
      }
    }
  ' "$full_path"
}

admin_current_specification() {
  local code="$1"
  local heading="$2"
  local code_sql
  local statement_path
  local full_path

  code_sql=$(db_sql_quote "$code")
  statement_path=$(db_query "
    SELECT statement_path FROM exercises WHERE code = $code_sql;
  ") || return 1
  [[ -n "$statement_path" ]] || return 0
  full_path="$SCRIPT_DIR/$statement_path"
  if [[ ! -f "$full_path" && -f "$EXERCISE_STORAGE_DIR/${statement_path##*/}" ]]; then
    full_path="$EXERCISE_STORAGE_DIR/${statement_path##*/}"
  fi
  [[ -f "$full_path" ]] || return 0
  awk -v heading="$heading" '
    $0 == heading { reading = 1; next }
    reading && ($0 == "DIRETÓRIO DE ENTREGA" ||
                $0 == "ARQUIVOS A ENTREGAR" ||
                $0 == "FUNÇÕES PERMITIDAS" ||
                $0 == "EXEMPLO DE EXECUÇÃO") { reading = 0 }
    reading { lines[++count] = $0 }
    END {
      first = 1
      while (first <= count && lines[first] == "") first++
      last = count
      while (last >= first && lines[last] == "") last--
      for (i = first; i <= last; i++) {
        printf "%s%s", lines[i], (i < last ? ORS : "")
      }
    }
  ' "$full_path"
}

admin_ensure_structured_exercise() {
  local code="$1"
  local code_sql
  local statement
  local example
  local statement_sql
  local example_sql
  local delivery_directory
  local delivery_files
  local allowed_functions
  local delivery_directory_sql
  local delivery_files_sql
  local allowed_functions_sql

  code_sql=$(db_sql_quote "$code")
  statement=$(db_query "SELECT statement FROM exercises WHERE code = $code_sql;") || return 1
  example=$(db_query "SELECT example FROM exercises WHERE code = $code_sql;") || return 1
  if [[ -z "$statement" ]]; then
    statement=$(admin_current_statement "$code") || return 1
  fi
  if [[ -z "$example" ]]; then
    example=$(admin_current_example "$code") || return 1
  fi
  delivery_directory=$(db_query "
    SELECT COALESCE(delivery_directory, '') FROM exercises WHERE code = $code_sql;
  ") || return 1
  delivery_files=$(db_query "
    SELECT COALESCE(delivery_files, '') FROM exercises WHERE code = $code_sql;
  ") || return 1
  allowed_functions=$(db_query "
    SELECT COALESCE(allowed_functions, '') FROM exercises WHERE code = $code_sql;
  ") || return 1
  if [[ -z "$delivery_directory" ]]; then
    delivery_directory=$(admin_current_specification "$code" "DIRETÓRIO DE ENTREGA") || return 1
  fi
  if [[ -z "$delivery_files" ]]; then
    delivery_files=$(admin_current_specification "$code" "ARQUIVOS A ENTREGAR") || return 1
  fi
  if [[ -z "$allowed_functions" ]]; then
    allowed_functions=$(admin_current_specification "$code" "FUNÇÕES PERMITIDAS") || return 1
  fi
  statement_sql=$(db_sql_quote "$statement")
  example_sql=$(db_sql_quote "$example")
  delivery_directory_sql=$(db_sql_quote "$delivery_directory")
  delivery_files_sql=$(db_sql_quote "$delivery_files")
  allowed_functions_sql=$(db_sql_quote "$allowed_functions")
  db_query "
    UPDATE exercises
    SET statement = $statement_sql,
        example = $example_sql,
        delivery_directory = $delivery_directory_sql,
        delivery_files = $delivery_files_sql,
        allowed_functions = $allowed_functions_sql
    WHERE code = $code_sql;
  "
}

admin_current_statement() {
  local code="$1"
  local code_sql
  local statement
  local statement_path
  local full_path

  code_sql=$(db_sql_quote "$code")
  statement=$(db_query "SELECT statement FROM exercises WHERE code = $code_sql;") || return 1
  if [[ -n "$statement" ]]; then
    printf '%s' "$statement"
    return 0
  fi

  statement_path=$(db_query "
    SELECT statement_path FROM exercises WHERE code = $code_sql;
  ") || return 1
  [[ -n "$statement_path" ]] || return 0
  full_path="$SCRIPT_DIR/$statement_path"
  if [[ ! -f "$full_path" && -f "$EXERCISE_STORAGE_DIR/${statement_path##*/}" ]]; then
    full_path="$EXERCISE_STORAGE_DIR/${statement_path##*/}"
  fi
  [[ -f "$full_path" ]] || return 0

  awk '
    $0 == "MISSÃO" { reading = 1; next }
    ($0 == "ESPECIFICAÇÕES" || $0 == "EXEMPLO DE EXECUÇÃO") && reading { reading = 0 }
    reading { lines[++count] = $0 }
    END {
      first = 1
      while (first <= count && lines[first] == "") first++
      last = count
      while (last >= first && lines[last] == "") last--
      for (i = first; i <= last; i++) {
        printf "%s%s", lines[i], (i < last ? ORS : "")
      }
    }
  ' "$full_path"
}

admin_create_exercise() {
  local user_id="$1"
  local code
  local name
  local description
  local statement
  local example
  local delivery_directory
  local delivery_files
  local allowed_functions
  local terminal_model
  local hints
  local pdf_url
  local code_sql
  local name_sql
  local description_sql
  local statement_sql
  local delivery_directory_sql
  local delivery_files_sql
  local allowed_functions_sql
  local terminal_model_sql
  local hints_sql
  local pdf_url_sql
  local relative_path
  local final_path

  if ! users_is_admin "$user_id"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi

  ui_read_line code "  Código do exercício > " || return 1
  code="${code^^}"
  if [[ ! "$code" =~ ^[A-Z0-9][A-Z0-9_-]{1,63}$ ]]; then
    printf '  Código inválido. Use de 2 a 64 letras, números, hífens ou sublinhados.\n'
    return 1
  fi

  ui_read_line name "  Nome > " || return 1
  if [[ -z "$name" ]]; then
    printf '  O nome do exercício é obrigatório.\n'
    return 1
  fi
  ui_read_line description "  Descrição > " || return 1
  if [[ -z "$description" ]]; then
    printf '  A descrição do exercício é obrigatória.\n'
    return 1
  fi
  ui_read_line delivery_directory "  Diretório de entrega (opcional) > " || return 1

  printf '\n  Arquivos a entregar (opcional):\n'
  ui_read_multiline delivery_files || return 1
  printf '\n  Funções permitidas (opcional):\n'
  ui_read_multiline allowed_functions || return 1
  printf '\n  Enunciado:\n'
  ui_read_multiline statement || return 1
  if [[ -z "$statement" ]]; then
    printf '  O enunciado do exercício é obrigatório.\n'
    return 1
  fi
  printf '\n  Exemplo de execução (opcional):\n'
  ui_read_multiline example || return 1
  printf '\n  Modelo do terminal (opcional):\n'
  ui_read_multiline terminal_model || return 1
  printf '\n  Dicas (opcional):\n'
  ui_read_multiline hints || return 1
  ui_read_line pdf_url "  URL HTTPS do PDF original (opcional) > " || return 1
  if ! admin_validate_pdf_url "$pdf_url"; then
    printf '  Informe uma URL HTTPS válida para o PDF.\n'
    return 1
  fi

  code_sql=$(db_sql_quote "$code")
  name_sql=$(db_sql_quote "$name")
  description_sql=$(db_sql_quote "$description")
  statement_sql=$(db_sql_quote "$statement")
  local example_sql
  example_sql=$(db_sql_quote "$example")
  delivery_directory_sql=$(db_sql_quote "$delivery_directory")
  delivery_files_sql=$(db_sql_quote "$delivery_files")
  allowed_functions_sql=$(db_sql_quote "$allowed_functions")
  terminal_model_sql=$(db_sql_quote "$terminal_model")
  hints_sql=$(db_sql_quote "$hints")
  pdf_url_sql=$(db_sql_quote "$pdf_url")
  relative_path="storage/exercises/$code.txt"
  final_path="$EXERCISE_STORAGE_DIR/$code.txt"

  if [[ -e "$final_path" ]]; then
    printf '  Já existe um arquivo de enunciado para o código %s.\n' "$code"
    return 1
  fi

  if ! db_query "
    INSERT INTO exercises (
      code, name, description, delivery_directory, delivery_files,
      allowed_functions, status, statement_path, statement, example,
      terminal_model, hints, pdf_url
    ) VALUES (
      $code_sql, $name_sql, $description_sql, $delivery_directory_sql,
      $delivery_files_sql, $allowed_functions_sql, 'available',
      $(db_sql_quote "$relative_path"), $statement_sql, $example_sql,
      $terminal_model_sql, $hints_sql, $pdf_url_sql
    );
  "; then
    printf '  Não foi possível cadastrar o exercício no banco de dados.\n' >&2
    return 1
  fi

  if ! admin_write_statement "$code" "$name" "$description" "$statement" \
    "$delivery_directory" "$delivery_files" "$allowed_functions" "$example"; then
    if ! db_query "DELETE FROM exercises WHERE code = $code_sql;" >/dev/null; then
      printf '  Não foi possível remover do banco o cadastro incompleto de %s.\n' "$code" >&2
    fi
    return 1
  fi

  printf '  Exercício %s cadastrado com sucesso.\n' "$code"
}

admin_exercises_menu() {
  local exercises
  local exercise
  local code
  local name
  local status
  local choice
  local -a exercise_list
  local -a menu_options

  if ! users_is_admin "$CURRENT_USER_ID"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi

  nav_push "EXERCÍCIOS"

  while true; do
    ui_screen_begin "EXERCÍCIOS CADASTRADOS"
    exercises=$(db_query "
      SELECT code || '|' || replace(name, '|', '/') || '|' || status
      FROM exercises ORDER BY code;
    ") || {
      nav_pop
      return 1
    }
    if [[ -z "$exercises" ]]; then
      printf '  Nenhum exercício cadastrado.\n'
    else
      mapfile -t exercise_list <<<"$exercises"
      for exercise in "${exercise_list[@]}"; do
        IFS='|' read -r code name status <<<"$exercise"
        printf '  %s - %s [%s]\n' "$(ui_sanitize "$code")" "$(ui_sanitize "$name")" "$(ui_sanitize "$status")"
      done
    fi
    menu_options=(
      "1|Cadastrar exercício"
      "2|Gerenciar exercício"
      "0|Voltar"
    )
    if ! ui_menu_choice choice "EXERCÍCIOS ADMIN" "${menu_options[@]}"; then
      nav_pop
      return 0
    fi
    case "$choice" in
    1)
      admin_create_exercise "$CURRENT_USER_ID"
      ui_pause
      ;;
    2)
      admin_select_exercise || {
        ui_pause
        continue
      }
      admin_manage_exercise "$ADMIN_SELECTED_EXERCISE"
      ;;
    0)
      nav_pop
      return 0
      ;;
    *)
      ui_error "Opção inválida."
      ui_pause
      ;;
    esac
  done
}

admin_select_exercise() {
  local exercises
  local choice
  local -a exercise_list
  local -a menu_options
  local selected

  nav_push "SELECIONAR EXERCÍCIO"

  exercises=$(db_query "SELECT code FROM exercises ORDER BY code;") || {
    nav_pop
    return 1
  }
  if [[ -z "$exercises" ]]; then
    ui_message "Não há exercícios para gerenciar."
    nav_pop
    return 1
  fi
  mapfile -t exercise_list <<<"$exercises"
  menu_options=()
  local index
  for index in "${!exercise_list[@]}"; do
    menu_options+=("$((index + 1))|${exercise_list[$index]}")
  done
  menu_options+=("0|Voltar")
  ui_menu_choice choice "SELECIONE UM EXERCÍCIO" "${menu_options[@]}" || { nav_pop; return 1; }
  if [[ "$choice" == "0" ]]; then
    nav_pop
    return 1
  fi
  if [[ ! "$choice" =~ ^[0-9]+$ ]] ||
    ((choice < 1 || choice > ${#exercise_list[@]})); then
    ui_error "Opção inválida."
    nav_pop
    return 1
  fi
  selected="${exercise_list[$((choice - 1))]}"
  ADMIN_SELECTED_EXERCISE="$selected"
  nav_pop
}

admin_manage_exercise() {
  local code="$1"
  local choice
  local -a menu_options

  if ! users_is_admin "$CURRENT_USER_ID"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi

  nav_push "GERENCIAR $code"

  while true; do
    ui_screen_begin "GERENCIAR EXERCÍCIO $code"
    local status
    status=$(db_query "SELECT status FROM exercises WHERE code = $(db_sql_quote "$code");") || {
      nav_pop
      return 1
    }
    if [[ "$status" == "archived" ]]; then
      menu_options=("1|Editar informações" "2|Reativar exercício")
    else
      menu_options=("1|Editar informações" "2|Arquivar exercício")
    fi
    menu_options+=("0|Voltar")
    ui_menu_choice choice "GERENCIAR EXERCÍCIO $code" "${menu_options[@]}" || { nav_pop; return 0; }
    case "$choice" in
    1)
      admin_edit_exercise "$code"
      ;;
    2)
      if [[ "$status" == "archived" ]]; then
        if ui_confirm "Reativar este exercício?"; then
          admin_reactivate_exercise "$code"
        else
          ui_warn "Reativação cancelada."
        fi
      else
        if ui_confirm "Arquivar este exercício?"; then
          admin_archive_exercise "$code"
        else
          ui_warn "Arquivamento cancelado."
        fi
      fi
      ui_pause
      nav_pop
      return 0
      ;;
    0)
      nav_pop
      return 0
      ;;
    *)
      ui_error "Opção inválida."
      ui_pause
      ;;
    esac
  done
}

admin_edit_exercise() {
  local code="$1"
  local code_sql
  local choice
  local column
  local label
  local field_value
  local value_sql

  if ! users_is_admin "$CURRENT_USER_ID"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi

  code_sql=$(db_sql_quote "$code")
  admin_ensure_structured_exercise "$code" || return 1
  while true; do
    ui_screen_begin "EDITAR EXERCÍCIO $code"
    ui_menu_choice choice "EDITAR $code" \
      "1|Nome" \
      "2|Descrição" \
      "3|Diretório de entrega" \
      "4|Arquivos a entregar" \
      "5|Funções permitidas" \
      "6|Missão" \
      "7|Exemplo de execução" \
      "8|Modelo do terminal" \
      "9|Dicas" \
      "10|PDF original (URL)" \
      "0|Voltar" || return 0
    case "$choice" in
    1) column="name"; label="Nome" ;;
    2) column="description"; label="Descrição" ;;
    3) column="delivery_directory"; label="Diretório de entrega" ;;
    4) column="delivery_files"; label="Arquivos a entregar" ;;
    5) column="allowed_functions"; label="Funções permitidas" ;;
    6) column="statement"; label="Missão" ;;
    7) column="example"; label="Exemplo de execução" ;;
    8) column="terminal_model"; label="Modelo do terminal" ;;
    9) column="hints"; label="Dicas" ;;
    10) column="pdf_url"; label="PDF original (URL)" ;;
    0) return 0 ;;
    *)
      ui_message "Opção inválida."
      ui_pause
      continue
      ;;
    esac

    field_value=$(db_query "SELECT $column FROM exercises WHERE code = $code_sql;") || return 1
    printf '\n  Valor atual:\n%s\n\n' "$(ui_sanitize "$field_value")"
    if [[ "$choice" == "1" || "$choice" == "2" || "$choice" == "3" ||
      "$choice" == "10" ]]; then
      local prompt="Novo $label (ENTER mantém o atual) > "
      [[ "$choice" == "10" ]] && prompt="Nova URL (vazia remove) > "
      ui_read_line field_value "$prompt" || return 1
      if [[ "$choice" != "10" && -z "$field_value" ]]; then
        continue
      fi
      if [[ "$choice" == "10" ]] && ! admin_validate_pdf_url "$field_value"; then
        ui_message "Informe uma URL HTTPS válida ou deixe vazio para remover."
        ui_pause
        continue
      fi
    else
      printf '  Novo %s (conteúdo vazio limpa o campo):\n' "$label"
      ui_read_multiline field_value || return 1
      if [[ "$choice" == "6" && -z "$field_value" ]]; then
        ui_message "A missão não pode ficar vazia."
        ui_pause
        continue
      fi
    fi

    value_sql=$(db_sql_quote "$field_value")
    if ! db_query "UPDATE exercises SET $column = $value_sql WHERE code = $code_sql;"; then
      printf '  Não foi possível atualizar o exercício.\n' >&2
      ui_pause
      continue
    fi
    ui_message "Informação atualizada."
    ui_pause
  done
}

admin_validate_pdf_url() {
  local value="$1"
  [[ -z "$value" ]] && return 0
  python3 -c '
import sys
from urllib.parse import urlsplit

value = sys.argv[1]
try:
    parsed = urlsplit(value)
    parsed.port
    valid = (
        len(value) <= 2048
        and parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not any(char.isspace() for char in value)
    )
except ValueError:
    valid = False
raise SystemExit(0 if valid else 1)
' "$value"
}

admin_archive_exercise() {
  local code="$1"
  local code_sql
  local changed

  if ! users_is_admin "$CURRENT_USER_ID"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi

  code_sql=$(db_sql_quote "$code")
  changed=$(db_query "
    UPDATE exercises SET status = 'archived'
    WHERE code = $code_sql AND status = 'available';
    SELECT changes();
  ") || return 1
  if [[ "$changed" != "1" ]]; then
    printf '  Exercício inexistente ou já arquivado.\n'
    return 1
  fi
  printf '  Exercício %s arquivado. Submissões e avaliações foram preservadas.\n' "$code"
}

admin_reactivate_exercise() {
  local code="$1"
  local code_sql
  local changed

  if ! users_is_admin "$CURRENT_USER_ID"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi

  code_sql=$(db_sql_quote "$code")
  changed=$(db_query "
    UPDATE exercises SET status = 'available'
    WHERE code = $code_sql AND status = 'archived';
    SELECT changes();
  ") || return 1
  if [[ "$changed" != "1" ]]; then
    printf '  Exercício inexistente ou não arquivado.\n'
    return 1
  fi
  printf '  Exercício %s reativado no catálogo.\n' "$code"
}

admin_adjust_points() {
  local admin_id="$1"
  local user_id="$2"
  local amount="$3"
  local reason="$4"
  local balance
  local reason_sql
  local changed

  if ! users_is_admin "$admin_id"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi
  if [[ ! "$user_id" =~ ^[0-9]+$ || ! "$amount" =~ ^-?[1-9][0-9]{0,8}$ ||
    -z "${reason//[[:space:]]/}" ]]; then
    printf '  Usuário, valor ou justificativa inválidos.\n'
    return 2
  fi

  balance=$(points_balance "$user_id") || return 1
  if [[ -z "$balance" ]]; then
    printf '  Usuário não encontrado.\n'
    return 1
  fi
  if ((balance + amount < 0)); then
    printf '  O ajuste não pode deixar o saldo negativo.\n'
    return 1
  fi

  reason_sql=$(db_sql_quote "$reason")
  changed=$(db_query "
    BEGIN IMMEDIATE;
    INSERT INTO admin_point_adjustments (user_id, admin_id, amount, reason)
    SELECT $user_id, $admin_id, $amount, $reason_sql
    WHERE EXISTS (SELECT 1 FROM users WHERE id = $user_id)
      AND EXISTS (
        SELECT 1 FROM users WHERE id = $admin_id AND is_admin = 1
      )
      AND $amount + COALESCE((
        SELECT SUM(amount) FROM points_ledger WHERE user_id = $user_id
      ), 0) + COALESCE((
        SELECT SUM(amount) FROM admin_point_adjustments WHERE user_id = $user_id
      ), 0) >= 0;
    SELECT changes();
    COMMIT;
  ") || return 1
  if [[ "$changed" != "1" ]]; then
    printf '  Ajuste recusado: saldo insuficiente ou usuário indisponível.\n' >&2
    return 1
  fi
  printf '  Ajuste de %+d ponto(s) registrado.\n' "$amount"
}

admin_point_adjustment_prompt() {
  local direction="$1"
  local amount
  local reason

  ui_read_line amount "  Quantidade de pontos > " || return 1
  if [[ ! "$amount" =~ ^[1-9][0-9]{0,8}$ ]]; then
    printf '  Informe uma quantidade inteira positiva.\n'
    return 1
  fi
  ui_read_line reason "  Justificativa > " || return 1
  if [[ -z "${reason//[[:space:]]/}" ]]; then
    printf '  A justificativa é obrigatória.\n'
    return 1
  fi
  if [[ "$direction" == "remove" ]]; then
    amount="-$amount"
  fi
  if ! ui_confirm "Aplicar ajuste de $amount ponto(s) na conta #$ADMIN_SELECTED_USER?"; then
    ui_warn "Ajuste cancelado."
    return 0
  fi
  admin_adjust_points "$CURRENT_USER_ID" "$ADMIN_SELECTED_USER" "$amount" "$reason"
}

admin_manage_user() {
  local user_id="$1"
  local username
  local choice
  local temporary_secret
  local confirmation

  if ! users_is_admin "$CURRENT_USER_ID"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi
  if [[ ! "$user_id" =~ ^[0-9]+$ ]]; then
    printf '  ID de usuário inválido.\n'
    return 2
  fi

  username=$(db_query "SELECT username FROM users WHERE id = $user_id;") || return 1
  while true; do
    ui_screen_begin "PERFIL DO USUÁRIO"
    users_profile_dashboard "$user_id" "$username" 1 || return 1
    ui_menu_choice choice "GERENCIAR USUÁRIO $(ui_sanitize "$username")" \
      "1|Definir senha temporária" \
      "2|Adicionar pontos" \
      "3|Remover pontos" \
      "0|Voltar" || return 0
    case "$choice" in
    1)
      ui_read_line temporary_secret "  Senha temporária (mínimo 8 caracteres) > " 1 || return 1
      ui_read_line confirmation "  Confirme a senha temporária > " 1 || return 1
      if [[ "$temporary_secret" != "$confirmation" ]]; then
        ui_message "As senhas não coincidem."
      elif ((${#temporary_secret} < 8)); then
        ui_message "A senha deve conter pelo menos 8 caracteres."
      elif ! ui_confirm "Definir senha temporária para $(ui_sanitize "$username")?"; then
        ui_warn "Redefinição cancelada."
      elif auth_store_password "$user_id" "$temporary_secret" 1 >/dev/null; then
        ui_message "Senha temporária definida. A troca será exigida no próximo login."
      fi
      ui_pause
      ;;
    2)
      ADMIN_SELECTED_USER="$user_id"
      admin_point_adjustment_prompt add
      ui_pause
      ;;
    3)
      ADMIN_SELECTED_USER="$user_id"
      admin_point_adjustment_prompt remove
      ui_pause
      ;;
    0)
      return 0
      ;;
    *)
      ui_message "Opção inválida."
      ui_pause
      ;;
    esac
  done
}

admin_users_menu() {
  local users
  local choice
  local user_id
  local username
  local role
  local completed
  local balance
  local -a user_list
  local -a menu_options

  if ! users_is_admin "$CURRENT_USER_ID"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi

  while true; do
    ui_screen_begin "PERFIS CADASTRADOS"
    users=$(db_query "
      SELECT u.id || '|' || u.username || '|' ||
        CASE WHEN u.is_admin = 1 THEN 'admin' ELSE 'usuário' END || '|' ||
        (SELECT COUNT(DISTINCT s.exercise_id) FROM submissions s
         WHERE s.user_id = u.id AND s.status = 'approved') || '|' ||
        (COALESCE((SELECT SUM(p.amount) FROM points_ledger p WHERE p.user_id = u.id), 0) +
         COALESCE((SELECT SUM(a.amount) FROM admin_point_adjustments a WHERE a.user_id = u.id), 0))
      FROM users u ORDER BY u.username;
    ") || return 1
    mapfile -t user_list <<<"$users"
    menu_options=()
    for user in "${user_list[@]}"; do
      IFS='|' read -r user_id username role completed balance <<<"$user"
      menu_options+=("$user_id|$(ui_sanitize "$username") ($role) | aprovados: $completed | pontos: $balance")
    done
    menu_options+=("0|Voltar")
    ui_menu_choice choice "PERFIS CADASTRADOS" "${menu_options[@]}" || return 0
    [[ "$choice" == "0" ]] && return 0
    if [[ ! "$choice" =~ ^[0-9]+$ ]] ||
      [[ -z "$(db_query "SELECT id FROM users WHERE id = $choice;")" ]]; then
      ui_message "ID de usuário inválido."
      ui_pause
      continue
    fi
    admin_manage_user "$choice"
  done
}

admin_menu() {
  local choice

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_admin_menu
    return $?
  fi

  if ! users_is_admin "$CURRENT_USER_ID"; then
    printf '  Acesso administrativo não autorizado.\n'
    return 1
  fi

  nav_push "ADMINISTRAÇÃO"

  while true; do
    ui_screen_begin "ADMINISTRAÇÃO"
    ui_menu_choice choice "ADMINISTRAÇÃO" \
      "1|Gerenciar exercícios" \
      "2|Perfis e pontos dos usuários" \
      "0|Voltar" || return 0

    case "$choice" in
    1)
      admin_exercises_menu
      ;;
    2)
      admin_users_menu
      ;;
    0)
      nav_pop
      return 0
      ;;
    *)
      ui_error "Opção inválida."
      ui_pause
      ;;
    esac
  done
}
