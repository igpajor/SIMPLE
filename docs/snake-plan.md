# Snake multiplayer no SIMPLE — plano técnico e prompt para o Copilot

*Rascunho para validação antes da implementação · 10/11/2026 · Parte 1 (plano) pode ser salva como `docs/SNAKE_PLAN.md`; Parte 2 é o prompt para colar no Copilot.*

## 1. Ponto de partida

O SIMPLE não é um site: é uma CLI em Bash acessada por SSH, com Broker/Proxy em Python (sockets Unix) e SQLite. Por isso o snake.io original (canvas + WebSocket + mouse) não se aplica. O jogo será uma **TUI em terminal (curses)** com **servidor autoritativo em Python**, mantendo o desenho do SIMPLE: nada de portas de rede novas, só sockets Unix.

Como Bash não sustenta um loop de renderização em tempo real, o cliente do jogo será um programa Python lançado pelo `simple.sh` a partir de um novo item de menu.

**Adaptações em relação ao snake.io**

| snake.io | Versão SIMPLE (terminal) |
| --- | --- |
| Movimento em ângulo livre, mouse | Grade discreta, 4 direções (setas, WASD ou hjkl) |
| Mapa enorme, centenas de jogadores | Arena única (ex.: 120×60), alvo de 32 humanos + bots |
| Boost gastando massa | Boost (Espaço) gasta 1 segmento a cada N ticks e solta comida |
| Morte vira orbs de comida | Igual: parte do corpo vira comida |
| Skins | Cor + glifo por jogador (fallback para 8 cores) |
| Placar global | Top 10 na tela + ranking persistido no SQLite |
| Minimapa | Minimapa opcional em ASCII (fase final) |

## 2. Decisões de arquitetura (propostas, a validar na Fase 0)

- **D1 — Processo separado.** O servidor do jogo roda como processo próprio (`snake_server.py`, asyncio), e não dentro do Broker. Motivo: um loop de 10 Hz dentro do Broker pode atrasar operações críticas (submissões, avaliações). Trade-off: precisa de unit systemd próprio e de um canal estreito com o Broker.
- **D2 — Servidor autoritativo, estado em memória.** O cliente só envia intenções (virar, boost). Nada de posição vinda do cliente. O SQLite é tocado apenas para ranking, e somente via Broker (o contrato atual de acesso ao banco não muda).
- **D3 — Só biblioteca padrão, Python 3.9+.** Sem dependências novas. Evitar `match`, `X | Y` em tempo de execução e outros recursos acima de 3.9.
- **D4 — Protocolo NDJSON** (uma mensagem JSON por linha) sobre socket Unix.
- **D5 — Identidade pelo mecanismo que o SIMPLE já usa** (vínculo UID Linux ↔ conta SIMPLE). O servidor deriva quem é o jogador; nunca confia em nome enviado pelo cliente. A Fase 0 deve descobrir exatamente como o Proxy faz isso e reaproveitar.
- **D6 — Engine puro, separado de I/O.** Regras do jogo em módulo sem socket/curses, determinístico com semente. É o que torna o jogo testável.
- **D7 — Modo local.** `--solo` executa engine + bots em processo único, sem servidor. Serve para desenvolvimento, testes e uso individual, coerente com o papel do modo local.

```
  SSH (tty)
     |
  simple.sh  --(item de menu Jogos > Snake)-->  snake_client.py (curses)
                                                    |  NDJSON, socket Unix
                                                    v
                                              snake_server.py (asyncio, 10 Hz)
                                                    |  engine puro (regras, bots)
                                                    |  canal estreito
                                                    v
                                                 Broker --> SQLite (snake_scores)
```

## 3. Regras do jogo (v1, tudo configurável em `config.py`)

