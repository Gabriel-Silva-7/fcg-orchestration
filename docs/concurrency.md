# Concorrência e preço selecionado

A prova usa os quatro serviços reais, PostgreSQL e RabbitMQ em outro projeto
privado criado por `scripts/prepare-local.py`. Recusa projeto com recursos prévios,
cria dados HTTP únicos e remove seus containers, rede e volumes. Não precisa de
hooks no código de produção nem altera contratos ou políticas de autorização.

Compile a probe de transporte e execute com fontes limpas selecionadas:

```sh
dotnet restore tests/rabbitmq/Probes/RabbitMq.Probes.csproj --locked-mode
dotnet build tests/rabbitmq/Probes/RabbitMq.Probes.csproj -c Release --no-restore
python3 scripts/concurrency.py --env-file /caminho/privado/novo/demo.env \
  --dotnet /caminho/para/dotnet --output /caminho/novo/para/evidencias
```

Uma sessão do próprio banco segura um lock Share na tabela Orders. Isso permite
observar duas requisições reais esperando, uma para inserir e outra pelo lock da
dupla usuário/jogo, antes de soltar a barreira. Com Payments suspenso, uma é aceita
e a outra recebe `order_already_pending`. Depois da aprovação, nova requisição
recebe `game_already_acquired`. Há apenas um pedido aberto e uma aquisição.

A mesma barreira permite editar e desativar um jogo por HTTP depois da seleção de
preço e antes do commit do pedido. Outro caso espera a expiração natural da promoção
com o INSERT bloqueado. Nas duas situações, a seleção de59,90 com25% permanece44,93
no pedido, pagamento e aquisição. A promoção expirada é observada pela consulta
pública antes da liberação do commit, sem relógio de teste ou SQL de alteração.

Dois processos Payments usam filas de entrada exclusivas e o mesmo banco próprio,
com limites500 e0. O pedido vem de uma compra HTTP real, e duas cópias de negócio
idênticas têm MessageIds diferentes. Um lock transacional por OrderId mantém ambos
esperando antes de liberar a disputa. A primeira decisão persistida determina
resultado, política e instante; o outro processo reutiliza esse winner. As duas
ordens de chegada exercitam Approved e Rejected. Depois, o host normal recebe o
pedido original ainda na sua fila e também deve preservar o resultado.

Os workers têm labels próprias, sobrescrevendo as labels Compose herdadas da imagem
para que não se confundam com o serviço principal. Seus IDs/Created são registrados,
e são removidos explicitamente antes da limpeza do projeto. A retomada do host
principal usa seu container existente porID/Created, sem reexecutar o bootstrap
restrito contra as filas extras deliberadas da fixture. Essas filas desaparecem
com o volume exclusivo ao final; não fazem parte da topologia de produção. Mensagens terminais
contraditórias são fixtures de conflito: Catalog e Notifications precisam preservar
a primeira decisão e o efeito correspondente, sem escolher um novo pagamento.

A evidência registra esperas observadas, fontes/imagens, pedidos e resultados;
InboxDelivered comprova consumo das duas cópias. Polling tem condição e prazo;
nenhuma prova depende de uma pausa arbitrária para supor concorrência.
