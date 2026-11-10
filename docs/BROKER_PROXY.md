# SIMPLE Broker e Proxy Unix

## Fronteira de confiança

```text
UID Linux ──SO_PEERCRED──> Proxy Unix ──socket privado──> Broker ──> SQLite
                                  │
                                  └── UID -> vínculo ativo -> conta SIMPLE
```

O proxy usa Linux `SO_PEERCRED` em cada conexão; UID/GID/PID não são aceitos do cliente. O Broker resolve o vínculo ativo pela operação interna `simple-proxy-v1`, disponível somente no socket privado, cujo acesso é limitado ao UID do serviço. Essa operação não faz parte da allowlist pública. O proxy não abre o banco nem disponibiliza consultas SQL.

Vínculo é identificação, não autenticação: a pessoa ainda fornece usuário e senha SIMPLE. No login, o proxy só encaminha `auth.login` se o nome corresponder à conta vinculada ao UID do kernel. `auth.register` fica bloqueado; um administrador deve provisionar a conta e criar o vínculo antes do primeiro login. O proxy associa o token recebido a UID, ID da conta e ID do vínculo ativo. Para cada chamada seguinte, verifica novamente o vínculo e rejeita tokens de outra identidade ou de uma associação revogada/substituída. O Broker continua validando o token, a conta ativa e as permissões administrativas.

O serviço é concorrente, limitado a 64 conexões simultâneas, com timeout de 10 segundos e limite de 65.536 bytes por requisição. O Broker aplica o mesmo limite de handlers e dimensiona o backlog do socket privado para comportar o pico permitido pelo proxy; conexões excedentes não criam threads adicionais. As mensagens são JSON em uma linha; chaves repetidas e constantes inválidas são rejeitadas. Logs registram UID, método e resultado, nunca credenciais ou tokens. Os tokens e sua associação ao UID ficam em memória no proxy; após reiniciar o proxy, participantes devem fazer login novamente.

## Preparação de permissões

Use um UID de serviço dedicado e distinto dos participantes. Broker e proxy precisam rodar com esse mesmo UID: o socket privado e o diretório do banco são restritos ao serviço. Crie previamente um grupo para participantes e prepare os diretórios pelo procedimento administrativo do host, sem torná-los graváveis por esse grupo:

```bash
sudo groupadd --system simple-users
sudo install -d -o simple-broker -g simple-broker -m 700 /var/lib/simple
sudo install -d -o simple-broker -g simple-broker -m 700 /run/simple-private
sudo install -d -o simple-broker -g simple-users -m 710 /run/simple-public
sudo usermod -aG simple-users simple-broker
sudo usermod -aG simple-users PARTICIPANTE
```

Substitua `simple-broker` pelo usuário de serviço escolhido. O processo do proxy precisa iniciar com o grupo suplementar `simple-users` para poder atribuir esse grupo ao socket. Adicione cada participante ao grupo de acesso e faça com que as sessões do serviço e dos participantes sejam renovadas para receber os novos grupos. O diretório público deve pertencer ao serviço, usar o grupo configurado e permanecer sem escrita pelo grupo ou por outros. O proxy falha se essa condição não for satisfeita; não corrige permissões automaticamente.

O socket privado do Broker fica em diretório `0700` e é criado com modo `0600`. O socket público é criado com modo `0660`, pertencente ao UID do proxy e ao grupo configurado. O diretório do banco deve continuar privado (`0700` ou mais restrito), e o arquivo SQLite e seus auxiliares devem ser `0600`. Não coloque os sockets em diretórios compartilhados graváveis nem exponha o socket privado.

## Inicialização manual

Inicialize o banco apenas durante provisionamento controlado, com caminho dedicado e backup. Não use o banco persistente de desenvolvimento sem um plano de migração e backup:

```bash
sudo -u simple-broker python3 /opt/simple/broker/simple_broker.py \
  --socket /run/simple-private/broker.sock \
  --database /var/lib/simple/simple.db \
  --initialize-database
```

Depois da inicialização controlada, inicie o Broker sem `--initialize-database` nas próximas execuções:

```bash
sudo -u simple-broker python3 /opt/simple/broker/simple_broker.py \
  --socket /run/simple-private/broker.sock \
  --database /var/lib/simple/simple.db
```