- Tick de 10 Hz. Cada célula é desenhada com 2 colunas (`██`) para parecer quadrada no terminal.
- Cada jogador tem uma cobra; só pode virar 90° (sem inversão de 180°); no máximo 1 virada aplicada por tick, com fila curta (2) para viradas rápidas.
- Comida: densidade-alvo de \~1 a cada 40 células; reposição contínua. Cada comida soma +1 segmento.
- Boost: só com comprimento mínimo (ex.: 6); move \~1,5× mais rápido; a cada 8 ticks em boost perde 1 segmento, que vira comida atrás.
- Colisão: cabeça contra corpo de outra cobra mata quem bateu; cabeça contra cabeça mata a menor (empate mata as duas); parede mata (decisão em aberto: parede mata ou teletransporta). Bater no próprio corpo é ignorado, como no snake.io (configurável).
- Morte: metade dos segmentos vira comida (valor 1–2). Quem matou ganha 1 kill.
- Respawn: posição livre aleatória com margem; invulnerabilidade de 3 ticks.
- Bots simples (desvio de parede/corpo, busca de comida próxima) mantêm a arena viva quando há poucos humanos.
- Placar por comprimento atual; recorde pessoal guardado ao morrer.

## 4. Estrutura de arquivos proposta

A Fase 0 deve ajustar isto às convenções reais do repositório (pastas existentes: `broker`, `config`, `core`, `database`, `deploy`, `docs`, `modules`, `tests`).

```
modules/snake/
  snake.sh                 # ponto de entrada Bash: checagens, mensagens em português, lança o cliente
  engine/
    config.py              # constantes e limites
    world.py               # estado: cobras, comida, índice espacial
    rules.py               # movimento, boost, colisão, morte
    bots.py                # IA simples
  server/
    snake_server.py        # loop asyncio, sessões, socket Unix
    protocol.py            # parse/validação/limites de mensagens
    identity.py            # identidade via mecanismo existente
    persistence.py         # cliente do Broker para ranking
  client/
    snake_client.py        # entrada curses
    render.py              # funções puras: estado -> células visíveis
    keys.py                # mapeamento de teclas
tests/snake/               # seguir o framework já usado em tests/
database/<padrão do repo>  # migração idempotente: snake_scores
deploy/simple-snake.service   # RASCUNHO, não instalar sem revisão
docs/SNAKE.md              # uso, regras, operação
docs/SNAKE_RECON.md        # resultado da Fase 0
```

## 5. Protocolo (NDJSON, campo `t` = tipo, versão `v`=1)

**Cliente → servidor**

- `hello`: `v`, `nick` opcional (apelido sanitizado), `cols`, `rows` (tamanho da viewport em células)
- `dir`: `d` em U, D, L, R
- `boost`: `on` booleano
- `resize`: `cols`, `rows`
- `ping`, `bye`

**Servidor → cliente**

- `welcome`: tamanho da arena, `tick_hz`, id do jogador
- `state`: `tick`, dados do jogador, cobras visíveis (corpo recortado à viewport + margem), comida visível, top 10
- `dead`: pontuação final, quem matou, posição no ranking
- `error`: código e mensagem curta

Regras: mensagem máxima de 1 KB do cliente; JSON inválido ou tipo desconhecido derruba a sessão após N faltas; caracteres de controle bloqueados na entrada (apelido) e na renderização; limite de \~30 mensagens/s por cliente.

## 6. Segurança e limites

- Sem porta TCP/UDP. Apenas socket Unix, com permissão restrita ao grupo dos participantes (espelhar o que o Proxy já faz).
- Uma sessão de jogo por conta SIMPLE; reconexão substitui a anterior.
- Cliente lento nunca trava o tick: fila de saída limitada, descartando snapshots antigos; passou do limite, desconecta.
- Inatividade de 30 s no menu/morto sem input: desconecta.
- Recortar o estado por viewport reduz tráfego e evita expor a arena inteira.
- O jogo não executa código de usuário nem lê arquivos do participante.
- Unit systemd (rascunho) com `NoNewPrivileges`, `ProtectSystem=strict`, `PrivateTmp`, `RuntimeDirectory`, `MemoryMax`, `CPUQuota`. **Não aplicar no servidor sem revisão e validação supervisionada**; o isolamento de produção não deve ser improvisado.

