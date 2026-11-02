# Revisão técnica do SIMPLE

## 1. Status

Fase 1 de análise concluída. Nesta Fase 2 foram executados os testes existentes,
criado `tests/test_phase2.py` e acrescentada a seção de resultados abaixo.
Nenhum código de aplicação, schema, configuração, permissão, arquivo de deploy
ou banco persistente foi alterado. As execuções foram somente locais; nada foi
verificado no servidor Oracle após `bbc70bb`.

## 2. Achados confirmados

| ID | Área | Severidade (crítica/alta/média/baixa) | Evidência (arquivo:linha) | Descrição | Correção provável |
|---|---|---|---|---|---|
| A-01 | Autenticação / estado da conta | média | `modules/users.sh:66` — consulta apenas `id` e `password_hash`; `broker/simple_broker.py:908-913` — seleciona e rejeita `is_active = 0` | A autenticação local não consulta `users.is_active`; uma conta desativada pelo Broker pode ainda autenticar se o mesmo banco for acessado no modo local. O Broker, por outro lado, bloqueia esse login. | Decidir se o estado inativo deve valer em todos os modos; se sim, incluir a verificação no login local e cobri-la com teste isolado. |
| A-02 | Persistência / imutabilidade | média | `docs/CLI.md:22` — “A referência não pode ser editada depois do envio”; `broker/simple_broker.py:65-67` — API oferece `submissions.create/list/get`, sem atualização; `database/schema.sql:40-53` — tabela sem guarda de atualização | A interface/API não oferece edição, mas o schema não impede `UPDATE` de conteúdo ou referência de submissão. A imutabilidade documentada é de interface, não uma invariável do banco. | Decidir se a promessa deve ser garantida também contra atualizações diretas; se sim, adicionar proteção de banco compatível com legados e teste de tentativa de alteração. |
| A-03 | Robustez / protocolo | baixa | `broker/simple_broker.py:1799-1803` — serializa e escreve resposta sem limite; `broker/simple_proxy.py:32,205-210` — proxy limita resposta a 131.072 bytes | O Broker não limita o tamanho das respostas. Consultas de histórico/listagem sem paginação podem produzir respostas grandes; o Proxy detecta o excesso só depois de receber dados do Broker. | Definir tamanho máximo e paginação para listagens/históricos; aplicar limite também no Broker e testar o caminho de resposta excedente. |
| A-04 | Migração local / recuperação | alta | `core/database.sh:48-68` — executa schema e ALTERs em chamadas sucessivas; `database/schema.sql:143-165` — substitui `trg_submission_validate`; `broker/simple_broker.py:236-284` — valida colunas e FKs, não triggers | O trigger garante exercício disponível, saldo suficiente e ausência de outra submissão pendente/aprovada. Se a inicialização local parar após o DROP, o trigger pode ficar ausente; a validação do Broker não verifica a presença dele. Isso pode permitir submissão sem saldo ou duplicada se esse banco for usado pelo Broker antes de uma nova inicialização. O impacto está limitado ao caminho local não transacional; migrações explícitas do Broker são transacionais e fazem backup. | Tornar a alteração do trigger atômica no caminho local e verificar invariantes de schema no Broker; manter migrações aditivas, com backup documentado. |
| A-05 | Interface / documentação operacional | baixa | `modules/broker_ui.sh:1015` — “o proxy SO_PEERCRED não está implementado”; `broker/simple_proxy.py:40-45,335-346` — proxy implementa credenciais do kernel e trata requisições | A tela administrativa de vínculos UID contém uma mensagem obsoleta e contradiz o proxy implementado e a documentação atual. | Remover ou atualizar a mensagem na interface e incluir uma asserção de texto/fluxo no teste da TUI. |
| A-06 | Segurança de terminal | média | `broker/simple_broker.py:856-863,977-985` — rejeita controles C0 e DEL, mas não C1; `modules/broker_ui.sh:29-33,137-141` — imprime perfil e feedback via `ui_wrap_text`; `core/ui.sh:132-161` — imprime sem sanitização | Campos persistidos do Broker aceitam U+0080–U+009F e a TUI os imprime. Entradas legadas ou alteradas diretamente no SQLite também não passam por sanitização de renderização. | Validar os controles definidos pelo contrato (incluindo C1 e TAB), converter TAB conforme o requisito e sanitizar de novo na camada de renderização. |

Não foi confirmado achado crítico. A-04 foi elevado de média para alta após
confirmar que o trigger removido sustenta regras essenciais de saldo e
submissão; o risco está condicionado a interromper a migração local e reutilizar
o banco antes de uma inicialização completa. A-06 é risco médio de controle de
terminal, confirmado pela diferença entre os controles validados e os
renderizados.

## 3. Divergências entre documentação e código

