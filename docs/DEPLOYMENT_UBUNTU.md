# Preparação de implantação: Ubuntu 22.04

Este documento e os arquivos em `deploy/` preparam o SIMPLE para execução local
por `systemd` em uma VM Ubuntu 22.04. Eles não acessam a Oracle Cloud, não
alteram SSH/firewall/regras de rede, não criam contas SIMPLE e não habilitam
participantes. **Não libere participantes por SSH até concluir e testar a seção
"Acesso temporário pelo SSH" abaixo.** A preparação do Broker/Proxy sozinha não
confina sessões SSH.

## Arquitetura e fronteiras

```text
usuário Unix em simple-users
       | SO_PEERCRED / socket Unix público
       v
simple-proxy.service (UID simple-broker, socket 0660)
       | socket Unix privado
       v
simple-broker.service (UID simple-broker)
       | único componente da aplicação autorizado a abrir SQLite
       v
/var/lib/simple/simple.db
```

O Proxy obtém a identidade do kernel, resolve o vínculo UID Linux → conta SIMPLE
no Broker e encaminha somente operações da allowlist. O Broker segue
responsável pela autenticação, autorização e acesso SQLite. Ambos usam o mesmo
UID de serviço; participantes não recebem acesso ao socket privado ou ao banco.
O Proxy não abre portas TCP. Senhas e tokens não são registrados.

## Identidades e diretórios

| Caminho/recurso | Proprietário | Modo | Gerenciado por |
|---|---|---:|---|
| `/opt/simple` | `ubuntu` (deploy), somente leitura para serviços | `0755` | Git |
| `/var/lib/simple` | `simple-broker:simple-broker` | `0700` | systemd `StateDirectory` |
| `/var/lib/simple/simple.db` | `simple-broker:simple-broker` | `0600` | Broker |
| SQLite `-wal`, `-shm`, `-journal` | `simple-broker:simple-broker` | `0600` | SQLite via Broker |
| `/var/lib/simple/backups` | `simple-broker:simple-broker` | `0700` | provisionamento |
| `/run/simple-private` | `simple-broker:simple-broker` | `0700` | systemd `RuntimeDirectory` |
| `/run/simple-private/broker.sock` | `simple-broker:simple-broker` | `0600` | Broker |
| `/run/simple-public` | `simple-broker:simple-users` | `0710` | systemd `RuntimeDirectory` |
| `/run/simple-public/proxy.sock` | `simple-broker:simple-users` | `0660` | Proxy |
| `/etc/systemd/system/*.service` | `root:root` | `0644` | instalação administrativa |

O modo `0710` permite aos membros de `simple-users` atravessar o diretório sem
listar seu conteúdo nem criar/remover arquivos. O socket público concede
conexão ao grupo, mas não escrita no diretório. `RuntimeDirectory` recria os
diretórios sob `/run` em cada início; não é necessário um arquivo tmpfiles
separado. O Broker valida as permissões do banco e auxiliares e falha fechado.

## Provisionamento

Os comandos abaixo são procedimentos para execução futura no host, não foram
executados nesta tarefa.

### Como `ubuntu`: obter o código

O usuário `ubuntu` deve ter acesso Git SSH de leitura ao repositório privado,
configurado fora do repositório. Não coloque chaves, tokens ou arquivos de
credenciais no projeto.

```bash
sudo install -d -o ubuntu -g ubuntu -m 0755 /opt/simple
sudo -u ubuntu git clone git@github.com:igpajor/SIMPLE.git /opt/simple
```

Se `/opt/simple` já contém um checkout, não clone por cima; use o procedimento
de atualização abaixo.

### Como `root`: usuários, grupos e diretórios

Execute uma vez. Os comandos de criação são condicionais para permitir
verificação/reexecução sem substituir contas existentes:

