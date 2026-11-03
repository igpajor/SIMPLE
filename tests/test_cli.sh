#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_DIR="$ROOT_DIR"
TEST_DIR="$(mktemp -d)"
DB_PATH="$TEST_DIR/simple.db"

cleanup() {
  rm -f -- "$TEST_DIR/simple.db" "$TEST_DIR/simple.db-wal" "$TEST_DIR/simple.db-shm" \
    "$TEST_DIR/legacy.db" "$TEST_DIR/legacy.db-wal" "$TEST_DIR/legacy.db-shm" \
    "$TEST_DIR/menu-input" "$TEST_DIR/admin-input" "$TEST_DIR/exercises/NEW-EX.txt"
  rm -f -- "$TEST_DIR/exercises/TEST-01.txt"
  rmdir "$TEST_DIR/exercises" 2>/dev/null || true
  rmdir "$TEST_DIR"
}
trap cleanup EXIT

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  local message="$3"

  [[ "$actual" == "$expected" ]] || fail "$message (esperado '$expected', recebido '$actual')"
}

source "$ROOT_DIR/core/database.sh"
source "$ROOT_DIR/core/auth.sh"
source "$ROOT_DIR/core/ui.sh"
source "$ROOT_DIR/core/navigation.sh"
source "$ROOT_DIR/modules/points.sh"
CURRENT_USER_ID=999
CURRENT_USERNAME="spoofed"
source "$ROOT_DIR/modules/users.sh"
assert_equals "" "$CURRENT_USER_ID" "sessão não pode ser injetada pelo ambiente"
assert_equals "" "$CURRENT_USERNAME" "nome de usuário não pode ser injetado pelo ambiente"
source "$ROOT_DIR/modules/exercises.sh"
source "$ROOT_DIR/modules/evaluations.sh"
source "$ROOT_DIR/modules/progress.sh"
EXERCISE_STORAGE_DIR="$TEST_DIR/exercises"
source "$ROOT_DIR/modules/admin.sh"

narrow_header=$(COLUMNS=16 ui_header)
if ! awk 'length($0) > 16 { exit 1 }' <<<"$narrow_header"; then
  fail "cabeçalho respeita terminais estreitos"
fi
narrow_message=$(COLUMNS=16 ui_message "Uma mensagem longa deve ser quebrada sem ultrapassar a largura.")
if ! awk 'length($0) > 16 { exit 1 }' <<<"$narrow_message"; then
  fail "mensagens respeitam terminais estreitos"
fi

DB_PATH="$TEST_DIR/legacy.db"
sqlite3 "$DB_PATH" "
  CREATE TABLE exercises (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    delivery_directory TEXT,
    delivery_files TEXT,
    allowed_functions TEXT,
    status TEXT NOT NULL DEFAULT 'available',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );
  INSERT INTO exercises (id, code, name, description)
  VALUES (1, 'OLD-01', 'Legado', 'Preservar dados existentes');
  CREATE TABLE users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
  );
  CREATE TABLE submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    exercise_id INTEGER NOT NULL REFERENCES exercises(id),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    status TEXT NOT NULL DEFAULT 'pending',
    submitted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
  );
  INSERT INTO users (username, password_hash)
  VALUES ('legacy-user', 'existing-hash');
  INSERT INTO submissions (user_id, exercise_id, content)
  VALUES (1, 1, 'conteúdo legado preservado');
"
db_init
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('exercises') WHERE name = 'statement_path';")" \
  "migração adiciona o caminho do enunciado"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('exercises') WHERE name = 'terminal_model';")" \
  "migração adiciona o modelo do terminal"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('exercises') WHERE name = 'hints';")" \
  "migração adiciona dicas"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('exercises') WHERE name = 'example';")" \
  "migração adiciona exemplo de execução"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('users') WHERE name = 'is_admin';")" \
  "migração adiciona o papel administrativo"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('users') WHERE name = 'must_change_password';")" \
