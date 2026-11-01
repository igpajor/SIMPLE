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

  feedback_sql=$(db_sql_quote "$feedback")
  db_query "
    INSERT INTO reviews (slot_id, reviewer_id, score, feedback)
    VALUES ($slot_id, $CURRENT_USER_ID, $score, $feedback_sql);
  "
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
  safe_submission_content=$(printf '%s' "$submission_content" |
    LC_ALL=C tr -d '\001-\010\013-\037\177') || return 1
  IFS='|' read -r _ submitter exercise_code content_kind commit_url delivery_directory <<<"$submission_info"

  ui_clear
  printf '\n  AVALIAR SUBMISSÃO %s\n' "$slot_id"
  ui_wrap_text "Usuário: $submitter | Exercício: $exercise_code" "$(ui_width)" 2
  printf '\n'
  if [[ "$content_kind" == "github_commit" ]]; then
    printf '  Referência da entrega:\n'
    ui_wrap_text "Commit: $commit_url" "$(ui_width)" 2
    ui_wrap_text "Diretório: $delivery_directory" "$(ui_width)" 2
    printf '\n'
  else
    printf '  Conteúdo legado enviado:\n'
    ui_wrap_text "$safe_submission_content" "$(ui_width)" 2
    printf '\n'
  fi

  while true; do
    ui_read_line score "  Nota (0 a 5) > " || return 1
    if [[ "$score" =~ ^[0-5]$ ]]; then
      break
    fi
    ui_message "A nota deve ser um número inteiro de 0 a 5."
  done

  ui_read_line feedback "  Comentário (opcional) > " || return 1
  if evaluation_record_review "$slot_id" "$score" "$feedback"; then
    if ((score >= 3)); then
      ui_message "Avaliação registrada: aprovado. Você recebeu 1 ponto."
    else
      ui_message "Avaliação registrada: reprovado. O usuário poderá tentar novamente. Você recebeu 1 ponto."
    fi
  else
    ui_message "Não foi possível registrar a avaliação."
  fi
  ui_pause
}

evaluations_menu() {
  local slots
  local slot
  local choice
  local -a slot_list
  local index

  if [[ "${BROKER_MODE:-0}" == "1" ]]; then
    broker_evaluations_menu
    return $?
  fi

  while true; do
    ui_clear
    ui_header
    printf '\n  AVALIAÇÕES ABERTAS\n\n'

    slots=$(db_query "
      SELECT slot.id || '|' || u.username || '|' || e.code || '|' || slot.created_at
      FROM evaluation_slots slot
      JOIN submissions s ON s.id = slot.submission_id
      JOIN users u ON u.id = s.user_id
      JOIN exercises e ON e.id = s.exercise_id
      WHERE slot.status = 'open'
        AND s.user_id != $CURRENT_USER_ID
      ORDER BY slot.created_at, slot.id;
    ") || return 1

    if [[ -z "$slots" ]]; then
      ui_message "Não há submissões de outros usuários aguardando avaliação."
      ui_pause
      return 0
    fi

    mapfile -t slot_list <<<"$slots"
    for index in "${!slot_list[@]}"; do
      IFS='|' read -r slot username code created_at <<<"${slot_list[$index]}"
      ui_wrap_text "[$((index + 1))] Slot $slot - $username / $code (desde $created_at)" \
        "$(ui_width)" 2
    done
    printf '\n  [0] Voltar\n\n'
    ui_read_line choice "  AVALIAÇÕES > " || return 0

    if [[ "$choice" == "0" ]]; then
      return 0
    fi
    if [[ ! "$choice" =~ ^[0-9]+$ ]] ||
      ((choice < 1 || choice > ${#slot_list[@]})); then
      ui_message "Opção inválida."
      ui_pause
      continue
    fi

    IFS='|' read -r slot _ <<<"${slot_list[$((choice - 1))]}"
    evaluation_review_prompt "$slot"
  done
}