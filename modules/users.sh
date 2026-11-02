#!/usr/bin/env bash

CURRENT_USER_ID=""
CURRENT_USERNAME=""
CURRENT_SESSION_TOKEN=""
CURRENT_USER_IS_ADMIN="0"
BROKER_MUST_CHANGE_PASSWORD="0"

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
  printf '  Conta criada. Você recebeu 1 ponto inicial.\n'
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
  printf '  Acesso autorizado.\n'
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

users_authenticate() {
  local choice
  local username
  local password
  local confirmation
  local change_required

  if [[ -n "$CURRENT_USER_ID" ]]; then
    return 0
  fi

  while [[ -z "$CURRENT_USER_ID" ]]; do
    ui_clear
    ui_header
    printf '\n  ACESSO\n\n'
    if [[ "${SIMPLE_MODE:-local}" == "proxy" ]]; then
      printf '  [1] Criar conta (disponível somente pela administração)\n'
    else
      printf '  [1] Criar conta\n'
    fi
    printf '  [2] Entrar\n'
    printf '  [0] Sair\n\n'
    ui_read_line choice "  ACESSO > " || return 1

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
        return 1
      fi
      return 0
      ;;
    0)
      return 1
      ;;
    *)
      ui_message "Opção inválida."
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
  local admin_view="${3:-0}"
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

  printf '\n  %s\n\n' "$([[ "$admin_view" == "1" ]] && printf 'PERFIL DO USUÁRIO' || printf 'MEU PERFIL')"
  printf '  Usuário: %s\n' "$username"
  [[ -n "$display_name" ]] && printf '  Nome de exibição: %s\n' "$display_name"
  [[ -n "$github_username" ]] && printf '  GitHub: https://github.com/%s\n' "$github_username"
  printf '  Criado em: %s\n' "$created_at"
  if [[ -n "$bio" ]]; then
    printf '  Biografia:\n'
    ui_wrap_text "$bio" "$(ui_width)" 4
  fi
  printf '  Exercícios aprovados: %s\n' "$completed_count"
  printf '  Saldo de pontos: %s\n\n' "$balance"
  printf '  EXERCÍCIOS CONCLUÍDOS\n'
  ui_separator
  if [[ -z "$completed_exercises" ]]; then
    printf '  Nenhum exercício aprovado ainda.\n'
  else
    while IFS='|' read -r code name; do
      printf '  %s - %s\n' "$code" "$name"
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

  while true; do
    ui_clear
    printf '\n  EDITAR MEU PERFIL\n\n'
    printf '  [1] Nome de exibição\n'
    printf '  [2] Usuário do GitHub\n'
    printf '  [3] Biografia curta\n'
    printf '  [0] Voltar\n\n'
    ui_read_line choice "  PERFIL > " || return 0
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
      return 0
      ;;
    *)
      ui_message "Opção inválida."
      ui_pause
      continue
      ;;
    esac

    current_value=$(db_query "
      SELECT COALESCE($field, '') FROM users WHERE id = $CURRENT_USER_ID;
    ") || return 1
    printf '\n  Valor atual:\n%s\n\n' "$current_value"
    if [[ "$field" == "bio" ]]; then
      printf '  Digite .fim sozinho em uma linha; conteúdo vazio remove a biografia.\n'
      ui_read_multiline value || return 1
      bio_without_newlines="${value//$'\n'/}"
      if ((${#value} > 500)) || [[ "$bio_without_newlines" =~ [[:cntrl:]] ]]; then
        ui_message "A biografia deve ter até 500 caracteres e não pode conter caracteres de controle."
        ui_pause
        continue
      fi
    else
      ui_read_line value "  Novo $label (vazio remove) > " || return 1
      if [[ "$value" =~ [[:cntrl:]] ]] || ((${#value} > 80)); then
        ui_message "$label deve ter até 80 caracteres e não pode conter caracteres de controle."
        ui_pause
        continue
      fi
      if [[ "$field" == "github_username" && -n "$value" &&
        ! "$value" =~ ^[A-Za-z0-9-]{1,39}$ ]]; then
        ui_message "Informe apenas um usuário GitHub válido (letras, números e hífens)."
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
    ui_message "$label atualizado."
    ui_pause
  done
}

users_profile_menu() {
  local choice

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_profile_menu
    return $?
  fi

  ui_clear
  ui_header
  users_profile_dashboard "$CURRENT_USER_ID" "$CURRENT_USERNAME" || return 1
  printf '\n'
  printf '  [1] Editar perfil\n'
  printf '  [2] Encerrar sessão\n'
  printf '  [0] Voltar\n\n'
  ui_read_line choice "  PERFIL > " || return 0

  if [[ "$choice" == "1" ]]; then
    users_edit_profile
  elif [[ "$choice" == "2" ]]; then
    CURRENT_USER_ID=""
    CURRENT_USERNAME=""
  fi
}