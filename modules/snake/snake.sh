#!/usr/bin/env bash

snake_menu() {
  local mode="${SIMPLE_MODE:-local}"
  local socket_path="${SNAKE_SOCKET:-}"
  local -a arguments

  if ! command -v python3 >/dev/null 2>&1; then
    ui_error "Python 3 é necessário para iniciar o Snake."
    ui_pause
    return 0
  fi

  if [[ "$mode" == "local" ]]; then
    arguments=(--solo)
  elif [[ "$mode" == "broker" || "$mode" == "proxy" ]]; then
    if [[ -z "$socket_path" ]]; then
      ui_error "Socket do Snake não configurado. Defina SNAKE_SOCKET."
      ui_pause
      return 0
    fi
    arguments=(--socket "$socket_path")
  else
    ui_error "Modo SIMPLE inválido para iniciar o Snake."
    ui_pause
    return 0
  fi

  python3 -m modules.snake.client.client "${arguments[@]}"
}
