# SIMPLE CLI

## Requisitos

- Bash
- SQLite (`sqlite3`)
- OpenSSL (`openssl`) para hash das senhas
- Python 3.9+ apenas para o modo broker

Inicie o aplicativo com `bash simple.sh`. O banco SQLite local é inicializado pelo próprio programa. Os testes isolados podem ser executados com `bash tests/test_cli.sh`.

## Contas

Crie uma conta com nome de usuário de 3 a 32 caracteres (letras, números, ponto, hífen ou sublinhado) e senha de pelo menos 8 caracteres. As senhas são armazenadas como hashes SHA-512 crypt; nunca são gravadas em texto puro. Cada conta recebe 1 ponto inicial.

## Perfil

Em **Meu perfil**, cada participante pode editar o nome de exibição, o usuário do GitHub e uma biografia de até 500 caracteres. O perfil e o progresso ficam visíveis ao próprio participante e aos administradores; não há uma consulta pública de perfis.

## Exercícios e avaliações entre usuários

Abra um exercício e escolha enviar a solução. O SIMPLE registra a URL de um commit do GitHub (`https://github.com/OWNER/REPOSITORY/commit/SHA`) e o diretório do projeto em que a solução está. O commit deve ser identificado por um SHA hexadecimal de 7 a 64 caracteres. O diretório deve ser relativo e não pode conter `..`. A referência não pode ser editada depois do envio; para enviar outra versão, crie uma nova tentativa. O SIMPLE armazena a referência, não baixa nem executa o código. A submissão abre um slot e debita 1 ponto.

Outro usuário pode avaliar o conteúdo de 0 a 5; a nota mínima para aprovação é 3. Quem registra a avaliação recebe 1 ponto, tanto em aprovação quanto em reprovação. Não é permitido avaliar a própria submissão. Uma reprovação permite nova submissão e novo slot, desde que o usuário tenha pontos; uma aprovação impede nova avaliação daquele exercício para o mesmo usuário.

O programa não baixa nem executa soluções submetidas. A pessoa avaliadora consulta a referência externa e registra a avaliação manualmente. Registros anteriores ao formato GitHub são preservados como submissões legadas.

## Cadastro administrativo de exercícios

Não há um administrador criado automaticamente. Depois de iniciar o aplicativo uma vez para aplicar as migrações, promova uma conta existente no SQLite, substituindo `SEU_USUARIO` pelo nome cadastrado:

```bash
sqlite3 database/simple.db "UPDATE users SET is_admin = 1 WHERE username = 'SEU_USUARIO';"
```

Reinicie o SIMPLE. Administradores verão a opção **Administração** no menu principal. No painel administrativo é possível:

- cadastrar exercícios com código, nome, descrição, diretório e arquivos de entrega, funções permitidas, enunciado, exemplo de execução, modelo de terminal e dicas;
- editar os dados de exercícios sem regravar os arquivos originais do enunciado, arquivar e reativar exercícios. O arquivamento remove o exercício do catálogo, mas mantém submissões e avaliações históricas;
- consultar perfis, progresso, saldo e exercícios aprovados de todos os usuários;
- definir uma senha temporária, que deverá ser trocada no próximo login, para permitir a recuperação de acesso;
- adicionar ou remover pontos com uma justificativa registrada no histórico.

O catálogo é carregado do banco de dados; novos exercícios não exigem alterações em scripts Bash. O modelo do terminal e o exemplo aparecem em blocos próprios, e as dicas ficam separadas do enunciado.
Exercícios só aparecem no progresso como concluídos depois de receberem aprovação (nota mínima 3). Submissões pendentes ou reprovadas não são listadas como exercícios concluídos.

As alterações de schema são aditivas e aplicadas por `db_init` quando o SIMPLE inicia. Antes de executar uma nova versão contra um banco de dados importante, faça uma cópia de segurança do arquivo SQLite. Os testes CLI usam um banco temporário e não precisam alterar o banco de produção.

## Implantação remota por SSH

O SIMPLE ainda não confina sessões SSH nem substitui os controles do sistema operacional. A autenticação do aplicativo não impede que uma conta Unix abra um shell ou leia arquivos acessíveis ao processo. Portanto, não exponha o comando diretamente a participantes em um servidor compartilhado sem uma configuração de acesso restrito revisada e testada. A preparação para `systemd` em Ubuntu também não habilita SSH nem significa que a aplicação está pronta para participantes remotos.

Antes de uma implantação, defina e valide o modelo de isolamento do host: entrada forçada no aplicativo com TTY, shell/SFTP e encaminhamentos desabilitados, permissões mínimas nos dados e isolamento entre processos de participantes. Uma conta Unix compartilhada, por si só, não isola usuários entre si. O SIMPLE nunca deve executar código submetido. Consulte [DEPLOYMENT_UBUNTU.md](./DEPLOYMENT_UBUNTU.md) para a preparação dos serviços e a análise das etapas de confinamento ainda pendentes.

## Broker local (não é uma implantação SSH)

No modo broker, o processo do serviço é a única camada autorizada a acessar o SQLite; a TUI Bash envia operações explicitamente permitidas por um socket Unix local. O protocolo não oferece uma operação SQL genérica, e as chamadas a `db_init` e `db_query` são bloqueadas no cliente nesse modo.

O fluxo do broker inclui cadastro/login e troca de senha; consulta e edição do próprio perfil; catálogo e especificações dos exercícios; submissões como referências a commits GitHub; reserva/cancelamento e avaliação de slots; progresso e histórico de pontos; e administração de exercícios, usuários, senhas, estado de contas, pontos, submissões, avaliações e visão geral do sistema. O servidor deriva o usuário do token e revalida permissões administrativas em cada operação administrativa.

