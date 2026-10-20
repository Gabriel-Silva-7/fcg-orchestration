# Scripts do ambiente

Os scripts de PostgreSQL em [`postgres/`](postgres/) validam as cinco senhas obrigatórias na partida, provisionam os quatro bancos em PGDATA vazio e verificam o estado persistido de roles, bancos e permissões. O Compose monta esses scripts como somente leitura. O [guia PostgreSQL](../docs/postgres.md) descreve início, testes reais, persistência, rotação e recuperação explícita de init parcial.

Scripts para revisões dos serviços, cluster e jornada integrada serão acrescentados com os respectivos componentes e provas. Limpeza deve identificar os recursos criados pela própria execução. Não executar prune ou reset global.
