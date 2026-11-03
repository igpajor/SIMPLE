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
3. Outro participante revisa a referência, dá uma nota de 0 a 5 e escreve um
   feedback. A nota mínima para aprovação é 3.
4. A pessoa avaliada pode responder ao feedback e avaliar o avaliador.
5. Consulte **Meu progresso** para acompanhar entregas, avaliações, respostas,
   notificações e pontos.

## Recursos

- Catálogo de exercícios de Shell Script com especificações e exemplos.
- Submissões rastreáveis por commit e diretório.
- Avaliações por pares com feedback obrigatório e resposta opcional.
- Perfil, progresso, notificações e histórico de pontos.
- Administração de exercícios, usuários e operação do sistema.
- Modo local para uso individual e modos Broker/Proxy para acesso multiusuário
  em Linux.

## Começar no modo local

### Requisitos

- Bash
- SQLite (`sqlite3`) e OpenSSL (`openssl`)
- Python 3.9+ para os modos Broker e Proxy
- Gum 2.0+ opcional, para entradas e confirmações aprimoradas

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

1. No cliente administrativo, escolha **Criar Conta** e crie uma conta SIMPLE
   de participante. O nome deve ter de 3 a 32 caracteres, começar com letra
   minúscula ou sublinhado e conter somente letras minúsculas, números, hífen
   ou sublinhado.
2. No servidor, execute o provisionador com esse nome:

   ```bash
   sudo /usr/bin/python3 /opt/simple/deploy/provision-participant.py NOME
   ```

3. Cole a chave pública SSH do participante e autentique-se com uma conta
   SIMPLE administradora. O comando cria a conta Linux, instala a chave,
   associa o UID à conta SIMPLE e só então habilita o acesso SSH restrito.
4. O participante conecta-se com a chave privada que permanece em seu
   dispositivo e entra na CLI usando a senha da conta SIMPLE.

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
