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

  if [[ "${COLUMNS:-}" =~ ^[0-9]+$ ]] && ((COLUMNS > 0)); then
    width="$COLUMNS"
  else
    width=$(tput cols 2>/dev/null || printf '80')
  fi

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
  local line

  width=$(ui_width)
  printf -v line '%*s' "$width" ''

  line=${line// /─}

  printf '%s\n' "$line"
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
    ui_wrap_text "AMBIENTE DE APRENDIZAGEM" "$width" 2
    printf '\n'

  fi
}

ui_message() {
  printf '\n'
  ui_wrap_text "$1" "$(ui_width)" 2
}

ui_pause() {
  printf '\n'
  read -r -p "  Pressione ENTER para continuar..." _
}

ui_read_line() {
  local variable_name="$1"
  local prompt="$2"
  local silent="${3:-0}"
  local read_value

  if [[ "$silent" == "1" ]]; then
    read -r -s -p "$prompt" read_value || return 1
    printf '\n'
  else
    read -r -p "$prompt" read_value || return 1
  fi

  read_value="${read_value%$'\r'}"
  printf -v "$variable_name" '%s' "$read_value"
}

ui_read_multiline() {
  local variable_name="$1"
  local line
  local captured_content=""
  local finished=0

  printf '  Digite .fim sozinho em uma linha para concluir.\n'
  while IFS= read -r line; do
    line="${line%$'\r'}"
    if [[ "$line" == ".fim" ]]; then
      finished=1
      break
    fi
    captured_content+="$line"$'\n'
  done
  if (( ! finished )); then
    printf '  Entrada interrompida antes de .fim.\n' >&2
    return 1
  fi

  captured_content="${captured_content%$'\n'}"
  printf -v "$variable_name" '%s' "$captured_content"
}

ui_wrap_text() {
  local text="$1"
  local width="$2"
  local indent="${3:-2}"
  local prefix
  local available
  local line

  ((indent > width - 1)) && indent=$((width - 1))
  printf -v prefix '%*s' "$indent" ''
  available=$((width - indent))
  ((available < 1)) && available=1

  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ -z "$line" ]]; then
      printf '\n'
      continue
    fi
    printf '%s' "$prefix"
    while ((${#line} > available)); do
      local part="${line:0:available}"
      local cut="${part% *}"
      [[ -z "$cut" ]] && cut="$part"
      printf '%s\n' "$cut"
      line="${line#"$cut"}"
      line="${line# }"
      printf '%s' "$prefix"
    done
    printf '%s\n' "$line"
  done <<<"$text"
}

# ==========================================
# SIMPLE - APRESENTAÇÃO DE EXERCÍCIOS
# ==========================================

ui_exercise_header() {
  local code="$1"
  local title="$2"
  local width

  width=$(ui_width)

  ui_separator
  printf '\n'
  ui_wrap_text "EXERCÍCIO: $code" "$width" 2
  printf '\n'
  ui_wrap_text "$title" "$width" 2
  printf '\n'
  ui_separator
}

ui_exercise_section() {
  local title="$1"
  local width

  width=$(ui_width)
  printf '\n'
  ui_wrap_text "$title" "$width" 2
  ui_separator
  printf '\n'
}

ui_code_block() {
  local content="$1"
  local width
  local content_width
  local line
  local prefix

  printf '\n'
  ui_wrap_text "┌─ EXEMPLO DE EXECUÇÃO" "$(ui_width)" 2
  width=$(ui_width)
  prefix="  │ "
  if ((width < ${#prefix})); then
    prefix="│ "
  fi
  if ((width < ${#prefix})); then
    prefix=""
  fi
  content_width=$((width - ${#prefix}))
  ((content_width < 1)) && content_width=1
  printf '%s\n' "$prefix"

  while IFS= read -r line || [[ -n "$line" ]]; do
    while ((${#line} > content_width)); do
      printf '%s%s\n' "$prefix" "${line:0:content_width}"
      line="${line:content_width}"
    done
    printf '%s%s\n' "$prefix" "$line"
  done <<<"$content"

  printf '%s\n' "$prefix"
  ui_separator
  printf '\n'
}
