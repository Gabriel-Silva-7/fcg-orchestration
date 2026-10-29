# Recuperação por serviço e transporte

Os comandos locais dos serviços usam a conexão do próprio banco e não abrem HTTP,
consumidores, dispatcher, migrations ou seed. A prévia é o padrão; `--apply` precisa
ser explícito. O schema deve ser provisionado pelo procedimento normal do ambiente.
Inspeção retorna IDs e metadados técnicos, sem payload ou contato. Não editar
payload/hash, apagar inbox ou recriar pedido para fazer um replay parecer correto.

## Catalog

```sh
docker --context orbstack compose --env-file .local/demo/demo.env exec -T catalog \
  dotnet FCG.Catalog.Api.dll recover-payments list --limit 20
docker --context orbstack compose --env-file .local/demo/demo.env exec -T catalog \
  dotnet FCG.Catalog.Api.dll recover-payments show --id <conflict-guid>
docker --context orbstack compose --env-file .local/demo/demo.env exec -T catalog \
  dotnet FCG.Catalog.Api.dll recover-payments replay --id <conflict-guid> --dry-run
docker --context orbstack compose --env-file .local/demo/demo.env exec -T catalog \
  dotnet FCG.Catalog.Api.dll recover-payments replay --id <conflict-guid> --apply
```

Preview usa snapshot read-only, sem contador, resolução ou tentativa. É pontual e
pode mudar antes da aplicação. Efeito, resolução e tentativa do replay real são
atômicos. Sem pedido compatível, mantém OrderNotFound; não cria Order. Restauração
é uma operação separada do dono, a partir de fonte íntegra e preservando a compra
original. Conflito contra terminal continua pendente. Um diagnóstico de identidade
mínima não é um evento completo: estado de identidade deve vir de reconciliação
por snapshots emitidos no Users.

## Payments

```sh
docker --context orbstack compose --env-file .local/demo/demo.env exec -T payments \
  dotnet FCG.Payments.dll recover-orders list --limit 20
docker --context orbstack compose --env-file .local/demo/demo.env exec -T payments \
  dotnet FCG.Payments.dll recover-orders replay --id <conflict-guid> --dry-run
docker --context orbstack compose --env-file .local/demo/demo.env exec -T payments \
  dotnet FCG.Payments.dll recover-orders replay --id <conflict-guid> --apply
```

Leitura precisa somente do banco; apply precisa da configuração normal do serviço,
incluindo o mesmo destino AMQP, para preparar a outbox sem iniciar host ou bus.
Decisão financeira ausente ou divergente permanece em quarentena: replay nunca
cria uma nova simulação. O resultado persistido precisa corresponder ao winner;
se ausente, pode ser reconstruído da decisão original. Resolução, tentativa em
PaymentRecoveryAttempts e saída durável são atômicas. `StagedOriginalResult` não
significa entrega imediata: o host normal entrega a outbox ao retomar. Repetir
conflito resolvido registra AlreadyResolved sem preparar outra publicação.

## Notifications

```sh
docker --context orbstack compose --env-file .local/demo/demo.env exec -T notifications \
  dotnet FCG.Notifications.dll recover-notifications conflicts
docker --context orbstack compose --env-file .local/demo/demo.env exec -T notifications \
  dotnet FCG.Notifications.dll recover-notifications replay --id <conflict-guid> --dry-run
docker --context orbstack compose --env-file .local/demo/demo.env exec -T notifications \
  dotnet FCG.Notifications.dll recover-notifications replay --id <conflict-guid> --apply
docker --context orbstack compose --env-file .local/demo/demo.env exec -T notifications \
  dotnet FCG.Notifications.dll recover-notifications intents
docker --context orbstack compose --env-file .local/demo/demo.env exec -T notifications \
  dotnet FCG.Notifications.dll recover-notifications intent --id <intent-guid>
docker --context orbstack compose --env-file .local/demo/demo.env exec -T notifications \
  dotnet FCG.Notifications.dll recover-notifications resume --id <intent-guid> --apply
```

Replay valida a cópia original e usa a mesma regra dos consumidores. Cadastro
atrasado continua sujeito à deduplicação de Welcome; snapshot não implica cadastro.
Pagamento divergente conserva a primeira observação. Uma confirmação persistida
sem sua observação é restauração inconsistente; restaurar a observação coerente
antes de reaplicar, sem selecionar outra decisão financeira.

Resume prepara contato pendente se disponível e antecipa retry de Ready em backoff.
Não muda destinatário fixado, não rouba lease ativo e não marca Sent ou emite console.
Lease expirado é retomado pelo dispatcher, que registra Abandoned. Sent permanece
concluído. A inspeção distingue RecoveryAttempts do operador e NotificationAttempts
de entrega. Crash após console e antes da confirmação ainda pode repetir a mesma
linha/ID; não há promessa de console exatamente uma vez.

## Redrive de `_error`

A ferramenta em `tools/FCG.Messaging.Operations` atua somente em cinco filas do
projeto e suas correspondentes `_error`. Requer `AMQP_HOST`, `AMQP_PORT`,
`AMQP_VHOST` (padrão `/fcg`), `AMQP_USERNAME` e `AMQP_PASSWORD`. Use a credencial do
serviço dono e passe segredos por ambiente privado, sem colocá-los nos argumentos.

