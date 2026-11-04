#!/usr/bin/env bash

broker_profile_menu() {
  local response
  local display_name
  local github_username
  local bio
  local created_at
  local choice
  local value
  local width

  while true; do
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request profile.get token) || return 1
    if [[ "$(broker_response_field "$response" ok)" != "True" ]]; then
      broker_response_error "$response"
      ui_pause
      return 1
    fi

    display_name=$(broker_response_field "$response" display_name) || return 1
    github_username=$(broker_response_field "$response" github_username) || return 1
    bio=$(broker_response_field "$response" bio) || return 1
    created_at=$(broker_response_field "$response" created_at) || return 1
    width=$(ui_width)

    ui_screen_begin "MEU PERFIL"

    ui_section "DADOS PESSOAIS"
    ui_labeled_value "Usuário" "$(ui_sanitize "$CURRENT_USERNAME")" "$width"
    [[ -n "$display_name" ]] && ui_labeled_value "Nome de exibição" "$(ui_sanitize "$display_name")" "$width"
    [[ -n "$github_username" ]] && ui_labeled_value "GitHub" "https://github.com/$(ui_sanitize "$github_username")" "$width"
    ui_labeled_value "Criado em" "$(ui_sanitize "$created_at")" "$width"

    if [[ -n "$bio" ]]; then
      printf '\n'
      ui_section "BIOGRAFIA"
      ui_wrap_text "$bio" "$width" 4
    fi

    printf '\n'
    ui_section "PROGRESSO"
    ui_labeled_value "Exercícios aprovados" "$(broker_response_array_length "$response" completed_exercises)" "$width"
    ui_labeled_value "Saldo de pontos" "$(broker_response_field "$response" points)" "$width"

    printf '\n'
    ui_section "EXERCÍCIOS CONCLUÍDOS"
    local completed_rows
    completed_rows=$(broker_response_rows "$response" completed_exercises) || return 1
    if [[ -z "$completed_rows" ]]; then
      ui_empty_state "Nenhum exercício aprovado ainda." "Complete exercícios para vê-los aqui."
    else
      local code name
      while IFS=$'\t' read -r code name; do
        code=$(db_hex_decode "$code") || return 1
        name=$(db_hex_decode "$name") || return 1
        ui_wrap_text "$(ui_sanitize "$code") - $(ui_sanitize "$name")" "$width" 2
      done <<<"$completed_rows"
    fi

    printf '\n'
    if ! ui_menu_choice choice "MEU PERFIL" \
      "1|Editar perfil" \
      "2|Encerrar sessão" \
      "0|Voltar"; then
      return 0
    fi

    case "$choice" in
    1)
      broker_edit_profile_menu || return 1
      ;;
    2)
      if ui_confirm "Encerrar sua sessão?"; then
        broker_logout || return 1
        ui_success "Sessão encerrada."
        CURRENT_USER_ID=""
        CURRENT_USERNAME=""
        CURRENT_SESSION_TOKEN=""
        CURRENT_USER_IS_ADMIN="0"
        return 0
      fi
      ;;
    0)
      return 0
      ;;
    *)
      ui_message "Opção inválida."
      ui_pause
      ;;
    esac
  done
}

# Edição campo a campo do perfil (modo broker).
# Espera que display_name, github_username, bio e CURRENT_SESSION_TOKEN
# estejam em escopo de chamada (escopo dinâmico do bash).
broker_edit_profile_menu() {
  local choice
  local value

  while true; do
    ui_screen_begin "EDITAR MEU PERFIL"
    if ! ui_menu_choice choice "EDITAR MEU PERFIL" \
      "1|Nome de exibição" \
      "2|Usuário do GitHub" \
      "3|Biografia curta" \
      "0|Voltar"; then
      return 0
    fi

    case "$choice" in
    1)
      ui_labeled_value "Nome de exibição atual" "${display_name:-(não definido)}" "$(ui_width)" 2
      ui_read_line value "  Novo nome de exibição (ENTER mantém) > " || continue
      [[ -z "$value" ]] && continue
      display_name="$value"
      broker_profile_update || return 1
      ui_success "Nome de exibição atualizado."
      ui_pause
      ;;
    2)
      ui_labeled_value "Usuário GitHub atual" "${github_username:-(não definido)}" "$(ui_width)" 2
      ui_read_line value "  Novo usuário GitHub (ENTER mantém) > " || continue
      [[ -z "$value" ]] && continue
      if [[ -n "$value" && ! "$value" =~ ^[A-Za-z0-9-]{1,39}$ ]]; then
        ui_error "Informe apenas um usuário GitHub válido (letras, números e hífens)."
        ui_pause
        continue
      fi
      github_username="$value"
      broker_profile_update || return 1
      ui_success "Usuário do GitHub atualizado."
      ui_pause
      ;;
    3)
      printf '  Biografia atual:\n'
      ui_wrap_text "$bio" "$(ui_width)" 2
      printf '  Nova biografia (vazia remove):\n'
      ui_read_multiline value || continue
      bio="$value"
      broker_profile_update || return 1
      ui_success "Biografia atualizada."
      ui_pause
      ;;
    0)
      return 0
      ;;
    *)
      ui_message "Opção inválida."
      ui_pause
      ;;
    esac
  done
}

# Envia profile.update com todos os três campos (protocolo do broker exige todos).
broker_profile_update() {
  local response_update
  response_update=$(printf '%s\0%s\0%s\0%s\0' \
    "$CURRENT_SESSION_TOKEN" "$display_name" "$github_username" "$bio" |
    broker_request profile.update token display_name github_username bio) || return 1
  if [[ "$(broker_response_field "$response_update" ok)" == "True" ]]; then
    return 0
  else
    broker_response_error "$response_update"
    return 1
  fi
}

