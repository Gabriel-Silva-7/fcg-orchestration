# Requisitos e caminho de demonstração

Referência: enunciado oficial Tech Challenge .NET, Fase2, sete páginas. A tabela
mapeia os requisitos literais aos procedimentos executáveis do código preparado.
Disponibilidade de código local e evidência de execução não significam publicação,
aprovação humana ou submissão. Use os SHAs e imagens registrados na execução.

| Requisito do enunciado | Página | Onde conferir e demonstrar |
|---|---:|---|
| Quatro serviços e repositórios independentes | 2–3 | [Tabela principal](../README.md), soluções e Dockerfile em cada repositório; [arquitetura](architecture.md) |
| Cadastro, autenticação JWT e autorização | 3 | README Users; login real e permissões na [jornada integrada](compose.md) |
| CRUD de jogos e início da compra | 3 | README Catalog; criação, edição, desativação e POST me/library no E2E |
| Pagamento simulado | 3 | README Payments; Approved44,93/Rejected600 sob limite500 no E2E |
| Boas-vindas e confirmação no console | 3–4 | README Notifications; intenção Sent acompanhada do log real no E2E |
| Cadastro → UserCreatedEvent → Welcome | 3 | Cadastro HTTP real, transporte e console na jornada |
| OrderPlacedEvent com UserId/GameId/Price | 3 | POST Catalog, outbox e consumo real Payments; [contratos](purchase-contracts.md) |
| PaymentProcessedEvent Approved/Rejected | 3–4 | Decisão real Payments, duas filas independentes Catalog/Notifications |
| Approved adiciona biblioteca e gera confirmação | 4 | GET biblioteca, registro da intenção e console; Rejected sem esses efeitos |
| Dockerfile próprio e multi-stage | 4 | Raiz dos quatro repositórios, SDK/runtime fixados por digest |
| .NET8 ou superior e solução/projeto próprios | 4 | net10.0, global.json e solução em cada repositório |
| /k8s por serviço, Deployments, ConfigMaps e Secrets | 4 | Templates de cada serviço; agregação privada em04-applications; [guia Kind](../k8s/README.md) |
| Compose executa aplicação completa | 5 | Preparar fontes/env e executar compose up --build conforme [guia](compose.md) |
| Biblioteca robusta de mensageria .NET | 5 | MassTransit8.5.11 nos quatro hosts; RabbitMQ.Client nas ferramentas operacionais |
| Comunicação interna por nome de Service | 5 | postgres/rabbitmq e Services das aplicações; diagnóstico DNS/HTTP e jornada no Kind |
| Deploy testado em cluster local | 5 | Kind: apply por fases, get pods, rollout, jornada, reinício de Pods e persistência |
| README com finalidade e variáveis por serviço | 6 | README de cada repositório e seus guias de Kubernetes |
| README principal com execução Docker/Kubernetes | 6 | [README principal](../README.md), [Compose](compose.md), [Kind](../k8s/README.md) |
| Links dos quatro repositórios e orquestração | 5 | URLs de origem no README; acesso do avaliador e revisões finais precisam ser conferidos na release |
| Vídeo de até20min com repositórios, Compose, fluxos e deploy | 5 | Material de gravação e mídia final têm aceite separado; documentação textual não comprova vídeo |
| Relatório PDF/TXT e postagem na data de entrega | 6 | O relatório deve conter os dados confirmados dos participantes e os links finais; sua conferência é separada das provas técnicas |

## Leitura das evidências

O E2E não insere fatos de compra por SQL: usa HTTP, transporte e consumidores reais.
Consultas SQL de observação usam a role do próprio banco. Os procedimentos de
[recuperação](recovery.md), [concorrência](concurrency.md) e
[identidade fora de ordem](identity-ordering.md) documentam cenários extras; fixtures
de restauração são identificadas separadamente dos pedidos reais.

A prova Kubernetes registra as fontes, arquivos renderizados, imagens exportadas,
identificadores no runtime e recursos efetivos. Um digest de índice OCI, um digest
de configuração e o imageID de um Pod podem ser diferentes; o vínculo entre eles
precisa ser verificado, sem chamar identificador local de digest publicado.

Outbox/inbox, deduplicação, persistência das simulações, recuperação operacional,
RS256,15min, limite500, atraso2s, promoções, TDD e duas auto-revisões são decisões de
engenharia do projeto. O enunciado exige os comportamentos e recursos da tabela,
sem prescrever esses mecanismos ou valores específicos.