- **Proxy implementado, mas descrito como não implementado na TUI:** a mensagem
  em `modules/broker_ui.sh:1015` conflita com `broker/simple_proxy.py:40-45` e
  com a topologia descrita em `docs/BROKER_PROXY.md:6-15`.
- **Imutabilidade da submissão:** `docs/CLI.md:22` afirma que a referência não
  pode ser editada. Não existe método de atualização na allowlist do Broker
  (`broker/simple_broker.py:65-67`), mas também não há trigger/constraint que
  impeça alteração direta no SQLite (`database/schema.sql:40-53`).
- **Migração local descrita como aditiva:** `docs/CLI.md:47` descreve alterações
  aditivas aplicadas por `db_init`; o schema também substitui um trigger
  existente (`database/schema.sql:143-145`). As colunas são verificadas antes
  de adicionar, mas a execução local não é atômica nem versionada
  (`core/database.sh:48-68`). A migração explícita do Broker tem backup e
  transação (`broker/simple_broker.py:350-386`), portanto os dois modos têm
  garantias diferentes.
- **Estado da conta entre modos:** o Broker rejeita login de conta inativa
  (`broker/simple_broker.py:908-913`); o login local seleciona apenas ID e hash
  (`modules/users.sh:66`) e não verifica `is_active`.

## 4. Suposições não verificadas

- **SQL injection local:** nos fluxos revisados, valores textuais passam por
  `db_sql_quote` (`core/database.sh:87-91`), e alguns campos numéricos são
  validados antes da interpolação. Não encontrei um caminho confirmado de
  injeção, mas não foi executada uma matriz completa de entradas hostis.
- **Senha:** `ui_read_line` usa `read -r -s` (`core/ui.sh:98`); no Broker, os
  valores são lidos de stdin pelo cliente (`core/broker_client.py:17-24`), não
  passados como argumento. Não foi feita análise de memória/processos ou
  instrumentação do terminal.
- **SO_PEERCRED / socket:** o Proxy obtém UID/GID/PID do kernel
  (`broker/simple_proxy.py:40-48`), valida a allowlist e o vínculo. O Broker
  aceita a operação interna somente de conexões cujo UID é o próprio UID do
  serviço (`broker/simple_broker.py:1768-1784`). Processos maliciosos sob o
  mesmo UID do serviço estão explicitamente fora da fronteira documentada.
- **Umask e janela de criação:** o Proxy aplica `umask(0o117)` ao criar o
  socket e depois define grupo/modo (`broker/simple_proxy.py:457-463`). O
  Broker aplica `chmod(0600)` após o bind (`broker/simple_broker.py:1963`); o
  diretório pai é validado como privado (`broker/simple_broker.py:1842-1868`).
  Não foi identificada exposição entre UIDs diferentes por essa janela sob as
  permissões validadas; a configuração manual e o sistema operacional não
  foram exercitados.
- **Saída de terminal:** reclassificada como risco confirmado na Fase 2. O
  validador do Broker não rejeita C1 (`broker/simple_broker.py:856-863`) e
  `ui_wrap_text` não sanitiza conteúdo (`core/ui.sh:132-161`); perfis e feedback
  são exibidos por outros usuários em algumas telas
  (`modules/broker_ui.sh:29-33,137-141`). C0/DEL é rejeitado em entradas
  usuais do Broker e o conteúdo legado de submissão é filtrado no CLI local
  (`modules/evaluations.sh:76-79`), mas isso não cobre C1, dados legados em
  outros campos ou alterações diretas no banco.
- **Confinamento SSH / isolamento do host:** não foi testado em host real. A
  própria documentação deixa essas tarefas fora da implementação atual
  (`docs/DEPLOYMENT_UBUNTU.md:302-318`).
- **SQL injection local:** continua como hipótese não demonstrada; as queries
  revisadas usam `db_sql_quote` para texto (`core/database.sh:87-91`), mas a
  matriz de entradas malformadas ainda não foi exercitada.

## 5. Lacunas de teste

Lacunas remanescentes após os testes adicionados na Fase 2:

