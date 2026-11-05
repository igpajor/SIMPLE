#!/usr/bin/env bash

evaluation_record_review() {
  local slot_id="$1"
  local score="$2"
  local feedback="$3"
  local feedback_sql

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0%s\0%s\0%s\0' \
      "$CURRENT_SESSION_TOKEN" "$slot_id" "$score" "$feedback" |
      broker_request evaluations.review.submit token slot_id score feedback) || return 1
    broker_check_response "$response"
    return $?
  fi

  if [[ ! "$slot_id" =~ ^[0-9]+$ ]]; then
    printf '  Slot inválido.\n'
    return 2
  fi

  if [[ ! "$score" =~ ^[0-5]$ ]]; then
    printf '  A nota deve ser um número inteiro de 0 a 5.\n'
    return 2
  fi
  if [[ -z "${feedback//[[:space:]]/}" ]]; then
    printf '  O feedback da avaliação é obrigatório.\n'
    return 2
  fi

  feedback_sql=$(db_sql_quote "$feedback")
  local inserted
  inserted=$(db_query "
    BEGIN IMMEDIATE;
    INSERT INTO reviews (slot_id, reviewer_id, score, feedback)
    SELECT $slot_id, $CURRENT_USER_ID, $score, $feedback_sql
    WHERE EXISTS (
      SELECT 1
      FROM evaluation_slots slot
      JOIN submissions submission ON submission.id = slot.submission_id
      JOIN evaluation_requests request
        ON request.slot_id = slot.id
       AND request.reviewer_id = $CURRENT_USER_ID
       AND request.status = 'accepted'
      WHERE slot.id = $slot_id AND slot.status = 'open'
        AND slot.accepted_by = $CURRENT_USER_ID
        AND submission.user_id != $CURRENT_USER_ID
    );
    SELECT changes();
    INSERT INTO notifications (user_id, type, review_id)
    SELECT s.user_id, 'review_received',
           (SELECT id FROM reviews WHERE slot_id = $slot_id)
    FROM evaluation_slots slot
    JOIN submissions s ON s.id = slot.submission_id
    WHERE slot.id = $slot_id AND changes() = 1;
    UPDATE evaluation_requests
    SET status = 'completed', responded_at = CURRENT_TIMESTAMP
    WHERE slot_id = $slot_id AND reviewer_id = $CURRENT_USER_ID
      AND status = 'accepted'
      AND EXISTS (SELECT 1 FROM reviews WHERE slot_id = $slot_id);
    COMMIT;
  ") || return 1
  if [[ "$inserted" != "1" ]]; then
    printf '  O autor ainda não aceitou este pedido ou o slot está indisponível.\n'
    return 1
  fi
}

evaluation_live_room_id() {
  local slot_id="$1"
  local room_id

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$slot_id" |
      broker_request evaluations.room.get token slot_id) || return 1
    broker_check_response "$response" || return 1
    broker_response_field "$response" room_id
    return $?
  fi

  [[ "$slot_id" =~ ^[0-9]+$ ]] || return 2
  local generated_room
  generated_room=$(openssl rand -hex 24) || {
    printf '  Não foi possível criar uma sala segura para a avaliação.\n' >&2
    return 1
  }
  room_id=$(db_query "
    BEGIN IMMEDIATE;
    UPDATE evaluation_slots
    SET live_room = '$generated_room'
    WHERE id = $slot_id AND status = 'open' AND live_room IS NULL
      AND EXISTS (
        SELECT 1
        FROM evaluation_requests request
        WHERE request.slot_id = evaluation_slots.id
          AND request.reviewer_id = evaluation_slots.accepted_by
          AND request.status = 'accepted'
      )
      AND EXISTS (
        SELECT 1 FROM submissions submission
        WHERE submission.id = evaluation_slots.submission_id
          AND (submission.user_id = $CURRENT_USER_ID
               OR evaluation_slots.accepted_by = $CURRENT_USER_ID)
      );
    SELECT live_room
    FROM evaluation_slots
    WHERE id = $slot_id AND status = 'open'
      AND EXISTS (
        SELECT 1
        FROM evaluation_requests request
        WHERE request.slot_id = evaluation_slots.id
          AND request.reviewer_id = evaluation_slots.accepted_by
          AND request.status = 'accepted'
      )
      AND EXISTS (
        SELECT 1 FROM submissions submission
        WHERE submission.id = evaluation_slots.submission_id
          AND (submission.user_id = $CURRENT_USER_ID
               OR evaluation_slots.accepted_by = $CURRENT_USER_ID)
      );
    COMMIT;
  ") || return 1
  if [[ ! "$room_id" =~ ^[a-f0-9]{48}$ ]]; then
    printf '  A sala não está disponível para esta conta ou avaliação.\n' >&2
    return 1
  fi
  printf '%s\n' "$room_id"
}

