# RabbitMQ e gate de topologia

O Compose provisiona RabbitMQ 4.3.6-management por digest e um bootstrap .NET independente das aplicações. O broker usa hostname `rabbitmq`, node `rabbit@rabbitmq`, Mnesia em `/var/lib/rabbitmq/mnesia` e volume `/var/lib/rabbitmq`. A validação local foi em arm64/OrbStack; outra plataforma exige validar as imagens.

## Iniciar e verificar

Na primeira configuração, copie `.env.example` para `.env` e substitua os valores DEMO públicos. Se `.env` já existir, complete as variáveis RabbitMQ sem sobrescrever as credenciais PostgreSQL. As cinco identidades RabbitMQ precisam ser distintas. Os nomes aceitam letras ASCII, números, `_`, `.` e `-`; senhas são opacas e obrigatórias. Admin de bootstrap é separado das quatro aplicações.

Use um nome de projeto dedicado. Os exemplos usam `fcg-fase2`; se já criou este ambiente com outro nome, conserve esse nome para recuperar os mesmos volumes. Mudar `-p` seleciona outro conjunto de dados. Não reutilize o projeto da Fase 1.

```sh
docker --context orbstack compose --env-file .env -p fcg-fase2 up -d --wait postgres rabbitmq
docker --context orbstack compose --env-file .env -p fcg-fase2 run --rm --build rabbitmq-bootstrap
```

O segundo comando espera e propaga o exit code do bootstrap. Só exit 0 e `rabbitmq bootstrap ready` liberam os produtores. A espera é autenticada, tem orçamento global de 60 segundos (configurável de 1 a 300 por `RABBITMQ_BOOTSTRAP_TIMEOUT_SECONDS`) e inclui todas as operações HTTP. Falhas são não zero: `configuration_invalid`, `authentication_failed`, `topology_conflict`, `bootstrap_timeout` ou `bootstrap_failed`. O código não imprime senha, Authorization, resposta de usuário ou stack de erro.

No Compose integrado, as aplicações dependem do serviço one-shot `rabbitmq-bootstrap` com `condition: service_completed_successfully`; a saúde do broker, sozinha, não comprova que as filas existem. A execução manual com `run --rm` valida o ambiente atual; `up` executa o bootstrap idempotente antes dos quatro hosts. Em Kubernetes, o Job equivalente deve completar antes dos Deployments das aplicações. Veja [Compose](compose.md) e [Kubernetes](../k8s/README.md).

Portas locais: AMQP `127.0.0.1:5672`, Management `127.0.0.1:15672`; `RABBITMQ_PORT` e `RABBITMQ_MANAGEMENT_PORT` permitem escolher portas livres. Dentro da rede Compose, aplicações usam `rabbitmq:5672`, vhost `/fcg` e sua própria credencial `RabbitMq__Username/Password`. Os quatro usuários de aplicação têm configure/write/read `.*` exclusivamente nesse vhost e nenhuma tag administrativa. Isso não promete isolamento entre filas dentro de `/fcg`.

## Topologia e compatibilidade

Cada endpoint tem exchange `fanout` durável e fila `classic` durável do mesmo nome:

| Evento (`FCG.Contracts:`) | Endpoints/filas |
|---|---|
| UserCreatedEvent, UserStateChangedEvent, UserStateSnapshotEvent | fcg-catalog-identity e fcg-notifications-identity |
| OrderPlacedEvent | fcg-payments-orders |
| PaymentProcessedEvent | fcg-catalog-payments e fcg-notifications-payments |

O caminho é exchange do evento → exchange de endpoint → fila, routing key vazia. Os eventos de identidade herdam de `IdentitySnapshot`: o MassTransit 8.5.11 também declara `FCG.Contracts:IdentitySnapshot` e liga a ele cada exchange concreto de identidade. Essa base não tem fila própria. São **11 exchanges geridos, 5 filas e 17 bindings explícitos** (12 entre exchanges e 5 para filas), além do default exchange do broker.

Exchanges são fanout/durable, sem auto-delete, internal ou argumentos. Filas são classic/durable, sem auto-delete ou exclusive. RabbitMQ 4.3 materializa `x-queue-type=classic` nos metadados mesmo quando a declaração usa argumentos vazios; só essa equivalência é aceita. Argumentos adicionais, tipo de fila/exchange ou bindings de saída inesperados são conflito. Endpoints `_error`, `_skipped` e o endpoint temporário do bus podem ser criados pelo MassTransit depois; não são provisionados aqui. Não configurar formatos de nome diferentes nos serviços.

