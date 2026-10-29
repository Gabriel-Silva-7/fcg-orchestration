# Contratos de compra v1

Os contratos representam compra e pagamento simulados em BRL. Os arquivos documentam os payloads e os DTOs disponíveis; endpoints, consumidores, tabelas, publicação no broker e regras de simulação são implementados pelos respectivos serviços. A [jornada integrada](compose.md) verifica esses comportamentos com os quatro hosts reais.

## Eventos

Namespace público: `FCG.Contracts`. Tipos e URNs de mensagem esperadas na integração MassTransit:

| Tipo | URN |
|---|---|
| OrderPlacedEvent | `urn:message:FCG.Contracts:OrderPlacedEvent` |
| PaymentProcessedEvent | `urn:message:FCG.Contracts:PaymentProcessedEvent` |

Cada serviço compila sua própria cópia, sem referência de projeto entre serviços. Identidade de negócio inclui todos os campos do record; `MessageId`, headers e tentativas pertencem ao transporte e não estão no payload. Esses tipos não implementam deduplicação persistida. A URN decorre do namespace/nome público; transporte e fan-out são verificados na jornada integrada.

```json
{"orderId":"11111111-1111-1111-1111-111111111111","userId":"22222222-2222-2222-2222-222222222222","gameId":"33333333-3333-3333-3333-333333333333","price":44.93,"placedAtUtc":"2026-10-19T12:34:56.123456Z"}
```

```json
{"orderId":"11111111-1111-1111-1111-111111111111","userId":"22222222-2222-2222-2222-222222222222","gameId":"33333333-3333-3333-3333-333333333333","price":44.93,"status":"Approved","processedAtUtc":"2026-10-19T12:34:58.123456Z"}
```

```json
{"orderId":"11111111-1111-1111-1111-111111111111","userId":"22222222-2222-2222-2222-222222222222","gameId":"33333333-3333-3333-3333-333333333333","price":500.01,"status":"Rejected","reason":"AmountAboveLimit","processedAtUtc":"2026-10-19T12:34:58.123456Z"}
```

Os exemplos são payloads, sem envelope de broker. Ordem das propriedades não tem significado. Os conversores locais de System.Text.Json garantem camelCase e o mesmo resultado com opções padrão ou Web, sem mudar a configuração global de outros contratos. No Catalog, tipos e codecs BCL ficam juntos em Application para os atributos de serialização não criarem dependência em Infrastructure; Domain permanece independente de wire/JSON. Nos workers ficam no próprio projeto.

## Validação e precisão

- Todos os GUIDs são obrigatórios e não vazios.
- `price` é decimal JSON numérico, sem moeda selecionável pelo cliente; representa BRL `numeric(18,2)`, de `0.00` a `9999999999999999.99`, sem fração de centavo. A leitura avalia o número exato antes da conversão decimal, evitando arredondamento silencioso de entradas longas. Representações equivalentes como `44.9300` e `4.493e1` são aceitas; a escrita usa duas casas.
- Instantes são `DateTimeOffset` com offset zero e ticks múltiplos de 10. JSON exige `yyyy-MM-ddTHH:mm:ss.ffffffZ`, sempre seis casas. UTC sem fração, offset escrito `+00:00` e sétima casa são rejeitados; não existe truncamento silencioso. Produtores devem gerar instantes nessa precisão. O intervalo é o de DateTimeOffset; não se proíbe futuro/passado no contrato.
- `Approved` exige Reason null; sua escrita omite `reason` (a leitura aceita ausência ou null). `Rejected` exige exatamente `AmountAboveLimit`. Status e motivo são sensíveis a maiúsculas. O único motivo v1 tem 16 caracteres.
- Todos os campos descritos são obrigatórios, exceto Reason em Approved. Raiz null, propriedades desconhecidas/duplicadas, valores inválidos e campos ausentes são falha de contrato. Argumentos inválidos em construção direta também falham. Nenhuma falha de contrato vira rejeição financeira.
- Records imutáveis comparam todos os campos, inclusive timestamps. Mesma identidade com outra data, preço, usuário, jogo ou decisão é divergência. Evolução aditiva de campos exige compatibilidade/versionamento explícitos porque os leitores v1 são estritos.

Preservar o instante e o payload originais nas republicações é responsabilidade da persistência de cada serviço. A precisão adotada é representável em PostgreSQL; estes testes não exercitam PostgreSQL nem prometem entrega/exatamente uma emissão.

## HTTP de pedidos

Os tipos HTTP vivem somente no Catalog. O contrato previsto é:

| Operação | Resposta |
|---|---|
| POST `/api/v1/me/library` com `{gameId}` | 202 com `{orderId,gameId,price,status:"Pending"}` e header Location `/api/v1/me/orders/{orderId}` |
| GET `/api/v1/me/orders/{orderId}` | 200 para titular; 404 `resource_not_found` para inexistente ou alheio |
| GET `/api/v1/me/orders` | 200 `{items,page,pageSize,totalCount}`; apenas pedidos do titular |
| GET `/api/v1/me/library` | Mantém histórico, paginação e isolamento |

`PurchaseRequest` não aceita UserId; a identidade vem do JWT. `PurchaseAcceptedResponse.Location` é metadado para o header, não campo do corpo. Detalhes usam exatamente `orderId,gameId,price,status,placedAtUtc,completedAtUtc,rejectionReason`, com nulls explícitos: Pending sem conclusão/motivo, Approved com conclusão e motivo null, Rejected com conclusão e AmountAboveLimit. Não incluem contato/identidade do titular. `PurchaseOrdersQuery` fixa padrão 1/20, page 1–1000000 e pageSize 1–100. O repositório ordena PlacedAtUtc decrescente e OrderId crescente; o DTO de página não executa consultas nem ordenação.

Após autenticação/autorização: GameId inválido resulta 400 `validation_error`; jogo inexistente/inativo resulta404 antes da duplicidade. Jogo disponível já adquirido/Approved resulta409 `game_already_acquired`. Somente Pending resulta409 `order_already_pending`. Neste último caso o corpo Problem Details preserva type/title/status/instance/code/traceId e acrescenta `orderId` e `location`, e a resposta deverá fornecer o mesmo header Location. O DTO `PendingOrderConflict` só pode ser preenchido após verificar titularidade; não faz autorização. `type` usa `urn:fcg:error:order-already-pending`. Os demais erros conservam o formato herdado, com `resource_not_found` ou `game_already_acquired` e sem referência a pedido alheio.

A precedência após disputa de banco reconsulta o estado confirmado: Approved/biblioteca é adquirido, Pending é pendente. Rejeição permite nova tentativa com outro OrderId. POST não tem chave de idempotência: após resposta perdida, consultar os próprios pedidos antes de decidir nova tentativa. Um primeiro GET pode observar estado terminal.

## Verificação local

Este repositório não contém projeto .NET. Executar nos repositórios Catalog, Payments e Notifications, com o SDK fixado por seu `global.json`:

```sh
dotnet test -p:RestoreLockedMode=true
dotnet test --no-restore --filter FullyQualifiedName~PurchaseContracts
dotnet build -c Release --no-restore
```

As suites usam arquivos JSON literais próprios em `tests/<projeto>/PurchaseContracts/fixtures`, serialização real, fronteiras de dinheiro/tempo, equivalência e falhas de contrato. A validação local não comprova o envelope/fan-out do broker, rotas HTTP, transações, isolamento de consulta ou armazenamento. Essas garantias exigem testes de integração próprios.