evaluation_open_live_room() {
  local slot_id="$1"
  local room_id
  local url

  room_id=$(evaluation_live_room_id "$slot_id") || {
    ui_error "Não foi possível acessar a sala desta avaliação."
    ui_pause
    return 1
  }
  url="https://meet.jit.si/SIMPLE-Eval-$room_id"
  ui_screen_begin "SALA AO VIVO · AVALIAÇÃO $slot_id"
  ui_panel "ÁUDIO, VÍDEO E COMPARTILHAMENTO"
  ui_wrap_text \
    "Abra este link no navegador do seu computador. A outra pessoa deve usar o mesmo link." \
    "$(ui_width)" 2
  printf '\n  Link da sala:\n  \033]8;;%s\033\\%s\033]8;;\033\\\n\n' "$url" "$url"
  ui_wrap_text \
    "Ao entrar, permita o microfone e use o compartilhamento de tela para mostrar o terminal. Qualquer pessoa com este link pode tentar entrar." \
    "$(ui_width)" 2
  ui_panel_end
  ui_pause
}

evaluation_respond_to_review() {
  local review_id="$1"
  local score="$2"
  local feedback="$3"
  local feedback_sql
  local updated

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0%s\0%s\0%s\0' \
      "$CURRENT_SESSION_TOKEN" "$review_id" "$score" "$feedback" |
      broker_request evaluations.review.respond token review_id score feedback) || return 1
    broker_check_response "$response"
    return $?
  fi

  if [[ ! "$review_id" =~ ^[0-9]+$ ]]; then
    printf '  Avaliação inválida.\n'
    return 2
  fi
  if [[ ! "$score" =~ ^[0-5]$ ]]; then
    printf '  A nota deve ser um número inteiro de 0 a 5.\n'
    return 2
  fi
  if [[ -z "${feedback//[[:space:]]/}" ]]; then
    printf '  A resposta da avaliação é obrigatória.\n'
    return 2
  fi

  feedback_sql=$(db_sql_quote "$feedback")
  updated=$(db_query "
    BEGIN IMMEDIATE;
    UPDATE reviews
    SET recipient_score = $score,
        recipient_feedback = $feedback_sql,
        recipient_responded_at = CURRENT_TIMESTAMP
    WHERE id = $review_id
      AND recipient_responded_at IS NULL
      AND slot_id IN (
        SELECT slot.id
        FROM evaluation_slots slot
        JOIN submissions s ON s.id = slot.submission_id
        WHERE s.user_id = $CURRENT_USER_ID
      );
    INSERT INTO notifications (user_id, type, review_id)
    SELECT reviewer_id, 'response_received', id
    FROM reviews
    WHERE id = $review_id
      AND changes() = 1
      AND slot_id IN (
        SELECT slot.id
        FROM evaluation_slots slot
        JOIN submissions s ON s.id = slot.submission_id
        WHERE s.user_id = $CURRENT_USER_ID
      );
    SELECT changes();
    COMMIT;
  ") || return 1
  if [[ "$updated" != "1" ]]; then
    printf '  Avaliação indisponível ou já respondida.\n'
    return 1
  fi
  return 0
}

evaluation_expire_pending_requests() {
  db_query "
    BEGIN IMMEDIATE;
    UPDATE evaluation_requests
    SET status = 'expired', responded_at = CURRENT_TIMESTAMP
    WHERE status = 'pending'
      AND requested_at <= datetime('now', '-24 hours');
    UPDATE evaluation_slots
    SET accepted_by = NULL, accepted_at = NULL, live_room = NULL
    WHERE status = 'open'
      AND EXISTS (
        SELECT 1 FROM evaluation_requests request
        WHERE request.slot_id = evaluation_slots.id
          AND request.reviewer_id = evaluation_slots.accepted_by
          AND request.status = 'expired'
          AND request.requested_at <= datetime('now', '-24 hours')
      )
      AND NOT EXISTS (
        SELECT 1 FROM evaluation_requests active
        WHERE active.slot_id = evaluation_slots.id
          AND active.reviewer_id = evaluation_slots.accepted_by
          AND active.status IN ('pending', 'accepted')
      );
    COMMIT;
  " >/dev/null
}

evaluation_pending_requests() {
  local response

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request evaluations.requests.list token) || return 1
    if ! broker_check_response "$response"; then
      return 1
    fi
    broker_response_rows "$response" requests
    return $?
  fi

  evaluation_expire_pending_requests || return 1
  db_query "
    SELECT hex(request.id) || char(9) || hex(slot.id) || char(9) ||
           hex(evaluator.username) || char(9) || hex(exercise.code) || char(9) ||
           hex(exercise.name) || char(9) || hex(request.requested_at) || char(9) ||
           hex(request.status)
    FROM evaluation_requests request
    JOIN evaluation_slots slot ON slot.id = request.slot_id
    JOIN submissions submission ON submission.id = slot.submission_id
    JOIN users evaluator ON evaluator.id = request.reviewer_id
    JOIN exercises exercise ON exercise.id = submission.exercise_id
    WHERE submission.user_id = $CURRENT_USER_ID
      AND slot.status = 'open'
      AND slot.accepted_by = request.reviewer_id
      AND request.status IN ('pending', 'accepted')
    ORDER BY request.requested_at, request.id;
  "
}

