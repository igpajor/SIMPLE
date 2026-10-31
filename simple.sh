#!/usr/bin/env bash

set -u

APP_NAME="SIMPLE"

exibir_cabecalho() {
  printf '%s\n' \
    '┌────────────────────────────────────────────────────────────────────┐' \
    '│                                                                    │' \
    '│   ███████╗██╗███╗   ███╗██████╗ ██╗     ███████╗                   │' \
    '│   ██╔════╝██║████╗ ████║██╔══██╗██║     ██╔════╝                   │' \
    '│   ███████╗██║██╔████╔██║██████╔╝██║     █████╗                     │' \
    '│   ╚════██║██║██║╚██╔╝██║██╔═══╝ ██║     ██╔══╝                     │' \
    '│   ███████║██║██║ ╚═╝ ██║██║     ███████╗███████╗                   │' \
    '│   ╚══════╝╚═╝╚═╝     ╚═╝╚═╝     ╚══════╝╚══════╝                   │' \
    '│                                                                    │' \
    '│                     AMBIENTE DE APRENDIZAGEM                       │' \
    '│                                                                    │' \
    '└────────────────────────────────────────────────────────────────────┘'
}

exibir_menu() {
  clear
  exibir_cabecalho

  printf '\n'
  printf '  SISTEMA     : SIMPLE\n'
  printf '  AMBIENTE    : TERMINAL\n'
  printf '  STATUS      : OPERACIONAL\n'
  printf '\n'
  printf '%s\n' '────────────────────────────────────────────────────────────────────'
  printf '  MENU PRINCIPAL\n'
  printf '%s\n' '────────────────────────────────────────────────────────────────────'
  printf '\n'
  printf '  [1] Exercícios\n'
  printf '  [2] Avaliações\n'
  printf '  [3] Meu progresso\n'
  printf '  [4] Meu perfil\n'
  printf '  [0] Sair\n'
  printf '\n'
  printf '%s\n' '────────────────────────────────────────────────────────────────────'
}

pausar() {
  printf '\n'
  read -r -p "  Pressione ENTER para continuar..." _
}

while true; do
  exibir_menu

  printf '\n'
  read -r -p "  SIMPLE > " escolha

  case "$escolha" in
  1)
    clear
    exibir_cabecalho
    printf '\n  EXERCÍCIOS\n\n'
    printf '  O catálogo de exercícios será implementado aqui.\n'
    pausar
    ;;
  2)
    clear
    exibir_cabecalho
    printf '\n  AVALIAÇÕES\n\n'
    printf '  O sistema de avaliações será implementado aqui.\n'
    pausar
    ;;
  3)
    clear
    exibir_cabecalho
    printf '\n  MEU PROGRESSO\n\n'
    printf '  Seu histórico de exercícios e pontos aparecerá aqui.\n'
    pausar
    ;;
  4)
    clear
    exibir_cabecalho
    printf '\n  MEU PERFIL\n\n'
    printf '  Seus dados de participante aparecerão aqui.\n'
    pausar
    ;;
  0)
    clear
    printf '\n  SIMPLE encerrado.\n\n'
    break
    ;;
  *)
    printf '\n  Opção inválida. Escolha uma opção do menu.\n'
    pausar
    ;;
  esac
done
