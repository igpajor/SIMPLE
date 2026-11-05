#!/usr/bin/env bash

CURRENT_USER_ID=""
CURRENT_USERNAME=""
CURRENT_SESSION_TOKEN=""
CURRENT_USER_IS_ADMIN="0"
BROKER_MUST_CHANGE_PASSWORD="0"

# Fallback para helpers de UI quando core/ui.sh não foi sourceado (ex: testes isolados).
if ! command -v ui_success >/dev/null 2>&1; then
  ui_success() { printf '  %s\n' "$*"; }
  ui_error()   { printf '  %s\n' "$*" >&2; }
  ui_warn()    { printf '  %s\n' "$*"; }
  ui_message() { printf '  %s\n' "$*"; }
fi

auth_register() {
  local username="$1"
  local password="$2"
  local password_hash
  local username_sql
  local password_hash_sql

  username="${username,,}"
  if [[ ! "$username" =~ ^[a-zA-Z0-9._-]{3,32}$ ]]; then
    printf '  Use um nome de usuário de 3 a 32 caracteres: letras, números, ponto, hífen ou sublinhado.\n'
    return 1
  fi

  if ((${#password} < 8)); then
    printf '  A senha deve conter pelo menos 8 caracteres.\n'
    return 1
  fi

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_auth_register "$username" "$password"
    return $?
  fi

  username_sql=$(db_sql_quote "$username")
  if [[ -n "$(db_query "SELECT id FROM users WHERE username = $username_sql LIMIT 1;")" ]]; then
    printf '  Este nome de usuário já está em uso.\n'
    return 1
  fi

  password_hash=$(auth_hash_password "$password") || return 1
  password_hash_sql=$(db_sql_quote "$password_hash")

  if ! db_query "INSERT INTO users (username, password_hash) VALUES ($username_sql, $password_hash_sql);"; then
    printf '  Não foi possível criar a conta.\n'
    return 1
  fi

  CURRENT_USER_ID=$(db_query "SELECT id FROM users WHERE username = $username_sql LIMIT 1;") || return 1
  CURRENT_USERNAME="$username"
  ui_success "Conta criada. Você recebeu 1 ponto inicial."
}

auth_login() {
  local username="$1"
  local password="$2"
  local username_sql
  local user_row
  local user_id
  local stored_hash
  local is_active

  username="${username,,}"
  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_auth_login "$username" "$password"
    return $?
  fi

  username_sql=$(db_sql_quote "$username")
  user_row=$(db_query "SELECT id || '|' || password_hash || '|' || is_active FROM users WHERE username = $username_sql LIMIT 1;") || return 1

  if [[ -z "$user_row" ]]; then
    printf '  Usuário ou senha inválidos.\n'
    return 1
  fi

  IFS='|' read -r user_id stored_hash is_active <<<"$user_row"
  if ! auth_verify_password "$password" "$stored_hash" || [[ "$is_active" != "1" ]]; then
    printf '  Usuário ou senha inválidos.\n'
    return 1
  fi

  CURRENT_USER_ID="$user_id"
  CURRENT_USERNAME="$username"
  ui_success "Acesso autorizado."
}

users_is_admin() {
  local user_id="$1"

  if [[ ! "$user_id" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    [[ "$user_id" == "$CURRENT_USER_ID" && "$CURRENT_USER_IS_ADMIN" == "True" ]]
    return
  fi

  [[ "$(db_query "SELECT is_admin FROM users WHERE id = $user_id;")" == "1" ]]
}

users_login_tui_available() {
  [[ -t 0 && -t 1 && -n "${TERM:-}" && "${TERM:-dumb}" != "dumb" ]] &&
    command -v python3 >/dev/null 2>&1 &&
    python3 -c 'import curses' >/dev/null 2>&1
}

users_authenticate_curses() {
  local payload
  local fields
  local action_hex
  local username_hex
  local password_hex
  local confirmation_hex
  local action
  local username
  local password
  local confirmation
  local error=""
  local output_file
  local change_required
  local auth_status

  output_file=$(mktemp "${TMPDIR:-/tmp}/simple-login.XXXXXX") || {
    ui_error "Não foi possível preparar a autenticação."
    return 1
  }

  while true; do
    exec 4>&1
    if ! payload=$(
      SIMPLE_MODE="${SIMPLE_MODE:-local}" \
        LOGIN_TUI_ERROR="$error" \
        LOGIN_TUI_USERNAME="${username:-}" \
        LOGIN_TUI_RESULT_FD=3 \
        python3 "$SCRIPT_DIR/modules/login_tui.py" 3>&1 1>&4
    ); then
      exec 4>&-
      rm -f -- "$output_file"
      return 1
    fi
    exec 4>&-
    fields=$(printf '%s' "$payload" | python3 -c '
import json
import sys
result = json.load(sys.stdin)
if not isinstance(result, dict):
    raise SystemExit("Resposta da tela de acesso inválida")
keys = ("action", "username", "password", "confirmation")
print("\t".join(str(result.get(key, "")).encode("utf-8").hex() for key in keys))
') || {
      rm -f -- "$output_file"
      ui_error "Resposta inválida da tela de acesso."
      return 1
    }
    IFS=$'\t' read -r action_hex username_hex password_hex confirmation_hex \
      <<<"$fields"
    action=$(db_hex_decode "$action_hex") || {
      rm -f -- "$output_file"
      return 1
    }
    username=$(db_hex_decode "$username_hex") || {
      rm -f -- "$output_file"
      return 1
    }
    password=$(db_hex_decode "$password_hex") || {
      rm -f -- "$output_file"
      return 1
    }
    confirmation=$(db_hex_decode "$confirmation_hex") || {
      rm -f -- "$output_file"
      return 1
    }
    error=""

    case "$action" in
    quit)
      rm -f -- "$output_file"
      return 1
      ;;
    register)
      if [[ "${SIMPLE_MODE:-local}" == "proxy" ]]; then
        error="Solicite ao administrador a criação e vinculação da sua conta SIMPLE."
        continue
      fi
      if [[ "$password" != "$confirmation" ]]; then
        error="As senhas não coincidem."
        continue
      fi
      if auth_register "$username" "$password" >"$output_file" 2>&1; then
        rm -f -- "$output_file"
        return 0
      fi
      error=$(<"$output_file")
      [[ -n "$error" ]] || error="Não foi possível criar a conta."
      ;;
    login)
      if auth_login "$username" "$password" >"$output_file" 2>&1; then
        rm -f -- "$output_file"
        if [[ "${BROKER_MODE:-0}" == "1" ]]; then
          change_required="$BROKER_MUST_CHANGE_PASSWORD"
        else
          change_required=$(db_query "
            SELECT must_change_password FROM users WHERE id = $CURRENT_USER_ID;
          ") || {
            rm -f -- "$output_file"
            return 1
          }
        fi
        if [[ "$change_required" == "1" ]] &&
          ! users_change_password_prompt "$CURRENT_USER_ID"; then
          CURRENT_USER_ID=""
          CURRENT_USERNAME=""
          CURRENT_SESSION_TOKEN=""
          CURRENT_USER_IS_ADMIN="0"
          return 1
        fi
        return 0
      fi
      error=$(<"$output_file")
      [[ -n "$error" ]] || error="Usuário ou senha inválidos."
      ;;
    *)
      rm -f -- "$output_file"
      ui_error "Ação inválida na tela de acesso."
      return 1
      ;;
    esac
  done
}

users_authenticate() {
  local choice
  local username
  local password
  local confirmation
  local change_required
  local width
  local vp
  local i
  local term_lines
  local content_lines=25

  if [[ -n "$CURRENT_USER_ID" ]]; then
    return 0
  fi

  nav_clear
  nav_push "ACESSO"

  if users_login_tui_available; then
    ui_clear
    if users_authenticate_curses; then
      # ui_screen_begin consumes this to avoid clearing between the TUI and home.
      # shellcheck disable=SC2034
      UI_SKIP_NEXT_SCREEN_CLEAR=1
      nav_pop
      return 0
    else
      auth_status=$?
    fi
    nav_pop
    return "$auth_status"
  fi

  while [[ -z "$CURRENT_USER_ID" ]]; do
    width=$(ui_width)

    # Padding vertical para centralizar o conteúdo na tela
    term_lines=$(tput lines 2>/dev/null || printf '24')
    [[ "$term_lines" =~ ^[0-9]+$ ]] || term_lines=24
    vp=$(( (term_lines - content_lines) / 2 ))
    [[ $vp -lt 0 ]] && vp=0

    ui_clear

    # Espaço superior para centralização vertical
    for ((i=0; i<vp; i++)); do
      printf '\n'
    done

    # Banner de login com arte ASCII centralizada
    ui_simple_banner "$width"

    ui_login_option "1" "Criar Conta" "$width"
    ui_login_option "2" "Entrar" "$width"
    ui_login_option "0" "Sair" "$width"
    printf '\n\n'

    # Leitura da opção
    if ! ui_read_line choice "  OPÇÃO > "; then
      nav_pop
      return 1
    fi

    case "$choice" in
    1)
      if [[ "${SIMPLE_MODE:-local}" == "proxy" ]]; then
        ui_message "Solicite ao administrador a criação e vinculação da sua conta SIMPLE."
        ui_pause
        continue
      fi
      ui_read_line username "  Nome de usuário > " || return 1
      ui_read_line password "  Senha > " 1 || return 1
      ui_read_line confirmation "  Confirme a senha > " 1 || return 1
      if [[ "$password" != "$confirmation" ]]; then
        ui_message "As senhas não coincidem."
        ui_pause
        continue
      fi
      if ! auth_register "$username" "$password"; then
        ui_pause
        continue
      fi
      nav_pop
      return 0
      ;;
    2)
      ui_read_line username "  Nome de usuário > " || return 1
      ui_read_line password "  Senha > " 1 || return 1
      if ! auth_login "$username" "$password"; then
        ui_pause
        continue
      fi
      if [[ "${BROKER_MODE:-0}" == "1" ]]; then
        change_required="$BROKER_MUST_CHANGE_PASSWORD"
      else
        change_required=$(db_query "
          SELECT must_change_password FROM users WHERE id = $CURRENT_USER_ID;
        ") || return 1
      fi
      if [[ "$change_required" == "1" ]] &&
        ! users_change_password_prompt "$CURRENT_USER_ID"; then
        CURRENT_USER_ID=""
        CURRENT_USERNAME=""
        # shellcheck disable=SC2034 # Reset shared Broker session state before the next login.
        CURRENT_SESSION_TOKEN=""
        CURRENT_USER_IS_ADMIN="0"
        nav_pop
        return 1
      fi
      nav_pop
      return 0
      ;;
    0)
      nav_pop
      return 1
      ;;
    *)
      ui_error "Opção inválida."
      ui_pause
      ;;
    esac
  done
}