broker_progress_menu() {
  local response
  local count
  local rows
  local encoded
  local code
  local name
  local score
  local feedback
  local created_at
  local submitter
  local reviewer
  local relation
  local response_score
  local response_feedback
  local response_at
  local notification_type
  local notification_user
  local notification_score
  local review_id
  local choice
  local -a pending_responses
  local -a response_options
  local amount
  local entry_type
  local details
  local pending_request_count
  local accepted_request_rows
  local slot_id
  local accepted_at

  response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
    broker_request progress.get token) || return 1
  if ! broker_check_response "$response"; then
    ui_pause
    return 1
  fi
  ui_screen_begin "MEU PROGRESSO"
  count=$(broker_response_nested_field "$response" submissions approved) || return 1
  printf '  Exercícios aprovados: %s\n' "$count"
  count=$(broker_response_field "$response" points) || return 1
  printf '  Saldo de pontos: %s\n\n' "$count"
  pending_request_count=$(broker_response_field \
    "$response" pending_evaluation_requests) || return 1
  if ((pending_request_count > 0)); then
    ui_message \
      "Você tem $pending_request_count pedido(s) de avaliação aguardando sua decisão. Acesse Minhas entregas." \
      warning
  fi
  accepted_request_rows=$(broker_response_rows \
    "$response" accepted_evaluation_requests) || return 1
  if [[ -n "$accepted_request_rows" ]]; then
    ui_section "PEDIDOS ACEITOS PARA VOCÊ AVALIAR"
    while IFS=$'\t' read -r _ slot_id submitter code name accepted_at; do
      slot_id=$(db_hex_decode "$slot_id") || return 1
      submitter=$(db_hex_decode "$submitter") || return 1
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      accepted_at=$(db_hex_decode "$accepted_at") || return 1
      ui_wrap_text \
        "Pedido #$slot_id aceito por $submitter: $code — $name ($accepted_at). Acesse Avaliações abertas." \
        "$(ui_width)" 2
    done <<<"$accepted_request_rows"
  fi
  ui_section "ENTREGAS POR STATUS"
  for relation in pending approved rejected; do
    count=$(broker_response_nested_field "$response" submissions "$relation") || return 1
    ui_wrap_text "$relation: $count" "$(ui_width)" 2
  done
  ui_section "EXERCÍCIOS CONCLUÍDOS"
  rows=$(broker_response_rows "$response" completed_exercises) || return 1
  if [[ -z "$rows" ]]; then
    ui_empty_state "Nenhum exercício aprovado ainda."
  else
    while IFS=$'\t' read -r code name; do
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      ui_wrap_text "$code - $name" "$(ui_width)" 2
    done <<<"$rows"
  fi
  ui_section "NOTIFICAÇÕES"
  rows=$(broker_response_rows "$response" notifications) || return 1
  if [[ -z "$rows" ]]; then
    ui_empty_state "Nenhuma notificação nova."
  else
    while IFS=$'\t' read -r notification_type code created_at \
      notification_score feedback notification_user; do
      code=$(db_hex_decode "$code") || return 1
      created_at=$(db_hex_decode "$created_at") || return 1
      notification_score=$(db_hex_decode "$notification_score") || return 1
      feedback=$(db_hex_decode "$feedback") || return 1
      notification_user=$(db_hex_decode "$notification_user") || return 1
      if [[ "$notification_type" == "review_received" ]]; then
        ui_wrap_text "Nova avaliação de $notification_user em $code: nota $notification_score/5" \
          "$(ui_width)" 2
      else
        ui_wrap_text "Nova resposta de $notification_user em $code: nota $notification_score/5 ao avaliador" \
          "$(ui_width)" 2
      fi
      ui_wrap_text "Feedback: $(ui_sanitize "$feedback")" "$(ui_width)" 4
      ui_wrap_text "Recebida em: $(ui_sanitize "$created_at")" "$(ui_width)" 4
    done <<<"$rows"
  fi
  local mark_response
  mark_response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
    broker_request notifications.mark_read token) || return 1
  broker_check_response "$mark_response" || return 1

  ui_section "HISTÓRICO DE AVALIAÇÕES"
  rows=$(broker_response_rows "$response" review_history) || return 1
  if [[ -z "$rows" ]]; then
    ui_empty_state "Nenhuma avaliação registrada."
  else
    while IFS= read -r encoded; do
      IFS=$'\t' read -r review_id code score feedback created_at response_score \
        response_feedback response_at submitter reviewer relation <<<"$encoded"
      code=$(db_hex_decode "$code") || return 1
      score=$(db_hex_decode "$score") || return 1
      feedback=$(db_hex_decode "$feedback") || return 1
      created_at=$(db_hex_decode "$created_at") || return 1
      submitter=$(db_hex_decode "$submitter") || return 1
      reviewer=$(db_hex_decode "$reviewer") || return 1
      relation=$(db_hex_decode "$relation") || return 1
      response_score=$(db_hex_decode "$response_score") || return 1
      response_feedback=$(db_hex_decode "$response_feedback") || return 1
      response_at=$(db_hex_decode "$response_at") || return 1
      if [[ "$relation" == "received" ]]; then
        ui_wrap_text "$code - nota $score/5, avaliado por $reviewer ($created_at)" "$(ui_width)" 2
      else
        ui_wrap_text "$code - nota $score/5, submissão de $submitter ($created_at)" "$(ui_width)" 2
      fi
      [[ -n "$feedback" ]] && ui_wrap_text "Feedback: $feedback" "$(ui_width)" 4
      if [[ -n "$response_score" ]]; then
        ui_wrap_text "Sua nota ao avaliador: $response_score/5" "$(ui_width)" 4
        ui_wrap_text "Sua resposta: $response_feedback ($response_at)" "$(ui_width)" 4
      fi
    done <<<"$rows"
  fi

  ui_section "RESPONDER A UMA AVALIAÇÃO"
  rows=$(broker_response_rows "$response" pending_review_responses) || return 1
  pending_responses=()
  if [[ -n "$rows" ]]; then
    while IFS=$'\t' read -r review_id code score feedback reviewer; do
      review_id=$(db_hex_decode "$review_id") || return 1
      code=$(db_hex_decode "$code") || return 1
      score=$(db_hex_decode "$score") || return 1
      feedback=$(db_hex_decode "$feedback") || return 1
      reviewer=$(db_hex_decode "$reviewer") || return 1
      pending_responses+=("$review_id"$'\t'"$code"$'\t'"$score"$'\t'"$feedback"$'\t'"$reviewer")
    done <<<"$rows"
  fi
  if ((${#pending_responses[@]} == 0)); then
    ui_empty_state "Nenhuma avaliação aguardando sua resposta."
  else
    response_options=()
    for review_id in "${!pending_responses[@]}"; do
      IFS=$'\t' read -r _ code _ _ reviewer <<<"${pending_responses[$review_id]}"
      response_options+=("$((review_id + 1))|$code — avaliação de $reviewer")
      ui_wrap_text "[${response_options[$review_id]%%|*}] ${response_options[$review_id]#*|}" \
        "$(ui_width)" 2
    done
    ui_wrap_text "Escolha uma avaliação para responder ou pressione Enter para continuar." \
      "$(ui_width)" 2
    if ui_read_line choice "  AVALIAÇÃO (Enter para continuar) > " &&
      [[ "$choice" =~ ^[0-9]+$ ]] &&
      ((choice >= 1 && choice <= ${#pending_responses[@]})); then
      IFS=$'\t' read -r review_id _ _ _ _ <<<"${pending_responses[$((choice - 1))]}"
      broker_evaluation_response_prompt "$review_id"
    fi
  fi

  ui_section "MOVIMENTAÇÕES DE PONTOS"
  rows=$(broker_response_rows "$response" points_history) || return 1
  if [[ -z "$rows" ]]; then
    ui_empty_state "Nenhuma movimentação registrada."
  else
    while IFS= read -r encoded; do
      IFS=$'\t' read -r amount entry_type details created_at <<<"$encoded"
      amount=$(db_hex_decode "$amount") || return 1
      entry_type=$(db_hex_decode "$entry_type") || return 1
      details=$(db_hex_decode "$details") || return 1
      created_at=$(db_hex_decode "$created_at") || return 1
      details=${details//$'\n'/ }
      ui_wrap_text "$(printf '%+d | %s | %s (%s)' "$amount" "$entry_type" "$details" "$created_at")" \
        "$(ui_width)" 2
    done <<<"$rows"
  fi
  ui_pause
}

broker_response_array_length() {
  local response="$1"
  local field="$2"

  printf '%s' "$response" |
    python3 -c '
import json
import sys
value = json.load(sys.stdin).get(sys.argv[1])
if not isinstance(value, list):
    raise SystemExit("Lista de resposta inesperada")
print(len(value))
' "$field"
}

broker_catalog_menu() {
  local response
  local rows
  local code_hex
  local name_hex
  local code
  local name
  local choice
  local -a exercise_rows
  local -a menu_options
  local index

  while true; do
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request catalog.list token) || return 1
    if ! broker_check_response "$response"; then
      ui_pause
      return 1
    fi
    rows=$(broker_response_rows "$response" exercises) || return 1
    exercise_rows=()
    if [[ -n "$rows" ]]; then
      while IFS=$'\t' read -r code_hex name_hex; do
        code=$(db_hex_decode "$code_hex") || return 1
        name=$(db_hex_decode "$name_hex") || return 1
        exercise_rows+=("$code"$'\t'"$name")
      done <<<"$rows"
    fi

    ui_screen_begin "EXERCÍCIOS"
    if ((${#exercise_rows[@]} == 0)); then
      ui_empty_state "Nenhum exercício disponível."
      ui_pause
      return 0
    fi
    menu_options=()
    for index in "${!exercise_rows[@]}"; do
      IFS=$'\t' read -r code name <<<"${exercise_rows[$index]}"
      menu_options+=("$((index + 1))|$code - $name")
    done
    menu_options+=("0|Voltar")
    ui_menu_choice choice "CATÁLOGO DE EXERCÍCIOS" "${menu_options[@]}" || return 0
    [[ "$choice" == "0" ]] && return 0
    if [[ ! "$choice" =~ ^[0-9]+$ ]] ||
      ((choice < 1 || choice > ${#exercise_rows[@]})); then
      ui_message "Opção inválida."
      ui_pause
      continue
    fi
    IFS=$'\t' read -r code _ <<<"${exercise_rows[$((choice - 1))]}"
    broker_catalog_render_exercise "$code" || ui_pause
  done
}

broker_catalog_render_exercise() {
  local code="$1"
  local response
  local requirements
  local value
  local name
  local description
  local mission
  local delivery_directory
  local delivery_files
  local allowed_functions
  local choice
  local width
  local points
  local eligible

  response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$code" |
    broker_request catalog.get token exercise_code) || return 1
  if ! broker_check_response "$response"; then
    ui_pause
    return 1
  fi
  width=$(ui_width)
  ui_screen_begin "EXERCÍCIO $code"
  name=$(broker_response_nested_field "$response" exercise name) || return 1
  description=$(broker_response_nested_field "$response" exercise description) || return 1
  delivery_directory=$(broker_response_nested_field "$response" exercise delivery_directory) || return 1
  delivery_files=$(broker_response_nested_field "$response" exercise delivery_files) || return 1
  allowed_functions=$(broker_response_nested_field "$response" exercise allowed_functions) || return 1
  mission=$(broker_response_nested_field "$response" exercise statement) || return 1
  local example
  example=$(broker_response_nested_field "$response" exercise example) || return 1
  local terminal_model
  terminal_model=$(broker_response_nested_field "$response" exercise terminal_model) || return 1
  local hints
  hints=$(broker_response_nested_field "$response" exercise hints) || return 1
  exercise_render_content "$name" "$description" "$mission" "$example" \
    "$delivery_directory" "$delivery_files" "$allowed_functions" \
    "$terminal_model" "$hints"
  requirements=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$code" |
    broker_request submissions.requirements token exercise_code) || return 1
  if ! broker_check_response "$requirements"; then
    ui_pause
    return 1
  fi
  local -a menu_options
  eligible=$(broker_response_field "$requirements" eligible) || return 1
  if [[ "$eligible" == "True" ]]; then
    points=$(broker_response_field "$requirements" points) || return 1
    printf '\n  Saldo: %s ponto(s). Enviar a solução abre um slot de avaliação.\n' "$points"
    menu_options=(
      "1|Registrar entrega para avaliação"
      "2|Chat deste exercício"
      "0|Voltar"
    )
  else
    value=$(broker_response_field "$requirements" reason) || return 1
    ui_wrap_text "Entrega indisponível: $value" "$width" 2
    menu_options=("2|Chat deste exercício" "0|Voltar")
  fi
  ui_menu_choice choice "EXERCÍCIO $code" "${menu_options[@]}" || return 0
  if [[ "$choice" == "1" && "$eligible" == "True" ]]; then
    exercise_submit_prompt "$code"
  elif [[ "$choice" == "2" ]]; then
    # shellcheck disable=SC2034 # Consumed by chat_menu in the sourced chat module.
    CHAT_INITIAL_CHANNEL="exercise:$code"
    # shellcheck disable=SC2034
    CHAT_INITIAL_ROOM_NAME="SALA · $code"
    chat_menu || ui_error "Não foi possível acessar a sala deste exercício."
  fi
}

broker_submission_render_detail() {
  local submission_id="$1"
  local response
  local id
  local code
  local name
  local description
  local mission
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

  response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$submission_id" |
    broker_request submissions.get token submission_id) || return 1
  if ! broker_check_response "$response"; then
    ui_pause
    return 1
  fi

  id=$(broker_response_nested_field "$response" submission id) || return 1
  code=$(broker_response_nested_field "$response" submission exercise_code) || return 1
  name=$(broker_response_nested_field "$response" submission exercise name) || return 1
  description=$(broker_response_nested_field "$response" submission exercise description) || return 1
  mission=$(broker_response_nested_field "$response" submission exercise statement) || return 1
  example=$(broker_response_nested_field "$response" submission exercise example) || return 1
  exercise_directory=$(broker_response_nested_field "$response" submission exercise delivery_directory) || return 1
  delivery_files=$(broker_response_nested_field "$response" submission exercise delivery_files) || return 1
  allowed_functions=$(broker_response_nested_field "$response" submission exercise allowed_functions) || return 1
  terminal_model=$(broker_response_nested_field "$response" submission exercise terminal_model) || return 1
  hints=$(broker_response_nested_field "$response" submission exercise hints) || return 1
  status=$(broker_response_nested_field "$response" submission status) || return 1
  kind=$(broker_response_nested_field "$response" submission content_kind) || return 1
  commit_url=$(broker_response_nested_field "$response" submission commit_url) || return 1
  directory=$(broker_response_nested_field "$response" submission delivery_directory) || return 1
  submitted_at=$(broker_response_nested_field "$response" submission submitted_at) || return 1
  slot_id=$(broker_response_nested_field "$response" submission slot_id) || return 1
  score=$(broker_response_nested_field "$response" submission score) || return 1
  feedback=$(broker_response_nested_field "$response" submission feedback) || return 1

  submission_render_detail "$id" "$code" "$name" "$description" "$mission" \
    "$example" "$exercise_directory" "$delivery_files" "$allowed_functions" \
    "$terminal_model" "$hints" "$status" "$kind" "$commit_url" "$directory" \
    "$submitted_at" "$slot_id" "$score" "$feedback"
}

broker_submissions_menu() {
  local response
  local rows
  local encoded
  local submission_id
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
  local choice
  local status_label
  local -a submission_rows
  local -a menu_options

  response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
    broker_request submissions.list token) || return 1
  if ! broker_check_response "$response"; then
    ui_pause
    return 1
  fi
  rows=$(broker_response_rows "$response" submissions) || return 1
  if [[ -n "$rows" ]]; then
    mapfile -t submission_rows <<<"$rows"
  else
    submission_rows=()
  fi
  if ((${#submission_rows[@]} == 0)); then
    ui_screen_begin "MINHAS ENTREGAS"
    ui_empty_state "Você ainda não registrou entregas."
    ui_wrap_text \
      "Se um pedido seu foi aceito, encontre a entrega em Avaliações abertas." \
      "$(ui_width)" 2
    if ! evaluation_handle_pending_requests; then
      ui_warn "Não foi possível carregar ou responder aos pedidos de avaliação."
    fi
    ui_pause
    return 0
  fi

  while true; do
    ui_screen_begin "MINHAS ENTREGAS"
    menu_options=()
    for encoded in "${submission_rows[@]}"; do
      IFS=$'\t' read -r submission_id code name status kind commit_url directory \
        submitted_at slot_id score feedback <<<"$encoded"
      submission_id=$(db_hex_decode "$submission_id") || return 1
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      status=$(db_hex_decode "$status") || return 1
      kind=$(db_hex_decode "$kind") || return 1
      commit_url=$(db_hex_decode "$commit_url") || return 1
      directory=$(db_hex_decode "$directory") || return 1
      submitted_at=$(db_hex_decode "$submitted_at") || return 1
      slot_id=$(db_hex_decode "$slot_id") || return 1
      score=$(db_hex_decode "$score") || return 1
      feedback=$(db_hex_decode "$feedback") || return 1

      status_label=$(submission_status_label "$status")
      if [[ -n "$score" ]]; then
        menu_options+=("$submission_id|$code — $name · $status_label · nota $score/5")
      else
        menu_options+=("$submission_id|$code — $name · $status_label")
      fi
    done
    ui_wrap_text \
      "Para avaliar uma entrega aceita, acesse Avaliações abertas." \
      "$(ui_width)" 2
    menu_options+=("0|Voltar")
    ui_menu_choice choice "ABRIR EXERCÍCIO ENTREGUE" "${menu_options[@]}" || return 1
    if [[ "$choice" == "0" ]]; then
      if ! evaluation_handle_pending_requests; then
        ui_error "Não foi possível carregar os pedidos de avaliação."
        ui_pause
      fi
      break
    fi
    if ! broker_submission_render_detail "$choice"; then
      ui_error "Não foi possível abrir esta entrega. Verifique a mensagem acima."
      ui_pause
      continue
    fi
    ui_pause
  done
}

broker_evaluation_review_prompt() {
  local slot_id="$1"
  local response
  local submitter
  local code
  local name
  local description
  local statement
  local kind
  local content
  local commit_url
  local directory
  local score
  local feedback
  local status
  local points

  response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$slot_id" |
    broker_request evaluations.slot.get token slot_id) || return 1
  if ! broker_check_response "$response"; then
    ui_pause
    return 1
  fi
  submitter=$(broker_response_nested_field "$response" slot username) || return 1
  code=$(broker_response_nested_field "$response" slot exercise_code) || return 1
  name=$(broker_response_nested_field "$response" slot exercise_name) || return 1
  description=$(broker_response_nested_field "$response" slot description) || return 1
  statement=$(broker_response_nested_field "$response" slot statement) || return 1
  kind=$(broker_response_nested_field "$response" slot content_kind) || return 1
  ui_screen_begin "AVALIAR SUBMISSÃO $slot_id"
  ui_panel "$code — $name"
  ui_wrap_text "Avaliado: $submitter" "$(ui_width)" 2
  ui_wrap_text "Avaliador: $CURRENT_USERNAME" "$(ui_width)" 2
  if [[ -n "$description" ]]; then
    ui_section "DESCRIÇÃO"
    ui_wrap_text "$description" "$(ui_width)" 2
  fi
  if [[ -n "$statement" ]]; then
    ui_section "EXERCÍCIO"
    ui_wrap_text "$statement" "$(ui_width)" 2
  fi
  if [[ "$kind" == "github_commit" ]]; then
    commit_url=$(broker_response_nested_field "$response" slot commit_url) || return 1
    directory=$(broker_response_nested_field "$response" slot delivery_directory) || return 1
    printf '\n  Referência da entrega:\n'
    ui_wrap_text "Commit: $commit_url" "$(ui_width)" 2
    ui_wrap_text "Diretório: $directory" "$(ui_width)" 2
  else
    content=$(broker_response_nested_field "$response" slot content) || return 1
    content=$(ui_sanitize "$content") || return 1
    printf '\n  Conteúdo legado enviado (não executado):\n'
    ui_wrap_text "$content" "$(ui_width)" 2
  fi
  ui_panel_end
  while true; do
    ui_read_line score "  Nota (0 a 5) > " || return 1
    [[ "$score" =~ ^[0-5]$ ]] && break
    ui_message "A nota deve ser um número inteiro de 0 a 5."
  done
  printf '  Feedback da avaliação (obrigatório):\n'
  ui_read_multiline feedback || return 1
  while [[ -z "${feedback//[[:space:]]/}" ]]; do
    ui_message "O feedback da avaliação é obrigatório."
    printf '  Feedback da avaliação (obrigatório):\n'
    ui_read_multiline feedback || return 1
  done
  if ! ui_confirm "Registrar esta nota e o feedback?"; then
    ui_warn "Avaliação cancelada; nenhuma alteração foi registrada."
    return 0
  fi
  status=$(printf '%s\0%s\0%s\0%s\0' \
    "$CURRENT_SESSION_TOKEN" "$slot_id" "$score" "$feedback" |
    broker_request evaluations.review.submit token slot_id score feedback) || return 1
  if broker_check_response "$status"; then
    points=$(broker_response_field "$status" points) || return 1
    if ((score >= 3)); then
      ui_success "Avaliação registrada: aprovado. $(ui_badge APROVADO success) Seu saldo é $points ponto(s)."
    else
      ui_warn "Avaliação registrada: reprovado. $(ui_badge REPROVADO error) Seu saldo é $points ponto(s)."
    fi
  else
    ui_message "Não foi possível registrar a avaliação."
  fi
  ui_pause
}

broker_evaluation_response_prompt() {
  local review_id="$1"
  local response
  local code
  local reviewer
  local score
  local feedback
  local recipient_score
  local recipient_feedback
  local status

  response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$review_id" |
    broker_request evaluations.review.get token review_id) || return 1
  if ! broker_check_response "$response"; then
    ui_pause
    return 1
  fi
  code=$(broker_response_nested_field "$response" review exercise_code) || return 1
  reviewer=$(broker_response_nested_field "$response" review reviewer) || return 1
  score=$(broker_response_nested_field "$response" review score) || return 1
  feedback=$(broker_response_nested_field "$response" review feedback) || return 1

  ui_screen_begin "RESPONDER AVALIAÇÃO"
  ui_panel "$code — avaliação de $reviewer"
  ui_wrap_text "Nota recebida: $score/5" "$(ui_width)" 2
  ui_wrap_text "Feedback: $feedback" "$(ui_width)" 2
  ui_panel_end
  while true; do
    ui_read_line recipient_score "  Sua nota ao avaliador (0 a 5) > " || return 1
    [[ "$recipient_score" =~ ^[0-5]$ ]] && break
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
  status=$(printf '%s\0%s\0%s\0%s\0' \
    "$CURRENT_SESSION_TOKEN" "$review_id" "$recipient_score" \
    "$recipient_feedback" |
    broker_request evaluations.review.respond token review_id score feedback) || return 1
  if broker_check_response "$status"; then
    ui_success "Resposta registrada. O avaliador foi notificado."
  else
    ui_message "Não foi possível registrar a resposta."
  fi
  ui_pause
}

broker_evaluations_menu() {
  local response
  local rows
  local encoded
  local slot_id
  local username
  local code
  local name
  local created_at
  local request_id
  local request_status
  local accepted
  local choice
  local -a slot_rows
  local -a menu_options

  while true; do
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request evaluations.slots.list token) || return 1
    if ! broker_check_response "$response"; then
      ui_pause
      return 1
    fi
    rows=$(broker_response_rows "$response" slots) || return 1
    if [[ -n "$rows" ]]; then
      mapfile -t slot_rows <<<"$rows"
    else
      slot_rows=()
    fi
    ui_screen_begin "AVALIAÇÕES ABERTAS"
    if ((${#slot_rows[@]} == 0)); then
      ui_empty_state "Não há submissões disponíveis para avaliação."
      ui_pause
      return 0
    fi
    menu_options=()
    for encoded in "${slot_rows[@]}"; do
      IFS=$'\t' read -r slot_id username code name created_at request_id \
        request_status accepted <<<"$encoded"
      slot_id=$(db_hex_decode "$slot_id") || return 1
      username=$(db_hex_decode "$username") || return 1
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      created_at=$(db_hex_decode "$created_at") || return 1
      request_id=$(db_hex_decode "$request_id") || return 1
      request_status=$(db_hex_decode "$request_status") || return 1
      accepted=$(db_hex_decode "$accepted") || return 1
      case "$request_status" in
        pending) menu_options+=("$slot_id|$code - pedido aguardando aceite") ;;
        accepted) menu_options+=("$slot_id|$code - $username aceitou seu pedido") ;;
        *)
          if [[ "$accepted" == "1" ]]; then
            menu_options+=("$slot_id|$username / $code - reservado por você")
          else
            menu_options+=("$slot_id|$username / $code - $name (desde $created_at)")
          fi
          ;;
      esac
    done
    menu_options+=("0|Voltar")
    ui_menu_choice choice "AVALIAÇÕES ABERTAS" "${menu_options[@]}" || return 0
    [[ "$choice" == "0" ]] && return 0
    if [[ ! "$choice" =~ ^[0-9]+$ ]]; then
      ui_message "ID de slot inválido."
      ui_pause
      continue
    fi
    encoded=""
    for encoded in "${slot_rows[@]}"; do
      IFS=$'\t' read -r slot_id username code name created_at request_id \
        request_status accepted <<<"$encoded"
      slot_id=$(db_hex_decode "$slot_id") || return 1
      if [[ "$slot_id" == "$choice" ]]; then
        code=$(db_hex_decode "$code") || return 1
        request_id=$(db_hex_decode "$request_id") || return 1
        request_status=$(db_hex_decode "$request_status") || return 1
        accepted=$(db_hex_decode "$accepted") || return 1
        break
      fi
    done
    if [[ "$slot_id" != "$choice" ]]; then
      ui_message "Slot indisponível."
      ui_pause
      continue
    fi
    if [[ "$request_status" == "pending" ]]; then
      ui_menu_choice choice "PEDIDO PARA $code" \
        "1|Cancelar meu pedido" "0|Voltar" || continue
      if [[ "$choice" == "1" ]]; then
        evaluation_cancel_request "$slot_id" &&
          ui_message "Pedido cancelado; o slot voltou à lista."
        ui_pause
      fi
      continue
    fi
    if [[ "$request_status" == "accepted" || "$accepted" == "1" ]]; then
      ui_menu_choice choice "SLOT $slot_id" \
        "1|Avaliar submissão" \
        "2|Liberar minha reserva" \
        "0|Voltar" || return 0
      case "$choice" in
      1)
        broker_evaluation_review_prompt "$slot_id"
        ;;
      2)
        if evaluation_cancel_request "$slot_id"; then
          ui_message "Reserva liberada."
        fi
        ui_pause
        ;;
      *)
        ;;
      esac
      continue
    fi
    if ! ui_confirm "Solicitar autorização para avaliar esta entrega?"; then
      continue
    fi
    if evaluation_start_request "$choice"; then
      ui_message "Pedido enviado. Você verá a entrega após o aceite do autor."
      ui_pause
    else
      ui_pause
    fi
  done
}

broker_admin_exercise_create() {
  local code
  local name
  local description
  local statement
  local delivery_directory
  local delivery_files
  local allowed_functions
  local example
  local terminal_model
  local hints
  local response

  ui_screen_begin "CADASTRAR EXERCÍCIO"
  ui_read_line code "  Código > " || return 1
  ui_read_line name "  Nome > " || return 1
  printf '  Descrição:\n'
  ui_read_multiline description || return 1
  printf '  Missão/enunciado:\n'
  ui_read_multiline statement || return 1
  ui_read_line delivery_directory "  Diretório de entrega > " || return 1
  printf '  Arquivos esperados:\n'
  ui_read_multiline delivery_files || return 1
  printf '  Funções permitidas:\n'
  ui_read_multiline allowed_functions || return 1
  printf '  Exemplo de execução:\n'
  ui_read_multiline example || return 1
  printf '  Modelo do terminal:\n'
  ui_read_multiline terminal_model || return 1
  printf '  Dicas:\n'
  ui_read_multiline hints || return 1
  response=$(printf '%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0%s\0' \
    "$CURRENT_SESSION_TOKEN" "$code" "$name" "$description" "$statement" \
    "$example" "$delivery_directory" "$delivery_files" "$allowed_functions" \
    "$terminal_model" "$hints" |
    broker_request admin.exercise.create token code name description statement example \
      delivery_directory delivery_files allowed_functions terminal_model hints) || return 1
  if broker_check_response "$response"; then
    ui_message "Exercício $(broker_response_field "$response" code) cadastrado."
  fi
  ui_pause
}

broker_admin_exercise_manage() {
  local code="$1"
  local status="$2"
  local choice
  local field
  local label
  local value
  local response
  local -a menu_options

  while true; do
    ui_screen_begin "GERENCIAR EXERCÍCIO $code"
    ui_wrap_text "GERENCIAR EXERCÍCIO $code [$status]" "$(ui_width)" 2
    printf '\n'
    if [[ "$status" == "archived" ]]; then
      menu_options=(
        "1|Editar nome" "2|Editar descrição"
        "3|Editar missão/enunciado" "4|Editar diretório de entrega"
        "5|Editar arquivos esperados" "6|Editar funções permitidas"
        "7|Editar exemplo" "8|Editar modelo do terminal"
        "9|Editar dicas" "a|Reativar exercício" "0|Voltar"
      )
    else
      menu_options=(
        "1|Editar nome" "2|Editar descrição"
        "3|Editar missão/enunciado" "4|Editar diretório de entrega"
        "5|Editar arquivos esperados" "6|Editar funções permitidas"
        "7|Editar exemplo" "8|Editar modelo do terminal"
        "9|Editar dicas" "a|Arquivar exercício" "0|Voltar"
      )
    fi
    ui_menu_choice choice "GERENCIAR EXERCÍCIO $code" \
      "${menu_options[@]}" || return 0
    case "$choice" in
    1) field=name; label="novo nome" ;;
    2) field=description; label="nova descrição" ;;
    3) field=statement; label="nova missão/enunciado" ;;
    4) field=delivery_directory; label="novo diretório" ;;
    5) field=delivery_files; label="novos arquivos esperados" ;;
    6) field=allowed_functions; label="novas funções permitidas" ;;
    7) field=example; label="novo exemplo" ;;
    8) field=terminal_model; label="novo modelo do terminal" ;;
    9) field=hints; label="novas dicas" ;;
    0) return 0 ;;
    a)
      if ! ui_confirm "Confirmar operação?"; then
        continue
      fi
      if [[ "$status" == "archived" ]]; then
        response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$code" |
          broker_request admin.exercise.reactivate token exercise_code) || return 1
        if broker_check_response "$response"; then status=available; fi
      else
        response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$code" |
          broker_request admin.exercise.archive token exercise_code) || return 1
        if broker_check_response "$response"; then status=archived; fi
      fi
      ui_pause
      ;;
    *)
      ui_message "Opção inválida."
      ui_pause
      continue
      ;;
    esac
    if [[ "$choice" =~ ^[1-9]$ ]]; then
      if [[ "$field" == "name" || "$field" == "delivery_directory" ]]; then
        ui_read_line value "  $label (vazio mantém o atual) > " || return 1
        [[ -z "$value" ]] && continue
      else
        printf '  %s:\n' "$label"
        ui_read_multiline value || return 1
      fi
      response=$(printf '%s\0%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$code" "$field" "$value" |
        broker_request admin.exercise.update token exercise_code field value) || return 1
      if ! broker_check_response "$response"; then
        ui_pause
        continue
      fi
      ui_pause
    fi
  done
}

