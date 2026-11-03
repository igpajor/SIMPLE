#!/usr/bin/env bash

progress_menu() {
  local completed_count
  local balance
  local history
  local approved_exercises
  local amount
  local entry_type
  local details
  local created_at
  local exercise_code
  local exercise_name

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
    SELECT e.code || '|' || r.score || '|' ||
           CASE WHEN s.user_id = $CURRENT_USER_ID THEN 'Recebida de ' || reviewer.username
                ELSE 'Avaliada por você (' || owner.username || ')' END ||
           '|' || r.created_at
    FROM reviews r
    JOIN evaluation_slots slot ON slot.id = r.slot_id
    JOIN submissions s ON s.id = slot.submission_id
    JOIN exercises e ON e.id = s.exercise_id
    JOIN users reviewer ON reviewer.id = r.reviewer_id
    JOIN users owner ON owner.id = s.user_id
    WHERE s.user_id = $CURRENT_USER_ID OR r.reviewer_id = $CURRENT_USER_ID
    ORDER BY r.created_at DESC, r.id DESC;
  ") || return 1

  nav_push "MEU PROGRESSO"

  ui_screen_begin "MEU PROGRESSO"
  printf '  Exercícios aprovados: %s\n' "$completed_count"
  printf '  Saldo de pontos: %s\n\n' "$balance"
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
    while IFS='|' read -r code score relation created_at; do
      ui_wrap_text "$(ui_sanitize "$code") - nota $score/5 - $(ui_sanitize "$relation") ($(ui_sanitize "$created_at"))" "$(ui_width)" 2
    done <<<"$history"
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