Em outro terminal ou supervisor, usando o mesmo UID de serviço:

```bash
sudo -u simple-broker python3 /opt/simple/broker/simple_proxy.py \
  --socket /run/simple-public/proxy.sock \
  --broker-socket /run/simple-private/broker.sock \
  --socket-group simple-users
```

O grupo e os diretórios precisam existir antes de iniciar os serviços. Não execute esses comandos como configuração SSH: nenhum serviço SSH, regra global ou unidade de produção é criada por esta documentação. Preserve a ordem de inicialização: Broker primeiro, proxy depois.

## Provisionamento de contas e cliente

Crie/promova a conta administrativa e inicie uma sessão administrativa pelo modo Broker local, em contexto de manutenção autorizado a acessar o socket privado. Use essa sessão para criar contas e, no painel, **Administração > Vínculos UID Linux** para associar cada conta ao UID confirmado pelo administrador no sistema operacional. Não use UID informado por um participante como verificação de identidade; a tabela associa a identidade previamente validada pelo administrador. Revogue o vínculo quando a conta do host ou a autorização do participante mudar. Nunca disponibilize o modo Broker nem o socket privado aos participantes.

No terminal do participante:

```bash
SIMPLE_MODE=proxy \
PROXY_SOCKET=/run/simple-public/proxy.sock \
bash simple.sh
```

O fluxo de login continua solicitando usuário e senha SIMPLE. A opção de cadastro não cria uma conta neste modo; o participante deve contatar um administrador. Os modos `local` e `broker` permanecem disponíveis para seus usos anteriores.

## Logs e recuperação

O proxy envia logs ao stderr do processo (e portanto ao supervisor que o iniciou). Os eventos contêm UID, método e resultado; não registre stdin, JSON completo, senha, token ou resposta integral. Uma falha de Broker resulta em erro explícito ao cliente e não tenta acessar SQLite diretamente.

- **Broker indisponível:** uma nova instância do proxy não inicia se o socket privado do Broker não existir ou estiver inseguro. Se o Broker parar depois, requisições falham explicitamente; recupere primeiro o Broker e teste novamente.
- **Socket público obsoleto:** o proxy só remove um socket desconectado que pertence ao seu UID e está no diretório validado. Não substitui arquivos regulares, links ou sockets de outro proprietário.
- **Conta desativada/vínculo revogado:** a resolução em cada requisição nega o acesso. Revogar pelo painel preserva o registro para consulta. Uma nova associação recebe outro ID, portanto os tokens ligados ao vínculo anterior não voltam a funcionar.
- **Troca de senha:** o Broker revoga sessões antigas e devolve token rotacionado; o proxy substitui o vínculo do token em memória.
- **Reinício do proxy:** tokens de cliente precisam de novo login; tokens não são persistidos.
- **Falha de resposta após uma operação:** confirme o estado pela interface antes de repetir ações não idempotentes, como envio, avaliação ou ajuste de pontos.

## Execução supervisionada em Ubuntu

Os serviços systemd e o procedimento de preparação para Ubuntu 22.04 estão em
[DEPLOYMENT_UBUNTU.md](./DEPLOYMENT_UBUNTU.md), com arquivos em `deploy/systemd/`.
As unidades usam o mesmo usuário `simple-broker`; o Proxy executa com grupo
primário `simple-users` para criar o socket `0660`. O systemd prepara os
diretórios de runtime, inicia o Broker antes do Proxy e limita o acesso gravável
ao diretório de dados/socket necessário.

O `ExecStart` não passa `--initialize-database`: o banco é criado em operação
one-shot explícita antes de habilitar o serviço. `--initialize-database-only`,
`--migrate-database-only`, `--validate-database-only` e `--backup-database`
executam tarefas SQLite pelo próprio Broker e encerram sem abrir um socket de
serviço. Antes de migrar, o Broker valida o backup SQLite; o schema e as
migrações são aplicados em uma transação e falhas causam rollback, mantendo o
backup para recuperação. O banco persistente não deve estar no checkout Git.
SIGTERM/SIGINT encerram o servidor e removem apenas o socket que a instância
criou, sem tocar em socket substituto ou no banco.

