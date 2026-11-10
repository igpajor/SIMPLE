# Reconhecimento do Snake multiplayer

## Escopo e fonte do plano

Esta é a Fase 0: reconhecimento, sem alterações no código da aplicação e sem
intervenções em produção. A branch de trabalho é `feature/snake`.

O caminho indicado no pedido, `docs/SNAKE_PLAN.md`, não existe nesta checkout.
O plano encontrado e lido integralmente é `docs/snake-plan.md`, atualmente não
rastreado pelo Git. Ele contém as mesmas fases, decisões e prompt da tarefa.
Este relatório não renomeia nem inclui esse arquivo no commit.

## Autenticação, identidade e IPC atual

O SIMPLE tem modos `local`, `broker` e `proxy`. No servidor multiusuário, o
launcher SSH força a execução de `simple.sh` em um terminal interativo; o
Proxy recebe a conexão pelo socket Unix público e obtém UID/GID/PID do kernel
com `SO_PEERCRED`. Não aceita identidade declarada pelo cliente. O Broker
resolve o UID em `identity_bindings`, exigindo vínculo ativo e conta SIMPLE
ativa. No Proxy, o login SIMPLE também verifica a senha; as sessões têm prazo
de validade e ficam vinculadas ao UID, ao usuário SIMPLE e ao vínculo ativo.

Topologia e permissões documentadas:

| Recurso | Proprietário/grupo | Modo |
|---|---|---:|
| `/var/lib/simple/simple.db` | `simple-broker:simple-broker` | `0600` |
| `/run/simple-private` e `broker.sock` | `simple-broker:simple-broker` | `0700` / `0600` |
| `/run/simple-public` | `simple-broker:simple-users` | `0710` |
| `/run/simple-public/proxy.sock` | `simple-broker:simple-users` | `0660` |

O Proxy e o Broker rodam atualmente com o mesmo UID de serviço. O Proxy pede a
resolução de identidade ao Broker pelo protocolo interno `simple-proxy-v1`;
essa operação só é aceita em conexão cujo UID de peer seja o UID efetivo do
Broker. A allowlist pública do Proxy é derivada de `METHOD_FIELDS` e exclui
`auth.register`. O Broker mantém as sessões SIMPLE e é o componente normal da
aplicação que abre SQLite.

**Implicação para o servidor do jogo:** o Broker original só aceitava chamadas
internas do próprio UID de serviço, e seu socket privado não é acessível a outro
usuário. Na Fase 4 foi implementado um canal separado e opcional, sem alterar
o socket privado existente: protocolo `simple-snake-v1`, socket configurado
por `--snake-socket`, e UID/GID dedicados informados ao Broker. O diretório do
canal deve ser previamente criado, pertencer ao Broker, ter o GID do serviço
Snake e modo `2710` (setgid, sem escrita do grupo); o socket é
publicado como `0660` e herda esse GID. O serviço Snake só é
autorizado por `SO_PEERCRED` e a única operação aceita é `identity.resolve`,
que reaproveita a validação de vínculo Linux ativo e conta SIMPLE ativa do
Broker. A interface de persistência e ranking ainda não existe; a Fase 6
precisará de operações específicas, limitadas e autorizadas, nunca de SQL
genérico.

O Broker não observa diretamente o peer do participante conectado ao Snake:
o processo do jogo retransmite somente o UID obtido do kernel, e deve ser
tratado como componente confiável. A validação da integração usa sockets
temporários; nenhuma unidade systemd, usuário/grupo de produção ou permissão
em servidor foi alterada. O provisionamento e a configuração desse canal
continuam pendentes para a fase de deploy supervisionada.

## Convenções e padrões existentes

### Organização e interfaces

- `simple.sh` carrega funções Bash de `core/` e `modules/`. Há modo local, com
  consultas por `core/database.sh`, e modos Broker/Proxy, com requisições via
  `core/broker_client.sh`; este último seleciona o socket de acordo com o modo.
- Os módulos Bash são organizados por área (`exercises.sh`, `evaluations.sh`,
  `users.sh`, entre outros). As telas Curses em Python ficam em módulos
  dedicados, como `home_tui.py`, `exercise_catalog_tui.py` e
  `account_dashboard_tui.py`; o Bash prepara configuração JSON, inicia o
  programa e valida o resultado retornado.
