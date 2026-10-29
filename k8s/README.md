# Kubernetes local

Os templates de infraestrutura estão em `templates/`. Eles usam Deployments,
Services, ConfigMaps, Secrets e PVCs; o bootstrap da topologia é um Job separado.
Valores reais são renderizados em pasta privada. **Não aplique os templates
com placeholders diretamente.** Os recursos dos quatro serviços entram em seus
próprios repositórios no incremento de implantação das aplicações.

O alvo exercitado é Darwin/arm64 com Docker do OrbStack, Kind0.33.0,
kubectl1.37.0 e node1.37.0 fixado por digest. As ferramentas locais e seus checksums
oficiais estão em [`versions.json`](../scripts/kubernetes/versions.json).
[Release oficial Kind](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0).
O helper não substitui binários globais nem altera o kubeconfig pessoal.

```sh
python3 scripts/kubernetes/prepare-tools.py --output .tools/kubernetes
```

Primeiro prepare outro ambiente privado com `scripts/prepare-local.py`, escolhendo
os quatro checkouts limpos. Depois renderize em um diretório novo:

```sh
python3 scripts/kubernetes/render.py --env-file /caminho/privado/demo.env \
  --output /caminho/privado/rendered
```

Cada render tem uma revisão aleatória e nomes próprios de ConfigMap/Secret; a
referência no PodTemplate muda ao renderizar nova configuração. O identificador
não deriva de senhas. Reaplicar exatamente o mesmo pacote preserva referências e
PVCs. Modificar senha de ambiente não altera automaticamente a role já persistida:
rotação precisa do procedimento explícito PostgreSQL.

As fases são diretórios finais de YAML:

1. `00-namespace`: namespace próprio.
2. `01-infrastructure`: configuração, Secrets, PVCs e Deployments PostgreSQL/RabbitMQ.
3. `02-bootstrap`: Job da topologia, somente depois do rollout da infraestrutura.
4. `03-diagnostics`: Deployment isolado para comprovar DNS e HTTP por Service.

O helper usa `kubectl apply -f <diretório>` em fases, com dry-run no servidor para
as fases posteriores ao namespace. Não presume que ordenar arquivos espere por
workloads: rollout, PVC Bound e Job Complete são gates explícitos. O StorageClass
WaitForFirstConsumer precisa dos Deployments antes da espera por Bound.
As aplicações serão aplicadas somente após o Job completar.

A prova descartável de infraestrutura cria cluster/kubeconfig próprios, importa
imagens locais e executa as fases:

```sh
python3 scripts/kubernetes/verify-infrastructure.py \
  --rendered /caminho/privado/rendered --tools .tools/kubernetes \
  --dotnet /caminho/para/dotnet --output /caminho/novo/infra-evidence.json
```

Ela verifica isolamento dos quatro bancos, Service DNS, persistência após troca
real de Pod e mensagem AMQP confirmada com consumidor ausente antes da troca do
broker. Reaplicar o pacote não pode trocar PVC. O relatório relaciona fonte Docker,
config blob exportado para arm64 e imagem observada no containerd; índice OCI,
config digest e imageID do Pod não são tratados como o mesmo identificador.
A importação usa `docker image save --platform linux/arm64` e `kind load
image-archive`, com referências locais únicas e `imagePullPolicy: Never`.

O PostgreSQL reutiliza os scripts de provisionamento/saúde da infraestrutura
Compose, com credenciais próprias por banco. RabbitMQ mantém hostname rabbitmq,
nodename rabbit@rabbitmq e Mnesia no mesmo PVC. O entrypoint da imagem prepara as
permissões do volume e reduz o usuário do processo; a prova verifica o UID real.

A execução remove apenas o cluster/volumes/rede que criou e identificou, preserva
containers preexistentes e confere o kubeconfig pessoal. Não execute junto com o
Compose completo na VM medida. Persistência validada é troca de Pod no mesmo
cluster/node/PVC; não representa HA ou recuperação após perda do volume/cluster.
Esta prova de infraestrutura não substitui a jornada dos quatro serviços no cluster.