evaluation_respond_to_request() {
  local request_id="$1"
  local decision="$2"

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0%s\0%s\0' \
      "$CURRENT_SESSION_TOKEN" "$request_id" "$decision" |
      broker_request evaluations.request.respond token request_id decision) || return 1
    broker_check_response "$response"
    return $?
  fi

  [[ "$request_id" =~ ^[0-9]+$ ]] || return 2
  [[ "$decision" == "accept" || "$decision" == "decline" ]] || return 2
  local next_status="accepted"
  [[ "$decision" == "decline" ]] && next_status="declined"
  local result
  result=$(db_query "
    BEGIN IMMEDIATE;
    UPDATE evaluation_requests
    SET status = '$next_status', responded_at = CURRENT_TIMESTAMP
    WHERE id = $request_id AND status = 'pending'
      AND slot_id IN (
        SELECT slot.id
        FROM evaluation_slots slot
        JOIN submissions submission ON submission.id = slot.submission_id
        WHERE submission.user_id = $CURRENT_USER_ID
          AND slot.status = 'open'
      );
    SELECT changes();
    UPDATE evaluation_slots
    SET accepted_by = NULL, accepted_at = NULL, live_room = NULL
    WHERE id = (
        SELECT slot_id FROM evaluation_requests WHERE id = $request_id
      )
      AND accepted_by = (
        SELECT reviewer_id FROM evaluation_requests WHERE id = $request_id
      )
      AND EXISTS (
        SELECT 1 FROM evaluation_requests request
        WHERE request.id = $request_id AND request.status = 'declined'
      );
    COMMIT;
  ") || return 1
  if [[ "$result" != "1" ]]; then
    printf '  Pedido indisponível ou já respondido.\n'
    return 1
  fi
  return 0
}

evaluation_start_request() {
  local slot_id="$1"

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$slot_id" |
      broker_request evaluations.accept token slot_id) || return 1
    broker_check_response "$response"
    return $?
  fi

  [[ "$slot_id" =~ ^[0-9]+$ ]] || return 2
  local result
  result=$(db_query "
    BEGIN IMMEDIATE;
    INSERT INTO evaluation_requests (slot_id, reviewer_id, status)
    SELECT slot.id, $CURRENT_USER_ID, 'pending'
    FROM evaluation_slots slot
    JOIN submissions submission ON submission.id = slot.submission_id
    WHERE slot.id = $slot_id AND slot.status = 'open'
      AND submission.user_id != $CURRENT_USER_ID
      AND slot.accepted_by IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM evaluation_requests declined
        WHERE declined.slot_id = slot.id
          AND declined.reviewer_id = $CURRENT_USER_ID
          AND declined.status = 'declined'
      );
    UPDATE evaluation_slots
    SET accepted_by = $CURRENT_USER_ID, accepted_at = CURRENT_TIMESTAMP
    WHERE id = $slot_id AND status = 'open' AND accepted_by IS NULL
      AND EXISTS (
        SELECT 1 FROM evaluation_requests request
        WHERE request.slot_id = $slot_id
          AND request.reviewer_id = $CURRENT_USER_ID
          AND request.status = 'pending'
      );
    SELECT changes();
    COMMIT;
  ") || return 1
  if [[ "$result" != "1" ]]; then
    printf '  Slot indisponível ou você já teve um pedido recusado para esta entrega.\n'
    return 1
  fi
}

evaluation_cancel_request() {
  local slot_id="$1"

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    local response
    response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$slot_id" |
      broker_request evaluations.cancel token slot_id) || return 1
    broker_check_response "$response"
    return $?
  fi

  [[ "$slot_id" =~ ^[0-9]+$ ]] || return 2
  local result
  result=$(db_query "
    BEGIN IMMEDIATE;
    UPDATE evaluation_requests
    SET status = 'cancelled', responded_at = CURRENT_TIMESTAMP
    WHERE id = (
      SELECT id FROM evaluation_requests
      WHERE slot_id = $slot_id AND reviewer_id = $CURRENT_USER_ID
        AND status IN ('pending', 'accepted')
      ORDER BY id DESC LIMIT 1
    );
    SELECT changes();
    UPDATE evaluation_slots
    SET accepted_by = NULL, accepted_at = NULL, live_room = NULL
    WHERE id = $slot_id AND status = 'open'
      AND accepted_by = $CURRENT_USER_ID;
    COMMIT;
  ") || return 1
  if [[ "$result" != "1" ]]; then
    printf '  Não há pedido seu que possa ser cancelado.\n'
    return 1
  fi
}

