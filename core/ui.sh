#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE INTERFACE
# Responsável pela apresentação visual
# ==========================================

ui_clear() {
  if [[ -t 1 && -n "${TERM:-}" ]]; then
    clear 2>/dev/null || printf '\033[2J\033[H'
  else
    printf '\n\n'
  fi
}

ui_width() {
  local width

  width=$(tput cols 2>/dev/null || printf '80')

  [[ "$width" =~ ^[0-9]+$ ]] || width=80

  if ((width > 72)); then
    width=72
  fi

  if ((width < 1)); then
    width=1
  fi

  printf '%s' "$width"
}

ui_separator() {
  local width

  width=$(ui_width)

  printf '%*s\n' "$width" '' | tr ' ' '─'
}

ui_header() {
  local width

  width=$(ui_width)

  if ((width >= 72)); then

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

  else

    printf '\n'
    printf '  SIMPLE\n'
    printf '  AMBIENTE DE APRENDIZAGEM\n'
    printf '\n'

  fi
}

ui_message() {
  printf '\n  %s\n' "$1"
}

ui_pause() {
  printf '\n'
  read -r -p "  Pressione ENTER para continuar..." _
}
