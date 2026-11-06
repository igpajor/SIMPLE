# SIMPLE CLI

## Requisitos

- Bash
- SQLite (`sqlite3`)
- OpenSSL (`openssl`) para hash das senhas
- Python 3.9+ apenas para o modo broker
- Gum 2.0+ é opcional para entradas, textos multilinha e confirmações

Os menus usam opções numeradas como navegação principal; digite a chave exibida e pressione Enter. No modo Broker/Proxy, um terminal interativo com Python Curses disponível mostra telas responsivas: a tela inicial apresenta boas-vindas, resumo de pontos/progresso e ações; **Meu espaço** reúne perfil, progresso e entregas. Dentro do painel, use as setas e Enter para navegar, E para editar o perfil, P para notificações e histórico de avaliações, e F3 para abrir o histórico de pontos. Em terminais estreitos, as informações do perfil e as entregas permanecem empilhadas. Sem TTY, a interface mantém os menus de linha de comando. Uma linha de status identifica o SIMPLE, a pessoa conectada, o modo e a área atual. Gum pode aprimorar entradas, textos multilinha e confirmações. Pausas sem TTY não consomem entrada automatizada, senhas permanecem mascaradas e entradas multilinha aceitam o fluxo de texto não interativo.

Inicie o aplicativo com `bash simple.sh`. O banco SQLite local é inicializado pelo próprio programa. Os testes isolados podem ser executados com `bash tests/test_cli.sh` e `bash tests/test_ui.sh`; a suíte Python usa `python3 -m unittest tests/test_broker.py tests/test_proxy.py tests/test_deployment.py tests/test_phase2.py tests/test_phase3.py`.

## Contas

Crie uma conta com nome de usuário de 3 a 32 caracteres (letras, números, ponto, hífen ou sublinhado) e senha de pelo menos 8 caracteres. As senhas são armazenadas como hashes SHA-512 crypt; nunca são gravadas em texto puro. Cada conta recebe 1 ponto inicial.

## Perfil

No modo Broker/Proxy, em **Meu espaço**, cada participante pode editar o nome de exibição, o usuário do GitHub e uma biografia de até 500 caracteres. O topo do painel mostra as iniciais, o nome, a mini bio e o saldo de pontos; a lista de entregas pode ser aberta para consultar o resumo do exercício, o status e, quando houver, o avaliador e o feedback. Em terminal Curses, P abre notificações, histórico de avaliações e respostas pendentes; F3 abre o histórico de movimentações de pontos. O perfil e o progresso ficam visíveis ao próprio participante e aos administradores; não há uma consulta pública de perfis.

## Exercícios e avaliações entre usuários

Abra um exercício e escolha enviar a solução. Primeiro, envie sua solução ao GitHub; no repositório, abra **Commits** ou **History**, selecione o commit que contém a solução e copie o endereço da página. Use o link direto do commit, no formato `https://github.com/OWNER/REPOSITORY/commit/SHA` — links de arquivo (`/blob/`) ou pasta/branch (`/tree/`) não servem. O SHA deve ter de 7 a 64 caracteres hexadecimais. Se o GitHub incluir um fragmento como `#diff-...`, ele é removido automaticamente; o SIMPLE registra o link do commit. Informe também o diretório da solução dentro do repositório, por exemplo `ex00` ou `Shell00/ex01`, ou `.` se estiver na raiz. O diretório deve ser relativo e não pode conter `..`. A referência não pode ser editada depois do envio; para enviar outra versão, crie uma nova tentativa. O SIMPLE armazena a referência, não baixa nem executa o código. A submissão abre um slot e debita 1 ponto.

Outro usuário solicita autorização para avaliar o slot. A pessoa autora encontra o pedido ao abrir a entrega em **Meu espaço > Minhas entregas** e pode aceitá-lo ou recusá-lo. Pedidos sem resposta expiram após 24 horas e são liberados na próxima operação do sistema que processar pedidos; a recusa devolve o slot à lista e impede que o mesmo avaliador peça novamente para aquela entrega. Após o aceite, o avaliador vê os dados do exercício, a descrição, a pessoa avaliada e a referência GitHub e pode concluir a avaliação com uma nota de 0 a 5 e feedback obrigatório; a nota mínima para aprovação é 3. O autor não pode retirar o aceite, mas o avaliador pode cancelar o pedido antes de registrar a avaliação. Quem conclui a avaliação recebe 1 ponto, tanto em aprovação quanto em reprovação. Não é permitido avaliar a própria submissão. Uma reprovação permite nova submissão e novo slot, desde que o usuário tenha pontos; uma aprovação impede nova avaliação daquele exercício para o mesmo usuário.

Depois do aceite, o SIMPLE abre a DM privada entre autor e avaliador e publica nela o link da sala em destaque. O avaliador também pode abrir essa mesma DM pela ação **Abrir sala de áudio e vídeo** na avaliação aceita. A conversa e o link ficam disponíveis somente às duas contas participantes. Em sessões SSH, abra o link no navegador do computador local; permita microfone/câmera e compartilhe a janela do terminal pela interface do Jitsi. A sala usa o serviço público meet.jit.si: o SIMPLE não controla a autenticação dos participantes nem consegue revogar um link que já foi compartilhado; trate-o como secreto e não o compartilhe fora da avaliação. Áudio e vídeo são transmitidos pelo Jitsi, não pelo SIMPLE; a sessão não é gravada pelo SIMPLE.

Depois de receber a avaliação, a pessoa pode responder ao feedback e dar uma nota de 0 a 5 ao avaliador. Essa resposta é opcional, mas, quando enviada, exige a nota e um comentário. **Meu espaço** reúne o estado das entregas e os dados de progresso. Novas avaliações e respostas geram avisos na tela inicial e ficam disponíveis como notificações em **Meu espaço > Meu progresso**; abrir essa tela marca os avisos como lidos.