A probe usa o pacote real MassTransit 8.5.11 e cópias exatas dos contratos aprovados, com origem/hash em `tests/rabbitmq/Probes/Contracts/ORIGIN.json`. Publica os cinco tipos e verifica nove entregas, payload, URNs e correlação. As cópias são exclusivas de teste, não uma biblioteca compartilhada pelas aplicações.

## Persistência, repetição e reparo

Publicações anteriores à primeira subida do consumidor ficam retidas porque suas filas já existem. A prova usa AMQP 0-9-1 com mandatory, persistent e publisher confirms rastreados; uma publicação sem destino precisa falhar. HTTP Management `routed=true` não é usado como substituto de publisher confirm.

Para recriar somente o broker mantendo os dados:

```sh
docker --context orbstack compose --env-file .env -p fcg-fase2 up -d --force-recreate --wait rabbitmq
docker --context orbstack compose --env-file .env -p fcg-fase2 run --rm rabbitmq-bootstrap
```

Conserve o mesmo projeto, volume, hostname e nodename. Não apague o volume, não mude o caminho Mnesia e não trate fila vazia recriada como prova de recuperação. O teste confirma a publicação persistente antes da recriação, mantém as mensagens sem consumo e só recebe/ack depois, nas duas filas. Não há promessa de HA, perda do volume ou exatamente uma entrega.

Bootstrap lê todos os recursos conhecidos antes de criar ausentes e faz readback completo. Repetição equivalente preserva mensagens, usuários e senhas; não há delete, purge ou rotação implícita. Falha parcial da API pode deixar recursos novos compatíveis; uma repetição completa os ausentes. O processo não é uma transação global nem deve concorrer com manutenção administrativa da topologia.

Tags, credenciais, permissões, argumentos ou bindings divergentes falham e exigem reparo explícito. Mudar senha no `.env` não altera o usuário no volume existente: o admin pode falhar na autenticação; uma app divergente produz conflito. Para rotacionar, faça a operação administrativa deliberada sem pôr senha em argumentos/logs, depois sincronize a configuração privada dos clientes. Não apague filas com mensagens para contornar um conflito.

Para parar mantendo os dados, use `compose ... stop rabbitmq`. Limpeza deve identificar e conferir a posse dos recursos do projeto; não use prune global. O guia PostgreSQL descreve seu próprio volume e recuperação.

## Testes reais

Requisitos: SDK conforme `global.json`, Docker/Compose e Python 3. O script usa apenas biblioteca padrão Python; RabbitMQ.Client e MassTransit ficam exclusivamente nas probes .NET, com lockfile.

```sh
DOCKER_CONTEXT=orbstack tests/rabbitmq/run.sh
python3 tests/postgres/test_postgres.py --context orbstack --case all
```

`DOTNET` permite apontar o executável de SDK quando necessário. `FCG_RABBITMQ_EVIDENCE_DIR` preserva IDs, labels, criação, portas e resultados de cleanup sem Config.Env. Cada execução usa credenciais geradas, portas loopback efêmeras e recursos próprios. O cleanup descobre também uma criação interrompida antes de registrar o ID e remove apenas identidades/labels da própria execução.

A suite cobre o Compose entregue, gate one-shot, ausência de consumidor, fanout, persistência/recriação, idempotência, falhas de auth/config/timeout, vhost/admin negados, drift preservado e publicação mandatory sem destino. Uma fixture de conflito ativa **somente naquele broker descartável** a opção legada `deprecated_features.permit.transient_nonexcl_queues=true`, para criar uma fila não durável incompatível e provar a preservação do marcador. O Compose de produto mantém a rejeição padrão dessas filas em RabbitMQ 4.3 ([documentação oficial](https://www.rabbitmq.com/docs/queues)).

Esses testes não provam consumidores de negócio, outbox, APIs ou Kubernetes. O PostgreSQL herdado deve continuar passando; a fixture fornece explicitamente as variáveis RabbitMQ porque Compose interpola o arquivo inteiro mesmo ao subir só Postgres.
