#!/usr/bin/env bash

broker_profile_menu() {
  local response
  local display_name
  local github_username
  local bio
  local created_at
  local choice
  local value

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
    ui_clear
    printf '\n  MEU PERFIL\n\n'
    printf '  Usuário: %s\n' "$CURRENT_USERNAME"
    printf '  Criado em: %s\n' "$created_at"
    [[ -n "$display_name" ]] && ui_wrap_text "Nome de exibição: $display_name" "$(ui_width)" 2
    [[ -n "$github_username" ]] && ui_wrap_text "GitHub: https://github.com/$github_username" "$(ui_width)" 2
    if [[ -n "$bio" ]]; then
      printf '  Biografia:\n'
      ui_wrap_text "$bio" "$(ui_width)" 4
    fi
    printf '  Exercícios aprovados: %s\n' \
      "$(broker_response_array_length "$response" completed_exercises)" || return 1
    printf '  Saldo de pontos: %s\n' \
      "$(broker_response_field "$response" points)" || return 1
    printf '\n  [1] Editar perfil\n'
    printf '  [0] Voltar\n\n'
    ui_read_line choice "  PERFIL > " || return 0
    case "$choice" in
    1)
      printf '\n  Nome de exibição atual: %s\n' "$display_name"
      ui_read_line value "  Novo nome de exibição (ENTER mantém) > " || return 0
      [[ -n "$value" ]] && display_name="$value"
      printf '  Usuário GitHub atual: %s\n' "$github_username"
      ui_read_line value "  Novo usuário GitHub (ENTER mantém) > " || return 0
      [[ -n "$value" ]] && github_username="$value"
      printf '  Biografia atual:\n%s\n' "$bio"
      printf '  Nova biografia (vazia remove; use .fim para concluir):\n'
      ui_read_multiline value || return 1
      local response_update
      response_update=$(printf '%s\0%s\0%s\0%s\0' \
        "$CURRENT_SESSION_TOKEN" "$display_name" "$github_username" "$value" |
        broker_request profile.update token display_name github_username bio) || return 1
      if [[ "$(broker_response_field "$response_update" ok)" == "True" ]]; then
        ui_message "Perfil atualizado."
      else
        broker_response_error "$response_update"
      fi
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
  local amount
  local entry_type
  local details

  response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
    broker_request progress.get token) || return 1
  if ! broker_check_response "$response"; then
    ui_pause
    return 1
  fi
  ui_clear
  printf '\n  MEU PROGRESSO\n\n'
  count=$(broker_response_nested_field "$response" submissions approved) || return 1
  printf '  Exercícios aprovados: %s\n' "$count"
  count=$(broker_response_field "$response" points) || return 1
  printf '  Saldo de pontos: %s\n\n' "$count"
  printf '  ENTREGAS POR STATUS\n'
  for relation in pending approved rejected; do
    count=$(broker_response_nested_field "$response" submissions "$relation") || return 1
    ui_wrap_text "$relation: $count" "$(ui_width)" 2
  done
  printf '\n  EXERCÍCIOS CONCLUÍDOS\n'
  ui_separator
  rows=$(broker_response_rows "$response" completed_exercises) || return 1
  if [[ -z "$rows" ]]; then
    printf '  Nenhum exercício aprovado ainda.\n'
  else
    while IFS=$'\t' read -r code name; do
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      ui_wrap_text "$code - $name" "$(ui_width)" 2
    done <<<"$rows"
  fi
  printf '\n  HISTÓRICO DE AVALIAÇÕES\n'
  ui_separator
  rows=$(broker_response_rows "$response" review_history) || return 1
  if [[ -z "$rows" ]]; then
    printf '  Nenhuma avaliação registrada.\n'
  else
    while IFS= read -r encoded; do
      IFS=$'\t' read -r code score feedback created_at submitter reviewer relation <<<"$encoded"
      code=$(db_hex_decode "$code") || return 1
      score=$(db_hex_decode "$score") || return 1
      feedback=$(db_hex_decode "$feedback") || return 1
      created_at=$(db_hex_decode "$created_at") || return 1
      submitter=$(db_hex_decode "$submitter") || return 1
      reviewer=$(db_hex_decode "$reviewer") || return 1
      relation=$(db_hex_decode "$relation") || return 1
      if [[ "$relation" == "received" ]]; then
        ui_wrap_text "$code - nota $score/5, avaliado por $reviewer ($created_at)" "$(ui_width)" 2
      else
        ui_wrap_text "$code - nota $score/5, submissão de $submitter ($created_at)" "$(ui_width)" 2
      fi
      [[ -n "$feedback" ]] && ui_wrap_text "Feedback: $feedback" "$(ui_width)" 4
    done <<<"$rows"
  fi
  printf '\n  MOVIMENTAÇÕES DE PONTOS\n'
  ui_separator
  rows=$(broker_response_rows "$response" points_history) || return 1
  if [[ -z "$rows" ]]; then
    printf '  Nenhuma movimentação registrada.\n'
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

    ui_clear
    printf '\n  CATÁLOGO DE EXERCÍCIOS\n\n'
    if ((${#exercise_rows[@]} == 0)); then
      ui_message "Nenhum exercício disponível."
      ui_pause
      return 0
    fi
    for index in "${!exercise_rows[@]}"; do
      IFS=$'\t' read -r code name <<<"${exercise_rows[$index]}"
      ui_wrap_text "[$((index + 1))] $code - $name" "$(ui_width)" 2
    done
    printf '\n  [0] Voltar\n\n'
    ui_read_line choice "  EXERCÍCIOS > " || return 0
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
  local field
  local value
  local choice
  local width
  local points

  response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$code" |
    broker_request catalog.get token exercise_code) || return 1
  if ! broker_check_response "$response"; then
    ui_pause
    return 1
  fi
  width=$(ui_width)
  ui_clear
  printf '\n  EXERCÍCIO %s\n\n' "$code"
  for field in name description statement delivery_directory delivery_files allowed_functions; do
    value=$(broker_response_nested_field "$response" exercise "$field") || return 1
    [[ -z "$value" ]] && continue
    case "$field" in
    name) ui_wrap_text "$value" "$width" 2 ;;
    description) printf '\n  DESCRIÇÃO\n'; ui_wrap_text "$value" "$width" 2 ;;
    statement) printf '\n  MISSÃO\n'; ui_wrap_text "$value" "$width" 2 ;;
    delivery_directory) printf '\n  Diretório de entrega:\n'; ui_wrap_text "$value" "$width" 4 ;;
    delivery_files) printf '\n  Arquivos esperados:\n'; ui_wrap_text "$value" "$width" 4 ;;
    allowed_functions) printf '\n  Funções permitidas:\n'; ui_wrap_text "$value" "$width" 4 ;;
    esac
  done
  value=$(broker_response_nested_field "$response" exercise example) || return 1
  if [[ -n "$value" ]]; then
    ui_code_block "$value"
  fi
  value=$(broker_response_nested_field "$response" exercise terminal_model) || return 1
  if [[ -n "$value" ]]; then
    printf '\n  MODELO DO TERMINAL\n'
    ui_code_block "$value"
  fi
  value=$(broker_response_nested_field "$response" exercise hints) || return 1
  if [[ -n "$value" ]]; then
    printf '\n  DICAS\n'
    while IFS= read -r field || [[ -n "$field" ]]; do
      ui_wrap_text "- $field" "$width" 2
    done <<<"$value"
  fi
  requirements=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$code" |
    broker_request submissions.requirements token exercise_code) || return 1
  if ! broker_check_response "$requirements"; then
    ui_pause
    return 1
  fi
  if [[ "$(broker_response_field "$requirements" eligible)" == "True" ]]; then
    points=$(broker_response_field "$requirements" points) || return 1
    printf '\n  Saldo: %s ponto(s). Enviar a solução abre um slot de avaliação.\n' "$points"
    printf '  [1] Registrar entrega para avaliação\n'
  else
    value=$(broker_response_field "$requirements" reason) || return 1
    ui_wrap_text "Entrega indisponível: $value" "$width" 2
  fi
  printf '  [0] Voltar\n\n'
  ui_read_line choice "  EXERCÍCIO > " || return 0
  if [[ "$choice" == "1" && "$(broker_response_field "$requirements" eligible)" == "True" ]]; then
    exercise_submit_prompt "$code"
  fi
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
  local -a submission_rows

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
  ui_clear
  printf '\n  MINHAS ENTREGAS\n\n'
  if ((${#submission_rows[@]} == 0)); then
    ui_message "Você ainda não registrou entregas."
  else
    for encoded in "${submission_rows[@]}"; do
      IFS=$'\t' read -r submission_id code name status kind commit_url directory \
        submitted_at slot_id score feedback <<<"$encoded"
      submission_id=$(db_hex_decode "$submission_id") || return 1
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      status=$(db_hex_decode "$status") || return 1
      kind=$(db_hex_decode "$kind") || return 1
      submitted_at=$(db_hex_decode "$submitted_at") || return 1
      slot_id=$(db_hex_decode "$slot_id") || return 1
      score=$(db_hex_decode "$score") || return 1
      ui_wrap_text "#$submission_id | $code - $name | $status | $submitted_at" \
        "$(ui_width)" 2
      if [[ "$kind" == "github_commit" ]]; then
        commit_url=$(db_hex_decode "$commit_url") || return 1
        directory=$(db_hex_decode "$directory") || return 1
        ui_wrap_text "Commit: $commit_url (diretório: $directory)" "$(ui_width)" 4
      fi
      if [[ -n "$score" ]]; then
        feedback=$(db_hex_decode "$feedback") || return 1
        printf '  Avaliação: %s/5\n' "$score"
        [[ -n "$feedback" ]] && ui_wrap_text "Feedback: $feedback" "$(ui_width)" 4
      else
        printf '  Slot de avaliação: %s\n' "$slot_id"
      fi
      printf '\n'
    done
  fi
  ui_pause
}

broker_evaluation_review_prompt() {
  local slot_id="$1"
  local response
  local submitter
  local code
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
  kind=$(broker_response_nested_field "$response" slot content_kind) || return 1
  ui_clear
  printf '\n  AVALIAR SUBMISSÃO %s\n' "$slot_id"
  ui_wrap_text "Usuário: $submitter | Exercício: $code" "$(ui_width)" 2
  if [[ "$kind" == "github_commit" ]]; then
    commit_url=$(broker_response_nested_field "$response" slot commit_url) || return 1
    directory=$(broker_response_nested_field "$response" slot delivery_directory) || return 1
    printf '\n  Referência da entrega:\n'
    ui_wrap_text "Commit: $commit_url" "$(ui_width)" 2
    ui_wrap_text "Diretório: $directory" "$(ui_width)" 2
  else
    content=$(broker_response_nested_field "$response" slot content) || return 1
    content=$(printf '%s' "$content" | LC_ALL=C tr -d '\001-\010\013-\037\177') || return 1
    printf '\n  Conteúdo legado enviado (não executado):\n'
    ui_wrap_text "$content" "$(ui_width)" 2
  fi
  while true; do
    ui_read_line score "  Nota (0 a 5) > " || return 1
    [[ "$score" =~ ^[0-5]$ ]] && break
    ui_message "A nota deve ser um número inteiro de 0 a 5."
  done
  printf '  Comentário (vazio para nenhum; use .fim para várias linhas):\n'
  ui_read_multiline feedback || return 1
  status=$(printf '%s\0%s\0%s\0%s\0' \
    "$CURRENT_SESSION_TOKEN" "$slot_id" "$score" "$feedback" |
    broker_request evaluations.review.submit token slot_id score feedback) || return 1
  if broker_check_response "$status"; then
    points=$(broker_response_field "$status" points) || return 1
    if ((score >= 3)); then
      ui_message "Avaliação registrada: aprovado. Seu saldo é $points ponto(s)."
    else
      ui_message "Avaliação registrada: reprovado. Seu saldo é $points ponto(s)."
    fi
  else
    ui_message "Não foi possível registrar a avaliação."
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
  local accepted
  local choice
  local confirmation
  local action_response
  local -a slot_rows

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
    ui_clear
    printf '\n  AVALIAÇÕES ABERTAS\n\n'
    if ((${#slot_rows[@]} == 0)); then
      ui_message "Não há submissões disponíveis para avaliação."
      ui_pause
      return 0
    fi
    for encoded in "${slot_rows[@]}"; do
      IFS=$'\t' read -r slot_id username code name created_at accepted <<<"$encoded"
      slot_id=$(db_hex_decode "$slot_id") || return 1
      username=$(db_hex_decode "$username") || return 1
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      created_at=$(db_hex_decode "$created_at") || return 1
      accepted=$(db_hex_decode "$accepted") || return 1
      if [[ "$accepted" == "1" ]]; then
        ui_wrap_text "Slot $slot_id - $username / $code - reservado por você" "$(ui_width)" 2
      else
        ui_wrap_text "[$slot_id] $username / $code - $name (desde $created_at)" "$(ui_width)" 2
      fi
    done
    printf '\n  Digite o ID do slot ou 0 para voltar.\n\n'
    ui_read_line choice "  AVALIAÇÕES > " || return 0
    [[ "$choice" == "0" ]] && return 0
    if [[ ! "$choice" =~ ^[0-9]+$ ]]; then
      ui_message "ID de slot inválido."
      ui_pause
      continue
    fi
    encoded=""
    for encoded in "${slot_rows[@]}"; do
      IFS=$'\t' read -r slot_id username code name created_at accepted <<<"$encoded"
      slot_id=$(db_hex_decode "$slot_id") || return 1
      if [[ "$slot_id" == "$choice" ]]; then
        accepted=$(db_hex_decode "$accepted") || return 1
        break
      fi
    done
    if [[ "$slot_id" != "$choice" ]]; then
      ui_message "Slot indisponível."
      ui_pause
      continue
    fi
    if [[ "$accepted" == "1" ]]; then
      printf '\n  [1] Avaliar submissão\n'
      printf '  [2] Liberar minha reserva\n'
      printf '  [0] Voltar\n\n'
      ui_read_line choice "  SLOT > " || return 0
      case "$choice" in
      1)
        broker_evaluation_review_prompt "$slot_id"
        ;;
      2)
        action_response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$slot_id" |
          broker_request evaluations.cancel token slot_id) || return 1
        if broker_check_response "$action_response"; then
          ui_message "Reserva liberada."
        fi
        ui_pause
        ;;
      *)
        ;;
      esac
      continue
    fi
    ui_read_line confirmation "  Reservar este slot para avaliar agora? [s/N] > " || return 0
    if [[ "${confirmation,,}" != "s" ]]; then
      continue
    fi
    action_response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$choice" |
      broker_request evaluations.accept token slot_id) || return 1
    if broker_check_response "$action_response"; then
      broker_evaluation_review_prompt "$choice"
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

  ui_read_line code "  Código > " || return 1
  ui_read_line name "  Nome > " || return 1
  printf '  Descrição (use .fim para concluir):\n'
  ui_read_multiline description || return 1
  printf '  Missão/enunciado (use .fim para concluir):\n'
  ui_read_multiline statement || return 1
  ui_read_line delivery_directory "  Diretório de entrega > " || return 1
  printf '  Arquivos esperados (use .fim para concluir):\n'
  ui_read_multiline delivery_files || return 1
  printf '  Funções permitidas (use .fim para concluir):\n'
  ui_read_multiline allowed_functions || return 1
  printf '  Exemplo de execução (use .fim para concluir):\n'
  ui_read_multiline example || return 1
  printf '  Modelo do terminal (use .fim para concluir):\n'
  ui_read_multiline terminal_model || return 1
  printf '  Dicas (use .fim para concluir):\n'
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
  local confirmation

  while true; do
    ui_clear
    printf '\n'
    ui_wrap_text "GERENCIAR EXERCÍCIO $code [$status]" "$(ui_width)" 2
    printf '\n'
    ui_wrap_text "[1] Editar nome" "$(ui_width)" 2
    ui_wrap_text "[2] Editar descrição" "$(ui_width)" 2
    ui_wrap_text "[3] Editar missão/enunciado" "$(ui_width)" 2
    ui_wrap_text "[4] Editar diretório de entrega" "$(ui_width)" 2
    ui_wrap_text "[5] Editar arquivos esperados" "$(ui_width)" 2
    ui_wrap_text "[6] Editar funções permitidas" "$(ui_width)" 2
    ui_wrap_text "[7] Editar exemplo" "$(ui_width)" 2
    ui_wrap_text "[8] Editar modelo do terminal" "$(ui_width)" 2
    ui_wrap_text "[9] Editar dicas" "$(ui_width)" 2
    if [[ "$status" == "archived" ]]; then
      printf '  [a] Reativar exercício\n'
    else
      printf '  [a] Arquivar exercício\n'
    fi
    printf '  [0] Voltar\n\n'
    ui_read_line choice "  EXERCÍCIO ADMIN > " || return 0
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
      ui_read_line confirmation "  Confirmar operação? [s/N] > " || return 0
      [[ "${confirmation,,}" == "s" ]] || continue
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
        printf '  %s (use .fim para concluir):\n' "$label"
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
    ui_clear
    printf '\n  EXERCÍCIOS CADASTRADOS\n\n'
    for index in "${!exercise_rows[@]}"; do
      IFS=$'\t' read -r id code name status <<<"${exercise_rows[$index]}"
      id=$(db_hex_decode "$id") || return 1
      code=$(db_hex_decode "$code") || return 1
      name=$(db_hex_decode "$name") || return 1
      status=$(db_hex_decode "$status") || return 1
      ui_wrap_text "[$((index + 1))] $code - $name [$status]" "$(ui_width)" 2
    done
    printf '\n  [c] Cadastrar exercício\n  [0] Voltar\n\n'
    ui_read_line choice "  EXERCÍCIOS ADMIN > " || return 0
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
  ui_clear
  printf '\n  PERFIL DO USUÁRIO\n\n'
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

  while true; do
    broker_admin_user_show "$user_id" || return 1
    printf '\n  [1] Redefinir senha\n'
    printf '  [2] Adicionar pontos\n'
    printf '  [3] Remover pontos\n'
    printf '  [4] Ativar/desativar conta\n'
    printf '  [5] Histórico de pontos\n'
    printf '  [6] Histórico de avaliações\n'
    printf '  [0] Voltar\n\n'
    ui_read_line choice "  USUÁRIO ADMIN > " || return 0
    case "$choice" in
    1)
      ui_read_line password "  Senha temporária (mínimo 8 caracteres) > " 1 || return 1
      ui_read_line confirmation "  Confirme a senha temporária > " 1 || return 1
      if [[ "$password" != "$confirmation" || ${#password} -lt 8 ]]; then
        ui_message "Senhas não coincidem ou têm menos de 8 caracteres."
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
        ui_read_line confirmation "  Desativar a conta? [s/N] > " || return 0
      else
        active=1
        ui_read_line confirmation "  Reativar a conta? [s/N] > " || return 0
      fi
      if [[ "${confirmation,,}" == "s" ]]; then
        response=$(printf '%s\0%s\0%s\0' \
          "$CURRENT_SESSION_TOKEN" "$user_id" "$active" |
          broker_request admin.user.state token user_id is_active) || return 1
        if ! broker_check_response "$response"; then
          ui_pause
          continue
        fi
      fi
      ui_pause
      ;;
    5)
      response=$(printf '%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$user_id" |
        broker_request admin.points.history token user_id) || return 1
      broker_check_response "$response" || return 1
      rows=$(broker_response_rows "$response" entries) || return 1
      ui_clear
      printf '\n  HISTÓRICO DE PONTOS DO USUÁRIO #%s\n\n' "$user_id"
      if [[ -z "$rows" ]]; then
        printf '  Nenhuma movimentação registrada.\n'
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
      ui_clear
      printf '\n  HISTÓRICO DE AVALIAÇÕES DO USUÁRIO #%s\n\n' "$user_id"
      if [[ -z "$rows" ]]; then
        printf '  Nenhuma avaliação registrada.\n'
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

  while true; do
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request admin.users.list token) || return 1
    broker_check_response "$response" || return 1
    rows=$(broker_response_rows "$response" users) || return 1
    user_rows=()
    if [[ -n "$rows" ]]; then
      while IFS= read -r encoded; do user_rows+=("$encoded"); done <<<"$rows"
    fi
    ui_clear
    printf '\n  PERFIS CADASTRADOS\n\n'
    for encoded in "${user_rows[@]}"; do
      IFS=$'\t' read -r user_id username is_admin is_active created_at approved points <<<"$encoded"
      user_id=$(db_hex_decode "$user_id") || return 1
      username=$(db_hex_decode "$username") || return 1
      is_admin=$(db_hex_decode "$is_admin") || return 1
      is_active=$(db_hex_decode "$is_active") || return 1
      approved=$(db_hex_decode "$approved") || return 1
      points=$(db_hex_decode "$points") || return 1
      ui_wrap_text "[$user_id] $username ($([[ "$is_admin" == "1" ]] && printf 'admin' || printf 'participante')) | $([[ "$is_active" == "1" ]] && printf 'ativa' || printf 'inativa') | aprovados: $approved | pontos: $points" \
        "$(ui_width)" 2
    done
    printf '\n  Digite o ID para gerenciar ou 0 para voltar.\n\n'
    ui_read_line choice "  USUÁRIOS > " || return 0
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
  local confirmation

  while true; do
    response=$(printf '%s\0' "$CURRENT_SESSION_TOKEN" |
      broker_request admin.identity.list token) || return 1
    broker_check_response "$response" || return 1
    rows=$(broker_response_rows "$response" identities) || return 1
    ui_clear
    printf '\n  VÍNCULOS DE IDENTIDADE LINUX\n\n'
    if [[ -z "$rows" ]]; then
      printf '  Nenhum vínculo cadastrado.\n'
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
    printf '\n  [1] Criar vínculo  [2] Alterar UID  [3] Revogar vínculo  [0] Voltar\n'
    ui_wrap_text "Este cadastro administrativo ainda não autentica usuários Linux; o proxy SO_PEERCRED não está implementado." \
      "$(ui_width)" 2
    ui_read_line choice "  VÍNCULOS > " || return 0
    case "$choice" in
    1)
      ui_read_line target_uid "  UID Linux > " || continue
      ui_read_line target_user_id "  ID da conta SIMPLE > " || continue
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
      response=$(printf '%s\0%s\0%s\0' "$CURRENT_SESSION_TOKEN" "$target_uid" "$new_uid" |
        broker_request admin.identity.update token linux_uid new_linux_uid) || return 1
      if broker_check_response "$response"; then
        ui_message "UID do vínculo atualizado."
      fi
      ui_pause
      ;;
    3)
      ui_read_line target_uid "  UID Linux a revogar > " || continue
      ui_read_line confirmation "  Confirma a revogação? (s/N) > " || continue
      if [[ "$confirmation" != "s" && "$confirmation" != "S" ]]; then
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
  ui_clear
  printf '\n  %s\n\n' "$title"
  if [[ "$field" == "submissions" ]]; then
    labels=("ID" "Usuário" "Exercício" "Status" "Tipo" "Commit" "Diretório" "Enviada em" "Slot")
  else
    labels=("Slot" "Status" "Participante" "Avaliador" "Reservado por" "Reservado em" "Exercício" "Nota" "Feedback" "Aberta em" "Concluída em")
  fi
  if ((${#entries[@]} == 0)); then
    printf '  Nenhum registro encontrado.\n'
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
  ui_clear
  printf '\n  VISÃO GERAL DO SISTEMA\n\n'
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
    ui_clear
    printf '\n  ADMINISTRAÇÃO\n\n'
    ui_wrap_text "[1] Gerenciar exercícios" "$(ui_width)" 2
    ui_wrap_text "[2] Perfis e pontos dos usuários" "$(ui_width)" 2
    ui_wrap_text "[3] Todas as submissões" "$(ui_width)" 2
    ui_wrap_text "[4] Histórico de avaliações" "$(ui_width)" 2
    ui_wrap_text "[5] Visão geral do sistema" "$(ui_width)" 2
    ui_wrap_text "[6] Vínculos UID Linux" "$(ui_width)" 2
    ui_wrap_text "[0] Voltar" "$(ui_width)" 2
    printf '\n'
    ui_read_line choice "  ADMIN > " || return 0
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

  ui_clear
  printf '\n  SIMPLE | %s\n\n' "$CURRENT_USERNAME"
  ui_wrap_text "[1] Catálogo de exercícios" "$(ui_width)" 2
  ui_wrap_text "[2] Avaliações" "$(ui_width)" 2
  ui_wrap_text "[3] Meu progresso" "$(ui_width)" 2
  ui_wrap_text "[4] Meu perfil" "$(ui_width)" 2
  if [[ "$CURRENT_USER_IS_ADMIN" == "True" ]]; then
    ui_wrap_text "[5] Administração" "$(ui_width)" 2
  fi
  ui_wrap_text "[6] Minhas entregas" "$(ui_width)" 2
  ui_wrap_text "[0] Sair" "$(ui_width)" 2
  printf '\n'
  ui_read_line choice "  MENU > " || return 1
  case "$choice" in
  1) exercises_menu ;;
  2) evaluations_menu ;;
  3) progress_menu ;;
  4) users_profile_menu ;;
  5)
    if [[ "$CURRENT_USER_IS_ADMIN" == "True" ]]; then
      admin_menu
    else
      ui_message "Acesso administrativo não autorizado."
      ui_pause
    fi
    ;;
  6) broker_submissions_menu ;;
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
