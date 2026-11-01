#!/usr/bin/env bash

points_balance() {
  local user_id="$1"

  if [[ ! "$user_id" =~ ^[0-9]+$ ]]; then
    printf '  ID de usuário inválido.\n' >&2
    return 2
  fi

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    if [[ "$user_id" != "$CURRENT_USER_ID" ]]; then
      printf '  No modo broker, só é permitido consultar o próprio saldo.\n' >&2
      return 1
    fi
    local response
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request points.balance token) || return 1
    broker_check_response "$response" || return 1
    broker_response_field "$response" points
    return $?
  fi

  db_query "
    SELECT
      COALESCE((SELECT SUM(amount) FROM points_ledger WHERE user_id = $user_id), 0) +
      COALESCE((SELECT SUM(amount) FROM admin_point_adjustments WHERE user_id = $user_id), 0);
  "
}

points_history() {
  local user_id="$1"

  if [[ ! "$user_id" =~ ^[0-9]+$ ]]; then
    printf '  ID de usuário inválido.\n' >&2
    return 2
  fi

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    if [[ "$user_id" != "$CURRENT_USER_ID" ]]; then
      printf '  No modo broker, só é permitido consultar o próprio histórico.\n' >&2
      return 1
    fi
    local response
    local rows
    local encoded
    local amount
    local entry_type
    local details
    local created_at
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request points.history token) || return 1
    broker_check_response "$response" || return 1
    rows=$(broker_response_rows "$response" entries) || return 1
    while IFS= read -r encoded; do
      [[ -z "$encoded" ]] && continue
      IFS=$'\t' read -r amount entry_type details created_at <<<"$encoded"
      amount=$(db_hex_decode "$amount") || return 1
      entry_type=$(db_hex_decode "$entry_type") || return 1
      details=$(db_hex_decode "$details") || return 1
      created_at=$(db_hex_decode "$created_at") || return 1
      details=${details//$'\n'/ }
      details=${details//|/\/}
      printf '%s|%s|%s|%s\n' "$amount" "$entry_type" "$details" "$created_at"
    done <<<"$rows"
    return 0
  fi

  db_query "
    SELECT amount || '|' || entry_type || '|' || details || '|' || created_at
    FROM (
      SELECT id, amount, entry_type, details, created_at
      FROM points_ledger
      WHERE user_id = $user_id
      UNION ALL
      SELECT id, amount, 'admin_adjustment', reason, created_at
      FROM admin_point_adjustments
      WHERE user_id = $user_id
    )
    ORDER BY created_at DESC, id DESC;
  "
}