O programa não baixa nem executa soluções submetidas. A pessoa avaliadora consulta a referência externa e registra a avaliação manualmente. Registros anteriores ao formato GitHub são preservados como submissões legadas.

O catálogo inicial inclui os dez exercícios Shell 00, de `SHELL00-EX00` a `SHELL00-EX09`. Enunciados, requisitos e exemplos são apresentados em seções; exemplos e modelos de terminal preservam quebras de linha em blocos próprios. O exemplo de entrada do exercício 07 foi omitido por conter material de terceiros protegido por direitos autorais.

## Cadastro administrativo de exercícios

Não há um administrador criado automaticamente. Depois de iniciar o aplicativo uma vez para aplicar as migrações, promova uma conta existente no SQLite, substituindo `SEU_USUARIO` pelo nome cadastrado:

```bash
sqlite3 database/simple.db "UPDATE users SET is_admin = 1 WHERE username = 'SEU_USUARIO';"
```

Reinicie o SIMPLE. Administradores verão a opção **Administração** no menu principal. No painel administrativo é possível:

- cadastrar exercícios com código, nome, descrição, diretório e arquivos de entrega, funções permitidas, enunciado, exemplo de execução, modelo de terminal e dicas;
- informar e editar uma URL HTTPS para o PDF original de cada exercício;
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

O fluxo do broker inclui cadastro/login e troca de senha; consulta e edição do próprio perfil; catálogo e especificações dos exercícios; submissões como referências a commits GitHub; pedidos de avaliação com aceite/recusa do autor, cancelamento e expiração após 24 horas; progresso e histórico de pontos; e administração de exercícios, usuários, senhas, estado de contas, pontos, submissões, avaliações e visão geral do sistema. O servidor deriva o usuário do token e revalida permissões administrativas em cada operação administrativa.

Na home `curses`, uma única operação autenticada agrega o resumo de pontos, exercícios aprovados, avaliações abertas, notificações e atividade do chat. O usuário é sempre derivado do token; o cliente não pode solicitar dados de outra conta.

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

## Interface de terminal

O SIMPLE roda inteiramente no terminal, sem dependência de interface gráfica. A apresentação se adapta ao ambiente:

- **Login em terminal interativo com Python `curses`**: tela responsiva para entrar ou criar conta, com navegação pelo teclado e senhas mascaradas. As regras de autenticação permanecem no Bash.
- **Tela inicial interativa**: em TTYs com `curses`, a home é centralizada e responsiva, cresce com a janela até 110 colunas por 32 linhas como o chat, e distribui as opções pelo espaço vertical disponível. Exibe resumo e notificações, aceita navegação por setas/Enter e mantém os atalhos numéricos. As ações seguem os fluxos Bash existentes.
- **Catálogo de exercícios interativo**: em TTYs com `curses`, permite navegar com setas/Enter e seleção contornada, sem atalhos numéricos limitados a nove itens; filtra por situação e busca por código ou nome. Os detalhes exibem a URL HTTPS do PDF original, e a ação **Ler PDF original** abre esse endereço no navegador padrão do ambiente. Em sessões remotas ou sem navegador gráfico, o link permanece disponível nos detalhes. Os detalhes mantêm entrega e chat disponíveis conforme o estado do exercício. O formulário de entrega separa link do commit, diretório e confirmação, e a tela preserva filtro e busca ao voltar dessas ações.
- **Sem TTY, sem suporte a `curses` ou com `TERM=dumb`**: login e home usam os fluxos Bash existentes; isso também preserva a entrada via stdin para testes e automação.
- **Chat interativo**: usa Python `curses` quando disponível, com layout centralizado e responsivo, identificação da conta, resumo do servidor e notificações sem prévias de mensagens. `F2` abre a DM ou sala da citação não lida mais recente; as demais telas continuam em Bash.
- **Navegação principal**: as telas `curses` aceitam setas e Enter, ou seleção direta pelo número; menus Bash exibem opções numeradas e confirmam a escolha com Enter.
- **Terminais interativos com Gum**: o Gum pode aprimorar entradas, campos multilinha e confirmações; os menus continuam numerados.
- **Sem Gum ou em Windows/cmd.exe**: menus e entradas usam o fallback Bash com `read`; cores podem ser desativadas conforme o terminal.
- **Sem TTY (pipes, testes, automação)**: a interface usa entrada padrão diretamente. Testes e scripts podem fornecer respostas via stdin; pausas não leem nem descartam dados.

### Navegação

Em qualquer menu:

| Tecla | Ação |
|-------|------|
| Setas | Navegar nas telas interativas `curses` |
| Número/letra exibido | Escolher a opção correspondente |
| `Enter` | Abrir a opção selecionada em `curses` ou confirmar a opção digitada no Bash |
| `0` | Voltar ou sair, conforme o menu |

### Sanitização de saída

Todo conteúdo persistido ou vindo de usuários (nomes, perfis, exercícios, feedback, histórico e entregas) é sanitizado antes da renderização. Controles C0/C1, DEL, ESC, retornos de carro e controles bidi selecionados são removidos; TAB vira espaços e quebras de linha são preservadas somente para conteúdo multilinha. A sanitização também se aplica a dados legados e alterados diretamente no banco.

### Contexto

O cabeçalho e a trilha de navegação mostram onde você está, por exemplo:

```
ACESSO > MENU PRINCIPAL > EXERCÍCIOS
```

Cada tela exibe o contexto atual (sistema, usuário, modo, pontos) para que fique claro "onde estou" e "o que posso fazer agora".