## 7. Persistência

- Tabela `snake_scores` (ex.: `user_id`, `best_length`, `kills_total`, `deaths_total`, `games`, `updated_at`), criada por migração **idempotente**, funcionando no banco local e no de produção, sem alterar tabelas existentes.
- Quem grava é o servidor do jogo (autoritativo), ao morrer o jogador, via Broker. O cliente nunca envia pontuação.
- Consulta de ranking pelo Broker com o mesmo padrão de limite e paginação já adotado nas respostas (limite por página e teto máximo).
- Recomendação: pontos do jogo **não** alimentam os pontos de aprendizagem do SIMPLE (decisão em aberto na seção 10).

## 8. Estratégia de testes (testes primeiro)

1. **Engine (unitários):** movimento, sem inversão, crescimento, matriz de colisões, custo do boost, drop na morte, reposição de comida, ranking, determinismo por semente.
2. **Propriedades/fuzz:** 10 mil ticks com entradas aleatórias mantêm invariantes (corpo contíguo, comprimento mínimo, comida dentro da arena, sem células duplicadas indevidas).
3. **Protocolo:** linhas malformadas, mensagem grande, tipos desconhecidos, caracteres de controle, excesso de taxa.
4. **Integração:** servidor em socket temporário com clientes falsos: entrar, mover, morrer, desconectar com limpeza, cliente lento sem travar o tick, uma sessão por conta.
5. **Cliente:** `render.py` testado sem curses (estado -> grade).
6. **Bash:** item de menu aparece, mensagem clara se faltar Python ou se o servidor não estiver ativo.
7. **Benchmark:** 40 cobras, 10 mil ticks; meta p99 do tick abaixo de 20 ms (orçamento de 100 ms) e uso baixo de CPU.

A suíte atual não pode regredir: registrar o baseline na Fase 0 e compará-lo ao final de cada fase.

## 9. Fases (o Copilot para ao fim de cada uma)

- **Fase 0 — Reconhecimento (somente leitura):** ler `plan.md`, `docs/`, `broker/`, `core/`, `modules/`, `tests/`, `deploy/`; entregar `docs/SNAKE_RECON.md` com convenções, autenticação, migrações, baseline de testes, divergências do plano e perguntas.
- **Fase 1 — Contrato e testes que falham:** `config.py`, spec do protocolo e testes do engine/protocolo escritos antes do código.
- **Fase 2 — Engine puro:** faz os testes da Fase 1 passarem; fuzz de invariantes.
- **Fase 3 — Bots, simulação headless e benchmark.**
- **Fase 4 — Servidor asyncio:** sessões, identidade, limites, testes de integração.
- **Fase 5 — Cliente curses e menu do `simple.sh`:** inclui `--solo`.
- **Fase 6 — Persistência:** migração, operações no Broker, ranking na interface.
- **Fase 7 — Deploy (rascunho):** unit systemd e `docs/SNAKE.md`; nada aplicado em produção.
- **Fase 8 — Polimento:** minimapa, kill feed, salas, aviso de recorde no chat global (se aprovado).

Trabalhar em branch `feature/snake`, commits pequenos, PR em rascunho.

## 10. Decisões em aberto (para você)

1. Parede mata ou teletransporta?
2. Pontos do jogo entram nos pontos de aprendizagem? (recomendo que não)
3. Bots ligados por padrão? Quantos?
4. Limite de jogadores simultâneos (proposta: 32)?
5. Anunciar recordes no chat global?
6. Quem pode jogar: todos os participantes ou só alguns perfis?
7. Aceita processo separado (D1) ou prefere dentro do Broker apesar do risco?

## 11. Prompt para o GitHub Copilot (Parte 2)

Cole o texto abaixo no Copilot (chat do agente ou issue atribuída ao Copilot), depois de commitar a Parte 1 como `docs/SNAKE_PLAN.md`.