```sh
dotnet restore tools/FCG.Messaging.Operations/FCG.Messaging.Operations.csproj --locked-mode
dotnet build tools/FCG.Messaging.Operations/FCG.Messaging.Operations.csproj -c Release --no-restore
dotnet tools/FCG.Messaging.Operations/bin/Release/net10.0/FCG.Messaging.Operations.dll \
  error-redrive --queue fcg-catalog-payments --message-id <message-guid> --dry-run
dotnet tools/FCG.Messaging.Operations/bin/Release/net10.0/FCG.Messaging.Operations.dll \
  error-redrive --queue fcg-catalog-payments --message-id <message-guid> --apply
```

Também pode ser construída como imagem multi-stage, com bases fixadas por digest:

```sh
docker --context orbstack build -f tools/FCG.Messaging.Operations/Dockerfile -t fcg-messaging-operations:local .
```

Executar essa imagem na rede do projeto requer os mesmos valores AMQP pelo
ambiente, com host `rabbitmq` e porta5672. Nenhuma porta HTTP é exposta pela ferramenta.
Não é um serviço iniciado no Compose por padrão.

A seleção é pelo MessageId GUID original, com até100 mensagens examinadas
(`--scan-limit 1..100`). Sem `--apply`, faz somente prévia e recoloca mensagens na
fila. Apply exige mensagem persistente, publica com confirmação e mandatory
somente na fila original pelo exchange padrão, preservando corpo, propriedades e
IDs; o ack da origem vem depois. Outras mensagens voltam para a fila. Não cria ou
repara filas. Destino ausente, falha ou dry-run deixam a origem recuperável.
Crash após publish e antes do ack pode duplicar: a aplicação usa suas regras
idempotentes. `_error` não substitui o replay de um conflito já confirmado pelo
consumidor, que deve chamar a aplicação local do dono.

Códigos dos CLIs de serviço:0 concluído/estado já atendido;1 falha segura;
2 argumentos inválidos;3 alvo ausente;4 pendência/conflito preservado. No redrive,
0 significa alvo encontrado (prévia) ou confirmado (apply);3 significa não encontrado
na varredura limitada. Nenhum comando tenta resolver pendências apagando dados.

## Verificação

```sh
python3 -m unittest discover -s tests/operations -v
```

Os testes usam broker real e recursos descartáveis próprios: prévia sem escrita,
ack após confirmação, preservação de mensagens não selecionadas, headers/corpo/IDs,
destino ausente e falha de autenticação. Os testes dos serviços cobrem efeitos e
resolução na mesma transação, previews read-only, CLI em processo separado,
concorrência, conflitos e retomada por lease. A prova sistêmica do broker e a prova
em Kubernetes são registradas separadamente da execução local dos componentes.

## Prova sistêmica descartável

Prepare outro ambiente privado com `scripts/prepare-local.py`, selecionando os
quatro checkouts limpos que contêm os comandos acima. A prova recusa projeto já
existente, registra imagens/SHAs/IDs e remove somente os recursos de seu projeto.
Ela cria dados HTTP únicos e executa também fixtures explícitas de falha no banco;
não aponte para um ambiente em uso. Compile primeiro as ferramentas de teste:

```sh
dotnet restore tests/rabbitmq/Probes/RabbitMq.Probes.csproj --locked-mode
dotnet build tests/rabbitmq/Probes/RabbitMq.Probes.csproj -c Release --no-restore
dotnet restore tools/FCG.Messaging.Operations/FCG.Messaging.Operations.csproj --locked-mode
dotnet build tools/FCG.Messaging.Operations/FCG.Messaging.Operations.csproj -c Release --no-restore
python3 scripts/recovery.py --env-file /caminho/privado/novo/demo.env \
  --dotnet /caminho/para/dotnet --output /caminho/novo/para/evidencias \
  --allow-owned-fault-fixtures
```

A primeira parte não apaga pedidos nem altera dados: consumidores iniciam depois
da publicação, o broker é realmente recriado com o mesmo volume/identidade e o
mesmo pedido HTTP termina. Uma compra de controle comprova o limite zero antes de
reentregar o pedido original sob essa configuração; seu resultado deve permanecer
inalterado. Outra mensagem original passa por retries, `_error`, reparo da causa e
redrive confirmado com o mesmo MessageId.

As partes seguintes são fixtures distintas de restauração parcial autorizada:
Payments recebe temporariamente um winner inconsistente, depois restaura seu valor
original; Catalog perde uma linha Pending previamente copiada do próprio banco e
recebe o resultado real em quarentena. Prévia/apply sem essa linha não criam pedido.
Somente restaurar a mesma linha/ID/snapshot permite aplicar atomicamente resultado,
aquisição e resolução. Nenhum diagnóstico tem seu payload/hash editado. Notifications
mantém Sent e a primeira observação após uma mensagem contraditória de teste. Essas
injeções não representam perda no cenário de persistência do broker nem um roteiro
automático de restauração de produção. A evidência distingue cada caso.
