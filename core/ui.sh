#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE INTERFACE
# Responsável pela apresentação visual
# ==========================================

# ---------------------------------------------------------------------------
# Detecção de ambiente
# ---------------------------------------------------------------------------

ui_is_windows_terminal() {
  case "$(uname -s 2>/dev/null)" in
    MINGW*|MSYS*|CYGWIN*|Windows_NT) return 0 ;;
    *) [[ "${WSL_DISTRO_NAME:-}" ]] && [[ -n "${WINDOWS_INTEROP:-}" ]] && return 0 ;;
  esac
  return 1
}

ui_colors_enabled() {
  if ui_is_windows_terminal; then
    return 1
  fi
  [[ -t 1 ]] && [[ -n "${TERM:-}" ]] && [[ "${TERM}" != "dumb" ]] && [[ -z "${NO_COLOR:-}" ]]
}

ui_color() {
  if ui_colors_enabled; then
    printf '%b' "$1"
  fi
}

ui_color_reset()   { ui_color '\033[0m'; }
ui_color_bold()    { ui_color '\033[1m'; }
ui_color_dim()     { ui_color '\033[2m'; }
ui_color_primary() { ui_color '\033[1;36m'; }   # ciano
ui_color_success() { ui_color '\033[1;32m'; }   # verde
ui_color_error()   { ui_color '\033[1;31m'; }   # vermelho
ui_color_warning() { ui_color '\033[1;33m'; }   # amarelo

# ---------------------------------------------------------------------------
# Sanitização de saída de terminal
#
# Conteúdo persistido ou vindo de usuários nunca deve injetar sequências de
# controle no terminal. Esta função remove controles C0 (0x00-0x1F, exceto
# newline/tab), C1 (0x80-0x9F) e DEL (0x7F), preservando texto legível.
# ---------------------------------------------------------------------------

