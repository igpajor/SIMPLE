#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC2034 # Consumed by core/database.sh.
SCRIPT_DIR="$ROOT_DIR"
TEST_DIR="$(mktemp -d)"
TEST_BIN="$TEST_DIR/bin"
mkdir -p -- "$TEST_BIN"

cleanup() {
  rm -f -- "$TEST_BIN/gum" "$TEST_BIN/fzf" \
    "$TEST_DIR/ui-log" "$TEST_DIR/menu-output"
  rmdir "$TEST_BIN" "$TEST_DIR"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

source "$ROOT_DIR/core/ui.sh"
source "$ROOT_DIR/core/database.sh"
source "$ROOT_DIR/core/broker_client.sh"
source "$ROOT_DIR/modules/chat.sh"

ui_colors_enabled() { return 0; }
color_sequence=$(ui_color_primary)
[[ "$color_sequence" == $'\033[1;36m' ]] ||
  fail "cores emitem sequências ANSI reais, não texto escapado"
ui_colors_enabled() { return 1; }
[[ -z "$(ui_color_primary)" ]] || fail "cores respeitam ambiente sem cor"

CHAT_TIMEZONE="America/Sao_Paulo"
[[ "$(chat_local_time "2026-11-04 19:19:00")" == "2026-11-04 16:19" ]] ||
  fail "horário UTC do chat é convertido para o fuso local configurado"
unset CHAT_AUTO_REFRESH
chat_auto_refresh_enabled ||
  fail "atualização automática do chat deve vir ativada por padrão"
[[ "${CHAT_REFRESH_SECONDS:-2}" == "2" ]] ||
  fail "polling automático usa intervalo padrão curto de 2 segundos"
CHAT_AUTO_REFRESH=1
chat_auto_refresh_enabled ||
  fail "atualização automática pode ser habilitada explicitamente"
CHAT_AUTO_REFRESH=0
if chat_auto_refresh_enabled; then
  fail "CHAT_AUTO_REFRESH=0 mantém o modo manual"
fi
[[ -z "$(broker_response_nested_field '{"preview":null}' --allow-null preview body)" ]] ||
  fail "prévia nula do dashboard é tratada como ausência de mensagem"
empty_last_field=$(broker_response_rows '{"rows":[{"first":"x","last":null}]}' \
  --separator "$CHAT_ROW_SEPARATOR" rows)
IFS="$CHAT_ROW_SEPARATOR" read -r empty_first empty_last <<<"$empty_last_field"
[[ "$(db_hex_decode "$empty_first")" == "x" &&
  -z "$(db_hex_decode "$empty_last")" ]] ||
  fail "separador do chat preserva campo vazio no fim da linha"

chat_rows_response='{"messages":[{"id":7,"username":"ana","user_id":2,"body":"Oi","created_at":"2026-11-04 19:19:00","pinned_at":null,"reactions":"","mentioned":0},{"id":8,"username":"bia","user_id":3,"body":"Olá @ana","created_at":"2026-11-04 19:20:00","pinned_at":null,"reactions":"👍:1","mentioned":1}]}'
chat_rows=$(broker_response_rows "$chat_rows_response" \
  --separator "$CHAT_ROW_SEPARATOR" messages)
CURRENT_USER_ID=2
CURRENT_USERNAME=ana
chat_row_one="${chat_rows%%$'\n'*}"
chat_row_two="${chat_rows#*$'\n'}"
IFS="$CHAT_ROW_SEPARATOR" read -r chat_id _ _ _ _ chat_pinned \
  chat_reactions chat_mentioned \
  <<<"$chat_row_one"
[[ "$(db_hex_decode "$chat_id")" == "7" &&
  "$(db_hex_decode "$chat_pinned")" == "" &&
  "$(db_hex_decode "$chat_reactions")" == "" &&
  "$(db_hex_decode "$chat_mentioned")" == "0" ]] ||
  fail "separador do chat preserva campos vazios no meio da linha"
CHAT_LAST_DATE=""
first_chat_render=$(chat_render_message_row "$chat_row_one")
[[ "$first_chat_render" == *"Oi"* &&
  "$first_chat_render" != *"mensagem fixada"* ]] ||
  fail "mensagem sem pin não aparece como fixada após parsing"
CHAT_LAST_DATE=""
second_chat_render=$(chat_render_message_row "$chat_row_two")
[[ "$second_chat_render" == *"@ você"* &&
  "$second_chat_render" == *"👍:1"* &&
  "$second_chat_render" != *"mensagem fixada"* ]] ||
  fail "menção e reação permanecem nos campos corretos sem pin"

CHAT_CHANNEL="global"
chat_command /sala ajuda
[[ "$CHAT_CHANNEL" == "help" && "$CHAT_ROOM_NAME" == "Ajuda" ]] ||
  fail "/sala ajuda seleciona o canal help"
chat_command /sala help
[[ "$CHAT_CHANNEL" == "help" && "$CHAT_ROOM_NAME" == "Ajuda" ]] ||
  fail "/sala help seleciona o canal help"

CHAT_HISTORY_HAS_MORE=True
CHAT_BUFFER=("$chat_row_one")
more_history_output=$(chat_render_buffer)
[[ "$more_history_output" == *"use /historico"* ]] ||
  fail "janela de conversa indica quando há histórico anterior"
CHAT_SYNC_RESPONSE='{"pinned":[{"id":12,"username":"ana","body":"Aviso importante","created_at":"2026-11-04 19:19:00"}]}'
pinned_output=$(chat_render_pinned "$CHAT_SYNC_RESPONSE")
[[ "$pinned_output" == *"FIXADAS"* &&
  "$pinned_output" == *"#12"* &&
  "$pinned_output" == *"Aviso importante"* ]] ||
  fail "chat destaca mensagens fixadas fora da janela recente"

cat >"$TEST_BIN/gum" <<'GUM'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$SIMPLE_UI_TEST_LOG"
case "$1" in
choose)
  printf '%s' "$SIMPLE_UI_TEST_CHOICE"
  ;;