| Regra/cenário sem cobertura suficiente | Teste sugerido | O que deve validar |
|---|---|---|
| Login local de conta desativada | `test_local_login_rejects_inactive_user` | Conta `is_active=0` não autentica em modo local; o banco deve ser temporário. |
| Entradas locais com aspas, `;`, `--`, quebras de linha e controles | `test_local_text_inputs_are_quoted_and_control_safe` | Dados ficam como dados, não executam SQL, e validações rejeitam controles conforme o campo. O caso de aspas em feedback existente não cobre a matriz completa (`tests/test_cli.sh:367-373`). |
| Limites de URL, SHA e diretório em fronteiras e caracteres inválidos | `test_submission_rejects_malformed_commit_references_and_paths` | URL com host/estrutura incorretos, SHA fora de 7–64 hex, caminho absoluto, `..` em cada componente, barras invertidas e controles são recusados tanto no modo local quanto Broker. O teste atual verifica host externo e `../` (`tests/test_cli.sh:350-369`). |
| Sequências de controle em texto armazenado/renderizado | `test_tui_does_not_emit_untrusted_terminal_controls` | Conteúdo vindo de perfil, exercício, feedback e histórico não produz ESC/OSC ou controles perigosos na saída do terminal; definir explicitamente o tratamento de newline/tab permitidos. |
| Imutabilidade efetiva de submissão | `test_submission_update_is_rejected` | Se a regra for uma garantia de banco, uma atualização direta de URL/diretório/conteúdo após insert deve falhar; preservar submissões legadas. |
| Interrupção/falha durante `db_init` local | `test_local_migration_failure_is_retryable_without_partial_rules` | Uma falha entre operações não deixa trigger/regra ausente após recuperação; confirmar comportamento e política de backup usando apenas banco temporário. |
| Resposta de listagem/histórico muito grande | `test_broker_bounds_or_paginates_large_responses` | Broker limita/pagina resposta antes de serializar/escrever; Proxy e cliente recebem erro explícito e não consomem memória sem limite. |
| Migração e backup local | `test_local_migration_requires_or_creates_backup` | Validar a decisão de produto para backup local e o comportamento em migração de banco legado; nunca usar `database/simple.db`. |
| Mensagem obsoleta da TUI sobre SO_PEERCRED | `test_uid_binding_tui_does_not_claim_proxy_peer_credentials_are_unimplemented` | Teste criado na Fase 2; falha intencionalmente enquanto a mensagem permanecer na tela. |

Já há cobertura relevante para allowlist, autenticação e autorização,
validação de campos do Broker, foreign keys, permissões de arquivos/sockets,
migração/backup explícitos do Broker, aprovação em 3+, autoavaliação, reserva
concorrente de slot, testes funcionais CLI e, agora, submissão concorrente e
rollback em `tests/test_phase2.py`. A concorrência de reserva de slot existente
não substituía esses testes, adicionados e executados na Fase 2.

## 6. Backlog priorizado

### Segurança

| ID | Item | Esforço | Dependências |
|---|---|---:|---|
| S-01 | Definir e alinhar o bloqueio de contas inativas nos modos local, Broker e Proxy. | P | Decisão sobre o suporte esperado ao modo local em ambiente multiusuário. |
| S-02 | Escolher se a imutabilidade da submissão é apenas regra da interface ou invariável do banco; se for invariável, proteger UPDATE sem apagar dados legados. | M | Decisão de produto e compatibilidade com bancos existentes. |
| S-03 | Especificar tratamento de controles em todos os campos renderizados; testar conteúdo persistido, não apenas entradas recentes. | M | Inventário dos caminhos de renderização e decisão sobre caracteres permitidos. |

### Correção de regras

| ID | Item | Esforço | Dependências |
|---|---|---:|---|
| R-01 | Fazer a autenticação local respeitar `is_active`, caso se confirme que a regra se aplica ao modo local. | P | S-01. |
| R-03 | Confirmar/garantir imutabilidade de submissões em nível de banco, se exigida pela regra. | M | S-02. |

### Cobertura de testes

| ID | Item | Esforço | Dependências |
|---|---|---:|---|
| T-03 | Adicionar testes de entradas hostis para SQL local, URL/SHA/diretório, e estado de conta em todos os modos. | M | S-01; decisões sobre regras de entrada. |
| T-04 | Testar renderização de texto com sequências de controle e conteúdo legado. | M | S-03. |
| T-05 | Testar respostas Broker extensas e limite/paginação ponta a ponta. | M | R-04. |
| T-06 | Exercitar falha/retomada da migração local e sua política de backup com banco temporário. | M | R-05. |
| T-07 | Remover a mensagem obsoleta da TUI; o teste já existe e falha enquanto ela persistir. | P | R-06. |

### Robustez

| ID | Item | Esforço | Dependências |
|---|---|---:|---|
| R-04 | Definir limite de resposta e estratégia de paginação para listagens/históricos do Broker; limitar antes da serialização. | M | Decidir limite e compatibilidade da API Broker. |
| R-05 | Definir tratamento de migração local: backup documentado/obrigatório, transação quando viável e recuperação após falha; manter alterações aditivas. | M | Decidir se modo local é suportado com dados importantes; revisão de compatibilidade SQLite. |
| R-06 | Corrigir o texto obsoleto no menu administrativo de vínculos UID. | P | Nenhuma. |

## 7. Restrições a preservar

