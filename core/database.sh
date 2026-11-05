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

  exists=$(sqlite3 -batch -bail -cmd ".timeout 5000" "$DB_PATH" \
    "SELECT COUNT(*) FROM pragma_table_info('$table') WHERE name = '$column';") || return 1

  if [[ "$exists" == "0" ]]; then
    sqlite3 -batch -bail -cmd ".timeout 5000" "$DB_PATH" \
      "ALTER TABLE $table ADD COLUMN $column $definition;" || return 1
  fi
}

db_ensure_columns() {
  local metadata
  local table
  local column
  local definition
  local key
  local migration_sql=""
  local -A existing_columns=()

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    printf '  Migrações SQLite locais indisponíveis no modo broker.\n' >&2
    return 2
  fi

  metadata=$(sqlite3 -batch -bail -cmd ".timeout 5000" "$DB_PATH" "
    SELECT 'exercises' || char(9) || name FROM pragma_table_info('exercises')
    UNION ALL SELECT 'users' || char(9) || name FROM pragma_table_info('users')
    UNION ALL SELECT 'evaluation_slots' || char(9) || name
      FROM pragma_table_info('evaluation_slots')
    UNION ALL SELECT 'reviews' || char(9) || name FROM pragma_table_info('reviews')
    UNION ALL SELECT 'submissions' || char(9) || name
      FROM pragma_table_info('submissions');
  ") || return 1
  while IFS=$'\t' read -r table column; do
    [[ -n "$table" && -n "$column" ]] || continue
    existing_columns["$table.$column"]=1
  done <<<"$metadata"

  while IFS=$'\t' read -r table column definition; do
    key="$table.$column"
    if [[ -z "${existing_columns[$key]:-}" ]]; then
      migration_sql+="ALTER TABLE $table ADD COLUMN $column $definition;"$'\n'
    fi
  done <<'MIGRATIONS'
exercises	statement_path	TEXT
exercises	statement	TEXT NOT NULL DEFAULT ''
exercises	example	TEXT NOT NULL DEFAULT ''
exercises	terminal_model	TEXT NOT NULL DEFAULT ''
exercises	hints	TEXT NOT NULL DEFAULT ''
exercises	pdf_url	TEXT NOT NULL DEFAULT ''
users	is_admin	INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0, 1))
users	must_change_password	INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1))
users	display_name	TEXT NOT NULL DEFAULT ''
users	github_username	TEXT NOT NULL DEFAULT ''
users	bio	TEXT NOT NULL DEFAULT ''
users	is_active	INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
evaluation_slots	accepted_by	INTEGER REFERENCES users(id)
evaluation_slots	accepted_at	DATETIME
reviews	recipient_score	INTEGER CHECK (recipient_score IS NULL OR recipient_score BETWEEN 0 AND 5)
reviews	recipient_feedback	TEXT NOT NULL DEFAULT ''
reviews	recipient_responded_at	DATETIME
submissions	content_kind	TEXT NOT NULL DEFAULT 'legacy' CHECK (content_kind IN ('legacy', 'github_commit'))
submissions	commit_url	TEXT
submissions	delivery_directory	TEXT
MIGRATIONS

  if [[ -z "$migration_sql" ]]; then
    return 0
  fi
  sqlite3 -batch -bail -cmd ".timeout 5000" "$DB_PATH" \
    "PRAGMA foreign_keys = ON; BEGIN IMMEDIATE; $migration_sql COMMIT;"
}

