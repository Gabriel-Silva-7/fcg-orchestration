# Fiap Cloud Games — ambiente integrado

Este repositório reúne a documentação e, nas próximas etapas, a execução integrada de quatro serviços independentes: Users, Catalog, Payments e Notifications.

O estado atual é a fundação documental. Ainda não há Compose executável, scripts de provisionamento ou manifestos Kubernetes de implantação. Nenhum comando abaixo instala ferramentas ou inicia serviços.

## Repositórios e responsabilidades

| Repositório | Responsabilidade |
|---|---|
| `fcg-users-api` | Identidade, perfis e administração de usuários |
| `fcg-catalog-api` | Jogos, promoções, pedidos e biblioteca |
| `fcg-payments-api` | Processamento de pagamento simulado |
| `fcg-notifications-api` | Contatos e notificações simuladas |
| `fcg-orchestration` | Ambiente integrado, configuração e procedimentos de reprodução |

Cada serviço mantém seu próprio código e build. A persistência pertence ao serviço dono; os bancos adotados por serviços diferentes são isolados e podem compartilhar a mesma instância PostgreSQL de desenvolvimento. A comunicação entre serviços será por RabbitMQ; nenhum banco de outro serviço deve ser acessado diretamente.

## Desenvolvimento e release

O [guia de desenvolvimento](docs/development.md) descreve a organização local e o uso de worktrees. O [guia de release](docs/release.md) define como uma combinação de versões deve ser identificada e verificada.

Desenvolvimento poderá usar código local em contextos explícitos. A entrega reproduzível deverá fixar revisões completas dos quatro serviços e digests das imagens, sem depender de `main` ou `latest`. Os arquivos executáveis e suas variáveis serão documentados quando existirem; não há `.env.example` com opções sem consumidor nesta etapa.

## Ambiente previsto

- SDK .NET 10.0.400 para os serviços, conforme seus próprios `global.json`.
- Docker com Compose; OrbStack é a engine usada na validação local.
- PostgreSQL 16 e RabbitMQ 4.3.6-management; versões e imagens imutáveis serão fixadas no ambiente executável.
- Kubernetes local com Kind como alvo. O procedimento e a versão ainda serão validados; não é pré-requisito para ler este repositório.

Consulte [scripts](scripts/README.md) e [Kubernetes](k8s/README.md) para a divisão de responsabilidades. Os READMEs de cada serviço descrevem os comandos que já funcionam naquele repositório. Estes documentos não comprovam integração, persistência ou recuperação entre serviços.

## Configuração e dados

Segredos reais, `.env` locais, credenciais privadas e artefatos de build não devem ser versionados. Futuras credenciais públicas de demonstração serão identificadas como tal e não poderão ser usadas em outro ambiente. Pagamentos e envio de e-mails serão simulados.

Procedimentos de reset e limpeza precisam identificar os recursos criados pela própria execução. Não usar `docker system prune`, apagar volumes indiscriminadamente ou tratar reset de dados como etapa normal de inicialização.
