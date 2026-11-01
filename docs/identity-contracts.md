# Contratos dos eventos de identidade

Users é dono da identidade; cada consumidor mantém sua projeção e seu banco.
Os tipos são copiados em cada repositório sob `FCG.Contracts`, sem projeto de
runtime compartilhado nem importação do banco de outro serviço. `UserCreatedEvent`
é o fato de cadastro, `UserStateChangedEvent` contém o estado completo alterado e
`UserStateSnapshotEvent` recupera somente estado. O transporte MassTransit/RabbitMQ
já está ligado nos hosts e usa topologia durável provisionada pela orquestração.
As URNs são `urn:message:FCG.Contracts:UserCreatedEvent`,
`urn:message:FCG.Contracts:UserStateChangedEvent` e
`urn:message:FCG.Contracts:UserStateSnapshotEvent`.

Os três payloads contêm `eventId`, `userId`, `eventVersion`, `occurredAtUtc`,
`createdAtUtc`, `name`, `email`, `role`, `state`. IDs são GUIDs não vazios.
`eventVersion` é Int64 positivo, durável e monotônico por usuário, avançado quando
há mudança real de snapshot; independe do token de concorrência HTTP ou xmin.
Cadastro é Active v1; alterações começam na v2. `createdAtUtc` preserva o instante
do cadastro, igual a `occurredAtUtc` no cadastro e nunca posterior à ocorrência.
Instantes UTC usam exatamente seis casas decimais e Z. O produtor ajusta seu
relógio antes de criar o snapshot; não se trunca silenciosamente dado recebido.

Contato não tem espaços nas extremidades. E-mail é minúsculo, até 256 caracteres,
um único endereço aceito por .NET MailAddress com Address idêntico ao valor dado.
Domínios de um rótulo e partes locais entre aspas são válidos; display name e
ângulos ao redor do endereço não são canônicos. Normalização acontece antes da
publicação, nunca silenciosamente no consumo. Papéis: `User`, `Administrator`;
estados: `Active`, `Blocked`, `Deleted`.

`IdentityContractJson.Read<T>` / `Write<T>` definem o codec explícito: camelCase,
todos os membros obrigatórios, rejeição de membros desconhecidos/duplicados,
enums nomeados, UTC em microssegundos e validação do payload. Os adaptadores reais
de transporte preservam essas regras; não basta assumir a serialização padrão.
Senha, hash, token e chave não pertencem ao evento. Logs identificam evento,
usuário e versão sem payload completo ou contato.

`IdentityProjectionPolicy` é a decisão pura: estado ausente ou versão válida maior
aplica; versão menor ou equivalente é ignorada; mesma versão divergente é conflito;
Deleted não volta a ativo por versão maior. Catalog compara usuário/versão/estado/
papel; Notifications compara também nome/e-mail. EventId, instante de emissão e
tipo fato/recuperação não definem equivalência. A biblioteca de contratos não faz
persistência, locks, retry, outbox ou fanout. Os hosts implementam publicação por
outbox e consumo transacional com compare-and-write no banco próprio, diagnóstico
de conflitos e tratamento de reentrega.

Cadastro e estado são fatos independentes. `UserCreatedEvent` atrasado pode criar
uma única intenção Welcome, mesmo quando a projeção v1 já foi superada pela v3.
Snapshot de recuperação não cria Welcome nem reconstrói intenções perdidas.
Recuperação é operação explícita de Users pelo caminho durável de publicação,
sem consulta cruzada. Deleted é terminal; Blocked permite desbloqueio. Notifications
retém contato inclusive de excluídos para concluir notificações de pedidos aceitos.

Notifications congela nome/e-mail/versão coerentes quando a intenção fica Ready;
retries mantêm o destinatário. PendingContact não inventa destinatário: o loop
recuperável revisita pendências inclusive após restart e chegada de contato.
O worker usa `Dispatcher:PollSeconds`, padrão de 1 segundo. Persistência e despacho
já estão implementados no host Notifications. Intenção única não garante uma única
linha no console: crash entre emissão e commit pode repeti-la. Não há e-mail real.

Fixtures em `IdentityContracts/Fixtures` dos testes de cada serviço verificam
roundtrip e rejeições do contrato. Restore locked, build Release e suíte completa
estão no README do serviço. Os testes de codec/decisão pura têm esse escopo;
testes de HTTP, assinatura, persistência/outbox e transporte ficam nas respectivas
suítes de integração. A jornada real dos quatro hosts é verificada separadamente
em Compose e Kind pela orquestração.
