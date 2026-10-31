#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/core/ui.sh"
source "$SCRIPT_DIR/core/navigation.sh"

APP_NAME="SIMPLE"

while true; do

  menu_principal

  case "$escolha" in
  1)
    ui_clear
    ui_header
    printf '\n  EXERCÍCIOS\n\n'
    ui_message "O catálogo de exercícios será implementado aqui."
    ui_pause
    ;;
  2)
    ui_clear
    ui_header
    printf '\n  AVALIAÇÕES\n\n'
    ui_message "O sistema de avaliações será implementado aqui."
    ui_pause
    ;;
  3)
    ui_clear
    ui_header
    printf '\n  MEU PROGRESSO\n\n'
    ui_message "Seu histórico de exercícios e pontos aparecerá aqui."
    ui_pause
    ;;
  4)
    ui_clear
    ui_header
    printf '\n  MEU PERFIL\n\n'
    ui_message "Seus dados de participante aparecerão aqui."
    ui_pause
    ;;
  0)
    ui_clear
    printf '\n  SIMPLE encerrado.\n\n'
    break
    ;;
  *)
    ui_message "Opção inválida. Escolha uma opção do menu."
    ui_pause
    ;;
  esac

done
