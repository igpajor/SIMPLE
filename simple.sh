#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

source "$SCRIPT_DIR/core/ui.sh"
source "$SCRIPT_DIR/core/navigation.sh"
source "$SCRIPT_DIR/core/database.sh"
source "$SCRIPT_DIR/core/auth.sh"
source "$SCRIPT_DIR/core/broker_client.sh"
source "$SCRIPT_DIR/modules/exercises.sh"
source "$SCRIPT_DIR/modules/points.sh"
source "$SCRIPT_DIR/modules/users.sh"
source "$SCRIPT_DIR/modules/evaluations.sh"
source "$SCRIPT_DIR/modules/progress.sh"
source "$SCRIPT_DIR/modules/chat.sh"
source "$SCRIPT_DIR/modules/admin.sh"
source "$SCRIPT_DIR/modules/broker_ui.sh"

BROKER_MODE="0"

case "${SIMPLE_MODE:-local}" in
broker)
  BROKER_MODE="1"
  if [[ -z "${BROKER_SOCKET:-}" ]]; then
    printf 'Socket do broker não configurado. Defina BROKER_SOCKET.\n' >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'Python 3 é necessário no modo broker.\n' >&2
    exit 1
  fi
  ;;
proxy)
  BROKER_MODE="1"
  if [[ -z "${PROXY_SOCKET:-}" ]]; then
    printf 'Socket do proxy não configurado. Defina PROXY_SOCKET.\n' >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'Python 3 é necessário no modo proxy.\n' >&2
    exit 1
  fi
  ;;
local)
  if ! db_init; then
    exit 1
  fi
  ;;
*)
  printf 'Modo inválido: use SIMPLE_MODE=local, broker ou proxy.\n' >&2
  exit 2
  ;;
esac

while true; do

  if ! users_authenticate; then
    break
  fi

  if [[ "$BROKER_MODE" == "1" ]]; then
    if ! broker_mode_menu; then
      break
    fi
    continue
  fi

  if ! menu_principal; then
    break
  fi

  case "$REPLY" in

  1)
    exercises_menu
    ;;
  2)
    evaluations_menu
    ;;
  3)
    if ! progress_menu; then
      ui_error "Não foi possível carregar Meu progresso. Consulte a mensagem acima."
      ui_pause
    fi
    ;;
  4)
    users_profile_menu
    ;;
  5)
    if users_is_admin "$CURRENT_USER_ID"; then
      admin_menu
    else
      if ! submissions_menu; then
        ui_error "Não foi possível carregar Minhas entregas. Consulte a mensagem acima."
        ui_pause
      fi
    fi
    ;;
  6)
    if users_is_admin "$CURRENT_USER_ID"; then
      if ! submissions_menu; then
        ui_error "Não foi possível carregar Minhas entregas. Consulte a mensagem acima."
        ui_pause
      fi
    else
      ui_error "Opção inválida. Escolha uma opção do menu."
      ui_pause
    fi
    ;;
  0)
    ui_clear
    ui_success "SIMPLE encerrado."
    printf '\n'
    break
    ;;
  *)
    ui_error "Opção inválida. Escolha uma opção do menu."
    ui_pause
    ;;
  esac

done