ui_sanitize() {
  local LC_ALL=C
  local input="$1"
  local output=""
  local index=0
  local length
  local byte
  local second
  local third
  local fourth

  input=${input//$'\t'/ }
  input=${input//$'\u2028'/ }
  input=${input//$'\u2029'/ }
  input=${input//$'\u202a'/}
  input=${input//$'\u202b'/}
  input=${input//$'\u202c'/}
  input=${input//$'\u202d'/}
  input=${input//$'\u202e'/}
  input=${input//$'\u2066'/}
  input=${input//$'\u2067'/}
  input=${input//$'\u2068'/}
  input=${input//$'\u2069'/}
  length=${#input}

  while ((index < length)); do
    printf -v byte '%d' "'${input:index:1}"
    if ((byte < 0x20 || byte == 0x7f)); then
      ((byte == 0x0a)) && output+=$'\n'
      ((index += 1))
      continue
    fi

    if ((byte >= 0xc2 && byte <= 0xdf && index + 1 < length)); then
      printf -v second '%d' "'${input:index+1:1}"
      if ((second >= 0x80 && second <= 0xbf)); then
        if ((byte == 0xc2 && second <= 0x9f)); then
          ((index += 2))
          continue
        fi
        output+="${input:index:2}"
        ((index += 2))
        continue
      fi
    elif ((byte >= 0xe0 && byte <= 0xef && index + 2 < length)); then
      printf -v second '%d' "'${input:index+1:1}"
      printf -v third '%d' "'${input:index+2:1}"
      if ((second >= 0x80 && second <= 0xbf &&
        third >= 0x80 && third <= 0xbf)) &&
        ! ((byte == 0xe0 && second < 0xa0)) &&
        ! ((byte == 0xed && second > 0x9f)); then
        if ((byte == 0xe2 && second == 0x80 && third >= 0xa8 && third <= 0xae)) ||
          ((byte == 0xe2 && second == 0x81 && third >= 0xa6 && third <= 0xa9)); then
          ((index += 3))
          continue
        fi
        output+="${input:index:3}"
        ((index += 3))
        continue
      fi
    elif ((byte >= 0xf0 && byte <= 0xf4 && index + 3 < length)); then
      printf -v second '%d' "'${input:index+1:1}"
      printf -v third '%d' "'${input:index+2:1}"
      printf -v fourth '%d' "'${input:index+3:1}"
      if ((second >= 0x80 && second <= 0xbf &&
        third >= 0x80 && third <= 0xbf &&
        fourth >= 0x80 && fourth <= 0xbf)) &&
        ! ((byte == 0xf0 && second < 0x90)) &&
        ! ((byte == 0xf4 && second > 0x8f)); then
        output+="${input:index:4}"
        ((index += 4))
        continue
      fi
    elif ((byte >= 0x20 && byte <= 0x7e)); then
      output+="${input:index:1}"
    fi

    ((index += 1))
  done

  printf '%s' "$output"
}

# ---------------------------------------------------------------------------
# Utilitários de tela
# ---------------------------------------------------------------------------

ui_clear() {
  if ui_can_interact && ! ui_is_windows_terminal; then
    clear 2>/dev/null || printf '\033[2J\033[H'
  else
    printf '\n\n'
  fi
}

ui_width() {
  local width

  if [[ "${COLUMNS:-}" =~ ^[0-9]+$ ]]; then
    width="${COLUMNS#"${COLUMNS%%[!0]*}"}"
    [[ -n "$width" ]] || width=0
    if ((${#width} > 3)); then
      width=120
    else
      width=$((width))
    fi
  else
    width=$(tput cols 2>/dev/null || printf '80')
  fi

  [[ "$width" =~ ^[0-9]+$ ]] || width=80

  if ((width > 120)); then
    width=120
  fi

  if ((width < 12)); then
    width=12
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

# ---------------------------------------------------------------------------
# Cabeçalho
# ---------------------------------------------------------------------------

ui_header() {
  if (( $(ui_width) < 24 )); then
    ui_color_bold
    ui_color_primary
    printf ' SIMPLE\n'
    ui_color_reset
    return 0
  fi

  ui_color_bold
  ui_color_primary
  printf ' SIMPLE '
  ui_color_reset
  ui_color_dim
  printf '· ambiente de aprendizagem\n'
  ui_color_reset
}

# ---------------------------------------------------------------------------
# Barra de status (consistente em todas as telas)
# ---------------------------------------------------------------------------

ui_statusline() {
  local context="${1:-}"
  local width
  local area
  local status

  width=$(ui_width)
  area="${context:-início}"
  if [[ -z "$context" ]] && (( ${#NAV_STACK[@]} > 0 )); then
    area="${NAV_STACK[${#NAV_STACK[@]}-1]}"
  fi
  status="$(ui_sanitize "${CURRENT_USERNAME:-desconectado}") · $(ui_sanitize "${SIMPLE_MODE:-local}") · $(ui_sanitize "$area")"

  if ((${#status} + 2 > width)); then
    status="$(ui_sanitize "${CURRENT_USERNAME:-desconectado}") · $(ui_sanitize "${SIMPLE_MODE:-local}")"
  fi

  ui_color_dim
  ui_wrap_text "$status" "$width" 1
  ui_color_reset
}

ui_screen_begin() {
  local title="$1"

  ui_clear
  ui_header
  ui_statusline "$title"
  nav_show_breadcrumb
  ui_separator
  printf '\n'
  ui_color_bold
  ui_color_primary
  ui_wrap_text "$(ui_sanitize "$title")" "$(ui_width)" 1
  ui_color_reset
  printf '\n'
}

# ---------------------------------------------------------------------------
# Mensagens com feedback visual
# ---------------------------------------------------------------------------

ui_message() {
  local message="$1"
  local style="${2:-info}"
  local color_fn

  case "$style" in
    success) color_fn=ui_color_success ;;
    error)   color_fn=ui_color_error ;;
    warning) color_fn=ui_color_warning ;;
    info|*)  color_fn=ui_color_primary ;;
  esac

  printf '\n'
  $color_fn
  ui_wrap_text "$message" "$(ui_width)" 2
  ui_color_reset
}

ui_success() { ui_message "$1" success; }
ui_error()   { ui_message "$1" error; }
ui_warn()    { ui_message "$1" warning; }

ui_pause() {
  printf '\n'
  if ! ui_can_interact; then
    return 0
  fi
  ui_color_dim
  read -r -p "  Pressione ENTER para continuar..." _
  ui_color_reset
}

ui_can_interact() {
  [[ -t 0 && -t 1 && -t 2 && -n "${TERM:-}" && "$TERM" != "dumb" ]]
}

# ---------------------------------------------------------------------------
# Estado vazio
# ---------------------------------------------------------------------------

ui_empty_state() {
  local title="$1"
  local hint="${2:-Nenhum item disponível.}"
  local width

  width=$(ui_width)
  printf '\n'
  ui_color_dim
  ui_wrap_text "$title" "$width" 2
  ui_separator
  ui_wrap_text "$hint" "$width" 2
  ui_color_reset
  printf '\n'
}

ui_section() {
  local title="$1"
  local width

  width=$(ui_width)
  printf '\n'
  ui_color_bold
  ui_wrap_text "$title" "$width" 2
  ui_color_reset
  ui_separator
}

ui_labeled_value() {
  local label="$1"
  local value="$2"
  local width="$3"
  local indent="${4:-2}"

  ui_wrap_text "$label: $value" "$width" "$indent"
}

# ---------------------------------------------------------------------------
# Badge / etiqueta colorida (status, tags, níveis)
# ---------------------------------------------------------------------------

ui_badge() {
  local text="$1"
  local style="${2:-info}"
  local color_fn

  case "$style" in
    success|approved) color_fn=ui_color_success ;;
    error|rejected)   color_fn=ui_color_error ;;
    warning|pending)  color_fn=ui_color_warning ;;
    info|*)           color_fn=ui_color_primary ;;
  esac

  printf ' ['
  $color_fn
  ui_color_bold
  printf '%s' "$(ui_sanitize "$text")"
  ui_color_reset
  printf ']'
}

# ---------------------------------------------------------------------------
# Painel com borda e título destacado
# ---------------------------------------------------------------------------

ui_panel() {
  local title="$1"
  local width
  local inner
  local border_top
  local border_bottom
  local pad

  width=$(ui_width)
  title=$(ui_tui_label "$title") || return 1
  inner=$((width - 4))

  ((inner < 1)) && inner=1

  printf '\n'
  ui_color_bold
  ui_color_primary
  printf '  ┌'
  printf -v border_top '%*s' "$inner" ''
  printf '%s' "${border_top// /─}"
  printf '┐\n'

  printf '  │ %s' "$title"
  pad=$((inner - ${#title} - 1))
  ((pad < 0)) && pad=0
  printf -v border_top '%*s' "$pad" ''
  printf '%s │\n' "${border_top// / }"
  ui_color_reset
}

ui_panel_end() {
  local width
  width=$(ui_width)
  ui_color_bold
  ui_color_primary
  printf '  └'
  printf -v border_bottom '%*s' "$((width - 4))" ''
  printf '%s' "${border_bottom// /─}"
  printf '┘\n'
  ui_color_reset
  printf '\n'
}

ui_home_intro() {
  local username
  local width

  username=$(ui_tui_label "$1") || return 1
  width=$(ui_width)
  ui_section "BEM-VINDO"
  ui_wrap_text "Olá, $username." "$width" 2
  ui_wrap_text \
    "Pratique, acompanhe seu progresso e ajude a comunidade avaliando entregas." \
    "$width" 2
}

ui_home_metrics() {
  local points
  local completed
  local reviews
  local width
  local summary

  points=$(ui_tui_label "$1") || return 1
  completed=$(ui_tui_label "$2") || return 1
  reviews=$(ui_tui_label "$3") || return 1
  width=$(ui_width)
  summary="Pontos: $points  |  Aprovados: $completed  |  Para avaliar: $reviews"

  ui_section "SEU RESUMO"
  if ((${#summary} + 4 <= width)); then
    ui_wrap_text "$summary" "$width" 2
  else
    ui_wrap_text "Pontos: $points" "$width" 2
    ui_wrap_text "Aprovados: $completed" "$width" 2
    ui_wrap_text "Para avaliar: $reviews" "$width" 2
  fi
}

# ---------------------------------------------------------------------------
# Menu de escolha
# ---------------------------------------------------------------------------

ui_menu_choice() {
  local variable_name="$1"
  local prompt="$2"
  shift 2

  local -a keys=()
  local -a labels=()
  local entry
  local key
  local label
  local index
  local input

  if (($# == 0)); then
    printf '  Nenhuma opção disponível para este menu.\n' >&2
    return 2
  fi

  for entry in "$@"; do
    if [[ "$entry" != *"|"* ]]; then
      printf '  Opção de menu malformada.\n' >&2
      return 2
    fi
    key="${entry%%|*}"
    label="${entry#*|}"
    if [[ -z "$key" || "$key" == *[!a-zA-Z0-9_-]* ]]; then
      printf '  Chave de menu inválida.\n' >&2
      return 2
    fi
    label=$(ui_tui_label "$label") || return 1
    keys+=("$key")
    labels+=("$label")
  done
  prompt=$(ui_tui_label "$prompt") || return 1

  printf '\n  %s\n' "$prompt"
  for index in "${!keys[@]}"; do
    ui_color_primary
    printf '  [%s] ' "${keys[$index]}"
    ui_color_reset
    printf '%s\n' "$(ui_sanitize "${labels[$index]}")"
  done
  while true; do
    ui_read_line input "  OPÇÃO > " || return 1
    for key in "${keys[@]}"; do
      if [[ "$input" == "$key" ]]; then
        printf -v "$variable_name" '%s' "$key"
        return 0
      fi
    done
    ui_error "Opção inválida."
  done
}

ui_confirm() {
  local prompt="$1"
  local confirmation
  prompt=$(ui_tui_label "$prompt") || return 1

  if ui_can_interact && command -v gum >/dev/null 2>&1; then
    gum confirm --affirmative="Sim" --negative="Não" "$prompt"
    return $?
  fi

  ui_read_line confirmation "$prompt [s/N] > " || return 1
  [[ "${confirmation,,}" == "s" ]]
}

ui_tui_label() {
  local label="$1"

  label=$(ui_sanitize "$label") || return 1
  label=${label//$'\n'/ }
  label=${label//$'\r'/ }
  printf '%s' "$label"
}

ui_read_line() {
  local variable_name="$1"
  local prompt="$2"
  local silent="${3:-0}"
  local read_value

  prompt=$(ui_tui_label "$prompt") || return 1
  if ui_can_interact && command -v gum >/dev/null 2>&1; then
    if [[ "$silent" == "1" ]]; then
      read_value=$(gum input --password --prompt "$prompt") || return 1
    else
      read_value=$(gum input --prompt "$prompt") || return 1
    fi
  elif [[ "$silent" == "1" ]]; then
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

  if ui_can_interact && command -v gum >/dev/null 2>&1; then
    printf '  Digite o texto; pressione Ctrl+D para concluir.\n'
    captured_content=$(gum write --header "Digite o texto e conclua com Ctrl+D") || return 1
    printf -v "$variable_name" '%s' "$captured_content"
    return 0
  fi

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

  text=$(ui_sanitize "$text") || return 1
  [[ "$width" =~ ^[0-9]+$ ]] || width=80
  ((width < 1)) && width=1
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

# ---------------------------------------------------------------------------
# Navegação / Breadcrumb
# ---------------------------------------------------------------------------

# Pilha de navegação (array global)
declare -g -a NAV_STACK=()

nav_push() {
  local label="$1"
  NAV_STACK+=("$(ui_tui_label "$label")")
}

nav_pop() {
  if (( ${#NAV_STACK[@]} > 0 )); then
    unset 'NAV_STACK[${#NAV_STACK[@]}-1]'
    NAV_STACK=("${NAV_STACK[@]}")
  fi
}

nav_clear() {
  NAV_STACK=()
}

nav_show_breadcrumb() {
  local width
  local crumb
  local path=""

  width=$(ui_width)

  if (( ${#NAV_STACK[@]} == 0 )); then
    return 0
  fi

  for crumb in "${NAV_STACK[@]}"; do
    path+=" > $crumb"
  done
  path="${path:3}" # remove o primeiro " > "

  ui_color_dim
  ui_wrap_text "$(ui_sanitize "$path")" "$width" 2
  ui_color_reset
}

# ---------------------------------------------------------------------------
# Apresentação de exercícios
# ---------------------------------------------------------------------------

ui_exercise_header() {
  local code="$1"
  local title="$2"
  local width

  width=$(ui_width)
  ui_screen_begin "EXERCÍCIO $code"
  ui_wrap_text "$title" "$width" 2
}

ui_exercise_section() {
  ui_section "$1"
}

ui_code_block() {
  local content="$1"
  local line

  printf '\n'
  content=$(ui_sanitize "$content") || return 1
  while IFS= read -r line || [[ -n "$line" ]]; do
    ui_color_dim
    printf '  │ '
    ui_color_reset
    printf '%s\n' "$line"
  done <<<"$content"
  printf '\n'
}

# ==========================================
# BANNER DE LOGIN - SIMPLE
# Arte ASCII responsiva, sem dependências
# ==========================================

UI_BANNER_TAGLINE="AMBIENTE DE APRENDIZAGEM"
UI_BANNER_ART_WIDTH=70
UI_BANNER_WIDE_MIN_WIDTH=72
UI_BANNER_COMPACT_MIN_WIDTH=30
UI_BANNER_FRAME_INNER_WIDTH=68
UI_BANNER_LOGO_WIDTH=46

_ui_banner_pad() {
  local width="$1"
  local content_width="$2"
  local padding=$(((width - content_width) / 2))
  ((padding < 0)) && padding=0
  printf '%*s' "$padding" ''
}

ui_simple_banner_variant() {
  local width="$1"

  if ((width >= UI_BANNER_WIDE_MIN_WIDTH)); then
    printf 'wide\n'
  elif ((width >= UI_BANNER_COMPACT_MIN_WIDTH)); then
    printf 'compact\n'
  else
    printf 'minimal\n'
  fi
}

_ui_simple_banner_wide() {
  local width="$1"
  local line
  local left_padding
  local right_padding
  local logo_padding=$(((UI_BANNER_FRAME_INNER_WIDTH - UI_BANNER_LOGO_WIDTH) / 2))
  local border=""
  local index
  local -a art=(
    ''
    '███████╗██╗███╗   ███╗██████╗ ██╗     ███████╗'
    '██╔════╝██║████╗ ████║██╔══██╗██║     ██╔════╝'
    '███████╗██║██╔████╔██║██████╔╝██║     █████╗'
    '╚════██║██║██║╚██╔╝██║██╔═══╝ ██║     ██╔══╝'
    '███████║██║██║ ╚═╝ ██║██║     ███████╗███████╗'
    '╚══════╝╚═╝╚═╝     ╚═╝╚═╝     ╚══════╝╚══════╝'
    ''
    'AMBIENTE DE APRENDIZAGEM'
    ''
  )

  for ((index = 0; index < UI_BANNER_FRAME_INNER_WIDTH; index++)); do
    border+="─"
  done
  printf '%s┌%s┐\n' "$(_ui_banner_pad "$width" "$UI_BANNER_ART_WIDTH")" "$border"
  for line in "${art[@]}"; do
    left_padding=$logo_padding
    right_padding=$((UI_BANNER_FRAME_INNER_WIDTH - ${#line} - left_padding))
    if [[ "$line" == "$UI_BANNER_TAGLINE" ]]; then
      left_padding=$(((UI_BANNER_FRAME_INNER_WIDTH - ${#line}) / 2))
      right_padding=$((UI_BANNER_FRAME_INNER_WIDTH - ${#line} - left_padding))
    fi
    ui_color_bold
    ui_color_primary
    printf '%s│%*s%s%*s│' \
      "$(_ui_banner_pad "$width" "$UI_BANNER_ART_WIDTH")" \
      "$left_padding" "" "$line" "$right_padding" ""
    ui_color_reset
    printf '\n'
  done
  printf '%s└%s┘\n' "$(_ui_banner_pad "$width" "$UI_BANNER_ART_WIDTH")" "$border"
}

_ui_simple_banner_compact() {
  local width="$1"
  local border="+----------------------------+"
  local line
  local left_padding
  local right_padding

  printf '%s%s\n' "$(_ui_banner_pad "$width" "${#border}")" "$border"
  for line in "SIMPLE" "$UI_BANNER_TAGLINE"; do
    left_padding=$(((28 - ${#line}) / 2))
    right_padding=$((28 - ${#line} - left_padding))
    printf '%s|%*s%s%*s|\n' \
      "$(_ui_banner_pad "$width" "${#border}")" \
      "$left_padding" "" "$line" "$right_padding" ""
  done
  printf '%s%s\n' "$(_ui_banner_pad "$width" "${#border}")" "$border"
}

_ui_simple_banner_minimal() {
  local width="$1"
  local title="SIMPLE"

  ((width < ${#title})) && title="${title:0:width}"
  ui_color_bold
  ui_color_primary
  printf '%s%s' "$(_ui_banner_pad "$width" "${#title}")" "$title"
  ui_color_reset
  printf '\n'
}

ui_simple_banner() {
  local width="${1:-}"

  if [[ ! "$width" =~ ^[0-9]+$ ]] || ((width < 1)); then
    width=$(ui_width) || return 1
  fi
  if [[ ! "$width" =~ ^[0-9]+$ ]] || ((width < 1)); then
    printf '  Largura inválida para o banner de login.\n' >&2
    return 2
  fi

  printf '\n'
  case "$(ui_simple_banner_variant "$width")" in
    wide) _ui_simple_banner_wide "$width" ;;
    compact) _ui_simple_banner_compact "$width" ;;
    minimal) _ui_simple_banner_minimal "$width" ;;
  esac
  printf '\n'
}

# Exibe a opção de login no cartão, sem a descrição secundária
ui_login_option() {
  local number="$1"
  local label="$2"
  local width="$3"
  local option
  local box_width=26
  local inner_width
  local padding
  local top="┌"
  local bottom="└"
  local content
  local index

  label=$(ui_tui_label "$label") || return 1
  option="$number | $label"
  ((box_width > width)) && box_width=$width
  inner_width=$((box_width - 4))

  if ((inner_width < 1 || ${#option} > inner_width)); then
    ui_wrap_text "$option" "$width" 2
    return
  fi

  padding=$(((width - box_width) / 2))
  for ((index = 0; index < box_width - 2; index++)); do
    top+="─"
    bottom+="─"
  done
  top+="┐"
  bottom+="┘"
  printf '%*s%s\n' "$padding" "" "$top"
  printf -v content "%-*s" "$inner_width" "$option"
  printf '%*s│ %s │\n' "$padding" "" "$content"
  printf '%*s%s\n' "$padding" "" "$bottom"
}
