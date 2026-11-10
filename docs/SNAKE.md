# Snake no SIMPLE

Snake é um jogo em grade para terminal, com modo solo e uma arena multiplayer
autoritativa. No multiplayer, o cliente envia somente intenções de direção e
boost; a posição e os resultados são calculados pelo servidor.

## Como jogar

No menu principal, escolha **Jogos > Snake**. Em modo local, o jogo inicia
automaticamente em solo, com bots. No modo Broker/Proxy, o cliente precisa do
socket Unix multiplayer configurado em `SNAKE_SOCKET`.

Controles:

- Setas, `WASD` ou `HJKL`: mudar a direção.
- Espaço: alternar o boost.
- `Q` ou `Esc`: sair.

O terminal deve ter pelo menos 32 colunas por 10 linhas; o minimapa lateral
aparece a partir de 48 colunas. Em terminais mais estreitos, as eliminações
recentes aparecem abaixo do campo. Aumente a janela se o cliente pedir. O campo
de jogo acompanha a cobra; recordes persistidos podem demorar até 30 segundos
para atualizar.

## Regras e limites

- Arena única de 120 × 60 células, com tick de 10 Hz.
- Multiplayer limitado a 32 pessoas, além de dois bots padrão.
- Todos compartilham a mesma arena; não há salas separadas. O HUD inclui um
  minimapa aproximado e até três eliminações recentes, sem avisos no chat global.
- As cobras usam cores variadas (ciano, verde, amarelo, azul, magenta e branco);
  comida é vermelha e vermelho não faz parte da paleta das cobras.
- A cobra inicia com quatro segmentos; comida aumenta o comprimento.
- O boost exige comprimento mínimo de seis segmentos e consome segmentos
  periodicamente.
- Paredes matam; a colisão com o próprio corpo não mata. O respawn tem três
  ticks de invulnerabilidade.
- A morte converte parte do corpo em comida. Resultados do jogo não concedem
  pontos de aprendizagem.
- Requer conta SIMPLE ativa e vínculo UID Linux ativo no multiplayer.
- Os recordes persistem melhor comprimento, eliminações, mortes e partidas.
  Só contas ativas com vínculo UID ativo aparecem no ranking persistido.
- Não há portas TCP/UDP; o jogo usa sockets Unix.
- Os corpos e as comidas continuam recortados à viewport; o minimapa transmite
  apenas posições grosseiras (grade de 20 × 10) para orientar a navegação.

## Modo solo

O modo local não requer Broker nem socket de servidor:

```bash
cd /opt/simple
SIMPLE_MODE=local /usr/bin/python3 -m modules.snake.client.client --solo
```

O modo solo é independente da conta SIMPLE e não grava recordes.

## Multiplayer e operação

O multiplayer depende de `simple-snake.service`, do socket privado dedicado de
identidade do Broker e do socket de jogo. O serviço Snake não abre SQLite:
registra resultados e consulta páginas limitadas do ranking por operações
restritas no Broker, autenticadas com `SO_PEERCRED`.

Os seguintes arquivos no repositório são **rascunhos para revisão**, não
instalação: [simple-snake.service](../deploy/systemd/simple-snake.service),
[30-snake.conf](../deploy/systemd/simple-broker.service.d/30-snake.conf) e
[simple-snake.conf](../deploy/tmpfiles.d/simple-snake.conf). Não foram
instalados nem aplicados em produção. O provisionamento de identidades,
permissões, `EnvironmentFile`, migração do banco e ativação requer validação
supervisionada; não habilite o multiplayer em um servidor sem essa revisão.

Para diagnóstico, consulte `journalctl -u simple-snake.service` e
`journalctl -u simple-broker.service`. Mensagens de conexão ou persistência
indisponível devem ser investigadas nos logs; o jogo não lê nem modifica o
banco diretamente.
