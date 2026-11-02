#!/usr/bin/env bash
set -euo pipefail

readonly APP_ROOT=/opt/simple
readonly DATA_DIRECTORY=/var/lib/simple
readonly DATABASE="${DATA_DIRECTORY}/simple.db"
readonly BROKER="${APP_ROOT}/broker/simple_broker.py"
readonly SCHEMA="${APP_ROOT}/database/schema.sql"

fail() {
    printf 'Erro: %s\n' "$*" >&2
    exit 1
}

if (( EUID != 0 )); then
    fail "execute este procedimento como root."
fi

getent passwd simple-broker >/dev/null || fail "o usuário simple-broker não existe."
getent group simple-broker >/dev/null || fail "o grupo simple-broker não existe."
[[ -x /usr/bin/runuser ]] || fail "runuser não está disponível."
[[ -f "$BROKER" && ! -L "$BROKER" ]] || fail "Broker ausente ou inválido em ${BROKER}."
[[ -f "$SCHEMA" && ! -L "$SCHEMA" ]] || fail "schema ausente ou inválido em ${SCHEMA}."
[[ -d "$DATA_DIRECTORY" && ! -L "$DATA_DIRECTORY" ]] || fail "diretório de dados ausente ou inválido."

directory_owner_mode="$(stat -c '%U:%a' -- "$DATA_DIRECTORY")"
[[ "$directory_owner_mode" == "simple-broker:700" ]] \
    || fail "${DATA_DIRECTORY} deve pertencer a simple-broker e ter modo 0700."

if command -v systemctl >/dev/null 2>&1 \
    && systemctl is-active --quiet simple-broker.service; then
    fail "pare simple-broker.service antes de inicializar o banco."
fi

if [[ -e "$DATABASE" || -L "$DATABASE" ]]; then
    fail "o banco já existe; nenhum arquivo foi alterado."
fi
for suffix in -wal -shm -journal; do
    if [[ -e "${DATABASE}${suffix}" || -L "${DATABASE}${suffix}" ]]; then
        fail "arquivo auxiliar preexistente impede a inicialização: ${DATABASE}${suffix}."
    fi
done

runuser -u simple-broker -- /usr/bin/python3 "$BROKER" \
    --database "$DATABASE" \
    --schema "$SCHEMA" \
    --initialize-database-only

database_owner_mode="$(stat -c '%U:%a' -- "$DATABASE")"
[[ -f "$DATABASE" && ! -L "$DATABASE" ]] || fail "o Broker não criou um arquivo SQLite regular."
[[ "$database_owner_mode" == "simple-broker:600" ]] \
    || fail "o banco criado não pertence a simple-broker com modo 0600."

runuser -u simple-broker -- /usr/bin/python3 "$BROKER" \
    --database "$DATABASE" \
    --validate-database-only

printf 'Banco de produção inicializado e validado em %s.\n' "$DATABASE"
