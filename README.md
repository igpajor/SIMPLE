# SIMPLE

Aplicação de terminal para praticar exercícios de Shell Script, acompanhar
progresso e participar de avaliações entre usuários. Desenvolvida em Bash,
com armazenamento SQLite.

> O SIMPLE registra referências para soluções no GitHub; ele não baixa nem
> executa o código enviado.

## Recursos

- Cadastro, login e perfil de usuário
- Catálogo de exercícios com especificações e exemplos
- Envio de soluções por URL de commit e diretório do projeto
- Avaliações entre usuários e acompanhamento de progresso
- Pontos, histórico e ferramentas administrativas
- Modos local, Broker e Proxy em socket Unix

## Requisitos

- Bash
- SQLite (`sqlite3`)
- OpenSSL (`openssl`)
- Python 3.9+ para os modos Broker e Proxy
- Gum 2.0+ opcional, para entradas e confirmações aprimoradas

## Início rápido

```bash
git clone https://github.com/igpajor/SIMPLE.git
cd SIMPLE
bash simple.sh
