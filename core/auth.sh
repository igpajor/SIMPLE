#!/usr/bin/env bash

auth_hash_password() {
  local password="$1"

  if ! command -v openssl >/dev/null 2>&1; then
    printf '  OpenSSL é necessário para proteger as senhas.\n' >&2
    return 1
  fi

  printf '%s' "$password" |
    openssl passwd -6 -stdin
}

auth_verify_password() {
  local password="$1"
  local stored_hash="$2"
  local prefix="\$6\$"
  local salt
  local computed_hash

  if [[ "$stored_hash" != "$prefix"* ]]; then
    return 1
  fi

  salt="${stored_hash#"$prefix"}"
  salt="${salt%%\$*}"
  if [[ -z "$salt" ]]; then
    return 1
  fi

  computed_hash=$(printf '%s' "$password" |
    openssl passwd -6 -salt "$salt" -stdin) || return 1

  [[ "$computed_hash" == "$stored_hash" ]]
}

auth_store_password() {
  local user_id="$1"
  local replacement="$2"
  local must_change="${3:-0}"
  local password_hash
  local password_hash_sql
  local changed

  if [[ ! "$user_id" =~ ^[0-9]+$ || ! "$must_change" =~ ^[01]$ ]]; then
    printf '  Dados inválidos para atualizar a senha.\n' >&2
    return 2
  fi
  if ((${#replacement} < 8)); then
    printf '  A senha deve conter pelo menos 8 caracteres.\n'
    return 1
  fi

  password_hash=$(auth_hash_password "$replacement") || return 1
  password_hash_sql=$(db_sql_quote "$password_hash")
  changed=$(db_query "
    UPDATE users
    SET password_hash = $password_hash_sql,
        must_change_password = $must_change
    WHERE id = $user_id;
    SELECT changes();
  ") || return 1
  if [[ "$changed" != "1" ]]; then
    printf '  Usuário não encontrado para atualizar a senha.\n' >&2
    return 1
  fi
}