- Preservar a fronteira Broker/Proxy: protocolo fechado, sem SQL genérico; o
  Broker é o único componente SIMPLE que acessa SQLite no modo Broker/Proxy.
- Preservar identidade do Proxy derivada de `SO_PEERCRED`, vínculo administrado,
  autenticação SIMPLE por senha e autorização revalidada pelo Broker.
- Não apresentar o Proxy/Broker como confinamento SSH, sandbox ou isolamento
  contra processos sob o mesmo UID do serviço.
- Migrações somente aditivas, com backup documentado e validação; não executar
  migração em banco persistente sem autorização e procedimento apropriado.
- Usar sempre banco temporário nos testes; não ler, inicializar ou alterar
  `database/simple.db` durante validações.
- Nenhuma mudança de permissão de produção sem passar por
  `docs/DEPLOYMENT_UBUNTU.md` e revisão operacional.
- Manter compatibilidade retroativa do protocolo; evoluções devem coordenar
  Broker, Proxy e cliente.
- Continuar sem baixar ou executar código submetido.

## Fase 2 — resultados

### Linha de base LOCAL

- Ambiente: Linux WSL2 (`uname`: kernel 6.18.54.1); UID efetivo 1000; Python
  3.14.4; SQLite 3.46.1; Bash 5.3.9; OpenSSL 3.5.5.
- Suíte existente, com `PYTHONDONTWRITEBYTECODE=1`:
  `python3 -m unittest tests/test_broker.py tests/test_proxy.py tests/test_deployment.py`
  seguido de `bash tests/test_cli.sh`. Os testes Broker/Proxy criam `TemporaryDirectory`
  (`tests/test_broker.py:37-42`, `tests/test_proxy.py:32-47`); o CLI usa
  `mktemp -d` (`tests/test_cli.sh:5-8`).
- Resultado Python: **46 testes, 45 passaram, 0 falharam, 1 pulou**; tempo
  reportado pelo unittest: **32,948 s**. O teste multi-UID real é exclusivo de
  root (`tests/test_proxy.py:454-456`), por isso foi pulado neste ambiente UID
  1000. O teste CLI terminou com `OK: testes do SIMPLE CLI passaram`; a medição
  do shell foi de 1 s (relógio de segundos inteiros). Total medido: **34 s**.
- Instabilidade: não observada nesta execução. Houve logs de rejeição de JSON
  inválido, desconexão e indisponibilidade do Broker, correspondentes a cenários
  exercitados; não foram falhas da suíte.
- Este é exclusivamente o resultado **LOCAL**. O estado no servidor Oracle
  após o commit `bbc70bb` continua pendente e independente.

### Auditoria da Parte B

