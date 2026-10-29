# Identidade e contato com entrega fora de ordem

A prova usa quatro hosts reais em outro ambiente privado descartável, preparado
por `scripts/prepare-local.py` com fontes limpas. O primeiro administrador recebe
um segundo administrador pelo bootstrap existente; nenhum endpoint de promoção é
criado. O override com a credencial nova fica apenas na pasta privada do ambiente.
A configuração normal é restaurada antes dos cenários.

```sh
dotnet restore tests/rabbitmq/Probes/RabbitMq.Probes.csproj --locked-mode
dotnet build tests/rabbitmq/Probes/RabbitMq.Probes.csproj -c Release --no-restore
python3 scripts/identity-ordering.py --env-file /caminho/privado/novo/demo.env \
  --dotnet /caminho/para/dotnet --output /caminho/novo/para/evidencias
```

Filas próprias de holding recebem os eventos originais de Users enquanto os
bindings para os consumidores são suspensos. Inspeção usa requeue; não há purge,
ack de descarte ou alteração de payload/hash para mudar fatos. A liberação copia
corpo e MessageId por AMQP confirmado para a fila original. A cópia em holding
permanece até a limpeza do volume descartável: a fixture admite duplicação, sem
alegar entrega exatamente uma vez. Duplicatas com novo MessageId exercitam também
a deduplicação permanente por identidade/versão/fato de cadastro.

O cadastro autentica em Users enquanto Catalog recebe503 e Retry-After1 por
projeção desconhecida. Depois de liberar o cadastro, uma compra real é aceita com
Payments suspenso. O usuário muda contato e é bloqueado. Users nega imediatamente;
a projeção antiga de Catalog continua válida durante o atraso controlado. Ao
receber o bloqueio, Catalog nega o mesmo JWT e eventos antigos não o desbloqueiam.
Retomar Payments ainda deve concluir a compra já aceita e adicionar a aquisição.

Notifications recebe a aprovação antes do contato e mantém PendingContact sem
recipient ou emissão antecipada. A reconciliação local de Users publica um snapshot
real; ele prepara a confirmação com o contato atual, sem inventar Welcome. O fato
de cadastro antigo pode chegar depois e gerar seu Welcome único, sem regredir a
projeção. Alterações posteriores de contato não mudam recipient já fixado nem
reemitem uma intenção Sent. O console contém somente endereço mascarado.

Com bindings normais restaurados, o relatório mede bloqueio, desbloqueio e exclusão
de usuário e administrador usando tokens reais. O intervalo monotônico começa
antes da requisição Users e termina na observação HTTP da projeção Catalog;
<=5s é objetivo verificado apenas nesse ambiente saudável. O atraso deliberado
maior que5s é registrado separadamente e demonstra que essa meta não é promessa
sob falha. Conta excluída permanece terminal diante de comandos de desbloqueio,
eventos antigos e uma fixture explícita de restauração inválida em versão maior.

JWT de produção permanece em15min/RS256/ClockSkew0. Uma fixture de teste usa a
chave privada do próprio ambiente para assinar claims já expiradas, derivadas de
um token real. Ela verifica rejeição antes da projeção; não é um token expirado
emitido pela API Users nem uma redução da política de duração. Chave, JWTs,
credenciais, contatos e payloads não são gravados no relatório de evidência.

Cleanup identifica somente os recursos do projeto fresco porID/Created/labels.
A prova não consulta banco alheio durante a execução dos serviços, não restaura
contas excluídas e não muda regras de autorização para obter um resultado favorável.