input)
  printf '%s' "$SIMPLE_UI_TEST_INPUT"
  ;;
write)
  printf '%s' "$SIMPLE_UI_TEST_MULTILINE"
  ;;
confirm)
  exit "${SIMPLE_UI_TEST_CONFIRM_STATUS:-0}"
  ;;
style)
  printf '%s' "${@: -1}"
  ;;
*)
  exit 2
  ;;
esac
GUM
chmod 700 "$TEST_BIN/gum"
cat >"$TEST_BIN/fzf" <<'FZF'
#!/usr/bin/env bash
printf 'called\n' >>"$SIMPLE_UI_TEST_LOG"
exit 99
FZF
chmod 700 "$TEST_BIN/fzf"
PATH="$TEST_BIN:$PATH"
export PATH
SIMPLE_UI_TEST_LOG="$TEST_DIR/ui-log"
export SIMPLE_UI_TEST_LOG
: >"$SIMPLE_UI_TEST_LOG"

ui_can_interact() { return 1; }
selected=""
ui_menu_choice selected "Menu principal" \
  "1|Primeiro" "2|Segundo" < <(printf '2\n') >"$TEST_DIR/menu-output"
menu_output=$(<"$TEST_DIR/menu-output")
[[ "$selected" == "2" ]] || fail "menu numerado seleciona a chave correta"
[[ "$menu_output" == *"[1] Primeiro"* && "$menu_output" == *"[2] Segundo"* ]] ||
  fail "menu primário exibe opções numeradas"
if grep -q '^choose ' "$TEST_DIR/ui-log"; then
  fail "menu numerado não chama seletor Gum"
fi

if grep -q '^called$' "$TEST_DIR/ui-log"; then
  fail "o menu principal não deve chamar fzf, mesmo quando instalado"
fi
ui_can_interact() { return 0; }

SIMPLE_UI_TEST_INPUT="senha-teste-123"
export SIMPLE_UI_TEST_INPUT
password=""
ui_read_line password "Senha > " 1
[[ "$password" == "$SIMPLE_UI_TEST_INPUT" ]] || fail "entrada mascarada preserva o valor"
grep -q -- '--password' "$TEST_DIR/ui-log" || fail "senha usa o modo mascarado do Gum"
if grep -q -- "$SIMPLE_UI_TEST_INPUT" "$TEST_DIR/ui-log"; then
  fail "senha não deve ser passada como argumento ao Gum"
fi

SIMPLE_UI_TEST_MULTILINE=$'primeira linha\nsegunda linha'
export SIMPLE_UI_TEST_MULTILINE
text=""
ui_read_multiline text
[[ "$text" == "$SIMPLE_UI_TEST_MULTILINE" ]] || fail "entrada multilinha preserva quebras de linha"

SIMPLE_UI_TEST_CONFIRM_STATUS=0
export SIMPLE_UI_TEST_CONFIRM_STATUS
ui_confirm "Confirmar?"
SIMPLE_UI_TEST_CONFIRM_STATUS=1
if ui_confirm "Confirmar?"; then
  fail "cancelamento do Gum deve retornar falso"
fi

label=$(ui_tui_label $'Nome\ncom\tcontroles\033[31m')
[[ "$label" == "Nome com controles[31m" ]] || fail "rótulos removem controles sem perder texto"
label=$(ui_tui_label $'A\u009bB\u202eC\u2066D\u2028E\u200dF')
[[ "$label" == "ABCD E‍F" ]] || fail "rótulos removem C1 e bidi, normalizam separadores e preservam ZWJ"
label=$(ui_tui_label "$(printf 'A\233[31mB')")
[[ "$label" == "A[31mB" ]] || fail "rótulos removem CSI de 8 bits"
label=$(ui_sanitize $'\u0100\u0085')
[[ "$label" == $'\u0100' ]] || fail "sanitização remove C1 sem corromper UTF-8 válido"

[[ "$(ui_simple_banner_variant 80)" == "wide" ]] ||
  fail "banner de login escolhe arte completa em 80 colunas"