evaluation_handle_pending_requests() {
  local rows
  local encoded
  local request_id
  local slot_id
  local reviewer
  local exercise_code
  local exercise_name
  local requested_at
  local request_status
  local choice
  local decision
  local result
  local -a requests

  rows=$(evaluation_pending_requests) || return 1
  [[ -n "$rows" ]] || return 0
  mapfile -t requests <<<"$rows"
  for encoded in "${requests[@]}"; do
    IFS=$'\t' read -r request_id slot_id reviewer exercise_code \
      exercise_name requested_at request_status <<<"$encoded"
    request_id=$(db_hex_decode "$request_id") || return 1
    slot_id=$(db_hex_decode "$slot_id") || return 1
    reviewer=$(db_hex_decode "$reviewer") || return 1
    exercise_code=$(db_hex_decode "$exercise_code") || return 1
    exercise_name=$(db_hex_decode "$exercise_name") || return 1
    requested_at=$(db_hex_decode "$requested_at") || return 1
    request_status=$(db_hex_decode "$request_status") || return 1
    ui_panel "PEDIDO PARA AVALIAR · $exercise_code"
    ui_wrap_text "Avaliador: $reviewer" "$(ui_width)" 2
    ui_wrap_text "Exercício: $exercise_name" "$(ui_width)" 2
    ui_wrap_text "Pedido recebido: $requested_at" "$(ui_width)" 2
    ui_panel_end
    if [[ "$request_status" == "accepted" ]]; then
      if ui_menu_choice choice "AVALIAÇÃO ACEITA · $exercise_code" \
        "1|Abrir sala de áudio e vídeo" \
        "0|Voltar"; then
        [[ "$choice" == "1" ]] && evaluation_open_live_room "$slot_id"
      fi
      continue
    fi
    if ! ui_menu_choice choice "PEDIDO $request_id" \
      "1|Aceitar avaliação" \
      "2|Recusar avaliação" \
      "0|Decidir depois"; then
      return 1
    fi
    case "$choice" in
      1) decision="accept" ;;
      2) decision="decline" ;;
      *) continue ;;
    esac
    result=$(evaluation_respond_to_request "$request_id" "$decision") || return 1
    if [[ "$decision" == "accept" ]]; then
      ui_success "Pedido aceito. O avaliador já pode consultar a entrega."
      evaluation_open_live_room "$slot_id" || return 1
    else
      ui_message "Pedido recusado. A entrega voltou à lista de avaliações."
    fi
  done
}