- `broker/simple_broker.py` é o dono das operações de negócio e da persistência.
  `broker/simple_proxy.py` implementa o endpoint Unix público, credenciais do
  kernel e encaminhamento da allowlist.
- As mensagens e erros apresentados ao participante são em português. Bash usa
  funções compartilhadas de UI; há sanitização de texto antes de renderizar.
  As telas Curses também filtram caracteres de controle. O protocolo é JSON por
  linha, com campos permitidos por operação.

### Testes

- A suíte Python usa `unittest` (`unittest.TestCase`), sem framework adicional.
  Os testes do Broker/Proxy usam banco e sockets temporários; testes de TUI
  dirigem processos/TTY de teste e verificam renderização e fluxos.
- Existem testes shell independentes `tests/test_cli.sh` e `tests/test_ui.sh`,
  executados com Bash e fixtures em diretórios temporários.
- O teste de integração com UIDs Linux distintos requer root. O teste
  `test_uid_binding_tui_does_not_claim_proxy_peer_credentials_are_unimplemented`
  falhou no baseline atual; detalhes estão na seção de baseline.

### Banco, migrações e respostas

- `database/schema.sql` define tabelas, índices, chaves estrangeiras,
  verificações e triggers com `IF NOT EXISTS`; há uma substituição explícita do
  trigger `trg_submission_validate`. `database/exercises.sql` popula o catálogo
  com `INSERT OR IGNORE`.
- No modo local, `db_init` aplica o schema e executa adições idempotentes de
  colunas, além da migração de reservas antigas de avaliação. Esse fluxo é
  separado do caminho de migração do Broker.
- O Broker tem uma lista `MIGRATIONS` para colunas e inicialização explícita do
  banco com transação e backup validado. Em produção o Broker valida o schema
  por padrão; não o migra automaticamente.
- Para respostas grandes, o cliente Python e o Proxy limitam a resposta a
  131.072 bytes. O Broker não aplica atualmente limite global equivalente ao
  serializar suas próprias respostas. A paginação existente mais clara é a do
  chat: página padrão de 50 mensagens, máximo configurável de 100 e navegação
  por IDs (`after_id`/`before_id`). Outros endpoints ainda retornam listas
  completas. O ranking do Snake deve ter limite/paginação explícitos no Broker,
  e limitar o resultado antes de serializá-lo.

## Baseline da suíte

Executado localmente em Linux, UID efetivo 1000, Python 3.14.4 e Bash 5.3.9,
antes de qualquer alteração neste relatório:

| Comando | Resultado |
|---|---|
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests` | 157 testes: 155 passaram, 1 falhou, 1 pulou |
| `bash tests/test_cli.sh` | Passou |
| `bash tests/test_ui.sh` | Passou |

A falha observada é
`test_uid_binding_tui_does_not_claim_proxy_peer_credentials_are_unimplemented`
em `tests/test_phase2.py`. O roteiro usa a sequência de opções do menu anterior;
no fluxo atual, a opção `5` abre o chat (outra opção no menu inicial para
administradores), e o teste termina sem alcançar a tela de vínculos UID. Assim,
a asserção que espera `VÍNCULOS DE IDENTIDADE LINUX` falha. Não foi alterado
código para contornar essa falha.

O teste pulado é
`test_real_distinct_uids_share_proxy_without_cross_identity_access`, que exige
root para assumir UIDs distintos. Os logs de rejeição e desconexão emitidos
durante a suíte correspondem a cenários exercitados; não foram falhas
adicionais. A documentação de CLI lista um conjunto Python menor; o baseline
acima usa descoberta para incluir todos os arquivos `test_*.py`.

## Integração do menu Jogos > Snake

Há dois fluxos de menu, que devem continuar distintos e compatíveis:

1. No modo local, `simple.sh` chama `menu_principal` de
   `core/navigation.sh` e despacha o `REPLY` num `case`. A opção de Snake deve
   ser incluída nas opções e no despacho do modo local.
2. Nos modos Broker e Proxy, `simple.sh` chama `broker_mode_menu` em
   `modules/broker_ui.sh`. Esse menu cria suas opções de forma dinâmica para
   distinguir administradores, já usa a TUI inicial compartilhada e tem um
   `case` separado. Também precisa expor Snake tanto com TTY/Curses quanto no
   fallback de linha de comando.

Recomenda-se um único ponto de entrada do Snake reutilizado por ambos os
despachos, sem mudar os números, autenticação, logout ou fluxos atuais além de
adicionar uma opção identificável. O cliente Curses deve ser iniciado como
processo Python separado; a entrada precisa explicar em português se Python,
TTY ou socket não estiver disponível. A opção `--solo` pode dispensar o
servidor, mas não deve inicializar ou acessar o banco diretamente.

## Impacto no deploy e operação

Atualmente existem apenas `simple-broker.service` e `simple-proxy.service`.
Ambos restringem `RestrictAddressFamilies=AF_UNIX`; o Proxy depende do Broker e
usa `/run/simple-public`, enquanto o Broker mantém `/run/simple-private` e
`/var/lib/simple`. O SSH restringe participantes do grupo `simple-users` a um
launcher com TTY, comando forçado e encaminhamentos desabilitados.

Não há usuário/grupo, socket, diretório RuntimeDirectory ou unidade de serviço
para Snake. Uma implantação futura provavelmente exigiria um serviço e um
socket Unix acessível pelos participantes, limites de recursos e uma
integração mínima com o Broker. Alterar usuários, grupos, permissões, serviços
ou configuração SSH afetaria produção e está fora desta Fase 0. Qualquer
unidade futura deve permanecer rascunho até revisão e validação supervisionada;
não se deve inferir nem aplicar isolamento que não tenha sido testado.

Há também uma exceção operacional já existente ao enunciado amplo “SQLite só
via Broker”: `deploy/bootstrap-production-admin.py` abre SQLite diretamente
durante o bootstrap administrativo, com serviços parados e execução sob o UID
de serviço. Os testes também manipulam diretamente bancos temporários. O
runtime do jogo pode e deve manter o acesso de persistência via Broker, sem
alterar esse procedimento de bootstrap; é necessário confirmar que essa é a
interpretação pretendida para o critério global.

## Divergências, propostas e perguntas abertas

1. **Nome/capitalização do plano:** somente `docs/snake-plan.md` existe e é não
   rastreado; `docs/SNAKE_PLAN.md` não existe. O conteúdo encontrado foi usado
   nesta Fase 0 e preservado. Confirmar o nome canônico antes da Fase 1; não
   renomear nem commitar o plano sem instrução.
2. **Servidor separado e resolução de identidade:** D1/D5 propõem processo
   independente, mas a operação interna atual do Broker só aceita o UID do
   próprio serviço. Proposta para avaliação, não decisão de implementação:
   manter processo separado e criar interface IPC Unix de capacidade mínima
   para uma conta de serviço Snake dedicada, sem conceder acesso ao socket
   privado existente nem ao arquivo SQLite. O Broker deve validar identidade e
   autorização para cada operação. Confirmar se o servidor pode ser o
   componente confiável que retransmite o UID obtido do peer do cliente.
3. **Regra “SQLite somente via Broker”:** o plano deve esclarecer se a regra é
   para todo o runtime do Snake/SIMPLE ou se preserva ferramentas administrativas
   offline já existentes. Proposta: proibir acesso SQLite no runtime do jogo e
   manter fixtures de teste e bootstrap administrativo como exceções explícitas.
4. **Contrato das respostas:** o limite do Broker não é hoje global e a
   paginação não é uniforme. Proposta: respostas de ranking estritamente
   limitadas e paginadas na operação Broker, com máximo testado; não depender
   apenas do limite do Proxy/cliente.
5. **Decisões do plano (seção 10), ainda sem resposta:** parede mata ou
   teletransporta; pontos de jogo afetam ou não pontos de aprendizagem
   (recomendação do plano: não); bots ligados por padrão e quantidade; limite
   simultâneo de jogadores (proposta: 32); anúncio de recordes no chat global;
   elegibilidade para jogar; e processo separado versus execução dentro do
   Broker. D1 também precisa ser compatível com a fronteira de identidade e
   serviço descrita acima.
6. **Outros limites a especificar antes da implementação:** grupo autorizado a
   conectar ao socket do jogo; política de uma sessão por conta e reconexão;
   tamanho/limites de protocolo e saída por cliente; retenção e desempate do
   ranking; e política de migração idempotente para banco local e de produção
   sem alterar tabelas existentes.

Não há mudança de código ou de produção nesta fase. A próxima fase deve aguardar
as decisões acima; especialmente, não implementar o canal de identidade nem
escolher política de jogo por suposição.