```bash
getent group simple-users >/dev/null || groupadd --system simple-users
getent passwd simple-broker >/dev/null || \
  useradd --system --user-group --no-create-home \
    --home-dir /nonexistent --shell /usr/sbin/nologin simple-broker
usermod -aG simple-users simple-broker
install -d -o simple-broker -g simple-broker -m 0700 /var/lib/simple
install -d -o simple-broker -g simple-broker -m 0700 /var/lib/simple/backups
```

O usuário de serviço não deve ser usado para login SSH. A associação a
`simple-users` é necessária ao serviço Proxy; não adicione participantes nesta
etapa. Participantes futuros não devem pertencer a grupos administrativos nem
receber `sudo`. Confirme a conta de serviço e seus grupos:

```bash
getent passwd simple-broker
id simple-broker
stat -c '%U:%G %a %n' /var/lib/simple /var/lib/simple/backups
```

### Como `root`: instalar e inicializar

Instale as unidades e carregue a configuração sem iniciar serviços:

```bash
install -o root -g root -m 0644 \
  /opt/simple/deploy/systemd/simple-broker.service \
  /etc/systemd/system/simple-broker.service
install -o root -g root -m 0644 \
  /opt/simple/deploy/systemd/simple-proxy.service \
  /etc/systemd/system/simple-proxy.service
systemctl daemon-reload
```

Inicialize o banco **uma única vez e explicitamente**, ainda antes de habilitar
ou iniciar os serviços:

```bash
bash /opt/simple/deploy/init-production-database.sh
```

O procedimento exige `root`, recusa serviço Broker ativo, caminho de dados com
proprietário/modo diferentes do esperado, banco existente (inclusive link
simbólico) e arquivos auxiliares SQLite preexistentes. Ele executa o mecanismo
one-shot do Broker como `simple-broker`, verifica proprietário e modo do arquivo
e pede ao Broker uma verificação de schema, integridade SQLite e chaves
estrangeiras. Se o banco já existir, o procedimento para sem alterá-lo. Não
copie `database/simple.db` nem inicialize o banco durante atualização/boot.

O comando one-shot equivalente, se executado manualmente pelo administrador,
é:

```bash
runuser -u simple-broker -- /usr/bin/python3 \
  /opt/simple/broker/simple_broker.py \
  --database /var/lib/simple/simple.db \
  --schema /opt/simple/database/schema.sql \
  --initialize-database-only
```

`--initialize-database-only` recusa banco ou arquivos auxiliares existentes,
valida o resultado e encerra sem criar socket ou iniciar servidor. Para um
upgrade de schema deliberado, após revisão e backup, use
`--migrate-database-only`; ele cria um backup antes de migrar e também encerra
sem iniciar o serviço. O `ExecStart` do systemd não usa nenhuma dessas opções.

### Como `root`: habilitar os serviços

O Proxy declara dependência e ordenação em relação ao Broker. Inicie o Broker
primeiro; antes de iniciar, o Proxy faz uma espera limitada e tenta conectar ao
socket privado para confirmar que o Broker está aceitando conexões. Em seguida,
habilite ambos no boot:

```bash
systemctl enable --now simple-broker.service
systemctl enable --now simple-proxy.service
```

Para iniciar/parar/reiniciar manualmente:

```bash
systemctl start simple-broker.service
systemctl start simple-proxy.service
systemctl stop simple-proxy.service simple-broker.service
systemctl restart simple-broker.service
```

Uma parada/reinicialização do Broker também para/reinicia o Proxy. Ambos têm
`Restart=on-failure`; o Proxy tem dependência vinculada ao Broker. `SIGTERM` e
`SIGINT` fazem os processos encerrarem o servidor e removerem somente o socket
que criaram (comparando tipo, UID, dispositivo e inode). A parada não remove o
banco. `ProtectSystem=strict`, `ProtectHome`, `NoNewPrivileges`, namespaces de
`/tmp` e restrição de família de sockets limitam os serviços; as exceções de
escrita ficam restritas aos diretórios de estado/runtime necessários.

