# Fiap Cloud Games — ambiente integrado

Este repositório executa a jornada integrada de Users, Catalog, Payments e Notifications por eventos RabbitMQ. Cada serviço tem código, build e persistência próprios.

O Compose já provisiona PostgreSQL 16 com quatro bancos e credenciais isoladas, volume persistente e readiness de provisionamento. Consulte o [guia PostgreSQL](docs/postgres.md) para iniciar, diagnosticar e executar os testes reais. RabbitMQ persistente e o gate de topologia também estão disponíveis no [guia RabbitMQ](docs/rabbitmq.md). As quatro aplicações fazem parte do Compose; o [guia integrado](docs/compose.md) explica fontes, chaves, inicialização e teste real. A implantação Kubernetes e sua prova de persistência estão disponíveis no [guia Kind](k8s/README.md); os quatro serviços possuem manifestos próprios e prova de implantação/configuração, com regressão e recuperação completas na jornada Kubernetes.

## Executar

1. Obtenha os quatro serviços em checkouts limpos nas revisões selecionadas. Identifique a combinação por cinco SHAs completos; nomes de branch podem mudar. Registre os SHAs efetivos conforme o [guia de release](docs/release.md).
2. Com clones irmãos `../fcg-users-api`, `../fcg-catalog-api`, `../fcg-payments-api` e `../fcg-notifications-api`, execute uma única preparação na raiz da orquestração:

   ```sh
   python3 scripts/prepare-local.py --compose-default
   export DOCKER_CONTEXT=orbstack
   docker compose up --build
   ```

   O `.env` ignorado aponta à configuração privada gerada. Em outro terminal, no mesmo repositório, `docker --context orbstack compose ps -a` mostra a stack. Para background use `docker --context orbstack compose up -d --build`. O [guia Compose](docs/compose.md) mostra caminhos explícitos, E2E e reinício. Sem os quatro checkouts o build literal é inviável: cada serviço é um repositório separado e a orquestração não baixa código automaticamente. `docker-compose up` é a grafia legada; a ferramenta validada é `docker compose`.
3. Para Kubernetes, siga o [guia Kind](k8s/README.md): renderize com `--applications`, execute `deploy.py up` com gates e, no diretório renderizado `04-applications`, reaplique com `kubectl apply -f .` e confira `kubectl get pods` usando kubeconfig e namespace dedicados. Execute o E2E com `--target k8s`.
4. Para revisão interativa, `access.py` mantém os acessos locais e recupera os forwards após troca de Pod. Os guias explicam parada e descarte dos recursos próprios.

As URLs abaixo identificam as origens Git. A listagem identifica os repositórios; acesso externo e disponibilidade remota de cada SHA exigem verificação separada.

## Repositórios e responsabilidades

| Repositório | Responsabilidade |
|---|---|
| [fcg-users-api](https://github.com/Gabriel-Silva-7/fcg-users-api) | Identidade, perfis e administração de usuários |
| [fcg-catalog-api](https://github.com/Gabriel-Silva-7/fcg-catalog-api) | Jogos, promoções, pedidos e biblioteca |
| [fcg-payments-api](https://github.com/Gabriel-Silva-7/fcg-payments-api) | Processamento de pagamento simulado |
| [fcg-notifications-api](https://github.com/Gabriel-Silva-7/fcg-notifications-api) | Contatos e notificações simuladas |
| [fcg-orchestration](https://github.com/Gabriel-Silva-7/fcg-orchestration) | Ambiente integrado, configuração e procedimentos de reprodução |

Cada serviço mantém seu próprio código e build. A persistência pertence ao serviço dono; os bancos adotados por serviços diferentes são isolados e podem compartilhar a mesma instância PostgreSQL de desenvolvimento. A comunicação de negócio entre serviços ocorre por RabbitMQ; nenhum banco de outro serviço deve ser acessado diretamente.

## Desenvolvimento e release

O [guia de desenvolvimento](docs/development.md) descreve a organização local e o uso de worktrees. O [guia de release](docs/release.md) define como uma combinação de versões deve ser identificada e verificada.

Desenvolvimento usa checkouts locais limpos em contextos explícitos, selecionados por `scripts/prepare-local.py`. A combinação usada precisa registrar revisões completas dos cinco repositórios e a cadeia de identificação das imagens, sem depender de `main` ou `latest`. O [pacote local de fontes](docs/release.md#pacote-local-de-fontes-para-revisão) registra os cinco commits e restaura clones novos; acesso externo e ensaio de reprodução limpa têm aceite separado no [guia de release](docs/release.md). PostgreSQL, RabbitMQ e imagens de build/runtime do bootstrap estão fixadas por digest; suas variáveis consumidas estão em `.env.example`, com valores exclusivamente DEMO. O E2E cobre cadastro/boas-vindas, perfil/senha/administração, autorização por estado propagado, promoções, CRUD e compra aprovada/rejeitada com histórico e confirmação, usando os quatro hosts reais. Recuperação operacional e interleavings avançados têm provas próprias.

## Ambiente

- SDK .NET 10.0.400 para os serviços, conforme seus próprios `global.json`.
- Docker com Compose; OrbStack é a engine usada na validação local.
- PostgreSQL 16 e RabbitMQ 4.3.6-management; ambas as imagens já estão fixadas por digest no Compose.
- Kubernetes local: Kind 0.33.0 / kubectl 1.37.0 / node 1.37.0 fixados, preparação e prova de infraestrutura no [guia Kind](k8s/README.md).

Os [procedimentos de recuperação](docs/recovery.md) separam replay local de negócio, retomada do dispatcher e redrive de transporte.

Consulte a [arquitetura e os eventos](docs/architecture.md), a [matriz de demonstração](docs/requirements.md), [scripts](scripts/README.md) e [Kubernetes](k8s/README.md) para a divisão de responsabilidades. Os READMEs de cada serviço descrevem seus comandos e configurações. Cada execução do E2E registra IDs únicos e imagens efetivas; o manifesto local não equivale a release pública ou integração em `main`.

## Configuração e dados

Segredos reais, `.env` locais, credenciais privadas e artefatos de build não devem ser versionados. As credenciais públicas em `.env.example` são identificadas como DEMO e não podem ser usadas em outro ambiente. Pagamentos e emissão de e-mails no console são simulados; não há cobrança ou envio externo.

Procedimentos de reset e limpeza precisam identificar os recursos criados pela própria execução. Não usar `docker system prune`, apagar volumes indiscriminadamente ou tratar reset de dados como etapa normal de inicialização.

[Prova de concorrência e snapshot](docs/concurrency.md) com quatro hosts reais e barreiras PostgreSQL.

[Prova de identidade e contato fora de ordem](docs/identity-ordering.md) com snapshots e eventos originais.

## Limite de portabilidade

A validação disponível foi executada em **darwin-arm64 com Docker no contexto
OrbStack**; os containers executam linux/arm64. O preparo de Kind/kubectl também
é específico desse alvo. Não foi validada a reprodução em Linux, Windows, x86_64
ou outra engine/contexto Docker. Esses alvos exigem adaptar e testar ferramentas,
acesso ao Docker, permissões/mounts e imagens antes de afirmar suporte.

Pré-requisito para a stack Kind deste perfil: engine Docker com pelo menos **7 GiB**
de memória disponível/configurada. O helper recusa engines abaixo desse orçamento.
