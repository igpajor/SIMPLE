#!/usr/bin/env bash

broker_request() {
  local method="$1"
  local socket_path

  shift

  if [[ "${SIMPLE_MODE:-local}" == "proxy" ]]; then
    socket_path="${PROXY_SOCKET:-}"
  else
    socket_path="${BROKER_SOCKET:-}"
  fi
  if [[ -z "$socket_path" ]]; then
    printf '  Socket do serviço SIMPLE não configurado.\n' >&2
    return 1
  fi
  python3 "$SCRIPT_DIR/core/broker_client.py" "$socket_path" "$method" "$@"
}

broker_response_field() {
  local response="$1"
  local field="$2"

  printf '%s' "$response" |
    python3 -c '
import json
import sys
value = json.load(sys.stdin).get(sys.argv[1], "")
if not isinstance(value, (str, int, float, bool)):
    raise SystemExit("Campo de resposta inesperado")
print(value)
' "$field"
}

broker_response_nested_field() {
  local response="$1"
  local allow_null=0
  shift
  if [[ "${1:-}" == "--allow-null" ]]; then
    allow_null=1
    shift
  fi

  printf '%s' "$response" |
    python3 -c '
import json
import sys
value = json.load(sys.stdin)
allow_null = sys.argv[1] == "1"
keys = sys.argv[2:]
for index, key in enumerate(keys):
    if not isinstance(value, dict):
        if allow_null and value is None:
            value = ""
            break
        parent = ".".join(keys[:index]) or "raiz"
        raise SystemExit(
            "Resposta incompatível do SIMPLE Broker: esperava um objeto em "
            + repr(parent)
            + ". Atualize os arquivos e reinicie simple-broker.service."
        )
    if key not in value:
        if index == len(keys) - 1:
            value = ""
            break
        path = ".".join(keys[: index + 1])
        raise SystemExit(
            "Resposta incompatível do SIMPLE Broker: campo "
            + repr(path)
            + " ausente. Atualize os arquivos e reinicie simple-broker.service."
        )
    value = value[key]
if value is None:
    value = ""
if not isinstance(value, (str, int, float, bool)):
    raise SystemExit("Campo de resposta inesperado")
print(value)
' "$allow_null" "$@"
}

broker_response_rows() {
  local response="$1"
  local separator=$'\t'
  shift
  if [[ "${1:-}" == "--separator" ]]; then
    [[ $# -ge 3 ]] || {
      printf 'Separador de linhas não informado.\n' >&2
      return 2
    }
    separator="$2"
    shift 2
  fi

  printf '%s' "$response" |
    python3 -c '
import json
import sys
value = json.load(sys.stdin)
separator = sys.argv[1]
if len(separator) != 1 or separator in "\r\n":
    raise SystemExit("Separador de linhas inválido")
for key in sys.argv[2:]:
    if not isinstance(value, dict):
        raise SystemExit("Objeto de resposta inesperado")
    value = value.get(key)
if isinstance(value, dict):
    value = [value]
if not isinstance(value, list):
    raise SystemExit("Lista de resposta inesperada")
for row in value:
    if not isinstance(row, dict):
        raise SystemExit("Registro de resposta inesperado")
    values = []
    for item in row.values():
        if item is None:
            item = ""
        if not isinstance(item, (str, int, float, bool)):
            raise SystemExit("Campo de registro inesperado")
        values.append(str(item).encode("utf-8").hex())
    print(separator.join(values))
' "$separator" "$@"
}

broker_response_strings() {
  local response="$1"
  shift

  printf '%s' "$response" |
    python3 -c '
import json
import sys
value = json.load(sys.stdin)
for key in sys.argv[1:]:
    if not isinstance(value, dict):
        raise SystemExit("Objeto de resposta inesperado")
    value = value.get(key)
if not isinstance(value, list):
    raise SystemExit("Lista de resposta inesperada")
for item in value:
    if not isinstance(item, str):
        raise SystemExit("Campo de lista inesperado")
    print(item.encode("utf-8").hex())
' "$@"
}

broker_response_error() {
  local response="$1"
  local error

  error=$(broker_response_field "$response" error) || return 1
  [[ -n "$error" ]] && printf '  %s\n' "$error"
}

broker_authenticate_response() {
  local response="$1"
  local ok

  ok=$(broker_response_field "$response" ok) || return 1
  if [[ "$ok" != "True" ]]; then
    broker_response_error "$response"
    return 1
  fi
  # shellcheck disable=SC2034 # Session state is consumed by the sourced UI modules.
  CURRENT_SESSION_TOKEN=$(broker_response_field "$response" token) || return 1
  # shellcheck disable=SC2034
  CURRENT_USER_ID=$(broker_response_field "$response" user_id) || return 1
  # shellcheck disable=SC2034
  CURRENT_USERNAME=$(broker_response_field "$response" username) || return 1
  # shellcheck disable=SC2034
  CURRENT_USER_IS_ADMIN=$(broker_response_field "$response" is_admin) || return 1
  BROKER_MUST_CHANGE_PASSWORD=$(broker_response_field "$response" must_change_password) || return 1
  if [[ "$BROKER_MUST_CHANGE_PASSWORD" == "True" ]]; then
    BROKER_MUST_CHANGE_PASSWORD="1"
  else
    BROKER_MUST_CHANGE_PASSWORD="0"
  fi
}

broker_check_response() {
  local response="$1"

  if [[ "$(broker_response_field "$response" ok)" == "True" ]]; then
    return 0
  fi
  broker_response_error "$response"
  return 1
}

broker_auth_register() {
  local username="$1"
  local password="$2"
  local response

  response=$(printf '%s\0%s\0' "$username" "$password" |
    broker_request auth.register username password) || return 1
  if ! broker_authenticate_response "$response"; then
    return 1
  fi
  printf '  Conta criada. Você recebeu 1 ponto inicial.\n'
}

broker_auth_login() {
  local username="$1"
  local password="$2"
  local response

  response=$(printf '%s\0%s\0' "$username" "$password" |
    broker_request auth.login username password) || return 1
  if ! broker_authenticate_response "$response"; then
    return 1
  fi
  printf '  Acesso autorizado.\n'
}

broker_change_password() {
  local password="$1"
  local response

  response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$password" |
    broker_request auth.change_password token password) || return 1
  if ! broker_check_response "$response"; then
    return 1
  fi
  CURRENT_SESSION_TOKEN=$(broker_response_field "$response" token) || return 1
  BROKER_MUST_CHANGE_PASSWORD=0
}

broker_logout() {
  local response

  response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
    broker_request auth.logout token) || return 1
  broker_check_response "$response"
}
