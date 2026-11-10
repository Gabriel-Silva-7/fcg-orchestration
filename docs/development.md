# Desenvolvimento local

A organização prevista usa clones irmãos dentro de um diretório escolhido por quem desenvolve:

```text
workspace/
  fcg-users-api/
  fcg-catalog-api/
  fcg-payments-api/
  fcg-notifications-api/
  fcg-orchestration/
```

Os nomes não exigem um caminho pessoal específico. O Compose atual provisiona PostgreSQL e RabbitMQ, incluindo o bootstrap da topologia do broker, conforme os guias de [PostgreSQL](postgres.md) e [RabbitMQ](rabbitmq.md). As quatro aplicações aceitam caminhos explícitos para clones irmãos ou worktrees. O [guia Compose](compose.md) mostra preparação, manifesto de fontes e E2E real sem depender dessa organização de pastas.

## Worktrees e revisão

Cada tarefa usa branch e worktree próprias no repositório dono do código. Uma worktree é um checkout adicional do mesmo histórico Git, não uma cópia manual para colar de volta.

De um repositório já clonado, estes comandos apenas inspecionam seu estado:

```bash
git status --short
git branch --show-current
git worktree list
git rev-parse HEAD
```

Antes de combinar serviços, registrar o HEAD de cada worktree usada e conferir os contratos consumidos. Dois serviços compilando separadamente não comprovam que a jornada integrada funciona. Repetir as verificações pertinentes na combinação efetivamente selecionada.

Os comandos de restore, testes, build e container pertencem ao README de cada serviço. Não executar builds de caminhos externos implícitos nem copiar o código dos serviços para este repositório.

## Posse dos recursos

Executar cenários com nomes, portas e dados próprios. Registrar IDs de containers, rede e volumes antes da limpeza e remover somente os recursos da execução. Conservar os dados quando a prova exigir reinício; recriar um banco vazio não comprova recuperação.

Os guias de [PostgreSQL](postgres.md) e [RabbitMQ](rabbitmq.md) documentam o provisionamento já executável. A jornada integrada completa dos quatro serviços está disponível no [Compose](compose.md) e no [Kind](../k8s/README.md).
