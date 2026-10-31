#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE NAVEGAÇÃO
# Responsável pelos menus do sistema
# ==========================================

menu_principal() {
  ui_clear
  ui_header

  printf '\n'
  printf '  SISTEMA     : SIMPLE\n'
  printf '  AMBIENTE    : TERMINAL\n'
  printf '  STATUS      : OPERACIONAL\n'
  printf '\n'

  ui_separator

  printf '  MENU PRINCIPAL\n'

  ui_separator

  printf '\n'
  printf '  [1] Exercícios\n'
  printf '  [2] Avaliações\n'
  printf '  [3] Meu progresso\n'
  printf '  [4] Meu perfil\n'
  printf '  [0] Sair\n'
  printf '\n'

  ui_separator

  printf '\n'
  read -r -p "  SIMPLE > " escolha
}