| ID | Item | Veredito | Evidência | Severidade | Observação |
|---|---|---|---|---|---|
| B-01 | Hash de senha | risco | `core/auth.sh:3-12,15-35` — `openssl passwd -6`; `modules/users.sh:66-74` — login local; `broker/simple_broker.py:555-587,893-915` — geração/verificação e `secrets.compare_digest`; `broker/simple_proxy.py:258-267` — Proxy encaminha o login | baixa | O hash é SHA-512 crypt (`-6`). A geração não especifica salt ou rounds; delega o salt ao OpenSSL e usa seus defaults. Na verificação o salt é extraído do hash armazenado. Broker compara com `secrets.compare_digest`; Bash usa `[[ computed == stored ]]`, sem garantia de comparação em tempo constante. Hash/verify local ocorre em `core/auth.sh`; no Broker, em `simple_broker.py`; Proxy não calcula hash. Senhas são lidas com `read -s` (`core/ui.sh:98`) e, no cliente Broker, chegam por stdin e seguem pelo socket (`core/broker_client.py:17-29`), não como argumentos de processo. |
| B-02 | Tentativas de login | risco | `broker/simple_broker.py:902-918` — cada `auth.login` consulta e verifica senha sem contador; `broker/simple_proxy.py:258-267` — encaminha login; `modules/users.sh:51-75` — verificação local | média | Não encontrei limite, atraso progressivo, bloqueio ou rate limit no CLI, Broker ou Proxy. A política mínima de tamanho de senha não limita tentativas. |
| B-03 | Tamanho/tempo de requisição | risco | `broker/simple_broker.py:27,1725-1746,1808-1817` — 65.536 bytes, timeout 10 s e limite 64 conexões; `broker/simple_proxy.py:32-35,333-354,401-410` — mesma requisição máxima/timeout e semáforo de conexões; `core/broker_client.py:27-38` — timeout 25 s | média | Broker/Proxy leem até `MAX_REQUEST_BYTES + 1`; ambos exigem newline final. JSON truncado com newline é rejeitado pelo parser. EOF sem newline gera resposta explícita de inválida se bytes chegaram. Timeout de leitura no Broker é logado e a conexão fecha sem resposta; no Proxy é tratado no caminho de erro. Clientes lentos podem ocupar conexões até o timeout de 10 s. |
| B-04 | SQLite local | risco | `core/database.sh:76-84` — `db_query` aplica `PRAGMA foreign_keys=ON`; `broker/simple_broker.py:608-609` — conexão Broker também aplica `busy_timeout=5000`; `core/database.sh:26-31` — introspecção/ALTER sem PRAGMA | baixa | O modo local não define `PRAGMA busy_timeout`; chamadas separadas de `sqlite3` podem falhar prontamente sob lock. Foreign keys são ativadas em cada `db_query`; chamadas diretas de introspecção/ALTER em `db_ensure_column` não as ativam. Broker define ambos por conexão. |
| B-05 | Transação de submissão | confirmado | `broker/simple_broker.py:1063-1090` — `BEGIN IMMEDIATE`, insert e `commit`; `database/schema.sql:167-178` — trigger grava débito e abre slot; `modules/exercises.sh:442-451` — um `db_query` de INSERT local | — | Broker mantém submissão e efeitos de trigger na mesma transação explícita `BEGIN IMMEDIATE`. Local não declara `BEGIN IMMEDIATE`, mas executa o INSERT em uma chamada SQLite; os triggers `AFTER INSERT` fazem débito e slot como parte da mesma instrução/transação implícita. |
| B-06 | Transação de avaliação | confirmado | `broker/simple_broker.py:1221-1248` — `BEGIN IMMEDIATE`, insert e `commit`; `database/schema.sql:197-214` — atualiza slot/submissão e premia avaliador; `modules/evaluations.sh:28-32` — um `db_query` para inserir review | — | No Broker, avaliação e efeitos do trigger ficam na transação explícita. No modo local, um único INSERT em uma invocação SQLite executa o trigger sob transação implícita; não há `BEGIN IMMEDIATE` explícito. |
| B-07 | `trg_submission_validate` e DDL/migrações | risco | `database/schema.sql:143-165` — DROP/CREATE do trigger que verifica disponibilidade, saldo e submissão ativa; `core/database.sh:48-68` — schema seguido por ALTERs separados; `broker/simple_broker.py:372-386` — transação explícita; `simple_broker.py:236-284` — valida colunas/FKs, não triggers | alta | Se a execução local parar após o DROP, o trigger não estará presente até próxima inicialização completa. Sem ele, o Broker também não detecta essa ausência na validação de schema. O modo explícito de migração do Broker tem backup e rollback atômico, mitigando esse caso. DDL do schema: 8 `CREATE TABLE IF NOT EXISTS`: `exercises`, `users`, `submissions`, `evaluation_slots`, `reviews`, `points_ledger`, `admin_point_adjustments`, `identity_bindings` (`database/schema.sql:3-108`); 8 `CREATE INDEX IF NOT EXISTS`: `idx_identity_bindings_active_uid`, `idx_identity_bindings_active_user`, `idx_points_welcome_once`, `idx_points_slot_cost_once`, `idx_points_review_reward_once`, `idx_submissions_user_exercise`, `idx_slots_status`, `idx_admin_adjustments_user` (`110-133`); 5 `CREATE TRIGGER`: `trg_users_initial_point`, `trg_submission_validate`, `trg_submission_open_slot`, `trg_review_validate`, `trg_review_complete` (`136-214`); 1 `DROP TRIGGER IF EXISTS trg_submission_validate` (`143`). Não há `DROP TABLE`/`ALTER TABLE` no schema. Migrações local e Broker têm 16 `ALTER TABLE ADD COLUMN` idempotentes por verificação de coluna (não versionadas): `exercises.statement_path`, `.statement`, `.example`, `.terminal_model`, `.hints`; `users.is_admin`, `.must_change_password`, `.display_name`, `.github_username`, `.bio`, `.is_active`; `evaluation_slots.accepted_by`, `.accepted_at`; `submissions.content_kind`, `.commit_url`, `.delivery_directory` (`core/database.sh:53-68`, `broker/simple_broker.py:35-53`). |
| B-08 | Limites de resposta e paginação | risco | `broker/simple_broker.py:27,1799-1803` — máximo só para requisição, sem limite de resposta no Broker; `broker/simple_proxy.py:32,205-210,393-395` — limite 131.072 bytes; `core/broker_client.py:31-38` — limite 131.072 bytes | média | Requisição: 65.536 bytes no Broker e Proxy. Resposta: Proxy e cliente limitam a 131.072 bytes; Broker não tem constante nem limite próprio. O Proxy recusa respostas excedentes sem truncar, mas o Broker já pode materializar/serializar resultados grandes. Não há paginação/cursor. Listagens sem `LIMIT` e máximo atual por endpoint: `catalog.list` (exercícios disponíveis), `submissions.list` (submissões do usuário), `evaluations.slots.list` (slots abertos elegíveis), `evaluations.history` (reviews do usuário), `points.history` (ledger/ajustes), `profile.get` (`completed_exercises`), `progress.get` (históricos e exercícios aprovados), `admin.identity.list`, `admin.users.list`, `admin.exercises.list`, `admin.submissions.list`, `admin.evaluations.list` e `admin.points.history`: sem máximo de linhas da aplicação; todos usam resultados completos/`fetchall`. `admin.identity.get` tem `LIMIT 1` (`simple_broker.py:1337`). |
| B-09 | UPDATEs em submissões | confirmado | `database/schema.sql:206-210` — `UPDATE submissions SET status`; busca no código de aplicação só encontrou essa atualização | — | Único UPDATE de `submissions` é do trigger de conclusão da avaliação e muda apenas `status` para approved/rejected. Sem fluxo existente atualizando conteúdo, referência ou proprietário. Conforme decisão da Fase 2, colunas imutáveis devem ser `content`, `content_kind`, `commit_url`, `delivery_directory`, `user_id` e `exercise_id`; estado/status e campos de fluxo permanecem atualizáveis. Nenhum trigger foi escrito. |
| B-10 | Renderização de texto | risco | `core/ui.sh:132-161,195-224` — wrappers não sanitizam; `broker/simple_broker.py:856-863,977-985` — valida C0/DEL mas deixa C1; `modules/evaluations.sh:76-93` — sanitiza somente submissão legada local | média | Inventário: perfil (username, display name, GitHub, bio, data, saldo e exercício concluído) em `modules/users.sh:237-275`, `modules/broker_ui.sh:21-35,714-779`; exercício (nome/título, descrição, missão, diretório, arquivos, funções, exemplo, modelo e dicas) em `modules/exercises.sh:250-367`, `modules/broker_ui.sh:232-296,490-635` (catálogo e administração); submissões (autor, exercício, status, data, URL, diretório, conteúdo legado, slot, nota e feedback) em `modules/evaluations.sh:49-93,125-160`, `modules/broker_ui.sh:300-410,1015-1100`; progresso, histórico de avaliação e pontos/detalhes em `modules/progress.sh:18-83`, `modules/points.sh:31-71`, `modules/broker_ui.sh:75-158,858-918,1058-1096` (inclui telas administrativas). Esses caminhos enviam campos a `ui_wrap_text`/`ui_code_block`, sem sanitização geral na renderização (`core/ui.sh:132-161,195-224`). O legado de submissão remove C0/DEL somente no prompt local e Broker (`modules/evaluations.sh:76-79`, `modules/broker_ui.sh:399`); não remove C1 nem protege as demais telas. Entrada recente de perfil rejeita controles C0/DEL em alguns campos, mas o validador `_text` do Broker também deixa C1; alterações diretas/legadas no DB contornam validação de escrita em todos esses caminhos. URL de commit passa regex ASCII na submissão local (`modules/exercises.sh:413`) e Broker (`broker/simple_broker.py:1055`); diretório é validado contra traversal, mas ainda pode vir de dados legados/alterados e renderizado diretamente. |
| B-11 | `deploy/` e systemd | confirmado | `deploy/systemd/simple-broker.service:8-32`; `simple-proxy.service:3-34`; `deploy/init-production-database.sh:15-56`; `deploy/wait-for-broker.sh:4-25`; `docs/DEPLOYMENT_UBUNTU.md:35-47,116-140,302-318` | — | Broker: `simple-broker:simple-broker`, `UMask=0077`, `StateDirectory=simple`/0700, `RuntimeDirectory=simple-private`/0700, DB e socket sob paths autorizados. Proxy: UID `simple-broker`, grupo primário `simple-users`, `UMask=0117`, `RuntimeDirectory=simple-public`/0710, socket 0660 via código, escrita apenas no diretório público. Ambos incluem `NoNewPrivileges`, `PrivateTmp`, `PrivateDevices`, `ProtectSystem=strict`, `ProtectHome`, proteções de kernel, `RestrictSUIDSGID`, `LockPersonality` e `RestrictAddressFamilies=AF_UNIX`; não incluem, por exemplo, `SystemCallFilter`, `ProtectProc`, `MemoryDenyWriteExecute` ou `RestrictNamespaces`. Proxy declara `Requires`, `BindsTo`, `PartOf`, `After` e preflight para o Broker, consistente com Broker→Proxy. Inicializador exige root, usuário/grupo, diretório 0700, recusa DB existente, inicializa e valida DB 0600 via Broker one-shot. Wait-for-broker faz até 100 tentativas. Não encontrei divergência material entre esses scripts/unidades e o procedimento documentado: ownership/modos, ordem, pré-inicialização e ausência de configuração SSH estão alinhados. |

