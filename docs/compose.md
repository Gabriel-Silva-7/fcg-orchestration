# Compose com os quatro serviços

O Compose executa Users, Catalog, Payments e Notifications, PostgreSQL com quatro
bancos/roles próprios e RabbitMQ com bootstrap de topologia. O bootstrap precisa
terminar com sucesso antes de qualquer aplicação. As aplicações aplicam suas
migrations e têm readiness próprio; `up -d` sozinho não comprova a jornada.

## Preparar fontes e configuração

Requer Docker com Compose, Python3, Git e OpenSSL. A validação local usa o contexto
Docker `orbstack`. Tenha quatro checkouts limpos com os incrementos que deseja
combinar. Os caminhos abaixo são exemplos de clones irmãos; worktrees explícitas
também são aceitas. O script registra os SHAs completos e recusa fontes sujas.
Ele não faz checkout, merge, download nem publicação.

Na raiz da orquestração:

```sh
python3 scripts/prepare-local.py \
  --users ../fcg-users-api \
  --catalog ../fcg-catalog-api \
  --payments ../fcg-payments-api \
  --notifications ../fcg-notifications-api \
  --output .local/demo

docker --context orbstack compose --env-file .local/demo/demo.env up -d --build
scripts/e2e.sh --target compose --env-file .local/demo/demo.env \
  --output .local/demo/e2e-first.json
scripts/e2e.sh --target compose --env-file .local/demo/demo.env \
  --output .local/demo/e2e-second.json
```

Os checkouts precisam conter as capacidades descritas nos READMEs dos serviços.
A preparação não certifica um SHA como release. As revisões selecionadas ficam em
`.local/demo/sources.json`; cada E2E registra imagens e IDs dos containers usados.
Fontes locais verificadas e teste integrado não comprovam acesso público, reprodução
por clone externo ou Kubernetes. Essas etapas têm verificação separada.

O diretório privado contém `demo.env` com credenciais aleatórias locais, par RSA
persistido e manifesto sem segredos. É ignorado pelo Git. Não envie esse diretório
nem imprima `docker compose config` com o ambiente real. A pasta tem modo0700 e o
arquivo de ambiente0600. Os PEMs têm leitura para o UID1654 através de mounts
somente leitura; a pasta privada protege seu acesso pelo host. A chave privada só
é montada em Users; Catalog recebe a pública; workers não recebem chaves JWT.
A geração falha se o destino já existir: reinicie com o ambiente existente para
preservar chaves/senhas/dados. Uma preparação interrompida requer inspeção do
novo diretório antes de removê-lo; não há sobrescrita ou rotação automática.

O ambiente gerado usa projeto exclusivo e portas loopback aleatórias (`0`). Para
descobrir endereços, consulte o mapeamento, sem imprimir configuração sensível:

```sh
docker --context orbstack compose --env-file .local/demo/demo.env ps -a
docker --context orbstack compose --env-file .local/demo/demo.env port users 8080
docker --context orbstack compose --env-file .local/demo/demo.env port catalog 8080
```

As variáveis de `.env.example` documentam nomes e exemplos públicos exclusivamente
DEMO, sem credenciais de seed padrão. Também é possível preparar configuração
externa manualmente. `AdminBootstrap__Enabled` fica desligado por padrão; o ambiente
gerado o habilita explicitamente com quatro valores novos. Mudar suas senhas não
redefine contas existentes. PostgreSQL e RabbitMQ também não rotacionam senhas de
volumes existentes apenas porque o env mudou.

`FCG_<SERVIÇO>_SOURCE` define contexto de build explícito e `FCG_<SERVIÇO>_IMAGE`
identifica a imagem local. As credenciais de banco geradas são hexadecimais, seguras
na conexão construída pelo Compose. A conexão padrão concatena esses valores. Para senhas que contenham separadores
ou aspas, forneça `ConnectionStrings__Default` diretamente no serviço por um
arquivo de override Compose, com quoting Npgsql apropriado e a mesma credencial
real usada no provisionamento. Alterar somente o password do env para incluir
aspas de conexão também alteraria a senha provisionada; use a conexão completa. Senhas de exemplos simples não
são adequadas fora do ambiente descartável. O bundle documental de configuração
pode usar outros issuer/audience/paths; mantenha os valores efetivos iguais em
Users/Catalog. O ambiente local usa `fcg-users-api`/`fcg-clients`, RS256,15min/skew0.
Não publique a privada nem altere chaves a cada partida.