evaluation_review_prompt() {
  local slot_id="$1"
  local submission_info
  local submission_content
  local safe_submission_content
  local submitter
  local exercise_code
  local exercise_name
  local exercise_description
  local exercise_statement
  local content_kind
  local commit_url
  local delivery_directory
  local score
  local feedback

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_evaluation_review_prompt "$slot_id"
    return $?
  fi

  submission_info=$(db_query "
    SELECT hex(u.username) || '|' || hex(e.code) || '|' || hex(e.name) || '|' ||
           hex(e.description) || '|' || hex(COALESCE(e.statement, '')) || '|' ||
           hex(s.content_kind) || '|' || hex(COALESCE(s.commit_url, '')) || '|' ||
           hex(COALESCE(s.delivery_directory, ''))
    FROM evaluation_slots slot
    JOIN submissions s ON s.id = slot.submission_id
    JOIN users u ON u.id = s.user_id
    JOIN exercises e ON e.id = s.exercise_id
    JOIN evaluation_requests request
      ON request.slot_id = slot.id
     AND request.reviewer_id = $CURRENT_USER_ID
     AND request.status = 'accepted'
    WHERE slot.id = $slot_id
      AND slot.status = 'open'
      AND slot.accepted_by = $CURRENT_USER_ID
      AND s.user_id != $CURRENT_USER_ID;
  ") || return 1

  if [[ -z "$submission_info" ]]; then
    ui_message "Esse pedido ainda não foi aceito, o slot está indisponível ou pertence a você."
    ui_pause
    return 1
  fi

  submission_content=$(db_query "
    SELECT s.content
    FROM evaluation_slots slot
    JOIN submissions s ON s.id = slot.submission_id
    WHERE slot.id = $slot_id AND s.content_kind = 'legacy';
  ") || return 1
  safe_submission_content=$(ui_sanitize "$submission_content") || return 1
  local submitter_hex
  local exercise_code_hex
  local exercise_name_hex
  local exercise_description_hex
  local exercise_statement_hex
  local content_kind_hex
  local commit_url_hex
  local delivery_directory_hex
  IFS='|' read -r submitter_hex exercise_code_hex exercise_name_hex \
    exercise_description_hex exercise_statement_hex content_kind_hex \
    commit_url_hex delivery_directory_hex <<<"$submission_info"
  submitter=$(db_hex_decode "$submitter_hex") || return 1
  exercise_code=$(db_hex_decode "$exercise_code_hex") || return 1
  exercise_name=$(db_hex_decode "$exercise_name_hex") || return 1
  exercise_description=$(db_hex_decode "$exercise_description_hex") || return 1
  exercise_statement=$(db_hex_decode "$exercise_statement_hex") || return 1
  content_kind=$(db_hex_decode "$content_kind_hex") || return 1
  commit_url=$(db_hex_decode "$commit_url_hex") || return 1
  delivery_directory=$(db_hex_decode "$delivery_directory_hex") || return 1

  ui_screen_begin "AVALIAR SUBMISSÃO $slot_id"
  ui_panel "$exercise_code — $exercise_name"
  ui_color_dim
  ui_wrap_text "Avaliado: $(ui_sanitize "$submitter")" "$(ui_width)" 2
  ui_wrap_text "Avaliador: $(ui_sanitize "$CURRENT_USERNAME")" "$(ui_width)" 2
  ui_color_reset
  [[ -n "$exercise_description" ]] && {
    ui_section "DESCRIÇÃO"
    ui_wrap_text "$exercise_description" "$(ui_width)" 2
  }
  [[ -n "$exercise_statement" ]] && {
    ui_section "EXERCÍCIO"
    ui_wrap_text "$exercise_statement" "$(ui_width)" 2
  }
  printf '\n'
  if [[ "$content_kind" == "github_commit" ]]; then
    printf '  Referência da entrega:\n'
    ui_wrap_text "GitHub: $(ui_sanitize "$commit_url")" "$(ui_width)" 2
    ui_wrap_text "Diretório: $(ui_sanitize "$delivery_directory")" "$(ui_width)" 2
    printf '\n'
  else
    printf '  Conteúdo legado enviado:\n'
    ui_wrap_text "$safe_submission_content" "$(ui_width)" 2
    printf '\n'
  fi
  ui_panel_end

  while true; do
    ui_read_line score "  Nota (0 a 5) > " || return 1
    if [[ "$score" =~ ^[0-5]$ ]]; then
      break
    fi
    ui_message "A nota deve ser um número inteiro de 0 a 5."
  done

  ui_read_line feedback "  Feedback da avaliação (obrigatório) > " || return 1
  while [[ -z "${feedback//[[:space:]]/}" ]]; do
    ui_message "O feedback da avaliação é obrigatório."
    ui_read_line feedback "  Feedback da avaliação (obrigatório) > " || return 1
  done
  if ! ui_confirm "Registrar esta nota e o feedback?"; then
    ui_warn "Avaliação cancelada; nenhuma alteração foi registrada."
    return 0
  fi
  if evaluation_record_review "$slot_id" "$score" "$feedback"; then
    if ((score >= 3)); then
      ui_success "Avaliação registrada: aprovado. $(ui_badge APROVADO success) Você recebeu 1 ponto."
    else
      ui_warn "Avaliação registrada: reprovado. $(ui_badge REPROVADO error) O usuário poderá tentar novamente. Você recebeu 1 ponto."
    fi
  else
    ui_message "Não foi possível registrar a avaliação."
  fi
  ui_pause
}

evaluation_response_prompt() {
  local review_id="$1"
  local row
  local code
  local reviewer
  local score
  local feedback
  local recipient_score
  local recipient_feedback

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_evaluation_response_prompt "$review_id"
    return $?
  fi

  row=$(db_query "
    SELECT hex(e.code) || '|' || hex(reviewer.username) || '|' ||
           r.score || '|' || hex(r.feedback)
    FROM reviews r
    JOIN evaluation_slots slot ON slot.id = r.slot_id
    JOIN submissions s ON s.id = slot.submission_id
    JOIN exercises e ON e.id = s.exercise_id
    JOIN users reviewer ON reviewer.id = r.reviewer_id
    WHERE r.id = $review_id
      AND s.user_id = $CURRENT_USER_ID
      AND r.recipient_responded_at IS NULL;
  ") || return 1
  if [[ -z "$row" ]]; then
    ui_message "Essa avaliação já foi respondida ou não pertence a você."
    ui_pause
    return 1
  fi
  IFS='|' read -r code reviewer score feedback <<<"$row"
  code=$(db_hex_decode "$code") || return 1
  reviewer=$(db_hex_decode "$reviewer") || return 1
  feedback=$(db_hex_decode "$feedback") || return 1

  ui_screen_begin "RESPONDER AVALIAÇÃO"
  ui_panel "$code — avaliação de $reviewer"
  ui_wrap_text "Nota recebida: $score/5" "$(ui_width)" 2
  ui_wrap_text "Feedback: $feedback" "$(ui_width)" 2
  ui_panel_end
  while true; do
    ui_read_line recipient_score "  Sua nota ao avaliador (0 a 5) > " || return 1
    if [[ "$recipient_score" =~ ^[0-5]$ ]]; then
      break
    fi
    ui_message "A nota deve ser um número inteiro de 0 a 5."
  done
  printf '  Resposta ao feedback (obrigatória):\n'
  ui_read_multiline recipient_feedback || return 1
  while [[ -z "${recipient_feedback//[[:space:]]/}" ]]; do
    ui_message "A resposta da avaliação é obrigatória."
    printf '  Resposta ao feedback (obrigatória):\n'
    ui_read_multiline recipient_feedback || return 1
  done
  if ! ui_confirm "Enviar sua resposta e nota ao avaliador?"; then
    ui_warn "Resposta cancelada; nenhuma alteração foi registrada."
    return 0
  fi
  if evaluation_respond_to_review "$review_id" "$recipient_score" "$recipient_feedback"; then
    ui_success "Resposta registrada. O avaliador foi notificado."
  else
    ui_message "Não foi possível registrar a resposta."
  fi
  ui_pause
}

evaluations_menu() {
  local slots
  local slot
  local choice
  local username
  local code
  local created_at
  local request_status
  local exercise_name
  local response
  local selected_slot
  local -a slot_list
  local -a menu_options

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_evaluations_menu
    return $?
  fi

  nav_push "AVALIAÇÕES"

  while true; do
    ui_screen_begin "AVALIAÇÕES ABERTAS"

    evaluation_expire_pending_requests || {
      nav_pop
      return 1
    }
    slots=$(db_query "
      SELECT slot.id || '|' || hex(u.username) || '|' || hex(e.code) || '|' ||
             hex(slot.created_at) || '|' || COALESCE(request.status, '') || '|' ||
             hex(e.name)
      FROM evaluation_slots slot
      JOIN submissions s ON s.id = slot.submission_id
      JOIN users u ON u.id = s.user_id
      JOIN exercises e ON e.id = s.exercise_id
      LEFT JOIN evaluation_requests request
        ON request.slot_id = slot.id
       AND request.reviewer_id = $CURRENT_USER_ID
       AND request.status IN ('pending', 'accepted')
      WHERE slot.status = 'open'
        AND s.user_id != $CURRENT_USER_ID
        AND (slot.accepted_by IS NULL OR slot.accepted_by = $CURRENT_USER_ID)
        AND NOT EXISTS (
          SELECT 1 FROM evaluation_requests declined
          WHERE declined.slot_id = slot.id
            AND declined.reviewer_id = $CURRENT_USER_ID
            AND declined.status = 'declined'
        )
      ORDER BY slot.created_at, slot.id;
    ") || {
      nav_pop
      return 1
    }

    if [[ -z "$slots" ]]; then
      ui_empty_state "Nenhuma avaliação disponível." \
        "As submissões de outras pessoas aparecerão aqui."
      ui_pause
      nav_pop
      return 0
    fi

    mapfile -t slot_list <<<"$slots"
    menu_options=()
    for selected_slot in "${slot_list[@]}"; do
      IFS='|' read -r slot username code created_at request_status exercise_name \
        <<<"$selected_slot"
      username=$(db_hex_decode "$username") || return 1
      code=$(db_hex_decode "$code") || return 1
      created_at=$(db_hex_decode "$created_at") || return 1
      exercise_name=$(db_hex_decode "$exercise_name") || return 1
      case "$request_status" in
        pending) menu_options+=("$slot|$code - pedido aguardando aceite") ;;
        accepted) menu_options+=("$slot|$code - $username aceitou seu pedido") ;;
        *) menu_options+=("$slot|$username / $code - $exercise_name (desde $created_at)") ;;
      esac
    done
    menu_options+=("0|Voltar")
    if ! ui_menu_choice choice "AVALIAÇÕES ABERTAS" "${menu_options[@]}"; then
      nav_pop
      return 0
    fi

    if [[ "$choice" == "0" ]]; then
      nav_pop
      return 0
    fi
    if [[ ! "$choice" =~ ^[0-9]+$ ]]; then
      ui_error "Opção inválida."
      ui_pause
      continue
    fi

    selected_slot=""
    for selected_slot in "${slot_list[@]}"; do
      IFS='|' read -r slot username code created_at request_status exercise_name \
        <<<"$selected_slot"
      if [[ "$slot" == "$choice" ]]; then
        break
      fi
    done
    if [[ -z "$selected_slot" ]]; then
      ui_error "Slot indisponível."
      ui_pause
      continue
    fi
    case "$request_status" in
      pending)
        if ui_menu_choice response "PEDIDO PARA $code" \
          "1|Cancelar meu pedido" "0|Voltar"; then
          if [[ "$response" == "1" ]]; then
            evaluation_cancel_request "$slot" &&
              ui_message "Pedido cancelado; o slot voltou à lista."
            ui_pause
          fi
        fi
        ;;
      accepted)
        ui_menu_choice response "SLOT $slot" \
          "1|Abrir sala de áudio e vídeo" \
          "2|Avaliar submissão" \
          "3|Cancelar meu pedido" \
          "0|Voltar" || continue
        case "$response" in
          1) evaluation_open_live_room "$slot" ;;
          2) evaluation_review_prompt "$slot" ;;
          3)
            evaluation_cancel_request "$slot" &&
              ui_message "Pedido cancelado; o slot voltou à lista."
            ui_pause
            ;;
        esac
        ;;
      *)
        if ui_confirm "Solicitar autorização para avaliar esta entrega?"; then
          if evaluation_start_request "$slot"; then
            ui_message "Pedido enviado. Você verá a entrega após o aceite do autor."
            ui_pause
          else
            ui_pause
          fi
        fi
        ;;
    esac
  done
}