### Estado dos testes novos

| Teste | Estado | Evidência/resultado |
|---|---|---|
| T-01 `test_concurrent_submissions_cannot_overdraw_or_duplicate_active_attempt` | passou | `tests/test_phase2.py:150-229`: 20 repetições concorrentes por modo Broker e local; saldo não negativo e limites de submissão ativa, slot e débito verificados. |
| T-02 `test_failed_submission_or_review_rolls_back_all_side_effects` | passou | `tests/test_phase2.py:231-422`: triggers de teste abortam slot/recompensa; confirma rollback de submissão, débito, slot, review, status e recompensa nos modos Broker e local. |
| T-07 `test_uid_binding_tui_does_not_claim_proxy_peer_credentials_are_unimplemented` | falhou, como esperado | `tests/test_phase2.py:425-457` recebeu a tela real de vínculos; a afirmação obsoleta aparece em `modules/broker_ui.sh:1015`. Falha mantida, sem skip/xfail ou correção da aplicação. |

Execução dos novos testes: `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest
tests/test_phase2.py` — **3 testes, 2 passaram, 1 falhou**, duração 2,605 s.
O resultado não altera a linha de base da suíte existente.

## Fase 3 — resultados

### 3.0 — Verificação de senha em argv

- **Concluído sem alteração da implementação:** o modo local passa a senha por
  stdin tanto ao gerar quanto ao verificar o hash (`core/auth.sh:11-12,31-33`);
  o Broker também chama `openssl` por nome, passa a senha pelo argumento
  `input=` e usa `-stdin` (`broker/simple_broker.py:555-587`). Não há caminho
  absoluto hardcoded para o executável; o `PATH` seleciona o comando.
