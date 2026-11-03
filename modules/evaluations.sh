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

  ui_read_line feedback "  Comentário (opcional) > " || return 1
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