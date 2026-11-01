#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE BANCO DE DADOS
# Responsável pela conexão com SQLite
# ==========================================

DB_PATH="${DB_PATH:-$SCRIPT_DIR/database/simple.db}"

db_ensure_column() {
  local table="$1"
  local column="$2"
  local definition="$3"
  local exists

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    printf '  Migrações SQLite locais indisponíveis no modo broker.\n' >&2
    return 2
  fi

  if [[ ! "$table" =~ ^[a-z_]+$ || ! "$column" =~ ^[a-z_]+$ ]]; then
    printf '  Nome inválido de tabela ou coluna para migração.\n' >&2
    return 2
  fi

  exists=$(sqlite3 -batch -bail "$DB_PATH" \
    "SELECT COUNT(*) FROM pragma_table_info('$table') WHERE name = '$column';") || return 1

  if [[ "$exists" == "0" ]]; then
    sqlite3 -batch -bail "$DB_PATH" \
      "ALTER TABLE $table ADD COLUMN $column $definition;" || return 1
  fi
}

db_init() {
  local schema_path="$SCRIPT_DIR/database/schema.sql"

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    printf '  Inicialização SQLite indisponível no modo broker.\n' >&2
    return 2
  fi

  if [[ ! -f "$schema_path" ]]; then
    printf '  Schema do banco de dados não encontrado: %s\n' "$schema_path" >&2
    return 1
  fi

  if ! sqlite3 -batch -bail "$DB_PATH" <"$schema_path"; then
    printf '  Não foi possível inicializar o banco de dados: %s\n' "$DB_PATH" >&2
    return 1
  fi

  if ! db_ensure_column exercises statement_path "TEXT" ||
    ! db_ensure_column exercises statement "TEXT NOT NULL DEFAULT ''" ||
    ! db_ensure_column exercises example "TEXT NOT NULL DEFAULT ''" ||
    ! db_ensure_column exercises terminal_model "TEXT NOT NULL DEFAULT ''" ||
    ! db_ensure_column exercises hints "TEXT NOT NULL DEFAULT ''" ||
    ! db_ensure_column users is_admin "INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1))" ||
    ! db_ensure_column users must_change_password "INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1))" ||
    ! db_ensure_column users display_name "TEXT NOT NULL DEFAULT ''" ||
    ! db_ensure_column users github_username "TEXT NOT NULL DEFAULT ''" ||
    ! db_ensure_column users bio "TEXT NOT NULL DEFAULT ''" ||
    ! db_ensure_column users is_active "INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))" ||
    ! db_ensure_column evaluation_slots accepted_by "INTEGER REFERENCES users(id)" ||
    ! db_ensure_column evaluation_slots accepted_at "DATETIME" ||
    ! db_ensure_column submissions content_kind "TEXT NOT NULL DEFAULT 'legacy' CHECK (content_kind IN ('legacy', 'github_commit'))" ||
    ! db_ensure_column submissions commit_url "TEXT" ||
    ! db_ensure_column submissions delivery_directory "TEXT"; then
    printf '  Não foi possível atualizar as colunas do banco de dados.\n' >&2
    return 1
  fi

  return 0
}

db_query() {
  local query="$1"

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    printf '  Acesso SQLite local bloqueado no modo broker.\n' >&2
    return 2
  fi

  sqlite3 -batch -bail "$DB_PATH" "PRAGMA foreign_keys = ON; $query"
}

db_sql_quote() {
  local value="$1"

  value=${value//\'/\'\'}
  printf "'%s'" "$value"
}

db_hex_decode() {
  local encoded="$1"
  local escaped=""
  local index

  if [[ ! "$encoded" =~ ^([A-Fa-f0-9]{2})*$ ]]; then
    printf '  Registro codificado inválido.\n' >&2
    return 1
  fi
  for ((index = 0; index < ${#encoded}; index += 2)); do
    escaped+="\\x${encoded:index:2}"
  done
  printf '%b' "$escaped"
}