broker_admin_exercises_menu() {
  local response
  local encoded
  local id
  local code
  local name
  local status
  local choice
  local selected_code
  local selected_status
  local index
  local -a exercise_rows
  local -a menu_options

  while true; do
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request admin.exercises.list token) || return 1
    broker_check_response "$response" || return 1
    rows=$(broker_response_rows "$response" exercises) || return 1
    exercise_rows=()
    if [[ -n "$rows" ]]; then
      while IFS= read -r encoded; do
        IFS=$'\t' read -r id code name status <<<"$encoded"
        exercise_rows+=("$id"$'\t'"$code"$'\t'"$name"$'\t'"$status")
      done <<<"$rows"
    fi
    ui_screen_begin "EXERCÍCIOS CADASTRADOS"
    menu_options=()
    for index in "${!exercise_rows[@]}"; do
      IFS=$'\t' read -r id code name status <<<"${exercise_rows[$index]}"
      id=$(db_hex_decode "$id") || return 1
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      status=$(db_hex_decode "$status") || return 1
      menu_options+=("$((index + 1))|$code - $name [$status]")
    done
    menu_options+=("c|Cadastrar exercício" "0|Voltar")
    ui_menu_choice choice "EXERCÍCIOS CADASTRADOS" \
      "${menu_options[@]}" || return 0
    case "$choice" in
    0) return 0 ;;
    c)
      broker_admin_exercise_create
      ;;
    *)
      if [[ ! "$choice" =~ ^[0-9]+$ ]] ||
        ((choice < 1 || choice > ${#exercise_rows[@]})); then
        ui_message "Opção inválida."
        ui_pause
        continue
      fi
      IFS=$'\t' read -r id selected_code name selected_status \
        <<<"${exercise_rows[$((choice - 1))]}"
      selected_code=$(db_hex_decode "$selected_code") || return 1
      selected_status=$(db_hex_decode "$selected_status") || return 1
      broker_admin_exercise_manage "$selected_code" "$selected_status"
      ;;
    esac
  done
}

broker_admin_user_show() {
  local user_id="$1"
  local response
  local username
  local is_admin
  local is_active
  local created_at
  local display_name
  local github_username
  local bio
  local points
  local completed
  local rows
  local code
  local name
  local status
  local total_reviews
  local reviews_given
  local reviews_received
  local value

  response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$user_id" |
    broker_request admin.user.get token user_id) || return 1
  broker_check_response "$response" || return 1
  username=$(broker_response_nested_field "$response" user username) || return 1
  is_admin=$(broker_response_nested_field "$response" user is_admin) || return 1
  is_active=$(broker_response_nested_field "$response" user is_active) || return 1
  created_at=$(broker_response_nested_field "$response" user created_at) || return 1
  display_name=$(broker_response_nested_field "$response" user display_name) || return 1
  github_username=$(broker_response_nested_field "$response" user github_username) || return 1
  bio=$(broker_response_nested_field "$response" user bio) || return 1
  points=$(broker_response_nested_field "$response" user points) || return 1
  ui_screen_begin "PERFIL DO USUÁRIO"
  ui_wrap_text "Usuário: $username (#$user_id)" "$(ui_width)" 2
  ui_wrap_text "Papel: $([[ "$is_admin" == "True" || "$is_admin" == "1" ]] && printf 'admin' || printf 'participante') | Conta: $([[ "$is_active" == "True" || "$is_active" == "1" ]] && printf 'ativa' || printf 'inativa')" \
    "$(ui_width)" 2
  [[ -n "$display_name" ]] && ui_wrap_text "Nome: $display_name" "$(ui_width)" 2
  [[ -n "$github_username" ]] && ui_wrap_text "GitHub: https://github.com/$github_username" "$(ui_width)" 2
  ui_wrap_text "Criado em: $created_at | Saldo: $points ponto(s)" "$(ui_width)" 2
  for status in pending approved rejected; do
    value=$(broker_response_nested_field "$response" user submissions "$status") || return 1
    [[ -z "$value" ]] && value=0
    printf '  Submissões %s: %s\n' "$status" "$value"
  done
  total_reviews=$(broker_response_nested_field "$response" user reviews total) || return 1
  reviews_given=$(broker_response_nested_field "$response" user reviews given) || return 1
  reviews_received=$(broker_response_nested_field "$response" user reviews received) || return 1
  printf '  Avaliações: %s (realizadas: %s, recebidas: %s)\n' \
    "$total_reviews" "$reviews_given" "$reviews_received"
  if [[ -n "$bio" ]]; then
    printf '  Biografia:\n'
    ui_wrap_text "$bio" "$(ui_width)" 4
  fi
  printf '\n  EXERCÍCIOS APROVADOS\n'
  completed=0
  rows=$(broker_response_rows "$response" user completed_exercises) || return 1
  while IFS=$'\t' read -r code name; do
    [[ -z "$code" ]] && continue
    code=$(db_hex_decode "$code") || return 1
    name=$(db_hex_decode "$name") || return 1
    ui_wrap_text "$code - $name" "$(ui_width)" 2
    ((completed += 1))
  done <<<"$rows"
  ((completed > 0)) || printf '  Nenhum exercício aprovado.\n'
}

broker_admin_user_manage() {
  local user_id="$1"
  local choice
  local response
  local active
  local password
  local confirmation
  local direction
  local amount
  local reason
  local rows
  local encoded
  local entry_type
  local details
  local created_at
  local score
  local submitter
  local reviewer
  local relation
  local code
  local -a menu_options

  while true; do
    broker_admin_user_show "$user_id" || return 1
    ui_menu_choice choice "ADMINISTRAR USUÁRIO $user_id" \
      "1|Redefinir senha" \
      "2|Adicionar pontos" \
      "3|Remover pontos" \
      "4|Ativar/desativar conta" \
      "5|Histórico de pontos" \
      "6|Histórico de avaliações" \
      "0|Voltar" || return 0
    case "$choice" in
    1)
      ui_read_line password "  Senha temporária (mínimo 8 caracteres) > " 1 || return 1
      ui_read_line confirmation "  Confirme a senha temporária > " 1 || return 1
      if [[ "$password" != "$confirmation" || ${#password} -lt 8 ]]; then
        ui_message "Senhas não coincidem ou têm menos de 8 caracteres."
      elif ! ui_confirm "Definir senha temporária para a conta #$user_id?"; then
        ui_warn "Redefinição cancelada."
      else
        response=$(printf '%s\0%s\0%s\0' \
          "$CURRENT_SESSION_TOKEN" "$user_id" "$password" |
          broker_request admin.password.reset token user_id password) || return 1
        if broker_check_response "$response"; then
          ui_message "Senha redefinida; a troca será exigida no próximo acesso."
        fi
      fi
      ui_pause
      ;;
    2|3)
      direction=add
      [[ "$choice" == "3" ]] && direction=remove
      ui_read_line amount "  Quantidade de pontos > " || return 1
      if [[ ! "$amount" =~ ^[1-9][0-9]{0,8}$ ]]; then
        ui_message "Informe uma quantidade positiva válida."
        ui_pause
        continue
      fi
      ui_read_line reason "  Justificativa > " || return 1
      if [[ -z "${reason//[[:space:]]/}" ]]; then
        ui_message "A justificativa é obrigatória."
        ui_pause
        continue
      fi
      [[ "$direction" == "remove" ]] && amount="-$amount"
      if ! ui_confirm "Aplicar ajuste de $amount ponto(s) na conta #$user_id?"; then
        ui_warn "Ajuste cancelado."
        continue
      fi
      response=$(printf '%s\0%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$user_id" "$amount" "$reason" |
        broker_request admin.points.adjust token user_id amount reason) || return 1
      if broker_check_response "$response"; then
        ui_message "Ajuste registrado. Novo saldo: $(broker_response_field "$response" points)."
      fi
      ui_pause
      ;;
    4)
      response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$user_id" |
        broker_request admin.user.get token user_id) || return 1
      broker_check_response "$response" || return 1
      active=$(broker_response_nested_field "$response" user is_active) || return 1
      if [[ "$active" == "True" ]]; then
        active=0
        if ! ui_confirm "Desativar a conta?"; then
          continue
        fi
      else
        active=1
        if ! ui_confirm "Reativar a conta?"; then
          continue
        fi
      fi
      response=$(printf '%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$user_id" "$active" |
        broker_request admin.user.state token user_id is_active) || return 1
      if ! broker_check_response "$response"; then
        ui_pause
        continue
      fi
      ui_pause
      ;;
    5)
      response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$user_id" |
        broker_request admin.points.history token user_id) || return 1
      broker_check_response "$response" || return 1
      rows=$(broker_response_rows "$response" entries) || return 1
      ui_screen_begin "HISTÓRICO DE PONTOS DO USUÁRIO #$user_id"
      if [[ -z "$rows" ]]; then
        ui_empty_state "Nenhuma movimentação registrada."
      else
        while IFS= read -r encoded; do
          IFS=$'\t' read -r amount entry_type details created_at <<<"$encoded"
          amount=$(db_hex_decode "$amount") || return 1
          entry_type=$(db_hex_decode "$entry_type") || return 1
          details=$(db_hex_decode "$details") || return 1
          created_at=$(db_hex_decode "$created_at") || return 1
          ui_wrap_text "$(printf '%+d | %s | %s (%s)' \
            "$amount" "$entry_type" "$details" "$created_at")" "$(ui_width)" 2
        done <<<"$rows"
      fi
      ui_pause
      ;;
    6)
      response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$user_id" |
        broker_request admin.user.get token user_id) || return 1
      broker_check_response "$response" || return 1
      rows=$(broker_response_rows "$response" user review_history) || return 1
      ui_screen_begin "HISTÓRICO DE AVALIAÇÕES DO USUÁRIO #$user_id"
      if [[ -z "$rows" ]]; then
        ui_empty_state "Nenhuma avaliação registrada."
      else
        while IFS= read -r encoded; do
          IFS=$'\t' read -r code score details created_at submitter reviewer relation <<<"$encoded"
          code=$(db_hex_decode "$code") || return 1
          score=$(db_hex_decode "$score") || return 1
          details=$(db_hex_decode "$details") || return 1
          created_at=$(db_hex_decode "$created_at") || return 1
          submitter=$(db_hex_decode "$submitter") || return 1
          reviewer=$(db_hex_decode "$reviewer") || return 1
          relation=$(db_hex_decode "$relation") || return 1
          ui_wrap_text "$code | nota $score/5 | $relation | de $submitter por $reviewer ($created_at)" \
            "$(ui_width)" 2
          [[ -n "$details" ]] && ui_wrap_text "Feedback: $details" "$(ui_width)" 4
        done <<<"$rows"
      fi
      ui_pause
      ;;
    0) return 0 ;;
    *) ui_message "Opção inválida."; ui_pause ;;
    esac
  done
}