"migração adiciona a troca obrigatória de senha"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('users') WHERE name = 'display_name';")" \
  "migração adiciona nome de exibição"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('users') WHERE name = 'github_username';")" \
  "migração adiciona usuário GitHub"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('users') WHERE name = 'bio';")" \
  "migração adiciona biografia"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('submissions') WHERE name = 'commit_url';")" \
  "migração adiciona referência de commit"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM pragma_table_info('submissions') WHERE name = 'delivery_directory';")" \
  "migração adiciona diretório da entrega"
assert_equals "0" "$(db_query "SELECT is_admin FROM users WHERE username = 'legacy-user';")" \
  "contas existentes não recebem acesso administrativo automaticamente"
assert_equals "OLD-01" "$(db_query "SELECT code FROM exercises WHERE id = 1;")" \
  "migração preserva exercícios existentes"
assert_equals "conteúdo legado preservado" "$(db_query "SELECT content FROM submissions WHERE id = 1;")" \
  "migração mantém o histórico de submissão legado"
assert_equals "legacy" "$(db_query "SELECT content_kind FROM submissions WHERE id = 1;")" \
  "submissões anteriores mantêm o tipo legado"

DB_PATH="$TEST_DIR/simple.db"
db_init
db_query "
  INSERT INTO exercises (code, name, description)
  VALUES
    ('SH00-EX00', 'Os verdadeiros sabem exibir um Z', ''),
    ('SH00-EX02', 'testShell00 Crie o arquivo testShell00 no seu diretório de entrega.', '');
"
db_init
assert_equals "10" "$(db_query "SELECT COUNT(*) FROM exercises WHERE code LIKE 'SHELL00-EX%';")" \
  "catálogo local carrega os dez exercícios Shell 00"
assert_equals "0" "$(db_query "
  SELECT COUNT(*) FROM exercises
  WHERE code IN ('SH00-EX00', 'SH00-EX02') AND status = 'available';
")" "aliases antigos deixam de aparecer no catálogo"
assert_equals "2" "$(db_query "
  SELECT COUNT(*) FROM exercises
  WHERE code IN ('SH00-EX00', 'SH00-EX02') AND status = 'archived';
")" "aliases antigos são arquivados sem excluir seus registros"
assert_equals "1" "$(db_query "
  SELECT COUNT(*) FROM exercises
  WHERE code = 'SHELL00-EX02'
    AND statement LIKE 'Crie todos esses arquivos e diretórios%'
    AND example LIKE '%drwx--xr-x%';
")" "catálogo preserva a estrutura e a formatação do exercício 02"
assert_equals "1" "$(db_query "
  SELECT COUNT(*) FROM exercises
  WHERE code = 'SHELL00-EX07'
    AND instr(hints, 'direitos autorais') > 0;
")" "catálogo sinaliza o exemplo omitido por direitos autorais"
shell_exercise_output=$(printf '0\n' | exercise_show SHELL00-EX02)
if [[ "$shell_exercise_output" != *"drwx--xr-x"* ]] ||
  [[ "$shell_exercise_output" != *"test6 -> test0"* ]]; then
  fail "enunciado do exercício preserva a formatação multilinha do PDF"
fi
if [[ "$shell_exercise_output" != *"Diretório de entrega: ex02/"* ]] ||
  [[ "$shell_exercise_output" != *"Arquivos a entregar: exo2.tar"* ]]; then
  fail "especificações exibem rótulo e valor na mesma linha"
fi
spec_line=$(grep -n -m1 '^  ESPECIFICAÇÕES$' <<<"$shell_exercise_output" | cut -d: -f1)
mission_line=$(grep -n -m1 '^  MISSÃO$' <<<"$shell_exercise_output" | cut -d: -f1)
if [[ ! "$spec_line" =~ ^[0-9]+$ || ! "$mission_line" =~ ^[0-9]+$ ]] ||
  ((spec_line >= mission_line)); then
  fail "especificações aparecem antes da missão"
fi
db_query "
  DROP TRIGGER trg_submission_validate;
  CREATE TRIGGER trg_submission_validate
  BEFORE INSERT ON submissions
  BEGIN
    SELECT CASE WHEN COALESCE((
      SELECT SUM(amount) FROM points_ledger WHERE user_id = NEW.user_id
    ), 0) < 1 THEN RAISE(ABORT, 'Saldo insuficiente') END;
  END;