```text
Você atuará como engenheiro sênior no repositório igpajor/SIMPLE (CLI em Bash + Broker/Proxy em Python por sockets Unix + SQLite, acessada por SSH).

OBJETIVO
Implementar um jogo Snake multiplayer em tempo real, inspirado no snake.io, como TUI em terminal (curses) com servidor autoritativo em Python, integrado ao menu do simple.sh. O plano técnico completo está em docs/SNAKE_PLAN.md. Leia-o inteiro antes de qualquer ação.

REGRAS INEGOCIÁVEIS
1. O código existente vence o plano. Se houver conflito, registre a divergência e pergunte; não improvise.
2. Não quebrar compatibilidade nem o contrato de segurança do SIMPLE. Não alterar comportamento de comandos, tabelas ou testes existentes.
3. Somente biblioteca padrão, Python 3.9+ (sem match/case, sem X | Y em tempo de execução). Nenhuma dependência nova.
4. Nenhuma porta TCP/UDP. Apenas sockets Unix, com permissões restritas, espelhando o que o Proxy já faz.
5. O servidor do jogo é autoritativo: o cliente envia só intenções (virar, boost). Identidade vem do mecanismo já existente (vínculo UID-conta SIMPLE), nunca de dados do cliente. O SQLite só é acessado via Broker.
6. Testes primeiro: escreva os testes (que devem falhar) antes da implementação de cada parte. Siga o framework de testes já usado em tests/.
7. Respeitar as convenções do projeto: bloqueio de caracteres de controle na entrada e na renderização; respostas do Broker com limite e paginação no padrão existente; mensagens ao usuário em português.
8. Migrações de banco idempotentes, funcionando no banco local e no de produção, sem tocar em tabelas existentes.
9. Deploy: produza apenas rascunhos (unit systemd, docs). NÃO aplique nada em produção e NÃO improvise isolamento de produção. Validação no servidor é supervisionada por mim.
10. Trabalhe na branch feature/snake, commits pequenos no estilo do histórico do repositório, PR em rascunho.

MODO DE TRABALHO
- Execute por fases (0 a 8, conforme docs/SNAKE_PLAN.md, seção 9).
- Ao fim de cada fase: rode a suíte completa, compare com o baseline, resuma o que foi feito, liste riscos e perguntas, e PARE. Só continue quando eu escrever 'continuar'.
- Não avance para a fase seguinte por conta própria.

TAREFA AGORA: FASE 0 (somente leitura, nenhum código de produção)
1. Leia: README.md, plan.md, docs/CLI.md, docs/DEPLOYMENT_UBUNTU.md e os diretórios broker/, core/, modules/, database/, deploy/, tests/ e simple.sh.
2. Rode a suíte de testes atual e registre o baseline (total, passou, pulou, falhas conhecidas).
3. Crie docs/SNAKE_RECON.md contendo:
   a. Como o Broker/Proxy autenticam e identificam o usuário (UID, sockets, permissões) e como o servidor do jogo pode reaproveitar isso.
   b. Convenções do repositório: estrutura de módulos, estilo Bash/Python, framework e padrão de testes, padrão de migrações, padrão de paginação e de mensagens.
   c. Onde e como plugar o item de menu Jogos > Snake no simple.sh sem afetar o fluxo atual.
   d. Impacto no deploy atual (usuários, grupos, sockets, systemd existentes).
   e. Divergências entre o plano e o código, com proposta de ajuste.
   f. Riscos e perguntas abertas, incluindo as decisões da seção 10 do plano.
4. Faça commit apenas de docs/SNAKE_RECON.md, pare e aguarde meu 'continuar'.

CRITÉRIOS DE ACEITE GLOBAIS (para o fim do projeto)
- Suíte existente sem regressões; testes novos cobrindo engine, protocolo, servidor, cliente (render) e Bash.
- Benchmark: 40 cobras a 10 Hz com p99 do tick abaixo de 20 ms.
- Cliente lento ou malicioso não trava o servidor nem os outros jogadores.
- Nenhum acesso direto ao SQLite fora do Broker; nenhuma porta de rede nova.
- docs/SNAKE.md explica uso, regras, operação e limites.
```