users_change_password_prompt() {
  local user_id="$1"
  local replacement
  local confirmation

  printf '\n  Sua senha foi redefinida por um administrador.\n'
  printf '  Defina uma nova senha para continuar.\n'
  while true; do
    ui_read_line replacement "  Nova senha (mínimo 8 caracteres) > " 1 || return 1
    ui_read_line confirmation "  Confirme a nova senha > " 1 || return 1
    if [[ "$replacement" != "$confirmation" ]]; then
      ui_message "As senhas não coincidem."
      continue
    fi
    if [[ "${BROKER_MODE:-0}" == "1" ]]; then
      if broker_change_password "$replacement"; then
        ui_message "Senha atualizada."
        return 0
      fi
    elif auth_store_password "$user_id" "$replacement"; then
      ui_message "Senha atualizada."
      return 0
    fi
  done
}

users_profile_dashboard() {
  local user_id="$1"
  local username="$2"
  local created_at
  local completed_count
  local balance
  local completed_exercises
  local code
  local name
  local display_name
  local github_username
  local bio
  local profile

  if [[ ! "$user_id" =~ ^[0-9]+$ ]]; then
    printf '  ID de usuário inválido.\n' >&2
    return 2
  fi

  created_at=$(db_query "SELECT created_at FROM users WHERE id = $user_id;") || return 1
  if [[ -z "$created_at" ]]; then
    printf '  Usuário não encontrado.\n'
    return 1
  fi
  completed_count=$(db_query "
    SELECT COUNT(DISTINCT exercise_id)
    FROM submissions
    WHERE user_id = $user_id AND status = 'approved';
  ") || return 1
  balance=$(points_balance "$user_id") || return 1
  profile=$(db_query "
    SELECT hex(COALESCE(display_name, '')) || '|' ||
           hex(COALESCE(github_username, '')) || '|' ||
           hex(COALESCE(bio, ''))
    FROM users WHERE id = $user_id;
  ") || return 1
  local display_name_hex
  local github_username_hex
  local bio_hex
  IFS='|' read -r display_name_hex github_username_hex bio_hex <<<"$profile"
  display_name=$(db_hex_decode "$display_name_hex") || return 1
  github_username=$(db_hex_decode "$github_username_hex") || return 1
  bio=$(db_hex_decode "$bio_hex") || return 1
  completed_exercises=$(db_query "
    SELECT e.code || '|' || replace(e.name, '|', '/')
    FROM submissions s
    JOIN exercises e ON e.id = s.exercise_id
    WHERE s.user_id = $user_id AND s.status = 'approved'
    GROUP BY e.id, e.code, e.name
    ORDER BY e.code;
  ") || return 1

  local width

  width=$(ui_width)

  ui_section "DADOS PESSOAIS"
  ui_labeled_value "Usuário" "$(ui_sanitize "$username")" "$width"
  [[ -n "$display_name" ]] && ui_labeled_value "Nome de exibição" "$(ui_sanitize "$display_name")" "$width"
  [[ -n "$github_username" ]] && ui_labeled_value "GitHub" "https://github.com/$(ui_sanitize "$github_username")" "$width"
  ui_labeled_value "Criado em" "$(ui_sanitize "$created_at")" "$width"

  if [[ -n "$bio" ]]; then
    printf '\n'
    ui_section "BIOGRAFIA"
    ui_wrap_text "$bio" "$width" 4
  fi

  printf '\n'
  ui_section "PROGRESSO"
  ui_labeled_value "Exercícios aprovados" "$completed_count" "$width"
  ui_labeled_value "Saldo de pontos" "$balance" "$width"

  printf '\n'
  ui_section "EXERCÍCIOS CONCLUÍDOS"
  if [[ -z "$completed_exercises" ]]; then
    ui_empty_state "Nenhum exercício aprovado ainda." "Complete exercícios para vê-los aqui."
  else
    while IFS='|' read -r code name; do
      ui_wrap_text "$(ui_sanitize "$code") - $(ui_sanitize "$name")" "$width" 2
    done <<<"$completed_exercises"
  fi
}

users_edit_profile() {
  local choice
  local field
  local label
  local value
  local value_sql
  local current_value
  local bio_without_newlines

  if [[ ! "${CURRENT_USER_ID:-}" =~ ^[0-9]+$ ]]; then
    printf '  Entre em uma conta para editar o perfil.\n'
    return 1
  fi

  nav_push "EDITAR PERFIL"

  while true; do
    ui_screen_begin "EDITAR MEU PERFIL"
    if ! ui_menu_choice choice "EDITAR MEU PERFIL" \
      "1|Nome de exibição" \
      "2|Usuário do GitHub" \
      "3|Biografia curta" \
      "0|Voltar"; then
      nav_pop
      return 0
    fi
    case "$choice" in
    1)
      field="display_name"
      label="Nome de exibição"
      ;;
    2)
      field="github_username"
      label="Usuário do GitHub"
      ;;
    3)
      field="bio"
      label="Biografia"
      ;;
    0)
      nav_pop
      return 0
      ;;
    *)
      ui_error "Opção inválida."
      ui_pause
      continue
      ;;
    esac

    current_value=$(db_query "
      SELECT COALESCE($field, '') FROM users WHERE id = $CURRENT_USER_ID;
    ") || {
      nav_pop
      return 1
    }
    printf '\n  Valor atual:\n%s\n\n' "$(ui_sanitize "$current_value")"
    if [[ "$field" == "bio" ]]; then
      printf '  Digite .fim sozinho em uma linha; conteúdo vazio remove a biografia.\n'
      ui_read_multiline value || {
        nav_pop
        return 1
      }
      bio_without_newlines="${value//$'\n'/}"
      if ((${#value} > 500)) || [[ "$bio_without_newlines" =~ [[:cntrl:]] ]]; then
        ui_error "A biografia deve ter até 500 caracteres e não pode conter caracteres de controle."
        ui_pause
        continue
      fi
    else
      ui_read_line value "  Novo $label (vazio remove) > " || {
        nav_pop
        return 1
      }
      if [[ "$value" =~ [[:cntrl:]] ]] || ((${#value} > 80)); then
        ui_error "$label deve ter até 80 caracteres e não pode conter caracteres de controle."
        ui_pause
        continue
      fi
      if [[ "$field" == "github_username" && -n "$value" &&
        ! "$value" =~ ^[A-Za-z0-9-]{1,39}$ ]]; then
        ui_error "Informe apenas um usuário GitHub válido (letras, números e hífens)."
        ui_pause
        continue
      fi
    fi

    value_sql=$(db_sql_quote "$value")
    if ! db_query "
      UPDATE users SET $field = $value_sql WHERE id = $CURRENT_USER_ID;
    "; then
      printf '  Não foi possível atualizar o perfil.\n' >&2
      ui_pause
      continue
    fi
    ui_success "$label atualizado."
    ui_pause
  done
}

users_profile_menu() {
  local choice

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_profile_menu
    return $?
  fi

  nav_push "MEU PERFIL"

  while true; do
    ui_screen_begin "MEU PERFIL"
    users_profile_dashboard "$CURRENT_USER_ID" "$CURRENT_USERNAME" || {
      nav_pop
      return 1
    }
    printf '\n'
    if ! ui_menu_choice choice "MEU PERFIL" \
      "1|Editar perfil" \
      "2|Encerrar sessão" \
      "0|Voltar"; then
      nav_pop
      return 0
    fi

    if [[ "$choice" == "1" ]]; then
      users_edit_profile || {
        nav_pop
        return 1
      }
    elif [[ "$choice" == "2" ]]; then
      if ui_confirm "Encerrar sua sessão?"; then
        ui_success "Sessão encerrada."
        CURRENT_USER_ID=""
        CURRENT_USERNAME=""
        nav_pop
        return 0
      fi
    elif [[ "$choice" == "0" ]]; then
      nav_pop
      return 0
    fi
  done
}