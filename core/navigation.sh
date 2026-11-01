#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE NAVEGAÇÃO
# Responsável pelos menus do sistema
# ==========================================

menu_principal() {
  local current_points

  current_points=$(points_balance "$CURRENT_USER_ID") || return 1
  ui_clear
  ui_header

  printf '\n'
  printf '  SISTEMA     : %s\n' "${APP_NAME:-SIMPLE}"
  printf '  AMBIENTE    : TERMINAL\n'
  printf '  STATUS      : OPERACIONAL\n'
  printf '  USUÁRIO     : %s\n' "${CURRENT_USERNAME:-}"
  printf '  PONTOS      : %s\n' "$current_points"
  printf '\n'

  ui_separator

  printf '  MENU PRINCIPAL\n'

  ui_separator

  printf '\n'
  printf '  [1] Exercícios\n'
  printf '  [2] Avaliações\n'
  printf '  [3] Meu progresso\n'
  printf '  [4] Meu perfil\n'
  if users_is_admin "$CURRENT_USER_ID"; then
    printf '  [5] Administração\n'
  fi
  printf '  [0] Sair\n'
  printf '\n'

  ui_separator

  printf '\n'
  ui_read_line REPLY "  SIMPLE > " || return 1
}