[[ "$(ui_simple_banner_variant 40)" == "compact" ]] ||
  fail "banner de login escolhe arte compacta em 40 colunas"
[[ "$(ui_simple_banner_variant 24)" == "minimal" ]] ||
  fail "banner de login escolhe arte mínima em 24 colunas"

ui_colors_enabled() { return 1; }
for banner_width in 120 80 72 71 40 30 29 24 12 6 4 1; do
  banner_output=$(NO_COLOR=1 ui_simple_banner "$banner_width")
  while IFS= read -r banner_line; do
    ((${#banner_line} <= banner_width)) ||
      fail "banner excede $banner_width colunas: $banner_line"
  done <<<"$banner_output"
done

banner_output=$(NO_COLOR=1 ui_simple_banner 80)
[[ "$banner_output" == *"███████╗██╗███╗   ███╗██████╗"* ]] ||
  fail "banner largo preserva a arte do primeiro protótipo SIMPLE"
[[ "$banner_output" == *"AMBIENTE DE APRENDIZAGEM"* ]] ||
  fail "banner largo preserva o texto original do cabeçalho"
[[ "$banner_output" == *"┌────────────────────────────────────────────────────────────────────┐"* ]] ||
  fail "banner largo mantém moldura ao redor do logo"
[[ "$banner_output" == *"│           ███████╗██╗███╗   ███╗██████╗"* ]] ||
  fail "logo fica centralizado dentro da moldura"
logo_start=-1
while IFS= read -r banner_line; do
  [[ "$banner_line" == *'│'*'██'* ]] || continue
  banner_inside="${banner_line#*│}"
  banner_trimmed="${banner_inside#"${banner_inside%%[! ]*}"}"
  banner_indent=$((${#banner_inside} - ${#banner_trimmed}))
  if ((logo_start < 0)); then
    logo_start=$banner_indent
  elif ((banner_indent != logo_start)); then
    fail "linhas do logo SIMPLE devem compartilhar o mesmo alinhamento"
  fi
done <<<"$banner_output"
((logo_start >= 0)) || fail "linhas do logo não encontradas na moldura"
[[ "$banner_output" != *$'\033'* ]] ||
  fail "banner sem cores não emite sequências ANSI"
banner_output=$(NO_COLOR=1 ui_simple_banner 40)
[[ "$banner_output" == *"SIMPLE"* &&
  "$banner_output" == *"AMBIENTE DE APRENDIZAGEM"* ]] ||
  fail "banner compacto mantém a identidade do SIMPLE"
login_option=$(ui_login_option 1 "Criar Conta" 80)
[[ "$login_option" == *"1 | Criar Conta"* &&
  "$login_option" != *"Novo usuário"* ]] ||
  fail "menu de login exibe somente número e ação"
banner_output=$(printf 'stdin-preservado\n' | {
  ui_simple_banner 80 >/dev/null
  IFS= read -r remaining && printf '%s' "$remaining"
})
[[ "$banner_output" == "stdin-preservado" ]] ||
  fail "banner não consome entrada padrão"

COLUMNS=80
home_intro=$(ui_home_intro $'ana\n[0] ação falsa\033[31m')
[[ "$home_intro" == *"Olá, ana [0] ação falsa[31m."* ]] ||
  fail "home mostra boas-vindas sem permitir injeção de controles"
[[ "$home_intro" != *$'\033'* ]] || fail "home não imprime escapes fornecidos no nome"
home_wide_metrics=$(ui_home_metrics 3 2 7)
[[ "$home_wide_metrics" == *"Pontos: 3  |  Aprovados: 2  |  Para avaliar: 7"* ]] ||
  fail "home agrupa métricas quando há largura suficiente"

COLUMNS=36
home_narrow_metrics=$(ui_home_metrics 3 2 7)
[[ "$home_narrow_metrics" == *"Aprovados: 2"* &&
  "$home_narrow_metrics" == *"Para avaliar: 7"* ]] ||
  fail "home empilha métricas em terminais estreitos"
[[ "$home_narrow_metrics" != *"Pontos: 3  |  Aprovados:"* ]] ||
  fail "home evita métricas horizontais em terminais estreitos"

ui_can_interact() { return 1; }
ui_menu_choice selected "Lista numerada" \
  "1|Item 1" "2|Item 2" "3|Item 3" "4|Item 4" \
  "5|Item 5" "6|Item 6" "7|Item 7" "8|Item 8" \
  < <(printf '3\n') >"$TEST_DIR/menu-output"
menu_output=$(<"$TEST_DIR/menu-output")
[[ "$selected" == "3" ]] || fail "menu numerado seleciona item em lista extensa"
[[ "$menu_output" == *"[8] Item 8"* ]] || fail "menu exibe todas as opções disponíveis"

pause_result=$(printf 'preservado\n' | {
  ui_pause >/dev/null
  IFS= read -r remaining && printf '%s' "$remaining"
})
[[ "$pause_result" == "preservado" ]] || fail "pausa sem TTY não consome a entrada automatizada"

printf 'OK: testes dos helpers de interface passaram.\n'