## O que a jornada prova

O script obtém token administrativo pelo login real de Users e cria jogos via HTTP
Catalog. Para cada execução, cadastra dois titulares com dados únicos, observa
Welcome como `Sent` no banco Notifications e emissão `[E-MAIL SIMULADO]` no console.
O usuário compra um jogo59,90 com promoções sobrepostas de10% e25%, cujo preço
calculado é44,93 (Approved), e outro600,00 (Rejected) sob limite500.
As requisições usam o preço do servidor e aceitam202 antes do resultado assíncrono.
Polling limitado observa o estado terminal mesmo se Pending já tiver passado.

Confere a decisão real de Payments e o mesmo instante no Catalog, a cópia independente
em Notifications, biblioteca com um único jogo/preço44,93 e confirmação emitida para
o aprovado. Após observar Rejected nos dois consumidores, exige ausência de aquisição
e de intenção/log de confirmação para esse pedido. Outro titular não vê pedidos nem
biblioteca; detalhe alheio404; tokenUser não cria jogo403. Os logs são conferidos sem
contato completo literal ou codificado na URL, senha ou token. IDs de execução/UserId/OrderId correlacionam a prova.

As consultas SQL de verificação usam a role de cada banco, somente leitura. Nenhum
serviço lê o banco de outro serviço e o teste não cria fatos por SQL ou publica
pagamentos artificiais. A observação de `Sent` é acompanhada do log real; não é
interpretada como e-mail externo. O dispatcher documenta a possível repetição do
console após crash, pois console e banco não compartilham transação.

Execute duas vezes para comprovar isolamento entre execuções. O E2E cria dados e
preserva volumes inclusive em falha, para diagnóstico. A jornada também verifica
GET/PATCH do próprio perfil, propagação do contato para Notifications, alteração
de senha com senha antiga recusada e token previamente emitido preservado. A lista
administrativa fornece versão para bloquear/desbloquear; versão antiga retorna409.
O mesmo JWT perde/recupera acesso conforme o estado recebido no Catalog. Exclusão
lógica repetida permanece204 e o registro continua visível na lista administrativa.
Após aquisição, edição e desativação do jogo mantêm histórico44,93 com título atual;
DELETE repetido204 e PUT inativo404. Antes de usar uma identidade recém-criada,
o script aguarda a projeção ativa com prazo, pois readiness não prova sua entrega.
Logins que recebem429 respeitam o limite padrão e aguardam a janela dentro de
um prazo de90s; o cenário não aumenta o limite de produção. Recuperação operacional
e interleavings avançados são validados em provas separadas. O atraso/limite
financeiro esperado aqui é o padrão2s/500; alterá-lo exige ajustar o cenário.

## Saúde, diagnóstico e parada

As quatro aplicações expõem `/health/live` e `/health/ready` e healthcheck Docker
na porta8080. A privada não é embutida em imagem. Banco e broker têm volumes
persistentes e usuários próprios. Parada/reinício preserva os dados:

```sh
docker --context orbstack compose --env-file .local/demo/demo.env stop
docker --context orbstack compose --env-file .local/demo/demo.env up -d
```

Se a topologia existente for incompatível, o bootstrap falha sem liberar produtores.
Não apague filas para corrigir silenciosamente a incompatibilidade. Consulte os guias
de [PostgreSQL](postgres.md) e [RabbitMQ](rabbitmq.md) para recuperação e rotação.
Logs podem ser consultados com `compose ... logs --no-color users catalog payments
notifications`; não ative logging sensível de EF, payloads completos ou Authorization.

`down` do projeto remove containers/rede e preserva volumes; o ambiente pode ser
recriado com o mesmo arquivo/chaves. Descarte de dados é uma ação separada, após
conferir o projeto e inventariar IDs/labels dos recursos próprios. Não há reset
implícito nem `down -v` no E2E; não use prune global.

## Verificação do preparo e da composição

```sh
python3 -m unittest discover -s tests/compose -v
```

Os testes conferem os gates, fronteira de chaves/bancos e preparação com Git/RSA
reais, recusa de fontes sujas/sobrescrita, permissões e build real com caminho que
contém espaços, dólar e apóstrofo. Não substituem a jornada dos quatro hosts.
