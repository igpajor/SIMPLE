#!/usr/bin/env bash

CHAT_BUFFER_LIMIT="${CHAT_HISTORY_WINDOW:-10}"
CHAT_ROW_SEPARATOR=$'\x1f'
CHAT_TTY_STATE=""
CHAT_READER_TYPING_ACTIVE=0
CHAT_TYPING_WARNING=0
CHAT_PREVIOUS_EXIT_TRAP=""

chat_local_time() {
  local created_at="$1"
  local converted

  if ! converted=$(
    TZ="${CHAT_TIMEZONE:-America/Sao_Paulo}" \
      date -d "$created_at UTC" '+%Y-%m-%d %H:%M' 2>/dev/null
  ); then
    printf 'Não foi possível converter o horário do chat; verifique CHAT_TIMEZONE.\n' >&2
    return 1
  fi
  printf '%s' "$converted"
}

chat_render_evaluation_room_card() {
  local slot_id="$1"
  local url="$2"
  local inner_width
  local rule
  local link_line
  local padding

  inner_width=$(($(ui_width) - 8))
  ((inner_width < 12)) && inner_width=12
  ((inner_width > 72)) && inner_width=72
  printf -v rule '%*s' "$inner_width" ''
  rule="${rule// /─}"
  ui_color_success
  ui_wrap_text "SALA DA AVALIAÇÃO · SLOT #$slot_id" "$(ui_width)" 2
  printf '  ┌%s┐\n' "$rule"
  while IFS= read -r link_line; do
    padding=$((inner_width - ${#link_line}))
    ((padding < 0)) && padding=0
    printf '  │%s%*s│\n' "$link_line" "$padding" ''
  done < <(printf '%s\n' "$url" | fold -w "$inner_width")
  printf '  └%s┘\n' "$rule"
  ui_color_reset
}

chat_render_message_row() {
  local encoded="$1"
  local suppress_date="${2:-0}"
  local message_id_hex
  local username_hex
  local user_id_hex
  local body_hex
  local created_at_hex
  local pinned_hex
  local reactions_hex
  local mentioned_hex
  local message_id
  local username
  local user_id
  local body
  local created_at
  local pinned_at
  local reactions
  local mentioned
  local local_datetime
  local date_label
  local time_label
  local color_index
  local color_code
  local char
  local index
  local code
  local -a colors=(32 33 34 35 36 91 92 93 94 95 96)

  IFS="$CHAT_ROW_SEPARATOR" read -r message_id_hex username_hex user_id_hex body_hex \
    created_at_hex pinned_hex reactions_hex mentioned_hex <<<"$encoded"
  message_id=$(db_hex_decode "$message_id_hex") || return 1
  username=$(db_hex_decode "$username_hex") || return 1
  user_id=$(db_hex_decode "$user_id_hex") || return 1
  body=$(db_hex_decode "$body_hex") || return 1
  created_at=$(db_hex_decode "$created_at_hex") || return 1
  pinned_at=$(db_hex_decode "$pinned_hex") || return 1
  reactions=$(db_hex_decode "$reactions_hex") || return 1
  mentioned=$(db_hex_decode "$mentioned_hex") || return 1
  [[ "$message_id" =~ ^[0-9]+$ && "$user_id" =~ ^[0-9]+$ ]] || return 1
  if [[ "$body" =~ ^SALA\ AO\ VIVO\ ·\ AVALIAÇÃO\ \#([0-9]+)\ ·\ (https://meet\.jit\.si/SIMPLE-Eval-[a-f0-9]{48})$ ]]; then
    chat_render_evaluation_room_card "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
    CHAT_LAST_AUTHOR=""
    return 0
  fi
  local_datetime=$(chat_local_time "$created_at")
  date_label="${local_datetime% *}"
  time_label="${local_datetime##* }"
  if [[ "$date_label" != "${CHAT_LAST_DATE:-}" ]]; then
    CHAT_LAST_DATE="$date_label"
    CHAT_LAST_AUTHOR=""
    if ((!suppress_date)); then
      ui_color_dim
      ui_wrap_text "$date_label" "$(ui_width)" 2
      ui_color_reset
    fi
  fi

  if [[ "$mentioned" == "1" ]]; then
    ui_color_warning
    ui_wrap_text "[#$message_id $time_label] @ você · $username: $body" \
      "$(ui_width)" 2
  elif [[ "$user_id" == "$CURRENT_USER_ID" ]]; then
    ui_color_success
    if [[ "${CHAT_LAST_AUTHOR:-}" == "$username" ]]; then
      ui_wrap_text "[#$message_id $time_label] $body" "$(ui_width)" 2
    else
      ui_wrap_text "[#$message_id $time_label] você: $body" "$(ui_width)" 2
    fi
  else
    color_index=0
    for ((index = 0; index < ${#username}; index++)); do
      char="${username:index:1}"
      printf -v code '%d' "'$char"
      color_index=$(((color_index + code) % ${#colors[@]}))
    done
    color_code="${colors[color_index]}"
    ui_color "\033[0;${color_code}m"
    if [[ "${CHAT_LAST_AUTHOR:-}" == "$username" ]]; then
      ui_wrap_text "[#$message_id $time_label] $body" "$(ui_width)" 2
    else
      ui_wrap_text "[#$message_id $time_label] $username: $body" \
        "$(ui_width)" 2
    fi
  fi
  CHAT_LAST_AUTHOR="$username"
  ui_color_reset
  if [[ -n "$pinned_at" ]]; then
    ui_color_warning
    ui_wrap_text "  📌 mensagem fixada · #$message_id" "$(ui_width)" 2
    ui_color_reset
  fi
  if [[ -n "$reactions" ]]; then
    ui_color_content
    ui_wrap_text "  $reactions" "$(ui_width)" 2
    ui_color_reset
  fi
}

chat_render_buffer() {
  local count="${#CHAT_BUFFER[@]}"
  local row

  CHAT_LAST_DATE=""
  if ((count == 0)); then
    if [[ "${CHAT_LAST_ID:-0}" == "0" ]]; then
      ui_empty_state "Ainda não há mensagens." "Envie a primeira mensagem para esta sala."
    else
      ui_empty_state "Nenhuma mensagem nova." "A conversa continua disponível ao atualizar."
    fi
    return 0
  fi
  ui_section "CONVERSA · últimas $count mensagens"
  for row in "${CHAT_BUFFER[@]}"; do
    chat_render_message_row "$row" || return 1
  done
  if [[ "${CHAT_HISTORY_HAS_MORE:-False}" == "True" ]]; then
    ui_color_dim
    ui_wrap_text "Há mensagens anteriores · use /historico." "$(ui_width)" 2
    ui_color_reset
  fi
}

chat_render_pinned() {
  local response="$1"
  local rows
  local row
  local id_hex
  local username_hex
  local body_hex
  local id
  local username
  local body

  rows=$(broker_response_rows "$response" --separator "$CHAT_ROW_SEPARATOR" pinned) ||
    return 1
  [[ -n "$rows" ]] || return 0
  ui_color_warning
  ui_wrap_text "FIXADAS" "$(ui_width)" 2
  while IFS= read -r row; do
    [[ -n "$row" ]] || continue
    IFS="$CHAT_ROW_SEPARATOR" read -r id_hex username_hex body_hex _ \
      <<<"$row"
    id=$(db_hex_decode "$id_hex") || return 1
    username=$(db_hex_decode "$username_hex") || return 1
    body=$(db_hex_decode "$body_hex") || return 1
    ui_wrap_text "📌 #$id · $(ui_sanitize "$username"): $(ui_sanitize "$body")" \
      "$(ui_width)" 2
  done <<<"$rows"
  ui_color_reset
}

chat_render_online_users() {
  local response="$1"
  local online_users
  local username_hex
  local username
  local joined
  local -a names=()

  online_users=$(broker_response_strings "$response" online_users) || return 1
  while IFS= read -r username_hex; do
    [[ -n "$username_hex" ]] || continue
    username=$(db_hex_decode "$username_hex") || return 1
    if [[ "$username" == "$CURRENT_USERNAME" ]]; then
      names+=("você")
    else
      names+=("$username")
    fi
  done <<<"$online_users"
  printf -v joined '%s, ' "${names[@]}"
  joined="${joined%, }"
  ui_color_success
  if ((${#names[@]})); then
    ui_wrap_text "Online (${#names[@]}): $joined" \
      "$(ui_width)" 2
  else
    ui_wrap_text "Ninguém online." "$(ui_width)" 2
  fi
  ui_color_reset
}

chat_render_typing_users() {
  local response="$1"
  local typing_users
  local username_hex
  local username
  local joined
  local -a names=()

  typing_users=$(broker_response_strings "$response" typing_users) || return 1
  while IFS= read -r username_hex; do
    [[ -n "$username_hex" ]] || continue
    username=$(db_hex_decode "$username_hex") || return 1
    names+=("$username")
  done <<<"$typing_users"
  if ((${#names[@]})); then
    printf -v joined '%s, ' "${names[@]}"
    joined="${joined%, }"
    ui_color_success
    ui_wrap_text "Digitando: $joined" \
      "$(ui_width)" 2
    ui_color_reset
  fi
}

chat_mark_typing() {
  local active="$1"
  local response

  response=$(printf '%s\0%s\0%s\0' \
    "$CURRENT_SESSION_TOKEN" "$CHAT_CHANNEL" "$active" |
    broker_request chat.typing token channel active) || return 1
  broker_check_response "$response"
}

chat_append_response_messages() {
  local response="$1"
  local rows
  local row
  local window_rows

  rows=$(broker_response_rows "$response" --separator "$CHAT_ROW_SEPARATOR" messages) ||
    return 1
  CHAT_NEW_MESSAGE_COUNT=0
  while IFS= read -r row; do
    if [[ -n "$row" ]]; then
      ((CHAT_NEW_MESSAGE_COUNT += 1))
    fi
  done <<<"$rows"
  window_rows=$(broker_response_rows "$response" --separator "$CHAT_ROW_SEPARATOR" window) ||
    return 1
  CHAT_BUFFER=()
  while IFS= read -r row; do
    [[ -n "$row" ]] && CHAT_BUFFER+=("$row")
  done <<<"$window_rows"
  while ((${#CHAT_BUFFER[@]} > CHAT_BUFFER_LIMIT)); do
    CHAT_BUFFER=("${CHAT_BUFFER[@]:1}")
  done
}

chat_pause_for_response() {
  printf '\n'
  ui_color_dim
  read -r -p "  Pressione ENTER para voltar..." _
  ui_color_reset
}

chat_show_result_rows() {
  local response="$1"
  local field="$2"
  local title="$3"
  local rows
  local row
  local has_more
  local empty_hint

  rows=$(broker_response_rows "$response" --separator "$CHAT_ROW_SEPARATOR" "$field") ||
    return 1
  has_more=$(broker_response_field "$response" has_more) || has_more="False"
  ui_screen_begin "$title"
  if [[ -z "$rows" ]]; then
    empty_hint="Tente outra busca."
    [[ "$title" == HISTÓRICO* ]] && empty_hint="Não há mais mensagens anteriores."
    ui_empty_state "Nenhum resultado." "$empty_hint"
  else
    while IFS= read -r row; do
      [[ -n "$row" ]] && chat_render_message_row "$row"
    done <<<"$rows"
    if [[ "$has_more" == "True" ]]; then
      ui_color_dim
      ui_wrap_text "Há mensagens anteriores · use /historico novamente para continuar." \
        "$(ui_width)" 2
      ui_color_reset
    fi
  fi
  chat_pause_for_response
}

chat_command() {
  local command="$1"
  local argument="${2:-}"
  local rest="${3:-}"
  local response
  local channel_response
  local rows
  local before_id
  local action
  local _channel
  local _name
  local _kind
  local _id
  local _message_id
  local _reason
  local _status
  local _at
  local _reporter
  local _author
  local _body

  case "$command" in
    /ajuda)
      ui_screen_begin "AJUDA DO CHAT"
      ui_wrap_text "/sair · /quem · /salas · /sala global|help|ajuda|exercise:CODIGO" "$(ui_width)" 2
      ui_wrap_text "/dm usuario · /historico · /buscar termo · /limpar" "$(ui_width)" 2
      ui_wrap_text "/reagir ID 👍|❤️|😂|+1 · /denunciar ID motivo" "$(ui_width)" 2
      if [[ "$CURRENT_USER_IS_ADMIN" == "True" ]]; then
        ui_wrap_text "/fixar ID · /desafixar ID · /ocultar ID · /restaurar ID · /relatorios · /apagar-historico" \
          "$(ui_width)" 2
      fi
      chat_pause_for_response
      ;;
    /quem)
      response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
        broker_request chat.dashboard token) || return 1
      if ! broker_check_response "$response"; then
        return 1
      fi
      ui_screen_begin "PESSOAS ONLINE"
      chat_render_online_users "$response" || return 1
      chat_pause_for_response
      ;;
    /salas)
      response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
        broker_request chat.rooms token) || return 1
      if ! broker_check_response "$response"; then
        return 1
      fi
      rows=$(broker_response_rows "$response" --separator "$CHAT_ROW_SEPARATOR" rooms) ||
        return 1
      ui_screen_begin "SALAS DO CHAT"
      while IFS= read -r row; do
        [[ -n "$row" ]] || continue
        IFS="$CHAT_ROW_SEPARATOR" read -r _channel _name _kind <<<"$row"
        _channel=$(db_hex_decode "$_channel") || return 1
        _name=$(db_hex_decode "$_name") || return 1
        ui_wrap_text "/sala $_channel · $_name" "$(ui_width)" 2
      done <<<"$rows"
      chat_pause_for_response
      ;;
    /sala)
      [[ -n "$argument" ]] || {
        ui_error "Uso: /sala global|ajuda|exercise:CODIGO"
        return 0
      }
      case "$argument" in
        global) CHAT_CHANNEL="global"; CHAT_ROOM_NAME="Chat global" ;;
        ajuda|help) CHAT_CHANNEL="help"; CHAT_ROOM_NAME="Ajuda" ;;
        exercise:*) CHAT_CHANNEL="$argument" ;;
        *) ui_error "Sala inválida. Use /salas para ver as opções."; return 0 ;;
      esac
      if [[ "$argument" == exercise:* ]]; then
        CHAT_ROOM_NAME="$argument"
      fi
      CHAT_LAST_ID=0
      CHAT_BUFFER=()
      CHAT_HISTORY_BEFORE=""
      CHAT_HISTORY_HAS_MORE="False"
      ;;
    /dm)
      [[ -n "$argument" ]] || {
        ui_error "Uso: /dm usuario"
        return 0
      }
      channel_response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$argument" |
        broker_request chat.dm.open token username) || return 1
      if ! broker_check_response "$channel_response"; then
        return 0
      fi
      CHAT_CHANNEL=$(broker_response_field "$channel_response" channel) || return 1
      CHAT_ROOM_NAME="DM · $argument"
      CHAT_LAST_ID=0
      CHAT_BUFFER=()
      CHAT_HISTORY_BEFORE=""
      CHAT_HISTORY_HAS_MORE="False"
      ;;
    /historico)
      if ((${#CHAT_BUFFER[@]} == 0)); then
        ui_message "Não há mensagens carregadas para buscar histórico."
        return 0
      fi
      if [[ -n "${CHAT_HISTORY_BEFORE:-}" ]]; then
        before_id="$CHAT_HISTORY_BEFORE"
      else
        IFS="$CHAT_ROW_SEPARATOR" read -r before_id _ <<<"${CHAT_BUFFER[0]}"
        before_id=$(db_hex_decode "$before_id") || return 1
      fi
      response=$(printf '%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$CHAT_CHANNEL" "$before_id" |
        broker_request chat.history token channel before_id) || return 1
      if ! broker_check_response "$response"; then
        return 0
      fi
      rows=$(broker_response_rows "$response" --separator "$CHAT_ROW_SEPARATOR" messages) ||
        return 1
      if [[ -n "$rows" ]]; then
        row="${rows%%$'\n'*}"
        IFS="$CHAT_ROW_SEPARATOR" read -r before_id _ <<<"$row"
        CHAT_HISTORY_BEFORE=$(db_hex_decode "$before_id") || return 1
      fi
      chat_show_result_rows "$response" messages "HISTÓRICO · $CHAT_ROOM_NAME"
      ;;
    /buscar)
      local query="$argument"
      [[ -n "$rest" ]] && query+=" $rest"
      [[ -n "$query" ]] || {
        ui_error "Uso: /buscar termo"
        return 0
      }
      response=$(printf '%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$CHAT_CHANNEL" "$query" |
        broker_request chat.search token channel query) || return 1
      if ! broker_check_response "$response"; then
        return 0
      fi
      chat_show_result_rows "$response" messages "BUSCA · $CHAT_ROOM_NAME"
      ;;
    /limpar)
      CHAT_BUFFER=()
      CHAT_LAST_ID=0
      CHAT_HISTORY_HAS_MORE="False"
      ;;
    /reagir)
      [[ "$argument" =~ ^[0-9]+$ && -n "$rest" ]] || {
        ui_error "Uso: /reagir ID 👍|❤️|😂|+1"
        return 0
      }
      response=$(printf '%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$argument" "$rest" |
        broker_request chat.react token message_id reaction) || return 1
      if broker_check_response "$response"; then
        ui_message "Reação atualizada."
      fi
      ;;
    /denunciar)
      [[ "$argument" =~ ^[0-9]+$ && -n "$rest" ]] || {
        ui_error "Uso: /denunciar ID motivo"
        return 0
      }
      response=$(printf '%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$argument" "$rest" |
        broker_request chat.report token message_id reason) || return 1
      if broker_check_response "$response"; then
        ui_message "Denúncia enviada à moderação."
      fi
      ;;
    /fixar|/desafixar|/ocultar|/restaurar)
      if [[ "$CURRENT_USER_IS_ADMIN" != "True" ]]; then
        ui_error "Acesso administrativo não autorizado."
        return 0
      fi
      [[ "$argument" =~ ^[0-9]+$ ]] || {
        ui_error "Uso: $command ID"
        return 0
      }
      case "$command" in
        /fixar) action=pin ;;
        /desafixar) action=unpin ;;
        /ocultar) action=hide ;;
        /restaurar) action=restore ;;
      esac
      response=$(printf '%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$argument" "$action" |
        broker_request chat.admin.moderate token message_id action) || return 1
      if broker_check_response "$response"; then
        ui_message "Moderação aplicada."
      fi
      ;;
    /relatorios)
      if [[ "$CURRENT_USER_IS_ADMIN" != "True" ]]; then
        ui_error "Acesso administrativo não autorizado."
        return 0
      fi
      response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
        broker_request chat.admin.reports token) || return 1
      if ! broker_check_response "$response"; then
        return 0
      fi
      rows=$(broker_response_rows "$response" --separator "$CHAT_ROW_SEPARATOR" reports) ||
        return 1
      ui_screen_begin "DENÚNCIAS ABERTAS"
      if [[ -z "$rows" ]]; then
        ui_empty_state "Nenhuma denúncia aberta."
      else
        while IFS= read -r row; do
          IFS="$CHAT_ROW_SEPARATOR" read -r _id _message_id _reason _status _at _reporter \
            _channel _author _body <<<"$row"
          _id=$(db_hex_decode "$_id") || return 1
          _message_id=$(db_hex_decode "$_message_id") || return 1
          _reason=$(db_hex_decode "$_reason") || return 1
          _reporter=$(db_hex_decode "$_reporter") || return 1
          _author=$(db_hex_decode "$_author") || return 1
          _body=$(db_hex_decode "$_body") || return 1
          ui_wrap_text "#$_id · mensagem #$_message_id · $_author: $_body" "$(ui_width)" 2
          ui_wrap_text "Denunciado por $_reporter: $_reason" "$(ui_width)" 4
        done <<<"$rows"
      fi
      chat_pause_for_response
      ;;
    /apagar-historico)
      if [[ "$CURRENT_USER_IS_ADMIN" != "True" ]]; then
        ui_error "Acesso administrativo não autorizado."
        return 0
      fi
      if ! ui_confirm "Apagar permanentemente todo o histórico da sala '$CHAT_ROOM_NAME'? Esta ação não pode ser desfeita."; then
        return 0
      fi
      response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$CHAT_CHANNEL" |
        broker_request chat.admin.clear token channel) || return 1
      if ! broker_check_response "$response"; then
        return 0
      fi
      local deleted_count
      deleted_count=$(broker_response_field "$response" deleted_count) || return 1
      [[ "$deleted_count" =~ ^[0-9]+$ ]] || {
        ui_error "O Broker retornou uma contagem de exclusões inválida."
        return 1
      }
      CHAT_BUFFER=()
      CHAT_LAST_ID=0
      CHAT_HISTORY_BEFORE=""
      CHAT_HISTORY_HAS_MORE="False"
      ui_success "$deleted_count mensagem(ns) apagada(s) permanentemente desta sala."
      ;;
    *)
      return 2
      ;;
  esac
}

