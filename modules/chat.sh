#!/usr/bin/env bash

chat_render_messages() {
  local response="$1"
  local rows
  local encoded
  local message_id
  local username
  local body
  local created_at
  local time_label

  rows=$(broker_response_rows "$response" messages) || return 1
  if [[ -z "$rows" ]]; then
    ui_empty_state "Nenhuma mensagem ainda." "Envie a primeira mensagem para o grupo."
    return 0
  fi

  while IFS= read -r encoded; do
    IFS=$'\t' read -r message_id username body created_at <<<"$encoded"
    message_id=$(db_hex_decode "$message_id") || return 1
    [[ "$message_id" =~ ^[0-9]+$ ]] || return 1
    username=$(db_hex_decode "$username") || return 1
    body=$(db_hex_decode "$body") || return 1
    created_at=$(db_hex_decode "$created_at") || return 1
    time_label="${created_at##* }"
    time_label="${time_label:0:5}"
    ui_color_content
    ui_wrap_text "[$time_label] $username: $body" "$(ui_width)" 2
    ui_color_reset
  done <<<"$rows"
}

chat_render_online_users() {
  local response="$1"
  local online_users
  local username_hex
  local username
  local count

  count=$(broker_response_field "$response" online_count) || return 1
  online_users=$(broker_response_strings "$response" online_users) || return 1
  ui_section "ONLINE AGORA · $count"
  if [[ -z "$online_users" ]]; then
    ui_color_content
    ui_wrap_text "Ninguém online." "$(ui_width)" 2
    ui_color_reset
    return 0
  fi
  while IFS= read -r username_hex; do
    username=$(db_hex_decode "$username_hex") || return 1
    ui_color_content
    ui_wrap_text "$username" "$(ui_width)" 2
    ui_color_reset
  done <<<"$online_users"
}

chat_menu() {
  local status_response
  local messages_response
  local send_response
  local rows
  local after_id=0
  local last_id
  local message

  if [[ "${BROKER_MODE:-0}" != "1" ]]; then
    ui_error "O chat global está disponível apenas no modo Broker/Proxy."
    return 1
  fi

  nav_push "CHAT GLOBAL"
  while true; do
    status_response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request chat.status token) || {
      nav_pop
      return 1
    }
    if ! broker_check_response "$status_response"; then
      nav_pop
      return 1
    fi

    messages_response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$after_id" |
      broker_request chat.messages token after_id) || {
      nav_pop
      return 1
    }
    if ! broker_check_response "$messages_response"; then
      nav_pop
      return 1
    fi

    ui_screen_begin "CHAT GLOBAL"
    chat_render_online_users "$status_response" || {
      nav_pop
      return 1
    }
    ui_section "MENSAGENS"
    chat_render_messages "$messages_response" || {
      nav_pop
      return 1
    }
    last_id=$(broker_response_field "$messages_response" last_id) || {
      nav_pop
      return 1
    }
    if [[ "$last_id" =~ ^[0-9]+$ ]]; then
      after_id="$last_id"
    else
      ui_error "O Broker retornou um cursor de mensagens inválido."
      nav_pop
      return 1
    fi

    printf '\n'
    ui_color_dim
    ui_wrap_text "Enter atualiza · /sair volta" "$(ui_width)" 2
    ui_color_reset
    if ! ui_read_line message "  MENSAGEM > "; then
      nav_pop
      return 1
    fi
    if [[ "$message" == "/sair" ]]; then
      break
    fi
    if [[ -z "$message" ]]; then
      continue
    fi

    send_response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$message" |
      broker_request chat.send token message) || {
      ui_error "Não foi possível enviar a mensagem."
      ui_pause
      continue
    }
    if ! broker_check_response "$send_response"; then
      ui_pause
      continue
    fi
  done
  nav_pop
}
