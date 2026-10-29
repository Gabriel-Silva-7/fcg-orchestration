# Scripts do ambiente

Os scripts de PostgreSQL em [`postgres/`](postgres/) validam as cinco senhas obrigatórias na partida, provisionam os quatro bancos em PGDATA vazio e verificam o estado persistido de roles, bancos e permissões. O Compose monta esses scripts como somente leitura. O [guia PostgreSQL](../docs/postgres.md) descreve início, testes reais, persistência, rotação e recuperação explícita de init parcial.

`prepare-local.py` seleciona fontes limpas e prepara ambiente privado sem sobrescrever chaves/credenciais; `e2e.sh --target compose --env-file <arquivo>` executa a jornada real com dados únicos e prazo finito, sem reset. Veja [Compose](../docs/compose.md). Scripts de cluster serão acrescentados nos incrementos Kubernetes. Limpeza deve identificar os recursos criados pela própria execução. Não executar prune ou reset global.