- O teste novo `test_password_is_passed_to_openssl_stdin_not_argv_in_local_and_broker`
  injeta shim de `openssl`, faz registro e login local e Broker, e confirma
  quatro invocações, `-stdin`, senha integral em stdin e senha ausente de argv
  (`tests/test_phase3.py:38-68,76-128`). Resultado: **passou**. Nenhuma
  implementação precisava ser alterada; portanto não houve uma falha vermelha
  causada pelo comportamento atual nem correção funcional neste item.
- **Suíte completa até 3.0:** comando Python
  `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_broker.py tests/test_proxy.py tests/test_deployment.py tests/test_phase2.py tests/test_phase3.py`
  — **50 testes: 48 passaram, 1 falhou e 1 foi pulado**, 35,502 s. A única
  falha é o T-07 intencional ainda pendente do item 3.1; o pulado é o teste
  multi-UID exclusivo de root (`tests/test_proxy.py:454-456`). Comando CLI
  separado `bash tests/test_cli.sh` — passou (`OK: testes do SIMPLE CLI
  passaram`). Ambos usam bancos temporários.
- **Riscos remanescentes:** apenas o texto obsoleto que mantém T-07 vermelho
  dentre os itens já executados; limites de tentativas, migrações, respostas,
  renderização, imutabilidade e os demais itens da Fase 3 ainda não foram
  tratados.

| Item | Estado | Teste vermelho → verde | Evidência |
|---|---|---|---|
| 3.0 — Senha em argv | concluído | Não foi necessário alterar código: o novo teste passou imediatamente após validar registro/login nos dois modos. | `core/auth.sh:11-12,31-33`; `broker/simple_broker.py:555-587`; `tests/test_phase3.py:76-128` |
| 3.1 — Mensagem TUI (R-06/T-07) | concluído | Vermelho: T-07 falhou primeiro pela frase obsoleta. Verde: mesma tela não contém a frase antiga e confirma positivamente a descrição correta. | `modules/broker_ui.sh:1015`; `tests/test_phase2.py:425-463` |
| 3.2 — Migração local/Broker (A-04/R-05) | parado | Validação exploratória: execução concorrente acusou `database is locked`; Broker permaneceu ativo com `trg_submission_validate` ausente. | `core/database.sh:48-68`; `broker/simple_broker.py:236-284,1960-1962`; tentativa em `tests/test_phase3.py` |
| 3.3 — Login local de conta inativa (S-01/R-01) | concluído | Vermelho: usuário inativo autenticava. Verde: login recusa com mensagem genérica. | `modules/users.sh:51-79`; `tests/test_phase3.py:133-166` |
| 3.4 — Tentativas de login no Broker (B-02) | pendente | — | — |
| 3.5 — Imutabilidade no banco (S-02/R-03) | pendente | — | — |
| 3.6 — Limites/paginação (R-04) | pendente | — | — |
| 3.7 — Controles de terminal (S-03/T-04) | pendente | — | — |
| 3.8 — Reforço de concorrência (T-01) | pendente | — | — |