broker_admin_users_menu() {
  local response
  local rows
  local encoded
  local user_id
  local username
  local is_admin
  local is_active
  local created_at
  local approved
  local points
  local choice
  local -a user_rows
  local -a menu_options

  while true; do
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request admin.users.list token) || return 1
    broker_check_response "$response" || return 1
    rows=$(broker_response_rows "$response" users) || return 1
    user_rows=()
    if [[ -n "$rows" ]]; then
      while IFS= read -r encoded; do user_rows+=("$encoded"); done <<<"$rows"
    fi
    ui_screen_begin "PERFIS CADASTRADOS"
    menu_options=()
    for encoded in "${user_rows[@]}"; do
      IFS=$'\t' read -r user_id username is_admin is_active created_at approved points <<<"$encoded"
      user_id=$(db_hex_decode "$user_id") || return 1
      username=$(db_hex_decode "$username") || return 1
      is_admin=$(db_hex_decode "$is_admin") || return 1
      is_active=$(db_hex_decode "$is_active") || return 1
      approved=$(db_hex_decode "$approved") || return 1
      points=$(db_hex_decode "$points") || return 1
      menu_options+=("$user_id|$username ($([[ "$is_admin" == "1" ]] && printf 'admin' || printf 'participante')) | $([[ "$is_active" == "1" ]] && printf 'ativa' || printf 'inativa') | aprovados: $approved | pontos: $points")
    done
    menu_options+=("0|Voltar")
    ui_menu_choice choice "PERFIS CADASTRADOS" "${menu_options[@]}" || return 0
    [[ "$choice" == "0" ]] && return 0
    if [[ ! "$choice" =~ ^[0-9]+$ ]]; then
      ui_message "ID inválido."
      ui_pause
      continue
    fi
    broker_admin_user_manage "$choice" || ui_pause
  done
}