submission_status_label() {
  case "$1" in
    pending) printf 'aguardando avaliação\n' ;;
    approved) printf 'aprovada\n' ;;
    rejected) printf 'reprovada\n' ;;
    *) printf '%s\n' "$1" ;;
  esac
}

submission_detail_field() {
  local label="$1"
  local value="$2"

  ui_color_bold
  ui_color_white
  ui_wrap_text "$label" "$(ui_width)" 2
  ui_color_reset
  ui_color_content
  ui_wrap_text "$value" "$(ui_width)" 4
  ui_color_reset
}

submission_render_detail() {
  local id="$1"
  local code="$2"
  local name="$3"
  local description="$4"
  local mission="$5"
  local example="$6"
  local exercise_directory="$7"
  local delivery_files="$8"
  local allowed_functions="$9"
  local terminal_model="${10}"
  local hints="${11}"
  local status="${12}"
  local kind="${13}"
  local commit_url="${14}"
  local directory="${15}"
  local submitted_at="${16}"
  local slot_id="${17}"
  local score="${18}"
  local feedback="${19}"
  local status_label

  ui_screen_begin "ENTREGA #$id · $code"
  exercise_render_content "$name" "$description" "$mission" "$example" \
    "$exercise_directory" "$delivery_files" "$allowed_functions" \
    "$terminal_model" "$hints"

  status_label=$(submission_status_label "$status")
  ui_section "SUA ENTREGA"
  submission_detail_field "Estado" "$status_label · Enviada em: $submitted_at"
  if [[ "$kind" == "github_commit" ]]; then
    submission_detail_field "Commit" "$commit_url"
    submission_detail_field "Diretório" "$directory"
  else
    submission_detail_field "Slot de avaliação" "$slot_id"
  fi

  ui_separator
  ui_section "AVALIAÇÃO"
  if [[ -n "$score" ]]; then
    submission_detail_field "Nota" "$score/5"
  else
    submission_detail_field "Nota" "Aguardando avaliação"
  fi
  if [[ -n "$feedback" ]]; then
    submission_detail_field "Feedback" "$feedback"
  else
    submission_detail_field "Feedback" "Ainda não há feedback."
  fi
}