"
db_init
assert_equals "1" "$(db_query "
  SELECT instr(sql, 'admin_point_adjustments') > 0
  FROM sqlite_master WHERE type = 'trigger' AND name = 'trg_submission_validate';
")" "migração atualiza a validação de saldo em bancos existentes"
db_query "
  INSERT INTO exercises (code, name, description, statement_path)
  VALUES ('TEST-01', 'Exercício de teste', 'Submissão manual', 'storage/exercises/TEST-01.txt');
"
mkdir -p -- "$EXERCISE_STORAGE_DIR"
cat >"$EXERCISE_STORAGE_DIR/TEST-01.txt" <<'EXERCISE'
EXERCÍCIO TEST-01
Exercício de teste

Submissão manual

MISSÃO

Missão original preservada.

ESPECIFICAÇÕES

DIRETÓRIO DE ENTREGA
ex00

ARQUIVOS A ENTREGAR
main.c

FUNÇÕES PERMITIDAS
write

EXEMPLO DE EXECUÇÃO
$ ./main
OK
EXERCISE

exercise_menu_output=$(printf '0\n' | exercises_menu)
if [[ "$exercise_menu_output" == *"command not found"* ]] ||
  [[ "$exercise_menu_output" != *"TEST-01 - Exercício de teste"* ]]; then
  fail "menu de exercícios usa as funções de interface corretas e lista exercícios"
fi
legacy_exercise_output=$(printf '0\n' | exercise_show TEST-01)
if [[ "$legacy_exercise_output" != *"Diretório de entrega: ex00"* ]] ||
  [[ "$legacy_exercise_output" != *"Arquivos a entregar: main.c"* ]] ||
  [[ "$legacy_exercise_output" != *"Funções permitidas: write"* ]]; then
  fail "enunciados legados exibem especificações com valores na mesma linha"
fi
spec_line=$(grep -n -m1 '^  ESPECIFICAÇÕES$' <<<"$legacy_exercise_output" | cut -d: -f1)
mission_line=$(grep -n -m1 '^  MISSÃO$' <<<"$legacy_exercise_output" | cut -d: -f1)
if [[ ! "$spec_line" =~ ^[0-9]+$ || ! "$mission_line" =~ ^[0-9]+$ ]] ||
  ((spec_line >= mission_line)); then
  fail "enunciado legado mostra especificações antes da missão"
fi
exercise_selection_output=$(printf '1\n0\n0\n' | exercises_menu)
if [[ "$exercise_selection_output" == *"Enunciado não encontrado."* ]] ||
  [[ "$exercise_selection_output" != *"Crie um arquivo z"* ]]; then
  fail "selecionar exercício abre seu enunciado usando o código decodificado"
fi

auth_register alice 'senha-segura-123'
alice_id="$CURRENT_USER_ID"
assert_equals "1" "$(points_balance "$alice_id")" "conta recebe crédito inicial"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM users WHERE password_hash != 'senha-segura-123';")" \
  "senha não é armazenada em texto puro"

auth_register bob 'outra-senha-segura'
bob_id="$CURRENT_USER_ID"
assert_equals "1" "$(points_balance "$bob_id")" "cada conta recebe crédito inicial"
if admin_create_exercise "$bob_id" >/dev/null 2>&1; then
  fail "conta sem papel administrativo não pode cadastrar exercícios"
fi

db_query "UPDATE users SET is_admin = 1 WHERE id = $alice_id;"
CURRENT_USER_ID="$alice_id"
admin_menu_output=$(printf '0\n' | menu_principal)
if [[ "$admin_menu_output" != *"[5] Administração"* ]] ||
  [[ "$admin_menu_output" != *"[6] Minhas entregas"* ]]; then
  fail "menu principal mostra administração para administradores"
fi

db_query "UPDATE users SET is_admin = 0 WHERE id = $bob_id;"
CURRENT_USER_ID="$bob_id"
regular_menu_output=$(printf '0\n' | menu_principal)
if [[ "$regular_menu_output" == *"[5] Administração"* ]]; then
  fail "menu principal oculta administração de contas comuns"