chat_sync() {
  local response
  local last_id
  local requested_after_id="$CHAT_LAST_ID"

  response=$(printf '%s\0%s\0%s\0' \
    "$CURRENT_SESSION_TOKEN" "$CHAT_CHANNEL" "$CHAT_LAST_ID" |
    broker_request chat.sync token channel after_id) || return 1
  if ! broker_check_response "$response"; then
    return 1
  fi
  chat_append_response_messages "$response" || return 1
  last_id=$(broker_response_field "$response" last_id) || return 1
  [[ "$last_id" =~ ^[0-9]+$ ]] || {
    ui_error "O Broker retornou um cursor de mensagens inválido."
    return 1
  }
  CHAT_LAST_ID="$last_id"
  if [[ "$requested_after_id" == "0" ]]; then
    CHAT_HISTORY_HAS_MORE=$(broker_response_field "$response" has_more) ||
      CHAT_HISTORY_HAS_MORE="False"
  fi
  CHAT_SYNC_RESPONSE="$response"
}

chat_tty_restore() {
  local saved_state="$CHAT_TTY_STATE"
  [[ -n "$saved_state" ]] || return 0
  if stty "$saved_state" 2>/dev/null; then
    CHAT_TTY_STATE=""
    return 0
  fi
  return 1
}