db_init() {
  local schema_path="$SCRIPT_DIR/database/schema.sql"
  local catalog_path="$SCRIPT_DIR/database/exercises.sql"

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    printf '  Inicialização SQLite indisponível no modo broker.\n' >&2
    return 2
  fi

  if [[ ! -f "$schema_path" ]]; then
    printf '  Schema do banco de dados não encontrado: %s\n' "$schema_path" >&2
    return 1
  fi

  if ! sqlite3 -batch -bail -cmd ".timeout 5000" "$DB_PATH" <"$schema_path"; then
    printf '  Não foi possível inicializar o banco de dados: %s\n' "$DB_PATH" >&2
    return 1
  fi

  if ! db_ensure_columns; then
    printf '  Não foi possível atualizar as colunas do banco de dados.\n' >&2
    return 1
  fi

  if ! db_query "
    INSERT INTO evaluation_requests (
      slot_id, reviewer_id, status, requested_at, responded_at
    )
    SELECT slot.id, slot.accepted_by,
           CASE WHEN slot.status = 'completed' THEN 'completed' ELSE 'accepted' END,
           COALESCE(slot.accepted_at, CURRENT_TIMESTAMP),
           COALESCE(slot.completed_at, slot.accepted_at, CURRENT_TIMESTAMP)
    FROM evaluation_slots slot
    WHERE slot.accepted_by IS NOT NULL
      AND NOT EXISTS (
        SELECT 1 FROM evaluation_requests request
        WHERE request.slot_id = slot.id
      );
  " >/dev/null; then
    printf '  Não foi possível migrar reservas de avaliação existentes.\n' >&2
    return 1
  fi

  if ! db_import_exercises; then
    printf '  Aviso: não foi possível importar exercícios do catálogo.\n' >&2
  fi

  if [[ ! -f "$catalog_path" ]]; then
    printf '  Catálogo de exercícios não encontrado: %s\n' "$catalog_path" >&2
    return 1
  fi
  if ! (
    set -o pipefail
    {
      printf 'BEGIN IMMEDIATE;\n'
      cat -- "$catalog_path"
      printf 'COMMIT;\n'
    } | sqlite3 -batch -bail -cmd ".timeout 5000" "$DB_PATH"
  ); then
    printf '  Não foi possível carregar o catálogo de exercícios.\n' >&2
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

  sqlite3 -batch -bail -cmd ".timeout 5000" "$DB_PATH" "PRAGMA foreign_keys = ON; $query"
}

db_sql_quote() {
  local value="$1"

  value=${value//\'/\'\'}
  printf "'%s'" "$value"
}

db_import_exercises() {
  local storage_dir="${EXERCISE_STORAGE_DIR:-$SCRIPT_DIR/storage/exercises}"
  local count

  # Só importa se a tabela estiver vazia
  count=$(db_query "SELECT COUNT(*) FROM exercises;") || return 1
  if [[ "$count" != "0" ]]; then
    return 0
  fi

  if [[ ! -d "$storage_dir" ]]; then
    return 0
  fi

  local file
  for file in "$storage_dir"/*.txt; do
    [[ -f "$file" ]] || continue

    python3 -c '
import os
import re
import sqlite3
import sys
import unicodedata

filepath, db_path = sys.argv[1:3]
with open(filepath, encoding="utf-8") as source:
    lines = source.read().splitlines()
filename = os.path.basename(filepath)

def normalize(value):
    value = unicodedata.normalize("NFD", value.strip().upper())
    return "".join(character for character in value
                   if unicodedata.category(character) != "Mn")

first_line = lines[0].strip() if lines else ""
header = re.match(r"EXERCICIO\s+([A-Z0-9][A-Z0-9_-]*)", normalize(first_line))
file_code = re.match(r"([A-Z0-9][A-Z0-9_-]*)", filename)
code = header.group(1) if header else (file_code.group(1) if file_code else filename[:-4])
name = lines[1].strip() if len(lines) > 1 else code
sections = {
    "description": [], "statement": [], "example": [],
    "delivery_directory": [], "delivery_files": [],
    "allowed_functions": [], "terminal_model": [], "hints": [],
}
section_map = {
    "DESCRICAO": "description", "MISSAO": "statement",
    "EXEMPLO DE EXECUCAO": "example",
    "DIRETORIO DE ENTREGA": "delivery_directory",
    "ARQUIVOS A ENTREGAR": "delivery_files",
    "FUNCOES PERMITIDAS": "allowed_functions",
    "MODELO DO TERMINAL": "terminal_model", "DICAS": "hints",
}
current = None
preamble = []
for line in lines[2:]:
    heading = normalize(line)
    if heading == "ESPECIFICACOES":
        current = None
    elif heading in section_map:
        current = section_map[heading]
    elif current is None:
        preamble.append(line)
    else:
        sections[current].append(line)

def content(name):
    values = sections[name]
    while values and not values[0].strip():
        values.pop(0)
    while values and not values[-1].strip():
        values.pop()
    return "\n".join(values)

description = content("description") or "\n".join(preamble).strip()
values = (
    code, name, description, content("statement"), content("example"),
    content("delivery_directory"), content("delivery_files"),
    content("allowed_functions"), content("terminal_model"), content("hints"),
    f"storage/exercises/{filename}",
)
with sqlite3.connect(db_path) as connection:
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("""
        INSERT OR IGNORE INTO exercises (
            code, name, description, statement, example, delivery_directory,
            delivery_files, allowed_functions, terminal_model, hints,
            status, statement_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "available", ?)
    """, values)
' "$file" "$DB_PATH" || {
      printf '  Não foi possível importar o exercício: %s\n' "$file" >&2
      return 1
    }
  done
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