broker_admin_identity_menu() {
  local response
  local rows
  local encoded
  local linux_uid
  local user_id
  local is_active
  local created_at
  local username
  local account_active
  local is_admin
  local choice
  local target_uid
  local new_uid
  local target_user_id

  while true; do
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request admin.identity.list token) || return 1
    broker_check_response "$response" || return 1
    rows=$(broker_response_rows "$response" identities) || return 1
    ui_screen_begin "VÍNCULOS DE IDENTIDADE LINUX"
    if [[ -z "$rows" ]]; then
      ui_empty_state "Nenhum vínculo cadastrado."
    else
      while IFS=$'\t' read -r linux_uid user_id is_active _ _ username account_active _; do
        linux_uid=$(db_hex_decode "$linux_uid") || return 1
        user_id=$(db_hex_decode "$user_id") || return 1
        is_active=$(db_hex_decode "$is_active") || return 1
        username=$(db_hex_decode "$username") || return 1
        account_active=$(db_hex_decode "$account_active") || return 1
        ui_wrap_text "UID $linux_uid -> SIMPLE #$user_id ($username) | vínculo: $([[ "$is_active" == "1" ]] && printf 'ativo' || printf 'revogado') | conta: $([[ "$account_active" == "1" ]] && printf 'ativa' || printf 'desativada')" \
          "$(ui_width)" 2
      done <<<"$rows"
    fi
    ui_wrap_text "O proxy identifica o UID real com SO_PEERCRED e exige vínculo ativo." \
      "$(ui_width)" 2
    ui_menu_choice choice "VÍNCULOS DE IDENTIDADE LINUX" \
      "1|Criar vínculo" \
      "2|Alterar UID" \
      "3|Revogar vínculo" \
      "0|Voltar" || return 0
    case "$choice" in
    1)
      ui_read_line target_uid "  UID Linux > " || continue
      ui_read_line target_user_id "  ID da conta SIMPLE > " || continue
      if ! ui_confirm "Vincular o UID $target_uid à conta #$target_user_id?"; then
        continue
      fi
      response=$(printf '%s\0%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$target_uid" "$target_user_id" |
        broker_request admin.identity.bind token linux_uid user_id) || return 1
      if broker_check_response "$response"; then
        ui_message "Vínculo criado."
      fi
      ui_pause
      ;;
    2)
      ui_read_line target_uid "  UID Linux atual > " || continue
      ui_read_line new_uid "  Novo UID Linux > " || continue
      if ! ui_confirm "Alterar o vínculo do UID $target_uid para $new_uid?"; then
        continue
      fi
      response=$(printf '%s\0%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$target_uid" "$new_uid" |
        broker_request admin.identity.update token linux_uid new_linux_uid) || return 1
      if broker_check_response "$response"; then
        ui_message "UID do vínculo atualizado."
      fi
      ui_pause
      ;;
    3)
      ui_read_line target_uid "  UID Linux a revogar > " || continue
      if ! ui_confirm "Confirma a revogação do vínculo UID $target_uid?"; then
        continue
      fi
      response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$target_uid" |
        broker_request admin.identity.revoke token linux_uid) || return 1
      if broker_check_response "$response"; then
        ui_message "Vínculo revogado."
      fi
      ui_pause
      ;;
    0) return 0 ;;
    *) ui_message "Opção inválida."; ui_pause ;;
    esac
  done
}