chat_tty_enter_raw() {
  if [[ ! -t 0 ]] || ! CHAT_TTY_STATE=$(stty -g 2>/dev/null); then
    CHAT_TTY_STATE=""
    return 2
  fi
  if ! stty -icanon -echo -isig -ixon min 1 time 0 2>/dev/null; then
    chat_tty_restore
    return 2
  fi
}

chat_render_new_messages() {
  local rows
  local row

  rows=$(broker_response_rows "$CHAT_SYNC_RESPONSE" --separator \
    "$CHAT_ROW_SEPARATOR" messages) || return 1
  [[ -n "$rows" ]] || return 0
  while IFS= read -r row; do
    [[ -n "$row" ]] || continue
    chat_render_message_row "$row" 1 || return 1
  done <<<"$rows"
}

chat_refresh_during_input() {
  local draft="$1"
  local show_empty="$2"
  local has_new_messages=0
  local sync_succeeded=0

  chat_tty_restore || return 1
  if chat_sync; then
    sync_succeeded=1
    if ((CHAT_NEW_MESSAGE_COUNT > 0)); then
      has_new_messages=1
    fi
  else
    CHAT_TYPING_WARNING=1
  fi

  if ((sync_succeeded && !has_new_messages && !show_empty)); then
    chat_tty_enter_raw
    return $?
  fi

  printf '\033[u\033[J\n'
  if ((has_new_messages)); then
    chat_render_new_messages || return 1
  elif ((show_empty)); then
    ui_color_dim
    ui_wrap_text "Nenhuma mensagem nova." "$(ui_width)" 2
    ui_color_reset
  else
    ui_warn "Não foi possível atualizar as mensagens."
  fi
  printf '\033[s  MENSAGEM > %s' "$draft"
  chat_tty_enter_raw
}