O caminho do banco deve ser absoluto e ficar em um diretório preexistente, pertencente ao usuário efetivo do Broker e sem permissões para grupo/outros (modo `0700` ou mais restrito). Os diretórios ancestrais não podem permitir escrita por usuários não confiáveis; diretórios root com sticky bit, como `/tmp`, são aceitos como ancestrais. O arquivo SQLite e seus auxiliares (`-wal`, `-shm`, `-journal`) devem ser arquivos regulares do usuário do Broker com modo privado (`0600`). Uma configuração insegura causa falha; o Broker não corrige permissões automaticamente. O uso de um diretório dedicado também protege os backups da inicialização explícita.

Para testar localmente, prepare o diretório e inicie o serviço em um terminal separado:

```bash
install -d -m 700 "$HOME/.local/share/simple-broker"
python3 broker/simple_broker.py \
  --socket "$HOME/.local/run/simple/broker.sock" \
  --database "$HOME/.local/share/simple-broker/simple.db" \
  --initialize-database
```

Em outro terminal, inicie o cliente:

```bash
SIMPLE_MODE=broker \
BROKER_SOCKET="$HOME/.local/run/simple/broker.sock" \
bash simple.sh
```

O socket e um banco novo são criados com permissão `0600`. O broker valida o schema por padrão e não migra o banco automaticamente. `--initialize-database` é explícito, aplica o schema/migrações aditivas e cria antes uma cópia SQLite consistente com permissão `0600` quando o banco já existe. Preserve o backup até confirmar a migração. Os testes usam bancos temporários: `python3 -m unittest tests/test_broker.py tests/test_proxy.py`.

Esta cobertura funcional ainda não é uma configuração de produção nem oferece isolamento entre usuários Unix que compartilham a mesma conta. O socket `0600` não permite acesso direto por sessões com UIDs diferentes. Não exponha o socket privado a containers nem habilite SSH com base apenas nesta implementação. O proxy Unix local está implementado, mas o confinamento de SSH e o isolamento do host precisam de projeto e validação próprios; o SIMPLE nunca executa código submetido.

Para implantação local supervisionada em Ubuntu 22.04, há unidades systemd, inicialização one-shot do banco, backup validado pelo Broker e procedimentos operacionais em [DEPLOYMENT_UBUNTU.md](./DEPLOYMENT_UBUNTU.md). Esses arquivos não configuram SSH nem tornam o SIMPLE pronto para receber participantes por SSH.

## Identidade Unix e acesso multiusuário

O vínculo administrado previsto é:

```text
UID Linux
    |
    v
Identidade do sistema operacional
    |
    v
Vínculo administrado
    |
    v
Conta SIMPLE
    |
    v
Sessão autenticada
    |
    v
Operações autorizadas
```

`identity_bindings` armazena no SQLite as associações atuais e o histórico de revogações. Um administrador pode consultar, criar, alterar o UID e revogar vínculos pelo painel **Administração > Vínculos UID Linux**. O UID do processo Broker não pode ser vinculado; índices únicos permitem no máximo um vínculo ativo por UID e por conta SIMPLE, enquanto vínculos revogados permanecem consultáveis e podem ser substituídos por uma nova associação. UID sem vínculo ativo não resolve para uma conta, e uma conta SIMPLE desativada perde acesso mesmo que seu vínculo continue ativo. Participantes não podem administrar vínculos nem declarar um UID em operações autenticadas.

### Proxy Unix escolhido

O modo multiusuário usa um proxy local central com socket Unix e `SO_PEERCRED` (Linux). O proxy obtém UID/GID/PID do kernel em cada conexão, resolve o vínculo ativo por uma operação interna restrita ao socket privado do Broker e encaminha apenas métodos SIMPLE permitidos. O participante continua autenticando com sua senha SIMPLE; o vínculo não substitui a senha. O proxy associa tokens emitidos pelo Broker ao UID e à versão do vínculo, e exige essa associação nas requisições subsequentes. O Broker permanece como único processo que acessa SQLite e verifica autorização SIMPLE em cada operação administrativa.

O proxy rejeita UID sem vínculo, vínculos revogados e contas desativadas; limita tamanho, timeout e conexões; valida mensagens; trata desconexões; e registra UID/operação/resultado sem tokens, senhas ou conteúdo de requisições. O socket privado do Broker não deve ser exposto aos participantes nem à rede. Contas são criadas e vinculadas por um administrador antes do primeiro login pelo proxy; o cadastro público fica desabilitado neste modo.

Um helper por usuário exigiria uma fronteira privilegiada adicional (por exemplo, helper setuid/capability ou regras de execução privilegiada), com mais superfície de instalação, atualização e auditoria. O proxy central foi escolhido por concentrar o tratamento do protocolo, limites, logging e verificação de `SO_PEERCRED`, mantendo acesso SQLite e decisões de autorização no Broker.

Veja [BROKER_PROXY.md](./BROKER_PROXY.md) para a topologia, permissões e inicialização manual do Broker e do proxy. Esta implementação não configura SSH nem é uma recomendação de prontidão para produção; validação operacional em um host multiusuário e configuração SSH restrita permanecem etapas separadas.

## Progresso

O menu de progresso mostra exercícios aprovados, saldo de pontos, avaliações recebidas/realizadas e movimentações de pontos.