broker_admin_history_menu() {
  local method="$1"
  local field="$2"
  local title="$3"
  local response
  local rows
  local encoded
  local -a entries
  local -a labels
  local -a values
  local label
  local value
  local index

  response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
    broker_request "$method" token) || return 1
  broker_check_response "$response" || return 1
  rows=$(broker_response_rows "$response" "$field") || return 1
  if [[ -n "$rows" ]]; then
    mapfile -t entries <<<"$rows"
  else
    entries=()
  fi
  ui_screen_begin "$title"
  if [[ "$field" == "submissions" ]]; then
    labels=("ID" "Usuário" "Exercício" "Status" "Tipo" "Commit" "Diretório" "Enviada em" "Slot")
  else
    labels=("Slot" "Status" "Participante" "Avaliador" "Reservado por" "Reservado em" "Exercício" "Nota" "Feedback" "Aberta em" "Concluída em")
  fi
  if ((${#entries[@]} == 0)); then
    ui_empty_state "Nenhum registro encontrado."
  else
    for encoded in "${entries[@]}"; do
      values=()
      IFS=$'\t' read -r -a values <<<"$encoded"
      for index in "${!values[@]}"; do
        value="${values[$index]}"
        value=$(db_hex_decode "$value") || return 1
        if ((index < ${#labels[@]})); then
          label="${labels[$index]}"
        else
          label="Campo $((index + 1))"
        fi
        ui_wrap_text "$label: $value" "$(ui_width)" 2
      done
      printf '\n'
    done
  fi
  ui_pause
}

broker_admin_report() {
  local response
  local key
  local value

  response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
    broker_request admin.report token) || return 1
  broker_check_response "$response" || return 1
  ui_screen_begin "VISÃO GERAL DO SISTEMA"
  for key in users active_users available_exercises archived_exercises submissions open_slots reviews; do
    value=$(broker_response_nested_field "$response" report "$key") || return 1
    ui_wrap_text "$key: $value" "$(ui_width)" 2
  done
  ui_pause
}

broker_admin_menu() {
  local choice

  if [[ "$CURRENT_USER_IS_ADMIN" != "True" ]]; then
    ui_message "Acesso administrativo não autorizado."
    ui_pause
    return 1
  fi
  while true; do
    ui_screen_begin "ADMINISTRAÇÃO"
    ui_menu_choice choice "ADMINISTRAÇÃO" \
      "1|Gerenciar exercícios" \
      "2|Perfis e pontos dos usuários" \
      "3|Todas as submissões" \
      "4|Histórico de avaliações" \
      "5|Visão geral do sistema" \
      "6|Vínculos UID Linux" \
      "0|Voltar" || return 0
    case "$choice" in
    1) broker_admin_exercises_menu ;;
    2) broker_admin_users_menu ;;
    3) broker_admin_history_menu admin.submissions.list submissions "SUBMISSÕES DO SISTEMA" ;;
    4) broker_admin_history_menu admin.evaluations.list evaluations "AVALIAÇÕES DO SISTEMA" ;;
    5) broker_admin_report ;;
    6) broker_admin_identity_menu ;;
    0) return 0 ;;
    *) ui_message "Opção inválida."; ui_pause ;;
    esac
  done
}