## Validação e limitações

Os testes automatizados exercitam o `SO_PEERCRED` real do processo atual, resolução, revogação/reatribuição, autenticação por senha, autorização no Broker, bloqueio de campos de identidade declarados, protocolo, desconexão e concorrência. Também verificam inicialização sem servidor, recusa de banco existente, backup validado e remoção do socket em SIGTERM. Um teste adicional executa dois UIDs reais distintos quando a suíte roda como root; sem root, ele é ignorado e a validação multiusuário real continua pendente.

O proxy deve ser executado como o usuário confiável do serviço. Processos maliciosos que já executem sob esse mesmo UID podem acessar o socket privado e estão fora da fronteira de isolamento. A solução também depende da administração correta dos membros do grupo público e dos vínculos. A preparação systemd não implementa validação de TTY/SSH, confinamento de shell, alta disponibilidade nem auditoria de um host de produção. Portanto, esta implementação não declara o SIMPLE pronto para produção ou SSH público.

## Chat e salas

Nos modos Broker e Proxy, o chat oferece as salas **global**, **ajuda** e uma
sala para cada exercício disponível, além de conversas diretas entre duas
contas. Use `/salas` para consultar as salas, `/sala global`, `/sala ajuda` (ou `/sala help`) ou
`/sala exercise:SHELL00-EX00` para mudar de canal e `/dm usuario` para abrir
uma conversa privada. O Broker valida o acesso a cada pedido; conversas
diretas só podem ser lidas pelos dois participantes.

O protocolo continua sendo requisição/resposta JSON por socket Unix, sem
WebSocket. No terminal interativo, a tela do chat usa Python `curses`: o
histórico e o campo de entrada são atualizados em áreas separadas, sem limpar
a conversa nem inserir mensagens no meio do texto digitado. O restante da
interface SIMPLE permanece em Bash; o login interativo também usa uma tela
responsiva `curses`, mantendo autenticação e validações nas funções Bash. As
duas telas usam o módulo `curses` do Python 3, que precisa estar disponível no
sistema (em algumas distribuições, pelo pacote `python3-curses`). Sem TTY ou
sem suporte a `curses`, o login mantém o fluxo Bash e o chat usa seu leitor de
linha alternativo.

Por padrão, a atualização automática fica ativa com intervalo de dois segundos;
use `CHAT_AUTO_REFRESH=0` para desativá-la ou `CHAT_REFRESH_SECONDS` para ajustar
o intervalo. No topo do chat global, DMs não lidas são sinalizadas como
`+N DM [usuario]`, sem exibir o conteúdo da mensagem; F2 ou `/dm usuario` abre
a conversa. A DM abre em uma janela sobre a sala atual, preservando histórico,
rolagem e rascunho da sala ao fundo. Esc ou `/voltar` fecha a DM e retorna à
sala anterior; `/sair` encerra o chat. Backspace e setas editam o texto,
Page Up/Page Down navegam no histórico e Ctrl+C retorna ao menu. Em terminais
sem suporte à tela curses,
há um leitor de linha alternativo, com atualização automática no modo
interativo; sem TTY, o leitor não faz polling enquanto aguarda entrada.
A tela carrega as últimas dez mensagens por padrão, oferece `/historico` para
páginas anteriores e `/buscar termo` para pesquisar na sala atual. Horários
persistidos em UTC são convertidos ao fuso configurado no cliente. Autoria
própria e menções recebem destaque; um indicador temporário mostra quando outra
pessoa está digitando na mesma sala.

O menu principal mostra pessoas online, quantidade de mensagens não lidas e
menções junto à opção do chat. DMs aparecem como `+N DM` nessa opção, sem
exposição da prévia ou do conteúdo. No chat global, o nome do remetente aparece
sem o corpo da mensagem; o conteúdo só aparece ao abrir a conversa privada. A
leitura é persistida por conta e por sala. Mensagens anteriores à criação da
conta não entram na contagem de não lidas.

