# Scripts do ambiente

Os scripts de PostgreSQL em [`postgres/`](postgres/) validam as cinco senhas obrigatórias na partida, provisionam os quatro bancos em PGDATA vazio e verificam o estado persistido de roles, bancos e permissões. O Compose monta esses scripts como somente leitura. O [guia PostgreSQL](../docs/postgres.md) descreve início, testes reais, persistência, rotação e recuperação explícita de init parcial.

`prepare-local.py` seleciona fontes limpas e prepara ambiente privado sem sobrescrever chaves/credenciais; `e2e.sh --target compose --env-file <arquivo>` executa a jornada real com dados únicos e prazo finito, sem reset. Veja [Compose](../docs/compose.md). Os scripts em `kubernetes/` preparam ferramentas, renderizam configuração privada, implantam o cluster próprio e supervisionam acesso local; veja [Kubernetes](../k8s/README.md). `recovery.py`, `concurrency.py` e `identity-ordering.py` executam provas descartáveis específicas, descritas nos respectivos guias em `docs/`. Limpeza deve identificar os recursos criados pela própria execução. Não executar prune ou reset global.

`release.py pack|restore` fixa os cinco HEADs em bundles Git com manifesto e verifica clones novos sem publicação. Veja [release](../docs/release.md).
