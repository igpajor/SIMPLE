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

  ui_clear
  ui_header
  printf '\n  MEU PROGRESSO\n\n'
  printf '  Exercícios aprovados: %s\n' "$completed_count"
  printf '  Saldo de pontos: %s\n\n' "$balance"
  printf '  EXERCÍCIOS CONCLUÍDOS\n'
  ui_separator
  if [[ -z "$approved_exercises" ]]; then
    printf '  Nenhum exercício aprovado ainda.\n'
  else
    while IFS='|' read -r exercise_code exercise_name; do
      ui_wrap_text "$exercise_code - $exercise_name" "$(ui_width)" 2
    done <<<"$approved_exercises"
  fi
  printf '\n'
  printf '  HISTÓRICO DE AVALIAÇÕES\n'
  ui_separator

  if [[ -z "$history" ]]; then
    printf '  Nenhuma avaliação registrada.\n'
  else
    while IFS='|' read -r code score relation created_at; do
      ui_wrap_text "$code - nota $score/5 - $relation ($created_at)" "$(ui_width)" 2
    done <<<"$history"
  fi

  printf '\n  MOVIMENTAÇÕES DE PONTOS\n'
  ui_separator
  history=$(points_history "$CURRENT_USER_ID") || return 1
  if [[ -z "$history" ]]; then
    printf '  Nenhuma movimentação registrada.\n'
  else
    while IFS='|' read -r amount entry_type details created_at; do
      ui_wrap_text "$(printf '%+d | %s | %s (%s)' "$amount" "$entry_type" "$details" "$created_at")" \
        "$(ui_width)" 2
    done <<<"$history"
  fi

  ui_pause
}