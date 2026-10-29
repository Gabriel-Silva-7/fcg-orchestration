# Fiap Cloud Games — ambiente integrado

Este repositório executa a jornada integrada de Users, Catalog, Payments e Notifications por eventos RabbitMQ. Cada serviço tem código, build e persistência próprios.

O Compose já provisiona PostgreSQL 16 com quatro bancos e credenciais isoladas, volume persistente e readiness de provisionamento. Consulte o [guia PostgreSQL](docs/postgres.md) para iniciar, diagnosticar e executar os testes reais. RabbitMQ persistente e o gate de topologia também estão disponíveis no [guia RabbitMQ](docs/rabbitmq.md). As quatro aplicações fazem parte do Compose; o [guia integrado](docs/compose.md) explica fontes, chaves, inicialização e teste real. Manifestos e validação Kubernetes continuam pendentes.

## Repositórios e responsabilidades

| Repositório | Responsabilidade |
|---|---|
| `fcg-users-api` | Identidade, perfis e administração de usuários |
| `fcg-catalog-api` | Jogos, promoções, pedidos e biblioteca |
| `fcg-payments-api` | Processamento de pagamento simulado |
| `fcg-notifications-api` | Contatos e notificações simuladas |
| `fcg-orchestration` | Ambiente integrado, configuração e procedimentos de reprodução |

Cada serviço mantém seu próprio código e build. A persistência pertence ao serviço dono; os bancos adotados por serviços diferentes são isolados e podem compartilhar a mesma instância PostgreSQL de desenvolvimento. A comunicação de negócio entre serviços ocorre por RabbitMQ; nenhum banco de outro serviço deve ser acessado diretamente.

## Desenvolvimento e release

O [guia de desenvolvimento](docs/development.md) descreve a organização local e o uso de worktrees. O [guia de release](docs/release.md) define como uma combinação de versões deve ser identificada e verificada.

Desenvolvimento usa checkouts locais limpos em contextos explícitos, selecionados por `scripts/prepare-local.py`. A entrega reproduzível deverá fixar revisões completas dos quatro serviços e digests das imagens, sem depender de `main` ou `latest`. PostgreSQL, RabbitMQ e imagens de build/runtime do bootstrap estão fixadas por digest; suas variáveis consumidas estão em `.env.example`, com valores exclusivamente DEMO. O E2E cobre cadastro/boas-vindas, perfil/senha/administração, autorização por estado propagado, promoções, CRUD e compra aprovada/rejeitada com histórico e confirmação, usando os quatro hosts reais. Recuperação operacional e interleavings avançados têm provas próprias.

## Ambiente

- SDK .NET 10.0.400 para os serviços, conforme seus próprios `global.json`.
- Docker com Compose; OrbStack é a engine usada na validação local.
- PostgreSQL 16 e RabbitMQ 4.3.6-management; ambas as imagens já estão fixadas por digest no Compose.
- Kubernetes local com Kind como alvo. O procedimento e a versão ainda serão validados; não é pré-requisito para ler este repositório.

Os [procedimentos de recuperação](docs/recovery.md) separam replay local de negócio, retomada do dispatcher e redrive de transporte.

Consulte [scripts](scripts/README.md) e [Kubernetes](k8s/README.md) para a divisão de responsabilidades. Os READMEs de cada serviço descrevem os comandos que já funcionam naquele repositório. Cada execução do E2E registra IDs únicos e imagens efetivas; o manifesto local não equivale a release pública ou integração em `main`.

## Configuração e dados

Segredos reais, `.env` locais, credenciais privadas e artefatos de build não devem ser versionados. As credenciais públicas em `.env.example` são identificadas como DEMO e não podem ser usadas em outro ambiente. Pagamentos e emissão de e-mails no console são simulados; não há cobrança ou envio externo.

Procedimentos de reset e limpeza precisam identificar os recursos criados pela própria execução. Não usar `docker system prune`, apagar volumes indiscriminadamente ou tratar reset de dados como etapa normal de inicialização.
