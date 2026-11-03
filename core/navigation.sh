#!/usr/bin/env bash

# ==========================================
# SIMPLE - MÓDULO DE NAVEGAÇÃO
# Responsável pelos menus do sistema
# ==========================================

menu_principal() {
  local choice
  local current_points
  local completed_count
  local open_reviews
  local unread_notifications
  local progress_label="Meu progresso"
  local -a menu_options

  current_points=$(points_balance "$CURRENT_USER_ID") || return 1
  completed_count=$(db_query "
    SELECT COUNT(DISTINCT exercise_id)
    FROM submissions
    WHERE user_id = $CURRENT_USER_ID AND status = 'approved';
  ") || return 1
  open_reviews=$(db_query "
    SELECT COUNT(*)
    FROM evaluation_slots slot
    JOIN submissions s ON s.id = slot.submission_id
    WHERE slot.status = 'open' AND s.user_id != $CURRENT_USER_ID;
  ") || return 1
  unread_notifications=$(progress_unread_notification_count) || return 1
  if ((unread_notifications > 0)); then
    progress_label+=" ($unread_notifications novas)"
  fi

  menu_options=(
    "1|Explorar exercícios"
    "2|Avaliações abertas"
    "3|$progress_label"
    "4|Meu perfil"
  )
  if users_is_admin "$CURRENT_USER_ID"; then
    menu_options+=("5|Administração")
    menu_options+=("6|Minhas entregas")
  else
    menu_options+=("5|Minhas entregas")
  fi
  menu_options+=("0|Sair")

  ui_screen_begin "INÍCIO"
  ui_home_intro "$CURRENT_USERNAME" || return 1
  ui_home_metrics "$current_points" "$completed_count" "$open_reviews" || return 1
  if ((unread_notifications > 0)); then
    ui_warn "Você tem $unread_notifications notificação(ões) nova(s). Confira Meu progresso."
  fi
  printf '\n'
  if ! ui_menu_choice choice "O QUE VOCÊ QUER FAZER?" "${menu_options[@]}"; then
    return 1
  fi

  REPLY="$choice"
  return 0
}
