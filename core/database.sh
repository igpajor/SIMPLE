#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE BANCO DE DADOS
# Responsável pela conexão com SQLite
# ==========================================

DB_PATH="$SCRIPT_DIR/database/simple.db"

db_init() {
  if [[ ! -f "$DB_PATH" ]]; then
    printf '\n  Banco de dados não encontrado.\n'
    return 1
  fi

  sqlite3 "$DB_PATH" "PRAGMA foreign_keys = ON;"
}

db_query() {
  local query="$1"

  sqlite3 -batch "$DB_PATH" "$query"
}