## Acesso temporário pelo SSH

O SIMPLE é uma CLI, não um site. O Broker e o Proxy usam somente sockets Unix;
não abra uma porta TCP para a aplicação. As etapas abaixo permitem uma sessão
SSH dedicada ao SIMPLE, sem shell, comandos remotos, SFTP ou encaminhamentos.
Use uma conta Unix separada por participante. **Não compartilhe a conta
`ubuntu`, não adicione participantes a `sudo` e não os adicione ao grupo antes
de instalar e validar as restrições.**

### Criar o primeiro administrador

O banco de produção começa sem usuários. O procedimento abaixo só funciona com
os dois serviços parados e com a tabela de usuários vazia. A senha é digitada
em prompt oculto; não a coloque na linha de comando:

```bash
sudo systemctl stop simple-proxy.service simple-broker.service
sudo -u simple-broker /usr/bin/python3 \
  /opt/simple/deploy/bootstrap-production-admin.py
sudo systemctl start simple-broker.service simple-proxy.service
```

Para administrar pelo socket privado, abra o cliente Broker numa sessão
administrativa no servidor:

```bash
sudo env SIMPLE_MODE=broker \
  BROKER_SOCKET=/run/simple-private/broker.sock \
  /usr/bin/bash /opt/simple/simple.sh
```

No cliente, use **Criar Conta** para provisionar a conta SIMPLE do participante;
depois autentique-se como administrador e abra **Administração > Vínculos UID
Linux** para associá-la ao UID Unix que será criado. O socket privado não deve
ser disponibilizado aos participantes.

### Instalar e validar as restrições SSH

O instalador preserva um backup do `sshd_config`, acrescenta um bloco
`Match Group simple-users`, executa `sshd -t` e confirma as opções efetivas
antes de manter a alteração. Em caso de validação malsucedida, restaura o
arquivo original. Ele **não recarrega** o SSH:

```bash
sudo bash /opt/simple/deploy/configure-simple-ssh.sh
sudo sshd -T -C user=simple-broker,host=localhost,addr=127.0.0.1 | \
  grep -E '^(forcecommand|authenticationmethods|passwordauthentication|disableforwarding|allowtcpforwarding|allowagentforwarding|allowstreamlocalforwarding|x11forwarding|permituserrc|permituserenvironment|permittty) '
```

Confirme `forcecommand /opt/simple/deploy/simple-ssh-entrypoint.sh`,
`authenticationmethods publickey`, `passwordauthentication no`,
`disableforwarding yes`, os encaminhamentos e X11 desativados, `permituserrc
no`, `permituserenvironment no` e `permittty yes`. `PermitUserEnvironment`
não é aceito dentro de `Match` no OpenSSH do Ubuntu 22.04; por isso, o
instalador verifica o valor efetivo (o padrão é `no`) e restaura a configuração
anterior se outra configuração global o habilitar. Mantenha a sessão `ubuntu`
atual aberta; abra outra sessão administrativa e valide a conexão antes de
recarregar:

```bash
sudo sshd -t
sudo systemctl reload ssh
```

O launcher rejeita comandos SSH e sessões sem TTY, limpa o ambiente recebido,
define `LANG=C.UTF-8` para manter o cálculo correto da largura dos caracteres
Unicode e inicia somente a CLI em modo Proxy. A conta do participante precisa
usar `/bin/bash` para que o OpenSSH execute o comando forçado; isso **não**
concede um shell, pois a configuração `Match` força o launcher em toda sessão
do grupo. Use apenas chave pública autorizada e mantenha autenticação por senha
desativada para o grupo.

### Testar antes do convite

Antes de criar a conta do participante:

1. Crie uma conta Unix descartável, com home privado, sem `sudo`, shell
   `/bin/bash` e associação somente ao grupo `simple-users` além do grupo
   primário próprio.
