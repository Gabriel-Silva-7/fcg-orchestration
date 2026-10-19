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

Os nomes não exigem um caminho pessoal específico. Quando o Compose existir, seus contextos locais deverão aceitar caminhos explícitos, permitindo usar clones irmãos ou worktrees fora dessa estrutura. Esse mecanismo ainda não foi implementado; não há variáveis de Compose funcionais para configurar agora.

## Worktrees e revisão

Cada tarefa usa branch e worktree próprias no repositório dono do código. Um executor escreve por worktree; outra ferramenta pode continuar após registrar a transferência. Uma worktree é um checkout adicional do mesmo histórico Git, não uma cópia manual para colar de volta.

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

O procedimento integrado de desenvolvimento será acrescentado junto do Compose real, com pré-requisitos, variáveis e resultados observados. Até lá, este documento define convenções, sem alegar provisionamento pronto.
