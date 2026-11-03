#!/usr/bin/env bash

progress_unread_notification_count() {
  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request notifications.count token) || return 1
    broker_check_response "$response" || return 1
    broker_response_field "$response" count
    return $?
  fi

  db_query "
    SELECT COUNT(*) FROM notifications
    WHERE user_id = $CURRENT_USER_ID AND read_at IS NULL;
  "
}

progress_mark_notifications_read() {
  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request notifications.mark_read token) || return 1
    broker_check_response "$response"
    return $?
  fi

  db_query "
    UPDATE notifications
    SET read_at = CURRENT_TIMESTAMP
    WHERE user_id = $CURRENT_USER_ID AND read_at IS NULL;
  "
}

progress_menu() {
  local completed_count
  local balance
  local history
  local notifications
  local pending_responses
  local approved_exercises
  local amount
  local entry_type
  local details
  local created_at
  local exercise_code
  local exercise_name
  local review_id
  local reviewer
  local review_score
  local review_feedback
  local response_score
  local response_feedback
  local response_at
  local submitter
  local relation
  local notification_type
  local notification_user
  local notification_score
  local notification_feedback
  local notification_at
  local choice
  local -a pending_response_list
  local -a response_options

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_progress_menu
    return $?
  fi

  completed_count=$(db_query "
    SELECT COUNT(DISTINCT exercise_id)
    FROM submissions
    WHERE user_id = $CURRENT_USER_ID AND status = 'approved';
  ") || return 1
  approved_exercises=$(db_query "
    SELECT e.code || '|' || replace(e.name, '|', '/')
    FROM submissions s
    JOIN exercises e ON e.id = s.exercise_id
    WHERE s.user_id = $CURRENT_USER_ID AND s.status = 'approved'
    GROUP BY e.id, e.code, e.name
    ORDER BY e.code;
  ") || return 1
  balance=$(points_balance "$CURRENT_USER_ID") || return 1
  history=$(db_query "
    SELECT hex(e.code) || '|' || r.score || '|' || hex(r.feedback) || '|' ||
           hex(r.created_at) || '|' || hex(reviewer.username) || '|' ||
           hex(owner.username) || '|' ||
           CASE WHEN s.user_id = $CURRENT_USER_ID THEN 'Recebida de ' || reviewer.username
                ELSE 'Avaliada por você (' || owner.username || ')' END || '|' ||
           COALESCE(r.recipient_score, '') || '|' ||
           hex(r.recipient_feedback) || '|' ||
           hex(COALESCE(r.recipient_responded_at, ''))
    FROM reviews r
    JOIN evaluation_slots slot ON slot.id = r.slot_id
    JOIN submissions s ON s.id = slot.submission_id
    JOIN exercises e ON e.id = s.exercise_id
    JOIN users reviewer ON reviewer.id = r.reviewer_id
    JOIN users owner ON owner.id = s.user_id
    WHERE s.user_id = $CURRENT_USER_ID OR r.reviewer_id = $CURRENT_USER_ID
    ORDER BY r.created_at DESC, r.id DESC;
  ") || return 1

  notifications=$(db_query "
    SELECT n.type || '|' || hex(e.code) || '|' ||
           hex(CASE WHEN n.type = 'review_received' THEN r.score
                    ELSE r.recipient_score END) || '|' ||
           hex(CASE WHEN n.type = 'review_received' THEN r.feedback
                    ELSE r.recipient_feedback END) || '|' ||
           hex(CASE WHEN n.type = 'review_received' THEN reviewer.username
                    ELSE owner.username END) || '|' || hex(n.created_at)
    FROM notifications n
    JOIN reviews r ON r.id = n.review_id
    JOIN evaluation_slots slot ON slot.id = r.slot_id
    JOIN submissions s ON s.id = slot.submission_id
    JOIN exercises e ON e.id = s.exercise_id
    JOIN users reviewer ON reviewer.id = r.reviewer_id
    JOIN users owner ON owner.id = s.user_id
    WHERE n.user_id = $CURRENT_USER_ID AND n.read_at IS NULL
    ORDER BY n.created_at DESC, n.id DESC;
  ") || return 1

  pending_responses=$(db_query "
    SELECT r.id || '|' || hex(e.code) || '|' || hex(reviewer.username) || '|' ||
           r.score || '|' || hex(r.feedback)
    FROM reviews r
    JOIN evaluation_slots slot ON slot.id = r.slot_id
    JOIN submissions s ON s.id = slot.submission_id
    JOIN exercises e ON e.id = s.exercise_id
    JOIN users reviewer ON reviewer.id = r.reviewer_id
    WHERE s.user_id = $CURRENT_USER_ID
      AND r.recipient_responded_at IS NULL
    ORDER BY r.created_at, r.id;
  ") || return 1

  nav_push "MEU PROGRESSO"

  ui_screen_begin "MEU PROGRESSO"
  printf '  Exercícios aprovados: %s\n' "$completed_count"
  printf '  Saldo de pontos: %s\n\n' "$balance"
  ui_section "NOTIFICAÇÕES"
  if [[ -z "$notifications" ]]; then
    ui_empty_state "Nenhuma notificação nova."
  else
    while IFS='|' read -r notification_type exercise_code notification_score \
      notification_feedback notification_user notification_at; do
      exercise_code=$(db_hex_decode "$exercise_code") || {
        nav_pop
        return 1
      }
      notification_score=$(db_hex_decode "$notification_score") || {
        nav_pop
        return 1
      }
      notification_feedback=$(db_hex_decode "$notification_feedback") || {
        nav_pop
        return 1
      }
      notification_user=$(db_hex_decode "$notification_user") || {
        nav_pop
        return 1
      }
      notification_at=$(db_hex_decode "$notification_at") || {
        nav_pop
        return 1
      }
      if [[ "$notification_type" == "review_received" ]]; then
        ui_wrap_text "Nova avaliação de $notification_user em $exercise_code: nota $notification_score/5" \
          "$(ui_width)" 2
      else
        ui_wrap_text "Nova resposta de $notification_user em $exercise_code: nota $notification_score/5 ao avaliador" \
          "$(ui_width)" 2
      fi
      ui_wrap_text "Feedback: $(ui_sanitize "$notification_feedback")" "$(ui_width)" 4
      ui_wrap_text "Recebida em: $(ui_sanitize "$notification_at")" "$(ui_width)" 4
    done <<<"$notifications"
  fi
  progress_mark_notifications_read || {
    nav_pop
    return 1
  }

  ui_section "EXERCÍCIOS CONCLUÍDOS"
  if [[ -z "$approved_exercises" ]]; then
    ui_empty_state "Nenhum exercício aprovado ainda."
  else
    while IFS='|' read -r exercise_code exercise_name; do
      ui_wrap_text "$(ui_sanitize "$exercise_code") - $(ui_sanitize "$exercise_name")" "$(ui_width)" 2
    done <<<"$approved_exercises"
  fi
  printf '\n'
  ui_section "HISTÓRICO DE AVALIAÇÕES"

  if [[ -z "$history" ]]; then
    ui_empty_state "Nenhuma avaliação registrada."
  else
    while IFS='|' read -r exercise_code review_score review_feedback \
      created_at reviewer submitter relation response_score response_feedback \
      response_at; do
      exercise_code=$(db_hex_decode "$exercise_code") || {
        nav_pop
        return 1
      }
      review_feedback=$(db_hex_decode "$review_feedback") || {
        nav_pop
        return 1
      }
      created_at=$(db_hex_decode "$created_at") || {
        nav_pop
        return 1
      }
      reviewer=$(db_hex_decode "$reviewer") || {
        nav_pop
        return 1
      }
      submitter=$(db_hex_decode "$submitter") || {
        nav_pop
        return 1
      }
      response_feedback=$(db_hex_decode "$response_feedback") || {
        nav_pop
        return 1
      }
      response_at=$(db_hex_decode "$response_at") || {
        nav_pop
        return 1
      }
      if [[ "$relation" == "Recebida de "* ]]; then
        ui_wrap_text "$(ui_sanitize "$exercise_code") - nota $review_score/5, avaliado por $(ui_sanitize "$reviewer") ($(ui_sanitize "$created_at"))" \
          "$(ui_width)" 2
      else
        ui_wrap_text "$(ui_sanitize "$exercise_code") - nota $review_score/5, submissão de $(ui_sanitize "$submitter") ($(ui_sanitize "$created_at"))" \
          "$(ui_width)" 2
      fi
      ui_wrap_text "Feedback: $(ui_sanitize "$review_feedback")" "$(ui_width)" 4
      if [[ -n "$response_score" ]]; then
        ui_wrap_text "Sua nota ao avaliador: $response_score/5" "$(ui_width)" 4
        ui_wrap_text "Sua resposta: $(ui_sanitize "$response_feedback") ($(ui_sanitize "$response_at"))" \
          "$(ui_width)" 4
      fi
    done <<<"$history"
  fi

  if [[ -n "$pending_responses" ]]; then
    ui_section "RESPONDER A UMA AVALIAÇÃO"
    mapfile -t pending_response_list <<<"$pending_responses"
    response_options=()
    for review_id in "${!pending_response_list[@]}"; do
      IFS='|' read -r _ exercise_code reviewer _ <<<"${pending_response_list[$review_id]}"
      exercise_code=$(db_hex_decode "$exercise_code") || {
        nav_pop
        return 1
      }
      reviewer=$(db_hex_decode "$reviewer") || {
        nav_pop
        return 1
      }
      response_options+=("$((review_id + 1))|$exercise_code — avaliação de $reviewer")
    done
    ui_wrap_text "Escolha uma avaliação para responder ou pressione Enter para continuar." \
      "$(ui_width)" 2
    for review_id in "${!response_options[@]}"; do
      ui_wrap_text "[${response_options[$review_id]%%|*}] ${response_options[$review_id]#*|}" \
        "$(ui_width)" 2
    done
    if ui_read_line choice "  AVALIAÇÃO (Enter para continuar) > " &&
      [[ "$choice" =~ ^[0-9]+$ ]] &&
      ((choice >= 1 && choice <= ${#pending_response_list[@]})); then
      IFS='|' read -r review_id _ <<<"${pending_response_list[$((choice - 1))]}"
      evaluation_response_prompt "$review_id"
    fi
  fi

  ui_section "MOVIMENTAÇÕES DE PONTOS"
  history=$(points_history "$CURRENT_USER_ID") || {
    nav_pop
    return 1
  }
  if [[ -z "$history" ]]; then
    ui_empty_state "Nenhuma movimentação registrada."
  else
    while IFS='|' read -r amount entry_type details created_at; do
      ui_wrap_text "$(printf '%+d | %s | %s (%s)' "$amount" "$(ui_sanitize "$entry_type")" "$(ui_sanitize "$details")" "$(ui_sanitize "$created_at")")" \
        "$(ui_width)" 2
    done <<<"$history"
  fi

  nav_pop
  ui_pause
}