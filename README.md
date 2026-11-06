<div align="center">

# SIMPLE

### Aprenda Shell Script praticando e trocando feedback com outras pessoas.

Ambiente de aprendizagem em terminal com exercícios, submissões rastreáveis e
avaliação por pares.

</div>

## Sobre o projeto

O SIMPLE foi criado para apoiar o aprendizado colaborativo de Shell Script.
Participantes praticam com exercícios, compartilham referências das soluções,
avaliam entregas de outras pessoas e acompanham seu próprio progresso.

A aplicação é uma CLI escrita principalmente em Bash. No modo multiusuário,
serviços locais em Python conectam a interface ao banco SQLite por sockets
Unix.

> **As submissões não são executadas pelo SIMPLE.** O sistema registra a URL
> do commit e informações da entrega; uma pessoa consulta a referência e faz a
> avaliação.

## Índice

- [Como funciona](#como-funciona)
- [Recursos](#recursos)
- [Começar no modo local](#começar-no-modo-local)
- [Acessar o servidor](#acessar-o-servidor)
- [Provisionar participantes](#provisionar-participantes)
- [Segurança](#segurança)
- [Documentação](#documentação)

## Como funciona

1. Escolha um exercício e leia seu enunciado, especificações e exemplos.
2. Registre a solução por meio da URL do commit no GitHub e do diretório de
   entrega.
3. Outro participante solicita autorização para avaliar; no modo Broker/Proxy,
   a pessoa autora aceita ou recusa o pedido em **Meu espaço > Minhas entregas**.
   Pedidos sem resposta expiram em 24 horas e são liberados na próxima operação
   do sistema que processar pedidos.
4. Após o aceite, o avaliador consulta o exercício e a referência, dá uma nota
   de 0 a 5 e escreve um feedback. A nota mínima para aprovação é 3.
5. A pessoa avaliada pode responder ao feedback e avaliar o avaliador.
6. No modo Broker/Proxy, consulte **Meu espaço** para acompanhar o perfil,
   entregas, avaliações, respostas, notificações e pontos. Em um terminal Curses,
   use T para abrir **Tripulantes**, buscar pessoas e ver o perfil compartilhável;
   D inicia uma DM e o retorno do chat mantém o perfil selecionado. Use F2 para
   abrir a notificação mais recente, P para notificações e avaliações, ou F3
   para o histórico de pontos. Pedidos pendentes aparecem ao entrar em Meu
   espaço; as notificações são atualizadas automaticamente.

## Recursos

- Catálogo de exercícios de Shell Script com especificações e exemplos.
- Submissões rastreáveis por commit e diretório.
- Avaliações por pares com feedback obrigatório e resposta opcional.
- Perfil, progresso, notificações e histórico de pontos.
- Chat colaborativo no modo Broker/Proxy: salas global, ajuda e por exercício,
  mensagens diretas, presença recente, menções, reações e moderação.
- Administração de exercícios, usuários e operação do sistema.
- Modo local para uso individual e modos Broker/Proxy para acesso multiusuário
  em Linux.

## Começar no modo local

### Requisitos

- Bash
- SQLite (`sqlite3`) e OpenSSL (`openssl`)
- Python 3.9+ para os modos Broker e Proxy
- Gum 2.0+ opcional, para entradas e confirmações aprimoradas

No catálogo, a ação **Ler PDF original** abre o link HTTPS no navegador padrão
do ambiente em que o SIMPLE está sendo executado. Em ambientes remotos sem
navegador gráfico, o link continua visível nos detalhes do exercício.

### Executar

```bash
git clone https://github.com/igpajor/SIMPLE.git
cd SIMPLE
bash simple.sh
```

O modo local cria ou utiliza `database/simple.db` no próprio checkout. É
indicado para desenvolvimento e uso individual; esse banco é separado do banco
de produção do servidor.

## Acessar o servidor

O SIMPLE não é um site: participantes acessam a CLI por SSH com chave pública.
Cada pessoa tem uma conta Linux e uma conta SIMPLE próprias, associadas por um
vínculo de UID. A chave SSH autentica a conexão ao servidor; a senha SIMPLE
autentica a pessoa dentro do aplicativo.

### Acesso administrativo

Conecte-se ao servidor pela conta operacional autorizada (por exemplo,
`ubuntu`) e inicie a CLI pelo socket privado do Broker:

```bash
sudo env SIMPLE_MODE=broker \
  BROKER_SOCKET=/run/simple-private/broker.sock \
  /usr/bin/bash /opt/simple/simple.sh
```

Na CLI, escolha **Entrar** e use a conta SIMPLE administrativa. Não use
`./simple.sh` para administrar o banco de produção: sem as variáveis acima,
esse comando inicia o modo local.

## Provisionar participantes

No servidor já configurado, execute:

```bash
cd /opt/simple
./cadastro NOME
```

O comando solicita a chave pública SSH e credenciais de uma conta SIMPLE
administradora. Ele cria as contas Linux e SIMPLE, instala a chave, associa o
UID e configura uma senha SIMPLE temporária, que deverá ser alterada no primeiro
login. A senha Linux fica bloqueada; o SSH usa somente chave pública. O comando
SSH exibido ao final usa `163.176.181.196`. A senha temporária é mostrada uma
única vez: compartilhe-a somente por um canal seguro.

Use `./cadastro --help` para ver as opções e `./cadastro --version` para
consultar a versão do utilitário.

O provisionador requer que o servidor já esteja configurado para acesso SSH
restrito. Consulte o [guia de implantação Ubuntu](./docs/DEPLOYMENT_UBUNTU.md)
antes de habilitar o acesso.

## Segurança

- Nunca solicite, envie ou copie a chave SSH privada; compartilhe apenas a
  chave pública.
- Use uma conta Linux e uma conta SIMPLE individuais por participante.
- O acesso de participante é restrito à CLI; não compartilhe a conta
  administrativa do servidor.
- O SIMPLE registra referências de submissão, mas não baixa nem executa o
  código entregue.

## Documentação

- [CLI e funcionalidades](./docs/CLI.md)
- [Implantação e operação em Ubuntu](./docs/DEPLOYMENT_UBUNTU.md)