fi
if [[ "$regular_menu_output" != *"[5] Minhas entregas"* ]] ||
  [[ "$regular_menu_output" == *"[6] Minhas entregas"* ]]; then
  fail "menu principal oferece acompanhamento das próprias entregas"
fi

cat >"$TEST_DIR/admin-input" <<'INPUT'
NEW-EX
Novo exercício
Descrição do novo exercício
src
main.c
Makefile
.fim
printf
write
.fim
Enunciado de teste.
Linha adicional.
.fim
SIMPLE > executar
Resultado esperado
.fim
SIMPLE > executa
Resultado esperado
.fim
Use cat para ler o arquivo.
.fim
INPUT
admin_create_exercise "$alice_id" <"$TEST_DIR/admin-input"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM exercises WHERE code = 'NEW-EX';")" \
  "administrador cadastra exercício"
assert_equals "SIMPLE > executa
Resultado esperado" "$(db_query "SELECT terminal_model FROM exercises WHERE code = 'NEW-EX';")" \
  "modelo do terminal é persistido"
assert_equals "Use cat para ler o arquivo." "$(db_query "SELECT hints FROM exercises WHERE code = 'NEW-EX';")" \
  "dicas são persistidas"
assert_equals "SIMPLE > executar
Resultado esperado" "$(db_query "SELECT example FROM exercises WHERE code = 'NEW-EX';")" \
  "exemplo de execução é um campo independente"
assert_equals "src" "$(db_query "SELECT delivery_directory FROM exercises WHERE code = 'NEW-EX';")" \
  "diretório de entrega é persistido"
assert_equals "main.c
Makefile" "$(db_query "SELECT delivery_files FROM exercises WHERE code = 'NEW-EX';")" \
  "arquivos de entrega são persistidos"
assert_equals "printf
write" "$(db_query "SELECT allowed_functions FROM exercises WHERE code = 'NEW-EX';")" \
  "funções permitidas são persistidas"