## Quatro serviços

Os templates `k8s/configmap.yaml`, `secret.yaml`, `deployment.yaml` e `service.yaml`
são mantidos no respectivo repositório Users/Catalog/Payments/Notifications. Depois
de preparar fontes limpas, acrescente `--applications` ao render:

```sh
python3 scripts/kubernetes/render.py --env-file /caminho/privado/demo.env \
  --output /caminho/privado/rendered --applications
```

A fase `04-applications` agrega essas fontes pelos SHAs completos e hashes dos
arquivos de origem. Antes de cada apply, o helper reconstrói o conteúdo esperado
com a configuração privada e rejeita divergência tanto da infraestrutura quanto
dos serviços. A fase produtora também exige Job de topologia Complete. Não há
segunda cópia manual de manifestos para manter sincronizada.

Cada Deployment usa uma réplica, rollout com no máximo uma réplica adicional,
startup/liveness em `/health/live`, readiness em `/health/ready`, UID1654 e Service
ClusterIP80→8080. Credenciais e bootstrap ficam em Secrets; chaves JWT têm Secret
separado, pública em Users/Catalog e privada apenas em Users. GSS local é desabilitado
explicitamente sem alterar SslMode. Paths, filas e nomes de configuração seguem o
mesmo contrato do Compose.

A prova completa de implantação constrói/importa as quatro imagens, testa o gate
negativo, executa cadastro/login e compras reais, confere UID/DNS/chaves e aplica
uma revisão de configuração700 e depois500:

```sh
python3 scripts/kubernetes/verify-applications.py \
  --rendered /caminho/privado/rendered --tools .tools/kubernetes \
  --output /caminho/novo/application-evidence.json
```

Cada alteração é renderizada como novo pacote privado e aplicada nas fases, com
rollout de infraestrutura, novo gate de topologia e rollout das aplicações. As
mesmas senhas e chaves devem ser preservadas; o helper recusa rotação implícita.
A configuração efetiva de Payments é observada no Pod e por uma compra600:
Approved sob700, Rejected após restaurar500. Tokens anteriores, pedidos, pagamentos,
aquisição44,93 e intenções Sent permanecem válidos. Reaplicar o último pacote sem
mudanças não deve alterar geração dos Deployments ou seus Pods.

O kubeconfig e o cache de descoberta são privados. O contexto dedicado recebe o
namespace do projeto, permitindo comandos de apresentação com `KUBECONFIG` apontado
para esse arquivo; o arquivo pessoal permanece inalterado. Os forwards são processos
filhos próprios em loopback, fechados antes de trocar Pods e reconectados para a nova
revisão. Recuperação automática e testes adicionais de lifecycle ficam na jornada
Kubernetes. A prova termina removendo somente o cluster e os recursos que criou.

## Jornada e acesso supervisionado

A jornada pública usa as mesmas asserções de negócio do Compose, com adaptação de
acesso, gate e identificação dos recursos. Em cluster preparado com esse pacote:

```sh
scripts/e2e.sh --target k8s --rendered /caminho/privado/rendered \
  --tools .tools/kubernetes --output /caminho/novo/journey.json
```

O comando cria e fecha somente seus próprios forwards em portas aleatórias. Ele
confere namespace, Job Complete, versão de configuração/sourceSHA dos Pods e a
cadeia de imagem contra os archives preparados. A evidência já existente não é
sobrescrita. O cluster continua sob responsabilidade de quem o preparou.

Para acesso persistente por outro terminal/cliente HTTP, use o supervisor em
foreground, com status privado. As portas padrão são8081/8082/8083/8084 e15672;
qualquer uma pode ser alterada com `--users-port`, `--catalog-port`,
`--payments-port`, `--notifications-port` ou `--rabbitmq-port`. Valor0 escolhe uma
porta livre e a registra no status:

```sh
python3 scripts/kubernetes/access.py --rendered /caminho/privado/rendered \
  --tools .tools/kubernetes --status-file /caminho/privado/access.json
```

`ready` só é anunciado após HTTP readiness e Management autenticado responderem.
Porta ocupada é recusada antes de iniciar filhos; seu dono não é encerrado. Após
troca do Pod, o supervisor tenta selecionar novamente o Service, preservando a
porta local. Orçamento padrão:90s e até5 tentativas por indisponibilidade; flags
`--recovery-seconds` e `--max-attempts` podem reduzir o teste. Esgotamento retorna
falha visível, não um endereço falsamente Ready. Ctrl+C/SIGTERM encerra somente os
filhos próprios. Os pipes são drenados para não bloquear sessões com muitas conexões.

A prova descartável completa executa duas jornadas por esse CLI, depois controla
parada de Payments, compra HTTP aceita e troca do Pod RabbitMQ com a mensagem
original ainda na fila. Só retoma Payments após conferir o mesmo corpo/MessageId
no mesmo PVC. Também substitui PostgreSQL e os quatro Pods de API, confere dados,
chaves, Services e contadores e testa colisão/reconexão/encerramento/esgotamento do
supervisor:

```sh
python3 scripts/kubernetes/verify-journey.py \
  --rendered /caminho/privado/rendered --tools .tools/kubernetes \
  --dotnet /caminho/para/dotnet --output /caminho/novo/kind-journey.json
```

Parada completa significa ausência de todos os Pods do consumidor, incluindo os
que ainda estão em Terminating; um Pod encerrando ainda pode processar trabalho.
Para leitura de logs após recuperação, a seleção é do Pod atual Running e não
exige que a condição Ready do kubelet já tenha atualizado. O relatório distingue
persistência de mensagem/negócio, evidências de imagem, disponibilidade e um único
snapshot de recursos após os cenários; esse snapshot não é teste de pico/capacidade.

## Manter o ambiente disponível para revisão

As provas `verify-*` são descartáveis. Para preparar um cluster que permanece
ativo ao final, use os mesmos gates pelo comando público:

```sh
python3 scripts/kubernetes/deploy.py up --rendered /caminho/privado/rendered \
  --tools .tools/kubernetes
```

Ele recusa cluster/kubeconfig já existentes, constrói/importa os artefatos,
registra posse antes de aplicar os recursos e só anuncia Ready após os rollouts e
Job de topologia. Em caso de falha de criação, limpa o que conseguiu criar. Depois
execute `access.py` para acesso persistente ou `scripts/e2e.sh --target k8s` para a
jornada real. O contexto dedicado já possui seu namespace:

```sh
KUBECONFIG=/caminho/privado/rendered/kubeconfig .tools/kubernetes/kubectl get nodes
KUBECONFIG=/caminho/privado/rendered/kubeconfig .tools/kubernetes/kubectl get pods
```

Para remover explicitamente apenas esse cluster registrado, incluindo seus dados
locais de PVC, encerre os acessos e use:

```sh
python3 scripts/kubernetes/deploy.py down --rendered /caminho/privado/rendered \
  --tools .tools/kubernetes --delete-owned-cluster
```

A flag é explícita porque essa operação descarta os dados do cluster criado;
não é um comando de recuperação. O helper confere ID/Created/labels da posse
registrada, não seleciona cluster por contexto global e preserva outras aplicações.
Os IDs de Docker são normalizados completos nas comparações de posse.

O supervisor resolve o selector e targetPort do Service para um Pod atual Ready,
ignora Pods em Terminating e escolhe o mais novo quando há transição. A porta local
só é reaberta para esse alvo. Isso evita gastar tentativas rápidas em um Pod ainda
sem listener durante seu bootstrap; o orçamento inclui a espera por candidato
Ready. A seleção é refeita em cada reconexão, sem depender de um nome antigo de Pod.
