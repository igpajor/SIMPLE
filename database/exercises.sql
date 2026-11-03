INSERT OR IGNORE INTO exercises (
    code, name, description, statement, example, delivery_directory,
    delivery_files, allowed_functions, terminal_model, hints, status
) VALUES
(
    'SHELL00-EX00',
    'Z',
    'Os verdadeiros sabem exibir um Z',
    'Crie um arquivo z que, ao executar um cat nele, exiba "Z" seguido de uma quebra de linha.',
    '$> cat z
Z
$>',
    'ex00/',
    'z',
    'Nenhuma',
    '',
    '',
    'available'
),
(
    'SHELL00-EX01',
    'testShell00',
    'O que são atributos?',
    'Crie o arquivo testShell00 no seu diretório de entrega.

Depois de resolver o exercício, execute o comando tar -cf testShell00.tar testShell00 para criar o arquivo a ser entregue.

Os "XX" não serão levados em conta. Um ano é uma resposta aceita no lugar da hora.',
    '%> ls -l
total XX
-r--r-xr-x 1 XX XX 40 Jun 1 23:42 testShell00
%>',
    'ex01/',
    'testShell00.tar',
    'Nenhuma',
    '',
    '',
    'available'
),
(
    'SHELL00-EX02',
    'Owi, de novo...',
    'Owi, de novo...',
    'Crie todos esses arquivos e diretórios. Faça o necessário para que a saída de um ls -l no seu diretório fique parecida com isto:

Depois de resolver o exercício, execute o comando tar -cf exo2.tar * para criar o arquivo a ser entregue.

Os "XX" não serão levados em conta. Um ano é uma resposta aceita no lugar da hora.',
    '%> ls -l
total XX
drwx--xr-x 2 XX XX XX Jun 1 20:47 test0
-rwx--xr-- 1 XX XX 4 Jun 1 21:46 test1
dr-x---r-- 2 XX XX XX Jun 1 22:45 test2
-r-----r-- 2 XX XX 1 Jun 1 23:44 test3
-rw-r----x 1 XX XX 2 Jun 1 23:43 test4
-r-----r-- 2 XX XX 1 Jun 1 23:44 test5
lrwxrwxrwx 1 XX XX 5 Jun 1 22:20 test6 -> test0
%>',
    'ex02/',
    'exo2.tar',
    'Nenhuma',
    '',
    '',
    'available'
),
(
    'SHELL00-EX03',
    'Conecte-me!',
    'Chave SSH',
    'Crie suas próprias chaves SSH. Em seguida:

Adicione sua chave SSH pública à sua entrega em um arquivo id_rsa_pub.
Atualize sua chave SSH pública no intra para se autorizar a enviar seus projetos ao nosso servidor git.

O nome do arquivo não foi escolhido por acaso.
Esses comandos serão úteis durante toda a sua piscine, não os esqueça!',
    '',
    'ex03/',
    'id_rsa_pub',
    'Nenhuma',
    '',
    '',
    'available'
),
(
    'SHELL00-EX04',
    'midLS',
    'midLS',
    'Coloque em um arquivo midLS a linha de comando a digitar para listar os arquivos e os diretórios do diretório atual, mas não os arquivos ocultos, nem "." nem ".." (nada que comece com um ponto), separados por vírgulas, ordenados por data de modificação e de modo que os diretórios sejam seguidos de um caractere barra.

O que não foi pedido não deve ser feito!',
    '',
    'ex04/',
    'midLS',
    'Nenhuma',
    '',
    'RTFM!',
    'available'
),
(
    'SHELL00-EX05',
    'GiT commit',
    'GiT commit?',
    'Crie um script shell que retorne os ids dos 5 últimos commits do seu repositório git.

Seu script será testado em nosso ambiente.',
    '%> bash git_commit.sh | cat -e
baa23b54f0adb7bf42623d6d0a6ed4587e11412a$
2f52d74b1387fa80eea844969e8dc5483b531ac1$
905f53d98656771334f53f59bb984fc29774701f$
5ddc8474f4f15b3fcb72d08fcb333e19c3a27078$
e94d0b448c03ec633f16d84d63beaef9ae7e7be8$
%>',
    'ex05/',
    'git_commit.sh',
    'Nenhuma',
    '',
    'RTFM!',
    'available'
),
(
    'SHELL00-EX06',
    'gitignore',
    'GiT',
    'Escreva um script shell curto que retorne a lista de arquivos existentes ignorados pelo seu repositório git atualmente presentes no seu repositório local.

Seu script será testado em nosso ambiente.',
    '%> bash git_ignore.sh | cat -e
.DS_Store$
mywork.c~$
%>',
    'ex06/',
    'git_ignore.sh',
    'Nenhuma',
    '',
    'RTFM!',
    'available'
),
(
    'SHELL00-EX07',
    'diff',
    '',
    'Crie o arquivo b tal que:

Consulte man patch.',
    '',
    'ex07/',
    'b',
    'Nenhuma',
    '',
    'O exemplo de entrada da referência foi omitido por conter texto de terceiros protegido por direitos autorais.',
    'available'
),
(
    'SHELL00-EX08',
    'clean',
    '',
    'Coloque em um arquivo clean uma linha de comando que irá procurar, a partir do diretório atual e em todos os seus subdiretórios, os arquivos cujo nome termina com ~, ou começa e termina com #.

A linha de comando exibirá e apagará os arquivos encontrados.
Apenas um comando é permitido: nada de ";" nem de "&&" nem de outros.',
    '',
    'ex08/',
    'clean',
    'Nenhuma',
    '',
    'man find',
    'available'
),
(
    'SHELL00-EX09',
    'Ilusões, não truques, Michael...',
    '',
    'Crie um arquivo mágico chamado ft_magic que deverá estar corretamente formatado para detectar arquivos do tipo 42 file e constituídos da string "42" no 42º byte.',
    '',
    'ex09/',
    'ft_magic',
    'Nenhuma',
    '',
    'man file',
    'available'
);

UPDATE exercises
SET status = 'archived'
WHERE code IN ('SH00-EX00', 'SH00-EX02');