2. Autorize uma chave pública de teste e confirme as opções efetivas de SSH
   para esse usuário com `sshd -T -C`.
3. Teste a sessão normal com TTY: deve abrir a CLI e exigir login SIMPLE.
4. Teste `ssh usuario@servidor id`, SFTP e tentativas de encaminhamento:
   comandos e SFTP devem ser recusados e encaminhamentos devem falhar. Confirme
   que o shell `ubuntu` administrativo continua funcionando numa segunda
   conexão.
5. Só então cadastre o UID real do participante no painel e crie a conta Unix
   e a chave SSH individuais.

Não convide participantes se qualquer teste falhar. O confinamento pelo
`ForceCommand` limita o que a sessão SSH executa, mas não é um container: todos
os participantes compartilham o kernel e outros recursos do host. Use somente
pessoas de confiança e um host sem dados não relacionados ao teste. No fim do
teste, revogue o vínculo UID, pare ou remova a conta Unix e retire-a de
`simple-users`.

## Operação e diagnóstico

### Como `ubuntu`: estado e logs

```bash
systemctl status simple-broker.service simple-proxy.service --no-pager
systemctl is-active simple-broker.service simple-proxy.service
journalctl -u simple-broker.service -u simple-proxy.service \
  --since today --no-pager
```

Os logs identificam o serviço e operações sem incluir senhas, tokens ou JSON das
requisições. Para verificar os caminhos e permissões:

```bash
sudo stat -c '%U:%G %a %n' \
  /var/lib/simple /var/lib/simple/simple.db \
  /run/simple-private /run/simple-private/broker.sock \
  /run/simple-public /run/simple-public/proxy.sock
```

Falhas com “diretório ... deve pertencer” ou “permissões privadas” devem ser
investigadas; não contorne a validação nem torne diretórios compartilhados
graváveis. Se o socket estiver obsoleto, primeiro confirme que o serviço
correspondente está inativo e que o caminho pertence ao serviço. O encerramento
normal e o ciclo de vida de `RuntimeDirectory` fazem a limpeza sem tocar em
socket alheio.

### Backup do banco

O backup é feito pela API SQLite do Broker, não por `sqlite3`, `cp` ou por um
segundo componente da aplicação. Como `ubuntu`, escolha um nome novo e solicite
ao Broker criar e validar o backup:

```bash
backup_name="simple-$(date -u +%Y%m%dT%H%M%SZ).sqlite"
sudo -u simple-broker /usr/bin/python3 \
  /opt/simple/broker/simple_broker.py \
  --database /var/lib/simple/simple.db \
  --backup-database "/var/lib/simple/backups/${backup_name}"
```

O destino precisa ser inexistente, absoluto e estar em diretório privado do
serviço. O Broker cria o arquivo com modo `0600`, usa o mecanismo de backup
consistente do SQLite e valida schema, integridade e permissões. Backups não são
removidos automaticamente; estabeleça retenção e cópia externa criptografada
em procedimento operacional separado. Não envie cópias de produção ao Git.

### Recuperação

Como `root`, pare os serviços para garantir que nenhum Broker esteja usando o
banco. Não apague arquivos `-wal`, `-shm` ou `-journal` manualmente; se
permanecerem após o encerramento, interrompa e investigue antes da recuperação:

```bash
systemctl stop simple-proxy.service simple-broker.service
test ! -e /var/lib/simple/simple.db-wal
test ! -e /var/lib/simple/simple.db-shm
test ! -e /var/lib/simple/simple.db-journal
```

Como `simple-broker` via `sudo -u`, valide e replique o backup escolhido para
um arquivo de staging novo:

```bash
sudo -u simple-broker /usr/bin/python3 \
  /opt/simple/broker/simple_broker.py \
  --database /var/lib/simple/backups/ARQUIVO-DE-BACKUP.sqlite \
  --backup-database /var/lib/simple/simple.restore.sqlite
```