broker_mode_menu() {
  local choice
  local unread_notifications
  local online_count="indisponível"
  local chat_status_response
  local chat_unread_count=0
  local chat_mention_count=0
  local chat_preview=""
  local chat_preview_user=""
  local chat_label="Chat global"
  local chat_label_open=0
  local progress_label="Meu progresso"
  local -a menu_options

  unread_notifications=$(progress_unread_notification_count) || return 1
  if chat_status_response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
    broker_request chat.dashboard token); then
    if broker_check_response "$chat_status_response"; then
      online_count=$(broker_response_field "$chat_status_response" online_count) ||
        online_count="indisponível"
      chat_unread_count=$(broker_response_field "$chat_status_response" unread_count) ||
        chat_unread_count=0
      chat_mention_count=$(broker_response_field "$chat_status_response" mention_count) ||
        chat_mention_count=0
      chat_preview=$(broker_response_nested_field \
        "$chat_status_response" --allow-null preview body) ||
        chat_preview=""
      chat_preview_user=$(broker_response_nested_field \
        "$chat_status_response" --allow-null preview username) ||
        chat_preview_user=""
    fi
  fi
  if [[ "$online_count" =~ ^[0-9]+$ ]]; then
    chat_label+=" ($online_count online"
    chat_label_open=1
  fi
  if [[ "$chat_unread_count" =~ ^[0-9]+$ ]] && ((chat_unread_count > 0)); then
    if ((chat_label_open)); then
      chat_label+=" · "
    else
      chat_label+=" ("
      chat_label_open=1
    fi
    chat_label+="$chat_unread_count novas"
    if [[ "$chat_mention_count" =~ ^[0-9]+$ ]] && ((chat_mention_count > 0)); then
      chat_label+=", $chat_mention_count menções"
    fi
  fi
  if ((chat_label_open)); then
    chat_label+=")"
  fi
  if ((unread_notifications > 0)); then
    progress_label+=" ($unread_notifications alertas)"
  fi
  ui_screen_begin "INÍCIO"
  ui_home_intro "$CURRENT_USERNAME" || return 1
  ui_labeled_value "Online agora" "$online_count" "$(ui_width)"
  if [[ -n "$chat_preview" ]]; then
    ui_color_content
    ui_wrap_text "Prévia do chat · $(ui_sanitize "$chat_preview_user"): $(ui_sanitize "$chat_preview")" \
      "$(ui_width)" 2
    ui_color_reset
  fi
  if ((unread_notifications > 0)); then
    ui_warn "Você tem $unread_notifications atualização(ões) pendente(s). Confira Meu progresso."
  fi
  menu_options=(
    "1|Catálogo de exercícios"
    "2|Avaliações abertas"
    "3|$progress_label"
    "4|Meu perfil"
  )
  if [[ "$CURRENT_USER_IS_ADMIN" == "True" ]]; then
    menu_options+=("5|Administração")
  else
    menu_options+=("5|Minhas entregas")
  fi
  menu_options+=("6|$chat_label")
  if [[ "$CURRENT_USER_IS_ADMIN" == "True" ]]; then
    menu_options+=("7|Minhas entregas")
  fi
  menu_options+=("0|Sair")
  printf '\n'
  if ! ui_menu_choice choice "MENU PRINCIPAL" "${menu_options[@]}"; then
    return 1
  fi
  case "$choice" in
  1)
    if ! exercises_menu; then
      ui_error "Não foi possível carregar o catálogo de exercícios."
      ui_pause
    fi
    ;;
  2)
    if ! evaluations_menu; then
      ui_error "Não foi possível carregar as avaliações abertas."
      ui_pause
    fi
    ;;
  3)
    if ! progress_menu; then
      ui_error "Não foi possível carregar Meu progresso. Consulte a mensagem acima."
      ui_pause
    fi
    ;;
  4)
    if ! users_profile_menu; then
      ui_error "Não foi possível carregar seu perfil."
      ui_pause
    fi
    ;;
  5)
    if [[ "$CURRENT_USER_IS_ADMIN" == "True" ]]; then
      admin_menu
    else
      if ! broker_submissions_menu; then
        ui_error "Não foi possível carregar Minhas entregas. Consulte a mensagem acima."
        ui_pause
      fi
    fi
    ;;
  6)
    if ! chat_menu; then
      ui_error "Não foi possível acessar o chat global."
      ui_pause
    fi
    ;;
  7)
    if ! broker_submissions_menu; then
      ui_error "Não foi possível carregar Minhas entregas. Consulte a mensagem acima."
      ui_pause
    fi
    ;;
  0)
    broker_logout || return 1
    # shellcheck disable=SC2034 # These globals reset the session for other sourced modules.
    CURRENT_USER_ID=""
    CURRENT_USERNAME=""
    CURRENT_SESSION_TOKEN=""
    CURRENT_USER_IS_ADMIN="0"
    ;;
  *)
    ui_message "Opção inválida."
    ui_pause
    ;;
  esac
  return 0
}