chat_auto_refresh_enabled() {
  case "${CHAT_AUTO_REFRESH:-1}" in
    1) return 0 ;;
    0) return 1 ;;
    *)
      ui_error "CHAT_AUTO_REFRESH deve ser 0 (manual) ou 1 (automático)."
      return 2
      ;;
  esac
}

chat_read_message() {
  local refresh_seconds="$1"
  local auto_refresh_active="$2"
  local key=""
  local draft=""
  local read_status
  local last_refresh=$SECONDS

  CHAT_INPUT_LINE=""
  CHAT_READER_TYPING_ACTIVE=0
  chat_tty_enter_raw || return $?

  printf '\033[s  MENSAGEM > '
  while true; do
    if ((auto_refresh_active)) && ((SECONDS - last_refresh >= refresh_seconds)); then
      chat_refresh_during_input "$draft" 0 || return $?
      last_refresh=$SECONDS
    fi
    if IFS= read -r -N 1 -t 0.25 key; then
      case "$key" in
        $'\n'|$'\r')
          if [[ -z "$draft" ]]; then
            chat_refresh_during_input "" 1 || return $?
            last_refresh=$SECONDS
            continue
          fi
          chat_tty_restore || return 1
          printf '\n'
          CHAT_INPUT_LINE="$draft"
          return 0
          ;;
        $'\003')
          chat_tty_restore || return 1
          printf '\r\033[K\n'
          if [[ -n "$draft" ]]; then
            CHAT_INPUT_LINE=""
            return 130
          fi
          return 131
          ;;
        $'\004')
          if [[ -z "$draft" ]]; then
            chat_tty_restore || return 1
            printf '\n'
            return 131
          fi
          ;;
        $'\177'|$'\b')
          if [[ -n "$draft" ]]; then
            draft="${draft%?}"
            printf '\b \b'
            if [[ -z "$draft" && "$CHAT_READER_TYPING_ACTIVE" == "1" ]]; then
              if chat_mark_typing 0; then
                CHAT_READER_TYPING_ACTIVE=0
              else
                CHAT_TYPING_WARNING=1
              fi
            fi
          fi
          ;;
        $'\033')
          if IFS= read -r -N 1 -t 0.05 key &&
            [[ "$key" == "[" || "$key" == "O" ]]; then
            while IFS= read -r -N 1 -t 0.01 key; do
              [[ "$key" == [@-~] ]] && break
            done
          fi
          ;;
        *)
          if [[ "$key" != *[[:cntrl:]]* ]]; then
            if ((${#draft} >= 500)); then
              printf '\a'
              continue
            fi
            draft+="$key"
            printf '%s' "$key"
            if [[ "$CHAT_READER_TYPING_ACTIVE" == "0" ]]; then
              if chat_mark_typing 1; then
                CHAT_READER_TYPING_ACTIVE=1
              else
                CHAT_TYPING_WARNING=1
              fi
            fi
          fi
          ;;
      esac
    else
      read_status=$?
      if ((read_status > 128)); then
        continue
      fi
      chat_tty_restore || return 1
      return 1
    fi
  done
}

chat_menu_loop() {
  local message=""
  local read_status
  local refresh_seconds="${CHAT_REFRESH_SECONDS:-2}"
  local command
  local argument
  local rest
  local send_response
  local unread_count
  local typing_active=0
  local last_synced_channel=""
  local last_synced_room_name=""
  local auto_refresh_active=0
  local incremental_screen=0

  if [[ "${BROKER_MODE:-0}" != "1" ]]; then
    ui_error "O chat global está disponível apenas no modo Broker/Proxy."
    return 1
  fi
  if [[ ! "$CHAT_BUFFER_LIMIT" =~ ^[0-9]+$ ]] ||
    ((CHAT_BUFFER_LIMIT < 1 || CHAT_BUFFER_LIMIT > 100)); then
    ui_error "CHAT_HISTORY_WINDOW deve estar entre 1 e 100."
    return 1
  fi
  if chat_auto_refresh_enabled; then
    auto_refresh_active=1
    if [[ ! "$refresh_seconds" =~ ^[0-9]+$ ]] ||
      ((refresh_seconds < 1 || refresh_seconds > 60)); then
      ui_error "CHAT_REFRESH_SECONDS deve estar entre 1 e 60."
      return 1
    fi
  else
    read_status=$?
    if ((read_status == 2)); then
      return 1
    fi
  fi

  CHAT_CHANNEL="${CHAT_INITIAL_CHANNEL:-global}"
  CHAT_ROOM_NAME="${CHAT_INITIAL_ROOM_NAME:-Chat global}"
  CHAT_INITIAL_CHANNEL=""
  CHAT_INITIAL_ROOM_NAME=""
  CHAT_LAST_ID=0
  CHAT_BUFFER=()
  CHAT_HISTORY_BEFORE=""
  CHAT_HISTORY_HAS_MORE="False"
  CHAT_TYPING_WARNING=0
  nav_push "CHAT GLOBAL"
  while true; do
    if ! chat_sync; then
      if [[ -n "$last_synced_channel" &&
        "$CHAT_CHANNEL" != "$last_synced_channel" ]]; then
        CHAT_CHANNEL="$last_synced_channel"
        CHAT_ROOM_NAME="$last_synced_room_name"
        CHAT_LAST_ID=0
        CHAT_BUFFER=()
        CHAT_HISTORY_BEFORE=""
        CHAT_HISTORY_HAS_MORE="False"
        continue
      fi
      nav_pop
      return 1
    fi
    last_synced_channel="$CHAT_CHANNEL"
    last_synced_room_name="$CHAT_ROOM_NAME"
    unread_count=$(broker_response_field "$CHAT_SYNC_RESPONSE" unread_count) ||
      unread_count=0

    if ((incremental_screen)); then
      if ((CHAT_NEW_MESSAGE_COUNT > 0)); then
        chat_render_new_messages || {
          nav_pop
          return 1
        }
      fi
      incremental_screen=0
    else
      ui_screen_begin "$CHAT_ROOM_NAME"
      if ! chat_render_online_users "$CHAT_SYNC_RESPONSE"; then
        nav_pop
        return 1
      fi
      if ! chat_render_typing_users "$CHAT_SYNC_RESPONSE"; then
        nav_pop
        return 1
      fi
      if ! chat_render_pinned "$CHAT_SYNC_RESPONSE"; then
        nav_pop
        return 1
      fi
      if ! chat_render_buffer; then
        nav_pop
        return 1
      fi
      if ((${#CHAT_BUFFER[@]} > 0 && CHAT_NEW_MESSAGE_COUNT == 0)); then
        ui_color_dim
        ui_wrap_text "Nenhuma mensagem nova; exibindo o histórico recente." \
          "$(ui_width)" 2
        ui_color_reset
      fi
      if [[ "$unread_count" =~ ^[0-9]+$ ]] && ((unread_count > 0)); then
        ui_color_warning
        ui_wrap_text "$unread_count mensagem(ns) nova(s) em outras salas." "$(ui_width)" 2
        ui_color_reset
      fi
      printf '\n'
      ui_color_dim
      if ((auto_refresh_active)); then
        ui_wrap_text "Atualiza a cada ${refresh_seconds}s sem limpar a tela · Enter envia · /ajuda" \
          "$(ui_width)" 2
      else
        ui_wrap_text "Autoatualização desativada · Enter vazio atualiza · Enter envia · /ajuda" \
          "$(ui_width)" 2
      fi
      ui_wrap_text "Limite: 500 caracteres · /historico carrega mensagens anteriores" \
        "$(ui_width)" 2
      ui_color_reset
    fi
    if ((CHAT_TYPING_WARNING)); then
      ui_warn "Indicador de digitação indisponível; o chat continua funcionando."
      CHAT_TYPING_WARNING=0
    fi

    message=""
    if ui_can_interact; then
      if chat_read_message "$refresh_seconds" "$auto_refresh_active"; then
        message="$CHAT_INPUT_LINE"
      else
        read_status=$?
        typing_active="$CHAT_READER_TYPING_ACTIVE"
        case "$read_status" in
          130)
            if ((typing_active)) && ! chat_mark_typing 0; then
              CHAT_TYPING_WARNING=1
            fi
            continue
            ;;
          131)
            if ((typing_active)) && ! chat_mark_typing 0; then
              CHAT_TYPING_WARNING=1
            fi
            break
            ;;
          2)
          if ! ui_read_line message "  MENSAGEM > "; then
            nav_pop
            return 0
          fi
            ;;
          1)
            nav_pop
            return 0
            ;;
          *)
            nav_pop
            return 1
            ;;
        esac
      fi
      typing_active="$CHAT_READER_TYPING_ACTIVE"
      if ((typing_active)) && ! chat_mark_typing 0; then
        CHAT_TYPING_WARNING=1
      fi
    else
      if ! ui_read_line message "  MENSAGEM > "; then
        nav_pop
        return 0
      fi
    fi

    if [[ "$message" == "/sair" ]]; then
      break
    fi
    if [[ "$message" == /* ]]; then
      command="${message%% *}"
      rest="${message#"$command"}"
      rest="${rest# }"
      argument="${rest%% *}"
      if [[ "$rest" == *" "* ]]; then
        rest="${rest#* }"
      else
        rest=""
      fi
      if chat_command "$command" "$argument" "$rest"; then
        continue
      else
        read_status=$?
        if ((read_status == 2)); then
          ui_error "Comando desconhecido. Digite /ajuda."
        else
          ui_pause
        fi
        continue
      fi
    fi
    if [[ -z "$message" ]]; then
      incremental_screen=1
      continue
    fi
    send_response=$(printf '%s\0%s\0%s\0' \
      "$CURRENT_SESSION_TOKEN" "$CHAT_CHANNEL" "$message" |
      broker_request chat.post token channel message) || {
      ui_error "Não foi possível enviar a mensagem."
      ui_pause
      continue
    }
    if ! broker_check_response "$send_response"; then
      ui_pause
    else
      incremental_screen=1
    fi
  done
  nav_pop
}

chat_menu() {
  local previous_exit_trap
  local menu_status
  local chat_tui_socket
  local chat_tui_channel
  local chat_tui_room_name

  if ui_can_interact && [[ "${TERM:-dumb}" != "dumb" ]]; then
    if [[ "${SIMPLE_MODE:-local}" == "proxy" ]]; then
      chat_tui_socket="${PROXY_SOCKET:-}"
    else
      chat_tui_socket="${BROKER_SOCKET:-}"
    fi
    chat_tui_channel="${CHAT_INITIAL_CHANNEL:-global}"
    chat_tui_room_name="${CHAT_INITIAL_ROOM_NAME:-Chat global}"
    CHAT_INITIAL_CHANNEL=""
    CHAT_INITIAL_ROOM_NAME=""
    CHAT_TUI_TOKEN="$CURRENT_SESSION_TOKEN" \
      CHAT_TUI_USER_ID="$CURRENT_USER_ID" \
      CHAT_TUI_USERNAME="$CURRENT_USERNAME" \
      CHAT_TUI_IS_ADMIN="$CURRENT_USER_IS_ADMIN" \
      CHAT_TUI_SOCKET="$chat_tui_socket" \
      CHAT_TUI_CHANNEL="$chat_tui_channel" \
      CHAT_TUI_ROOM_NAME="$chat_tui_room_name" \
      python3 "$SCRIPT_DIR/modules/chat_tui.py"
    return $?
  fi

  previous_exit_trap=$(trap -p EXIT)
  CHAT_PREVIOUS_EXIT_TRAP="$previous_exit_trap"
  trap 'chat_tty_restore; if [[ -n "$CHAT_PREVIOUS_EXIT_TRAP" ]]; then eval "$CHAT_PREVIOUS_EXIT_TRAP"; else trap - EXIT; fi' EXIT
  if chat_menu_loop; then
    menu_status=0
  else
    menu_status=$?
  fi
  if ! chat_tty_restore; then
    ui_error "Não foi possível restaurar o estado do terminal."
    menu_status=1
  fi
  if [[ -n "$previous_exit_trap" ]]; then
    eval "$previous_exit_trap"
  else
    trap - EXIT
  fi
  CHAT_PREVIOUS_EXIT_TRAP=""
  return "$menu_status"
}