Como `root`, preserve o banco atual e mova o staging para o caminho de produção.
Execute no mesmo shell; a operação falha se o nome de preservação já existir.
Os dois arquivos ficam no mesmo filesystem e já foram criados/validados pelo
Broker:

```bash
restore_path="/var/lib/simple/simple.db.pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
sudo test ! -e "$restore_path" && sudo test ! -L "$restore_path" &&
  sudo mv -- /var/lib/simple/simple.db "$restore_path" &&
  sudo mv -- /var/lib/simple/simple.restore.sqlite /var/lib/simple/simple.db
```

Como `simple-broker` via `sudo -u`, valide antes de reiniciar:

```bash
sudo -u simple-broker /usr/bin/python3 \
  /opt/simple/broker/simple_broker.py \
  --database /var/lib/simple/simple.db \
  --validate-database-only
```

Se a validação falhar, não inicie serviços: como `root`, mova o arquivo
restaurado para outro nome e restaure o arquivo `.pre-restore-*` ao nome
`simple.db`; depois valide novamente pelo Broker. Se válida, como `root`,
reinicie primeiro o Broker e depois o Proxy. Essa recuperação não altera
configuração de SSH/rede.

## Atualização pelo Git

Como `ubuntu`, confirme que o checkout não contém alterações locais. Comandos
Git não devem ser executados como `simple-broker`; os serviços não escrevem em
`/opt/simple`.

```bash
git -C /opt/simple status --short --branch
git -C /opt/simple fetch origin
git -C /opt/simple log --oneline HEAD..origin/main
```

Depois de revisar o código recebido, como `root`, pare primeiro Proxy e Broker:

```bash
systemctl stop simple-proxy.service simple-broker.service
```

Como `ubuntu`, atualize somente fast-forward:

```bash
git -C /opt/simple pull --ff-only origin main
```

Revise alterações de schema e documentação antes de reiniciar. Se houver
migração, crie também um backup nomeado pelo modo `--backup-database` e execute
explicitamente `--migrate-database-only` como `simple-broker`; não passe
`--initialize-database` no `ExecStart`. Alterações nas unidades devem ser
instaladas por `root` e seguidas de `systemctl daemon-reload`. Após validar,
como `root`, inicie Broker e Proxy na ordem indicada acima. Se qualquer teste
ou validação falhar, mantenha os serviços parados e investigue; não publique
uma atualização parcialmente validada.

## SSH e isolamento futuro

Esta preparação não configura SSH e não autoriza login de participantes. A
autenticação SIMPLE e o grupo do socket não confinam processos Unix. Antes de
qualquer acesso de participantes, uma etapa separada precisa projetar,
implementar e testar, no mínimo:

- comando forçado (`ForceCommand` ou mecanismo equivalente) que inicie somente
  o cliente SIMPLE em modo Proxy e rejeite comandos enviados pelo cliente SSH;
- caminho de login/shell que permita o comando forçado sem fornecer shell
  interativo; a conta de serviço `simple-broker` continua com `nologin`;
- bloqueio de SFTP/subsystem, shell e comandos arbitrários;
- encaminhamento TCP, de agente, X11 e túnel desabilitados;
- política de TTY deliberada: a TUI precisa de terminal, mas a ausência de TTY
  deve falhar sem fallback para shell;
- isolamento e permissões de home, arquivos temporários, processos, sinais e
  diretórios de cada participante;
- validação da compatibilidade do comando forçado com a versão e configuração
  Ubuntu/OpenSSH, revisão de `sshd -t` e testes com contas Unix descartáveis,
  sem expor o socket privado ou o banco.

Não aplique configurações globais/`Match` de SSH com base somente nesta
documentação. Uma futura configuração precisa evitar SFTP, forwarding e
execução de comandos e provar que participantes não conseguem ler dados,
processos ou arquivos uns dos outros. Nenhuma porta ou participante deve ser
liberado antes dessa etapa.
