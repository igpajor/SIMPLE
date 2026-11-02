# SIMPLE Broker e Proxy Unix

## Fronteira de confiança

```text
UID Linux ──SO_PEERCRED──> Proxy Unix ──socket privado──> Broker ──> SQLite
                                  │
                                  └── UID -> vínculo ativo -> conta SIMPLE
```

O proxy usa Linux `SO_PEERCRED` em cada conexão; UID/GID/PID não são aceitos do cliente. O Broker resolve o vínculo ativo pela operação interna `simple-proxy-v1`, disponível somente no socket privado, cujo acesso é limitado ao UID do serviço. Essa operação não faz parte da allowlist pública. O proxy não abre o banco nem disponibiliza consultas SQL.

Vínculo é identificação, não autenticação: a pessoa ainda fornece usuário e senha SIMPLE. No login, o proxy só encaminha `auth.login` se o nome corresponder à conta vinculada ao UID do kernel. `auth.register` fica bloqueado; um administrador deve provisionar a conta e criar o vínculo antes do primeiro login. O proxy associa o token recebido a UID, ID da conta e ID do vínculo ativo. Para cada chamada seguinte, verifica novamente o vínculo e rejeita tokens de outra identidade ou de uma associação revogada/substituída. O Broker continua validando o token, a conta ativa e as permissões administrativas.

O serviço é concorrente, limitado a 64 conexões simultâneas, com timeout de 10 segundos e limite de 65.536 bytes por requisição. As mensagens são JSON em uma linha; chaves repetidas e constantes inválidas são rejeitadas. Logs registram UID, método e resultado, nunca credenciais ou tokens. Os tokens e sua associação ao UID ficam em memória no proxy; após reiniciar o proxy, participantes devem fazer login novamente.

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
serviço. O banco persistente não deve estar no checkout Git. SIGTERM/SIGINT
encerram o servidor e removem apenas o socket que a instância criou, sem tocar
em socket substituto ou no banco.

## Validação e limitações

Os testes automatizados exercitam o `SO_PEERCRED` real do processo atual, resolução, revogação/reatribuição, autenticação por senha, autorização no Broker, bloqueio de campos de identidade declarados, protocolo, desconexão e concorrência. Também verificam inicialização sem servidor, recusa de banco existente, backup validado e remoção do socket em SIGTERM. Um teste adicional executa dois UIDs reais distintos quando a suíte roda como root; sem root, ele é ignorado e a validação multiusuário real continua pendente.

O proxy deve ser executado como o usuário confiável do serviço. Processos maliciosos que já executem sob esse mesmo UID podem acessar o socket privado e estão fora da fronteira de isolamento. A solução também depende da administração correta dos membros do grupo público e dos vínculos. A preparação systemd não implementa validação de TTY/SSH, confinamento de shell, alta disponibilidade nem auditoria de um host de produção. Portanto, esta implementação não declara o SIMPLE pronto para produção ou SSH público.
