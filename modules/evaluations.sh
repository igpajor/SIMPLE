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
  db_query "
    BEGIN IMMEDIATE;
    INSERT INTO reviews (slot_id, reviewer_id, score, feedback)
    VALUES ($slot_id, $CURRENT_USER_ID, $score, $feedback_sql);
    INSERT INTO notifications (user_id, type, review_id)
    SELECT s.user_id, 'review_received',
           (SELECT id FROM reviews WHERE slot_id = $slot_id)
    FROM evaluation_slots slot
    JOIN submissions s ON s.id = slot.submission_id
    WHERE slot.id = $slot_id;
    COMMIT;
  "
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

evaluation_review_prompt() {
  local slot_id="$1"
  local submission_info
  local submission_content
  local safe_submission_content
  local submitter
  local exercise_code
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
    SELECT slot.id || '|' || u.username || '|' || e.code || '|' ||
           s.content_kind || '|' || COALESCE(s.commit_url, '') || '|' ||
           COALESCE(s.delivery_directory, '')
    FROM evaluation_slots slot
    JOIN submissions s ON s.id = slot.submission_id
    JOIN users u ON u.id = s.user_id
    JOIN exercises e ON e.id = s.exercise_id
    WHERE slot.id = $slot_id
      AND slot.status = 'open'
      AND s.user_id != $CURRENT_USER_ID;
  ") || return 1

  if [[ -z "$submission_info" ]]; then
    ui_message "Esse slot não existe, já foi avaliado ou pertence a você."
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
  IFS='|' read -r _ submitter exercise_code content_kind commit_url delivery_directory <<<"$submission_info"

  ui_screen_begin "AVALIAR SUBMISSÃO $slot_id"
  ui_panel "SUBMISSÃO $slot_id"
  ui_color_dim
  ui_wrap_text "Usuário: $(ui_sanitize "$submitter") | Exercício: $(ui_sanitize "$exercise_code")" "$(ui_width)" 2
  ui_color_reset
  printf '\n'
  if [[ "$content_kind" == "github_commit" ]]; then
    printf '  Referência da entrega:\n'
    ui_wrap_text "Commit: $(ui_sanitize "$commit_url")" "$(ui_width)" 2
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
  local -a slot_list
  local -a menu_options
  local index

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_evaluations_menu
    return $?
  fi

  nav_push "AVALIAÇÕES"

  while true; do
    ui_screen_begin "AVALIAÇÕES ABERTAS"

    slots=$(db_query "
      SELECT slot.id || '|' || u.username || '|' || e.code || '|' || slot.created_at
      FROM evaluation_slots slot
      JOIN submissions s ON s.id = slot.submission_id
      JOIN users u ON u.id = s.user_id
      JOIN exercises e ON e.id = s.exercise_id
      WHERE slot.status = 'open'
        AND s.user_id != $CURRENT_USER_ID
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
    for index in "${!slot_list[@]}"; do
      IFS='|' read -r slot username code created_at <<<"${slot_list[$index]}"
      menu_options+=("$((index + 1))|Slot $slot - $username / $code (desde $created_at)")
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
    if [[ ! "$choice" =~ ^[0-9]+$ ]] ||
      ((choice < 1 || choice > ${#slot_list[@]})); then
      ui_error "Opção inválida."
      ui_pause
      continue
    fi

    IFS='|' read -r slot _ <<<"${slot_list[$((choice - 1))]}"
    evaluation_review_prompt "$slot"
  done
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

  ui_screen_begin "MINHAS ENTREGAS"
  if [[ -z "$rows" ]]; then
    ui_empty_state "Você ainda não registrou entregas." \
      "Abra um exercício para enviar uma referência de commit."
    nav_pop
    ui_pause
    return 0
  fi

  while IFS='|' read -r id code name status kind commit_url directory \
    submitted_at slot_id score feedback; do
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

    ui_panel "#$id · $code — $name"
    case "$status" in
    pending) status_label="aguardando avaliação" ;;
    approved) status_label="aprovada" ;;
    rejected) status_label="reprovada" ;;
    esac
    ui_color_dim
    ui_wrap_text "Estado: $(ui_badge "$status_label" "$status") · Enviada em: $submitted_at" "$(ui_width)" 2
    ui_color_reset
    if [[ "$kind" == "github_commit" ]]; then
      ui_wrap_text "Commit: $commit_url" "$(ui_width)" 2
      ui_wrap_text "Diretório: $directory" "$(ui_width)" 2
    else
      ui_wrap_text "Slot de avaliação: $slot_id" "$(ui_width)" 2
    fi
    if [[ -n "$score" ]]; then
      ui_wrap_text "Nota: $(ui_badge "$score/5" "info")" "$(ui_width)" 2
      [[ -n "$feedback" ]] && ui_wrap_text "Feedback: $feedback" "$(ui_width)" 2
    fi
    ui_panel_end
  done <<<"$rows"

  nav_pop
  ui_pause
}