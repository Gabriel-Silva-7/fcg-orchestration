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