Mensagens têm até 500 caracteres, uma linha e sem caracteres de controle.
Menções usam `@usuario`; há reações `👍`, `❤️`, `😂` e `+1`, denúncia de
mensagens e avisos fixados por administradores. Administradores podem consultar
denúncias, ocultar mensagens e restaurá-las depois. A ocultação é reversível,
não remove o conteúdo do banco e não há expurgo automático. Comandos disponíveis:
`/ajuda`, `/quem`, `/salas`, `/sala`, `/dm`, `/historico`, `/buscar`, `/limpar`,
`/reagir ID reação` e `/denunciar ID motivo`. Para administradores:
`/relatorios`, `/ocultar ID`, `/restaurar ID`, `/fixar ID` e `/desafixar ID`.
Administradores também podem usar `/apagar-historico` dentro de uma sala para
apagar permanentemente todas as mensagens daquela sala. A interface exige
confirmação explícita; a exclusão não pode ser desfeita e remove também as
reações, menções e denúncias associadas. Outras salas não são afetadas.

A presença fica em memória no Broker e considera online quem fez uma
requisição autenticada nos últimos cinco minutos. O envio é limitado a uma
mensagem por conta a cada dois segundos. Reiniciar o Broker limpa sessões e
presença; será necessário entrar novamente.

Os valores padrão podem ser ajustados por ambiente. Configure as variáveis do
Broker em um drop-in systemd e as variáveis do cliente no ambiente da sessão
SSH. Valores inválidos do Broker impedem a inicialização do serviço:

| Variável | Serviço | Padrão | Limites |
|---|---|---:|---:|
| `SIMPLE_CHAT_PAGE_SIZE` | Broker | 50 | 1–100 |
| `SIMPLE_CHAT_ONLINE_WINDOW_SECONDS` | Broker | 300 | 30–86400 |
| `SIMPLE_CHAT_SEND_INTERVAL_SECONDS` | Broker | 2 | 1–3600 |
| `CHAT_AUTO_REFRESH` | Cliente | `1` (ativado) | `0` ou `1` |
| `CHAT_REFRESH_SECONDS` | Cliente | 2 | 1–60 |
| `CHAT_HISTORY_WINDOW` | Cliente | 10 | 1–100 |
| `CHAT_TIMEZONE` | Cliente | `America/Sao_Paulo` | Fuso reconhecido pelo sistema |

`CHAT_HISTORY_WINDOW` não pode exceder `SIMPLE_CHAT_PAGE_SIZE` se for necessário
exibir toda a janela em um único pedido ao Broker.

`/limpar` limpa somente a janela local e recarrega as mensagens recentes; não
apaga conteúdo do histórico.

Em uma instalação existente, atualize primeiro o checkout `/opt/simple` com o
usuário que o possui. Depois pare Broker e Proxy e faça a migração explícita do
banco com backup automático antes de reiniciar os serviços:

```bash
sudo systemctl stop simple-proxy.service simple-broker.service
sudo -u simple-broker python3 /opt/simple/broker/simple_broker.py \
  --database /var/lib/simple/simple.db \
  --migrate-database-only
sudo systemctl start simple-broker.service
sudo systemctl start simple-proxy.service
systemctl is-active simple-broker.service simple-proxy.service
```

Atualize também os arquivos do cliente antes de usar as salas e comandos novos.
O Proxy libera automaticamente as operações por meio da allowlist derivada de
`METHOD_FIELDS`. Assim, `users.search` e `users.profile.get` ficam disponíveis
quando Broker e Proxy estão executando a versão que declara esses métodos.
Se o Proxy responder `Operação não suportada pelo proxy`, atualize o checkout
do servidor e reinicie os dois serviços para recarregar o código importado.
Esses métodos não exigem migração do banco de dados.

Como `root`, pare os serviços; como `ubuntu`, atualize o checkout; depois, como
`root`, inicie Broker e Proxy nesta ordem:

```bash
sudo systemctl stop simple-proxy.service simple-broker.service
sudo -u ubuntu git -C /opt/simple pull --ff-only origin main
sudo systemctl start simple-broker.service
sudo systemctl start simple-proxy.service
systemctl is-active simple-broker.service simple-proxy.service
```

Consulte [Atualização pelo Git](./DEPLOYMENT_UBUNTU.md#atualização-pelo-git)
para validar o checkout e revisar alterações antes de aplicá-las.