### 3.1 — R-06 + T-07

- Teste atualizado primeiro e reproduzido vermelho pela antiga afirmação
  (`tests/test_phase2.py:456-457`); após a correção, passou com verificação da
  ausência do texto obsoleto e presença da nova mensagem
  (`tests/test_phase2.py:456-463`).
- Correção restrita ao texto de vínculos UID: “O proxy identifica o UID real
  com SO_PEERCRED e exige vínculo ativo.” (`modules/broker_ui.sh:1015`). O
  proxy obtém as credenciais do kernel e resolve o vínculo ativo
  (`broker/simple_proxy.py:40-48,221-230`).
- **Suíte completa:** `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_broker.py tests/test_proxy.py tests/test_deployment.py tests/test_phase2.py tests/test_phase3.py`
  — **50 testes, 49 passaram, 0 falharam, 1 pulou**, 33,774 s. O pulado é o
  teste multi-UID exclusivo de root (`tests/test_proxy.py:454-456`).
  `bash tests/test_cli.sh` passou. Tudo usa recursos temporários.
- **Riscos remanescentes:** o item 3.2 está parado conforme explicado abaixo;
  os itens 3.4–3.8 ainda não foram executados. Não há regressões observadas.

### 3.2 — A-04 / R-05: migração local atômica e validação de triggers

- **Parado antes de alterar implementação.** A validação exploratória mostrou
  que uma chamada local concorrente pode falhar com `database is locked`
  (`core/database.sh:26-31,48-68`), e que o Broker inicia mesmo após remover
  `trg_submission_validate`: o processo permaneceu ativo até o timeout. A
  inicialização de serviço chama `validate_database()` mas a verificação atual
  cobre colunas/FKs, não triggers (`broker/simple_broker.py:236-284,1960-1962`).
- O teste de falha de schema inserida entre `DROP TRIGGER` e `CREATE TRIGGER`
  não reproduziu trigger ausente no código atual; portanto não prova atomicidade
  da sequência posterior de ALTERs. As tentativas exploratórias não foram
  mantidas como testes finais e nenhum patch/commit foi criado para este item.
- **Motivo da parada:** satisfazer em conjunto a migração indivisível, o teste
  de falha no meio dos ALTERs, a corrida com submissões e a validação de cinco
  triggers requer uma decisão de arquitetura/backup e excede o limite aproximado
  de 150 linhas de diff definido para esta fase. A escolha pendente é se `db_init`
  deve reutilizar a migração transacional do Broker (que cria backup em bancos
  existentes) ou receber um caminho transacional local compartilhado, sem
  backup automático por inicialização. A atualização de validação ainda deve
  exigir os cinco triggers também no Broker.
- **Resultado dos testes exploratórios:** rollback após erro entre DROP/CREATE
  manteve o trigger no caso testado; corrida mostrou uma submissão aceita e a
  outra falhando com `database is locked`; inicialização do Broker sem o trigger
  não foi recusada. O teste de corrida e de inicialização falharam pelos motivos
  acima. Sem commit no item 3.2.
- **Risco pendente:** integridade de regras depende da presença dos triggers;
  falta uma verificação explícita em startup. A idempotência dos outros quatro
  triggers usa `CREATE TRIGGER IF NOT EXISTS`
  (`database/schema.sql:136,167,180,197`), então a redefinição futura de seus
  corpos não chega a bancos já existentes sem migração que os substitua.
  Recomenda-se `PRAGMA user_version` para migrações locais versionadas, mas a
  implementação fica pendente junto com a decisão arquitetural acima.

### 3.3 — S-01 / R-01: login local de conta inativa

- **Vermelho:** `test_local_login_rejects_inactive_user` registrou e desativou
  conta em DB temporário; antes da correção, `auth_login` retornou sucesso
  (`tests/test_phase3.py:133-166`).
- **Verde:** o mesmo teste passou após `auth_login` incluir `is_active` na
  consulta e recusar conta inativa com a mesma mensagem genérica de credenciais
  inválidas (`modules/users.sh:51-79`). A verificação da senha continua sendo
  feita antes da decisão de estado da conta.
- **Suíte completa:** `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests/test_broker.py tests/test_proxy.py tests/test_deployment.py tests/test_phase2.py tests/test_phase3.py`
  — **51 testes: 50 passaram, 0 falharam, 1 pulou**, 34,241 s. O pulado é
  multi-UID/root (`tests/test_proxy.py:454-456`). `bash tests/test_cli.sh`
  passou. Os testes usam DB temporário.
- **Risco remanescente:** login local continua sendo modo individual/dev/teste,
  conforme decisão desta fase; limitação de tentativas do Broker (3.4) ainda
  está pendente. Nenhuma regressão observada.