if [[ "$(<"$EXERCISE_STORAGE_DIR/NEW-EX.txt")" != *"DIRETÓRIO DE ENTREGA
src

ARQUIVOS A ENTREGAR
main.c
Makefile

FUNÇÕES PERMITIDAS
printf
write"* ]]; then
  fail "enunciado inclui as especificações de entrega"
fi
exercise_output=$(printf '0\n' | exercise_show NEW-EX)
if [[ "$exercise_output" != *"MODELO DO TERMINAL"* ]] ||
  [[ "$exercise_output" != *"DICAS"* ]] ||
  [[ "$exercise_output" != *"SIMPLE > executa"* ]]; then
  fail "visualização do exercício exibe modelo do terminal e dicas"
fi
if [[ "$exercise_output" != *"Diretório de entrega"* ]] ||
  [[ "$exercise_output" != *"Funções permitidas"* ]] ||
  [[ "$exercise_output" != *"Makefile"* ]] ||
  [[ "$exercise_output" != *"EXEMPLO DE EXECUÇÃO"* ]]; then
  fail "visualização apresenta todas as especificações"
fi
if admin_archive_exercise NEW-EX >/dev/null 2>&1; then
  fail "conta sem papel administrativo não pode arquivar exercícios"
fi
CURRENT_USER_ID="$alice_id"
legacy_file_hash=$(sha256sum "$EXERCISE_STORAGE_DIR/TEST-01.txt")
printf '3\nupdated-dir\n\n0\n' | admin_edit_exercise TEST-01 >/dev/null
assert_equals "updated-dir" "$(db_query "SELECT delivery_directory FROM exercises WHERE code = 'TEST-01';")" \
  "admin atualiza especificação de exercício legado"
assert_equals "Missão original preservada." "$(db_query "SELECT statement FROM exercises WHERE code = 'TEST-01';")" \
  "admin estrutura a missão antiga sem alterar seu conteúdo"
assert_equals "$legacy_file_hash" "$(sha256sum "$EXERCISE_STORAGE_DIR/TEST-01.txt")" \
  "edição preserva o arquivo original do enunciado"
assert_equals "$" "$(db_query "SELECT substr(example, 1, 1) FROM exercises WHERE code = 'TEST-01';")" \
  "migração para campos estruturados preserva o exemplo original"

printf '3\nbuild\n\n0\n' | admin_edit_exercise NEW-EX >/dev/null
assert_equals "build" "$(db_query "SELECT delivery_directory FROM exercises WHERE code = 'NEW-EX';")" \
  "admin edita campos de entrega"
new_exercise_file_hash=$(sha256sum "$EXERCISE_STORAGE_DIR/NEW-EX.txt")
printf '6\nTexto atualizado.\nEXEMPLO DE EXECUÇÃO\n$ make\n.fim\n\n0\n' |
  admin_edit_exercise NEW-EX >/dev/null
assert_equals "Texto atualizado.
EXEMPLO DE EXECUÇÃO
$ make" "$(db_query "SELECT statement FROM exercises WHERE code = 'NEW-EX';")" \
  "admin edita o enunciado do exercício"
updated_exercise_output=$(printf '0\n' | exercise_show NEW-EX)
if [[ "$updated_exercise_output" != *"EXEMPLO DE EXECUÇÃO"* ]] ||
  [[ "$updated_exercise_output" != *"Diretório de entrega"* ]] ||
  [[ "$updated_exercise_output" != *"build"* ]]; then
  fail "enunciado atualizado preserva exemplos e especificações"
fi
assert_equals "$new_exercise_file_hash" "$(sha256sum "$EXERCISE_STORAGE_DIR/NEW-EX.txt")" \
  "edição estruturada não reescreve o arquivo original"

admin_profiles_output=$(printf '0\n' | admin_users_menu)
if [[ "$admin_profiles_output" != *"alice (admin)"* ]] ||
  [[ "$admin_profiles_output" != *"bob (usuário)"* ]]; then
  fail "admin consulta perfis cadastrados"
fi

CURRENT_USER_ID="$alice_id"
printf '1\nAlice Example\n\n2\nalice-gh\n\n3\nEstudante\nAprendiz colaborativa.\n.fim\n\n0\n' |
  users_edit_profile >/dev/null
assert_equals "Alice Example" "$(db_query "SELECT display_name FROM users WHERE id = $alice_id;")" \
  "participante define nome de exibição"
assert_equals "alice-gh" "$(db_query "SELECT github_username FROM users WHERE id = $alice_id;")" \
  "participante define usuário GitHub"
assert_equals "Estudante
Aprendiz colaborativa." "$(db_query "SELECT bio FROM users WHERE id = $alice_id;")" \
  "participante define biografia curta"
profile_output=$(users_profile_dashboard "$alice_id" alice)
if [[ "$profile_output" != *"Alice Example"* ]] ||
  [[ "$profile_output" != *"https://github.com/alice-gh"* ]] ||
  [[ "$profile_output" != *"Aprendiz colaborativa."* ]]; then
  fail "perfil mostra os dados editáveis do próprio participante"
fi
admin_profile_output=$(users_profile_dashboard "$alice_id" alice 1)
if [[ "$admin_profile_output" != *"Alice Example"* ]] ||
  [[ "$admin_profile_output" != *"https://github.com/alice-gh"* ]] ||
  [[ "$admin_profile_output" != *"Aprendiz colaborativa."* ]]; then
  fail "administrador consulta os campos do perfil"
fi
invalid_profile_output=$(printf '2\ncontém espaço\n\n0\n' | users_edit_profile 2>&1)
if [[ "$invalid_profile_output" == *"Informe apenas um usuário GitHub válido"* ]]; then
  :
else
  fail "perfil valida o usuário do GitHub"
fi

if auth_login alice 'senha-incorreta'; then
  fail "login rejeita senha incorreta"
fi
auth_login alice 'senha-segura-123'
assert_equals "$alice_id" "$CURRENT_USER_ID" "login restaura a identidade"

CURRENT_USER_ID="$alice_id"
commit_url="https://github.com/acme/simple/commit/abcdef0123456789"
exercise_submit TEST-01 "$commit_url" "ex00"
alice_first_slot="$(db_query "SELECT id FROM evaluation_slots WHERE status = 'open';")"
assert_equals "0" "$(points_balance "$alice_id")" "abrir slot consome um ponto"
assert_equals "github_commit" "$(db_query "SELECT content_kind FROM submissions WHERE id = 1;")" \
  "nova submissão registra referência GitHub"
assert_equals "$commit_url" "$(db_query "SELECT commit_url FROM submissions WHERE id = 1;")" \
  "nova submissão persiste URL de commit"
assert_equals "ex00" "$(db_query "SELECT delivery_directory FROM submissions WHERE id = 1;")" \
  "nova submissão persiste diretório do repositório"
assert_equals "Entrega referenciada no GitHub" "$(db_query "SELECT content FROM submissions WHERE id = 1;")" \
  "nova submissão não armazena código-fonte"
submission_output=$(submissions_menu)
if [[ "$submission_output" != *"aguardando avaliação"* ]] ||
  [[ "$submission_output" != *"$commit_url"* ]]; then
  fail "participante consegue acompanhar a própria entrega"
fi
if exercise_submit TEST-01 "https://example.com/acme/simple/commit/abcdef0123456789" "ex00" \
  >/dev/null 2>&1; then
  fail "submissão rejeita host que não seja GitHub"
fi
if exercise_submit TEST-01 "$commit_url" "../fora-do-repo" >/dev/null 2>&1; then
  fail "submissão rejeita diretório fora da raiz do repositório"
fi

if evaluation_record_review "$alice_first_slot" 5 "autoavaliação" >/dev/null 2>&1; then
  fail "sistema impede autoavaliação"
fi

CURRENT_USER_ID="$bob_id"
review_output=$(printf "2\nAjustar: use ' aspas\ns\n" |
  evaluation_review_prompt "$alice_first_slot")
if [[ "$review_output" != *"$commit_url"* ]] ||
  [[ "$review_output" != *"Diretório: ex00"* ]] ||
  [[ "$review_output" == *"minha solução"* ]]; then
  fail "avaliador recebe referência externa sem conteúdo de código"
fi
assert_equals "rejected" "$(db_query "SELECT status FROM submissions WHERE user_id = $alice_id;")" \
  "nota abaixo de 3 reprova a submissão"
assert_equals "2" "$(points_balance "$bob_id")" "avaliador recebe ponto mesmo ao reprovar"
CURRENT_USER_ID="$alice_id"
assert_equals "1" "$(progress_unread_notification_count)" \
  "pessoa avaliada recebe notificação de nova avaliação"

progress_output=$(printf '\n' | progress_menu 2>&1)
if [[ "$progress_output" != *"Nova avaliação de bob em TEST-01"* ]] ||
  [[ "$progress_output" != *"Feedback: Ajustar: use ' aspas"* ]]; then
  fail "progresso mostra notificação e feedback recebido"
fi
assert_equals "0" "$(progress_unread_notification_count)" \
  "abrir meu progresso marca notificações como lidas"
alice_review_id="$(db_query "SELECT id FROM reviews WHERE slot_id = $alice_first_slot;")"
response_output=$(printf "5\nConcordo com os pontos levantados.\n.fim\ns\n\n" |
  evaluation_response_prompt "$alice_review_id")
if [[ "$response_output" != *"Resposta registrada"* ]]; then
  fail "pessoa avaliada consegue responder pelo menu de progresso"
fi
assert_equals "5" "$(db_query "SELECT recipient_score FROM reviews WHERE id = $alice_review_id;")" \
  "pessoa avaliada atribui nota ao avaliador"
assert_equals "Concordo com os pontos levantados." \
  "$(db_query "SELECT recipient_feedback FROM reviews WHERE id = $alice_review_id;")" \
  "pessoa avaliada registra resposta ao feedback"
if evaluation_respond_to_review "$alice_review_id" 4 "Resposta duplicada" >/dev/null 2>&1; then
  fail "sistema impede responder a mesma avaliação mais de uma vez"
fi
CURRENT_USER_ID="$bob_id"
assert_equals "1" "$(progress_unread_notification_count)" \
  "avaliador recebe notificação da resposta"
CURRENT_USER_ID="$alice_id"
db_query "
  UPDATE reviews
  SET feedback = char(27) || ']52;c;payload' || char(7) || char(155) ||
                 'hostil' || char(10) || 'legado'
  WHERE slot_id = $alice_first_slot;
"
submission_output=$(submissions_menu)
if [[ "$submission_output" == *$'\033'* || "$submission_output" == *$'\233'* ]] ||
  [[ "$submission_output" != *"payloadhostil"* ]]; then
  fail "dados legados e alterados diretamente não injetam controles no terminal"
fi
if exercise_submit TEST-01 "$commit_url" "ex00" >/dev/null 2>&1; then
  fail "sistema exige pontos para abrir novo slot"
fi

CURRENT_USER_ID="$bob_id"
exercise_submit TEST-01 "$commit_url" "ex00"
bob_slot="$(db_query "SELECT id FROM evaluation_slots WHERE status = 'open';")"
CURRENT_USER_ID="$alice_id"
evaluation_record_review "$bob_slot" 4 "Aprovado"
assert_equals "1" "$(points_balance "$alice_id")" "avaliar outro usuário concede ponto"

exercise_submit TEST-01 "$commit_url" "ex00"
alice_retry_slot="$(db_query "SELECT id FROM evaluation_slots WHERE status = 'open';")"
CURRENT_USER_ID="$bob_id"
evaluation_record_review "$alice_retry_slot" 3 "Correção aprovada"
assert_equals "approved" "$(db_query "SELECT status FROM submissions WHERE user_id = $alice_id AND status = 'approved';")" \
  "nota mínima 3 aprova a submissão"
assert_equals "1" "$(db_query "SELECT COUNT(*) FROM submissions WHERE user_id = $alice_id AND status = 'approved';")" \
  "usuário só tem uma aprovação por exercício"
assert_equals "2" "$(points_balance "$bob_id")" "revisão da nova tentativa recompensa o avaliador"

if exercise_submit TEST-01 "$commit_url" "ex00" >/dev/null 2>&1; then
  fail "sistema impede nova submissão para exercício já aprovado"
fi

if evaluation_record_review "$alice_retry_slot" 5 "avaliação duplicada" >/dev/null 2>&1; then
  fail "sistema impede avaliar slot concluído mais de uma vez"
fi

assert_equals "3" "$(db_query "SELECT COUNT(*) FROM points_ledger WHERE entry_type = 'slot_opened';")" \
  "cada submissão cria apenas o débito do próprio slot"
assert_equals "3" "$(db_query "SELECT COUNT(*) FROM reviews;")" \
  "histórico registra as três avaliações"

assert_equals "3" "$(db_query "
  SELECT COUNT(*) FROM reviews r
  JOIN evaluation_slots slot ON slot.id = r.slot_id
  JOIN submissions s ON s.id = slot.submission_id
  WHERE s.user_id = $alice_id OR r.reviewer_id = $alice_id;
")" "histórico de progresso inclui avaliações recebidas e realizadas"

progress_output=$(printf '\n' | progress_menu 2>&1)
if [[ "$progress_output" == *"no such column"* ]] ||
  [[ "$progress_output" != *"HISTÓRICO DE AVALIAÇÕES"* ]] ||
  [[ "$progress_output" != *"MOVIMENTAÇÕES DE PONTOS"* ]]; then
  fail "tela de progresso renderiza histórico de avaliações e pontos sem erros SQLite"
fi

profile_output=$(users_profile_dashboard "$alice_id" alice)
if [[ "$profile_output" != *"TEST-01"* ]] ||
  [[ "$profile_output" == *"NEW-EX"* ]]; then
  fail "dashboard lista exercícios aprovados e não revela os demais"
fi

admin_adjust_points "$alice_id" "$alice_id" 1 "Crédito de teste"
assert_equals "1" "$(points_balance "$alice_id")" "ajuste administrativo soma ao saldo"
assert_equals "1" "$(db_query "
  SELECT COUNT(*) FROM admin_point_adjustments
  WHERE user_id = $alice_id AND admin_id = $alice_id AND reason = 'Crédito de teste';
")" "ajuste registra administrador e justificativa"
adjustment_history=$(points_history "$alice_id")
if [[ "$adjustment_history" != *"admin_adjustment"* ]] ||
  [[ "$adjustment_history" != *"Crédito de teste"* ]]; then
  fail "histórico de pontos inclui ajustes administrativos"
fi

db_query "INSERT INTO exercises (code, name, description) VALUES ('TEST-02', 'Segundo', 'Pontos admin');"
CURRENT_USER_ID="$alice_id"
exercise_submit TEST-02 "$commit_url" "ex00"
assert_equals "0" "$(points_balance "$alice_id")" \
  "saldo administrativo permite abrir slot de avaliação"
if admin_adjust_points "$alice_id" "$alice_id" -1 "Remoção inválida" >/dev/null 2>&1; then
  fail "ajuste administrativo não permite saldo negativo"
fi

admin_adjust_points "$alice_id" "$bob_id" 3 "Correção de pontuação"
assert_equals "5" "$(points_balance "$bob_id")" "admin pode ajustar pontos de outro usuário"
printf '3\n2\nAjuste de teste\ns\n0\n' | admin_manage_user "$bob_id" >/dev/null
assert_equals "3" "$(points_balance "$bob_id")" \
  "admin remove pontos pelo painel de usuários"
printf '1\nsenha-temporaria-123\nsenha-temporaria-123\ns\n0\n' |
  admin_manage_user "$bob_id" >/dev/null
assert_equals "1" "$(db_query "SELECT must_change_password FROM users WHERE id = $bob_id;")" \
  "senha temporária exige troca no próximo login"
CURRENT_USER_ID=""
CURRENT_USERNAME=""
change_password_output=$(printf '2\nbob\nsenha-temporaria-123\nnova-senha-segura-123\nnova-senha-segura-123\n' |
  users_authenticate)
if [[ "$change_password_output" != *"Senha atualizada."* ]]; then
  fail "login comunica a troca obrigatória da senha"
fi
assert_equals "0" "$(db_query "SELECT must_change_password FROM users WHERE id = $bob_id;")" \
  "usuário troca senha temporária antes de entrar"
CURRENT_USER_ID=""
if ! auth_login bob "nova-senha-segura-123"; then
  fail "login funciona com senha recém-alterada"
fi
CURRENT_USER_ID="$alice_id"
admin_archive_exercise TEST-01
assert_equals "archived" "$(db_query "SELECT status FROM exercises WHERE code = 'TEST-01';")" \
  "admin arquiva exercício"
assert_equals "3" "$(db_query "SELECT COUNT(*) FROM reviews;")" \
  "arquivar exercício preserva avaliações"
assert_equals "" "$(db_query "
  SELECT code FROM exercises WHERE code = 'TEST-01' AND status = 'available';
")" "catálogo exclui exercícios arquivados"
reactivation_cancel_output=$(printf '2\nn\n\n0\n' | admin_manage_exercise TEST-01)
if [[ "$reactivation_cancel_output" != *"Reativação cancelada."* ]] ||
  [[ "$(db_query "SELECT status FROM exercises WHERE code = 'TEST-01';")" != "archived" ]]; then
  fail "admin pode cancelar reativação"
fi
printf '2\ns\n\n0\n' | admin_manage_exercise TEST-01 >/dev/null
assert_equals "available" "$(db_query "SELECT status FROM exercises WHERE code = 'TEST-01';")" \
  "admin reativa exercício com confirmação"

CURRENT_USER_ID="$alice_id"
CURRENT_USERNAME="alice"
printf '0\r\n' >"$TEST_DIR/menu-input"
menu_choice=""
ui_read_line menu_choice "menu > " <"$TEST_DIR/menu-input"
assert_equals "0" "$menu_choice" "entrada CRLF é normalizada"

if menu_principal </dev/null >/dev/null 2>&1; then
  fail "menu encerra corretamente quando a entrada termina"
fi

session_output=$(printf '2\nalice\nsenha-segura-123\n0\n' |
  DB_PATH="$DB_PATH" bash "$ROOT_DIR/simple.sh")
if [[ "$session_output" != *"Acesso autorizado."* ]]; then
  fail "login persiste após iniciar o CLI em outro processo"
fi

printf 'OK: testes do SIMPLE CLI passaram.\n'