submission_render_local_detail() {
  local submission_id="$1"
  local row
  local id_hex
  local code_hex
  local name_hex
  local description_hex
  local mission_hex
  local statement_path_hex
  local example_hex
  local exercise_directory_hex
  local delivery_files_hex
  local allowed_functions_hex
  local terminal_model_hex
  local hints_hex
  local status_hex
  local kind_hex
  local commit_url_hex
  local directory_hex
  local submitted_at_hex
  local slot_id_hex
  local score_hex
  local feedback_hex
  local id
  local code
  local name
  local description
  local mission
  local statement_path
  local example
  local exercise_directory
  local delivery_files
  local allowed_functions
  local terminal_model
  local hints
  local status
  local kind
  local commit_url
  local directory
  local submitted_at
  local slot_id
  local score
  local feedback
  local full_path

  [[ "$submission_id" =~ ^[0-9]+$ ]] || return 2
  row=$(db_query "
    SELECT hex(s.id) || '|' || hex(e.code) || '|' || hex(e.name) || '|' ||
           hex(COALESCE(e.description, '')) || '|' ||
           hex(COALESCE(e.statement, '')) || '|' ||
           hex(COALESCE(e.statement_path, '')) || '|' ||
           hex(COALESCE(e.example, '')) || '|' ||
           hex(COALESCE(e.delivery_directory, '')) || '|' ||
           hex(COALESCE(e.delivery_files, '')) || '|' ||
           hex(COALESCE(e.allowed_functions, '')) || '|' ||
           hex(COALESCE(e.terminal_model, '')) || '|' ||
           hex(COALESCE(e.hints, '')) || '|' ||
           hex(s.status) || '|' || hex(s.content_kind) || '|' ||
           hex(COALESCE(s.commit_url, '')) || '|' ||
           hex(COALESCE(s.delivery_directory, '')) || '|' ||
           hex(s.submitted_at) || '|' || hex(COALESCE(slot.id, '')) || '|' ||
           hex(COALESCE(r.score, '')) || '|' || hex(COALESCE(r.feedback, ''))
    FROM submissions s
    JOIN exercises e ON e.id = s.exercise_id
    LEFT JOIN evaluation_slots slot ON slot.submission_id = s.id
    LEFT JOIN reviews r ON r.slot_id = slot.id
    WHERE s.id = $submission_id AND s.user_id = $CURRENT_USER_ID;
  ") || return 1
  if [[ -z "$row" ]]; then
    ui_error "A entrega selecionada não foi encontrada."
    return 1
  fi

  IFS='|' read -r id_hex code_hex name_hex description_hex mission_hex \
    statement_path_hex example_hex exercise_directory_hex delivery_files_hex \
    allowed_functions_hex terminal_model_hex hints_hex status_hex kind_hex \
    commit_url_hex directory_hex submitted_at_hex slot_id_hex score_hex \
    feedback_hex <<<"$row"
  id=$(db_hex_decode "$id_hex") || return 1
  code=$(db_hex_decode "$code_hex") || return 1
  name=$(db_hex_decode "$name_hex") || return 1
  description=$(db_hex_decode "$description_hex") || return 1
  mission=$(db_hex_decode "$mission_hex") || return 1
  statement_path=$(db_hex_decode "$statement_path_hex") || return 1
  example=$(db_hex_decode "$example_hex") || return 1
  exercise_directory=$(db_hex_decode "$exercise_directory_hex") || return 1
  delivery_files=$(db_hex_decode "$delivery_files_hex") || return 1
  allowed_functions=$(db_hex_decode "$allowed_functions_hex") || return 1
  terminal_model=$(db_hex_decode "$terminal_model_hex") || return 1
  hints=$(db_hex_decode "$hints_hex") || return 1
  status=$(db_hex_decode "$status_hex") || return 1
  kind=$(db_hex_decode "$kind_hex") || return 1
  commit_url=$(db_hex_decode "$commit_url_hex") || return 1
  directory=$(db_hex_decode "$directory_hex") || return 1
  submitted_at=$(db_hex_decode "$submitted_at_hex") || return 1
  slot_id=$(db_hex_decode "$slot_id_hex") || return 1
  score=$(db_hex_decode "$score_hex") || return 1
  feedback=$(db_hex_decode "$feedback_hex") || return 1

  if [[ -z "$mission" && -n "$statement_path" ]]; then
    full_path="$SCRIPT_DIR/$statement_path"
    if [[ ! -f "$full_path" && -f "$EXERCISE_STORAGE_DIR/${statement_path##*/}" ]]; then
      full_path="$EXERCISE_STORAGE_DIR/${statement_path##*/}"
    fi
    if [[ ! -f "$full_path" ]]; then
      ui_error "Arquivo do enunciado indisponível: $statement_path"
      return 1
    fi
    mission=$(<"$full_path")
  fi

  submission_render_detail "$id" "$code" "$name" "$description" "$mission" \
    "$example" "$exercise_directory" "$delivery_files" "$allowed_functions" \
    "$terminal_model" "$hints" "$status" "$kind" "$commit_url" "$directory" \
    "$submitted_at" "$slot_id" "$score" "$feedback"
}

submissions_menu() {
  local rows
  local id
  local code
  local name
  local status
  local kind
  local commit_url
  local directory
  local submitted_at
  local slot_id
  local score
  local feedback
  local encoded
  local choice
  local status_label
  local -a submission_rows
  local -a menu_options

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_submissions_menu
    return $?
  fi

  nav_push "MINHAS ENTREGAS"
  rows=$(db_query "
    SELECT hex(s.id) || '|' || hex(e.code) || '|' || hex(e.name) || '|' ||
           hex(s.status) || '|' || hex(s.content_kind) || '|' ||
           hex(COALESCE(s.commit_url, '')) || '|' ||
           hex(COALESCE(s.delivery_directory, '')) || '|' ||
           hex(s.submitted_at) || '|' || hex(COALESCE(slot.id, '')) || '|' ||
           hex(COALESCE(r.score, '')) || '|' || hex(COALESCE(r.feedback, ''))
    FROM submissions s
    JOIN exercises e ON e.id = s.exercise_id
    LEFT JOIN evaluation_slots slot ON slot.submission_id = s.id
    LEFT JOIN reviews r ON r.slot_id = slot.id
    WHERE s.user_id = $CURRENT_USER_ID
    ORDER BY s.submitted_at DESC, s.id DESC;
  ") || {
    nav_pop
    return 1
  }

  if [[ -z "$rows" ]]; then
    ui_screen_begin "MINHAS ENTREGAS"
    ui_empty_state "Você ainda não registrou entregas." \
      "Abra um exercício para enviar uma referência de commit."
    nav_pop
    ui_pause
    return 0
  fi

  mapfile -t submission_rows <<<"$rows"
  while true; do
    ui_screen_begin "MINHAS ENTREGAS"
    menu_options=()
    for encoded in "${submission_rows[@]}"; do
      IFS='|' read -r id code name status kind commit_url directory \
        submitted_at slot_id score feedback <<<"$encoded"
      id=$(db_hex_decode "$id") || {
        nav_pop
        return 1
      }
      code=$(db_hex_decode "$code") || {
        nav_pop
        return 1
      }
      name=$(db_hex_decode "$name") || {
        nav_pop
        return 1
      }
      status=$(db_hex_decode "$status") || {
        nav_pop
        return 1
      }
      kind=$(db_hex_decode "$kind") || {
        nav_pop
        return 1
      }
      commit_url=$(db_hex_decode "$commit_url") || {
        nav_pop
        return 1
      }
      directory=$(db_hex_decode "$directory") || {
        nav_pop
        return 1
      }
      submitted_at=$(db_hex_decode "$submitted_at") || {
        nav_pop
        return 1
      }
      slot_id=$(db_hex_decode "$slot_id") || {
        nav_pop
        return 1
      }
      score=$(db_hex_decode "$score") || {
        nav_pop
        return 1
      }
      feedback=$(db_hex_decode "$feedback") || {
        nav_pop
        return 1
      }

      status_label=$(submission_status_label "$status")
      if [[ -n "$score" ]]; then
        menu_options+=("$id|$code — $name · $status_label · nota $score/5")
      else
        menu_options+=("$id|$code — $name · $status_label")
      fi
    done

    menu_options+=("0|Voltar")
    ui_menu_choice choice "ABRIR EXERCÍCIO ENTREGUE" "${menu_options[@]}" || {
      nav_pop
      return 1
    }
    if [[ "$choice" == "0" ]]; then
      evaluation_handle_pending_requests || {
        ui_error "Não foi possível carregar os pedidos de avaliação."
        ui_pause
      }
      break
    fi
    if ! submission_render_local_detail "$choice"; then
      ui_error "Não foi possível abrir esta entrega. Verifique a mensagem acima."
      ui_pause
      continue
    fi
    ui_pause
  done
  nav_pop
}