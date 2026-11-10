# Contrato do protocolo do Snake

Este documento define a versão 1 do protocolo cliente-servidor para a Fase 1.
É NDJSON sobre socket Unix: cada mensagem é um único objeto JSON UTF-8 seguido
por LF. A identidade do jogador nunca é fornecida pelo cliente.

## Limites e validação

- Cada linha recebida tem no máximo 1.024 bytes, incluindo LF. Exige-se uma
  única linha completa; JSON inválido, chave duplicada, constantes JSON não
  finitas, objeto não-JSON, campos extras ou tipos incorretos são inválidos.
- A validação é estrita por tipo de mensagem: só os campos listados abaixo são
  aceitos. `bool` não é aceito como inteiro.
- O protocolo usa versão `1`, anunciada no `hello`; versões desconhecidas são
  recusadas.
- Um nome opcional (`nick`) é somente um rótulo visual, nunca identidade,
  username, autorização ou chave de persistência. Deve ter de 1 a 16 caracteres
  e rejeitar caracteres de controle e formato. Ausência de `nick` é válida.
- `cols` deve estar entre 10 e 120 células; `rows`, entre 5 e 60. O cliente
  informa células lógicas; cada célula de jogo ocupa duas colunas no terminal.
- A sessão é encerrada depois de três mensagens inválidas. O limite é 30
  mensagens por segundo por cliente. O servidor aplica esses limites sem
  bloquear o tick.
- Snapshots de saída são limitados a 131.072 bytes. A fila de saída comporta
  até dois snapshots; snapshots `state` antigos podem ser descartados. Cliente
  que não drena a fila dentro do limite operacional é desconectado.

## Cliente para servidor

| `t` | Campos além de `t` | Semântica |
|---|---|---|
| `hello` | `v: 1`, `cols`, `rows`, `nick` opcional | Primeiro pedido; cria/substitui a sessão do mesmo vínculo SIMPLE. |
| `dir` | `d: "U" \| "D" \| "L" \| "R"` | Intenção de direção; nunca envia posição. |
| `boost` | `on: boolean` | Solicita ativar/desativar boost. |
| `resize` | `cols`, `rows` | Atualiza a viewport. |
| `ping` | nenhum | Verifica atividade da sessão. |
| `bye` | nenhum | Encerra a sessão voluntariamente. |

O cliente não pode enviar `user_id`, `username`, pontuação, comprimento,
posição, corpo, comidas, resultado de colisão ou estado do mundo. Campos
desconhecidos, inclusive esses campos de identidade/estado, são erro de
protocolo. Identidade e autorização vêm do UID de peer (`SO_PEERCRED`) e do
vínculo ativo validado no Broker.

## Interface pura da engine

Os testes da Fase 1 fixam a interface pública da engine, sem I/O:

- `World(width, height, seed)` cria um mundo determinístico.
- `add_snake(player_id, body, direction)` adiciona uma cobra com o corpo
  ordenado da cabeça à cauda; retorna o estado mutável da cobra.
- `add_food(position, value=1)`, `set_direction(player_id, direction)`,
  `set_boost(player_id, enabled)` e `tick()` constroem/simulam a partida.
- `replenish_food()` preenche o alvo de uma comida por 40 células livres,
  usando a semente do mundo. `food` mapeia posição a valor.
- `snakes` é mapeado por ID do jogador; estado de cobra expõe `body`,
  `direction` e `alive`. `leaderboard(limit)` retorna registros com
  `player_id` e comprimento, em ordem decrescente de comprimento.
- Movimento e colisões são simultâneos por tick. Empates cabeça-a-cabeça matam
  ambas; em tamanhos diferentes sobrevive a maior. A cobra que bate no corpo
  de outra morre. A autocolisão não mata. Morte larga `floor(comprimento / 2)`
  segmentos como comidas de valor 1 ou 2.

## Servidor para cliente

As respostas são também objetos JSON por linha. `welcome` retorna `v`, o
identificador efêmero da sessão, dimensões da arena e frequência de tick.
As formas são:

- `{"t":"welcome","v":1,"session_id":string,"player_id":string,"arena":{"cols":int,"rows":int},"tick_hz":10}`.
  IDs são opacos e efêmeros, não revelam UID nem ID SIMPLE.
- `{"t":"state","tick":int,"player":{"id":string,"length":int,"alive":bool,"kills":int,"deaths":int,"boosting":bool},"snakes":[...],"food":[...],"leaderboard":[...]}`.
  Cada cobra visível tem `id`, `nick` (opcional), `body` como lista de pares
  `[x,y]` em células, `length`, `alive`, `color` e `glyph`. A lista de corpo é
  recortada à viewport mais margem. Cada comida tem `x`, `y` e `value`. O placar
  contém até dez registros `{rank,nick,length}`.
- `{"t":"dead","score":int,"length":int,"killer":string|null,"tick":int}`.
  `score` é o comprimento final; `killer` é o ID efêmero da cobra responsável,
  ou `null`.
- `{"t":"error","code":string,"message":string}`. Códigos estáveis incluem
  `invalid_request`, `unauthorized`, `capacity`, `version_unsupported` e
  `server_error`.

Coordenadas e dimensões são inteiros; nomes são texto sanitizado. `state` traz
somente cobras e comidas visíveis dentro da viewport mais margem, além do
placar. `dead` informa o resultado final e o autor da colisão, se aplicável.
`error` contém mensagem curta em português, sem stack trace nem dados internos.

O cliente trata tipos de servidor desconhecidos como erro de protocolo e não
renderiza texto recebido sem sanitização. Mensagens de servidor também ficam
sujeitas ao limite máximo acima.

## Regras v1 fixadas para o contrato

- Tick de 10 Hz; arena padrão 120 × 60; até 32 jogadores humanos e dois bots
  ativos por padrão.
- Paredes matam; pontos do Snake não alteram pontos de aprendizagem; recordes
  não são anunciados no chat global.
- Elegibilidade exige conta SIMPLE ativa e vínculo UID ativo. A arquitetura
  escolhida é servidor separado, com IPC Unix estreito autorizado pelo Broker.
- A cobra inicia com quatro segmentos; comida alvo de uma unidade por 40
  células; comida consumida soma um segmento.
- Boost exige seis segmentos, tem velocidade de 1,5× e solta um segmento a cada
  oito ticks ativos. Uma direção oposta imediata é recusada; no máximo duas
  viradas ficam enfileiradas e no máximo uma é aplicada por tick.
- Colisão com outra cobra segue as regras de corpo/cabeça do plano; empate de
  cabeças mata ambas. Colisão consigo mesma não mata na versão 1.
- Morte transforma metade do corpo em comida, com valores unitários de 1 ou 2;
  respawn tem três ticks de invulnerabilidade.
- Dimensões, limites e regras numéricas são centralizados em
  `modules/snake/engine/config.py`; alterações futuras devem ajustar testes e
  esta especificação no mesmo commit.
