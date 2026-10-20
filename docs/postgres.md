# PostgreSQL local

O Compose provisiona PostgreSQL 16 com imagem por digest, volume persistente e quatro bancos independentes. Não executa migrations de negócio. Os serviços devem aplicar suas próprias migrations usando a credencial do banco correspondente.

| Banco | Role de aplicação | Senha no ambiente de provisionamento |
|---|---|---|
| `fcg_users` | `fcg_users` | `FCG_USERS_DB_PASSWORD` |
| `fcg_catalog` | `fcg_catalog` | `FCG_CATALOG_DB_PASSWORD` |
| `fcg_payments` | `fcg_payments` | `FCG_PAYMENTS_DB_PASSWORD` |
| `fcg_notifications` | `fcg_notifications` | `FCG_NOTIFICATIONS_DB_PASSWORD` |

Cada role pode criar e usar tabelas no próprio banco. Não possui superuser, criação de banco/role, replicação, bypass RLS ou associação a outra role. `CONNECT` de PUBLIC é revogado dos quatro bancos e de `postgres`, `template1`, `template0`. As aplicações não podem conectar aos bancos dos vizinhos ou de manutenção. O administrador `fcg_bootstrap`, configurado por `POSTGRES_PASSWORD`, é exclusivo de provisionamento/manutenção e nunca deve ser usado na configuração das APIs.

## Iniciar

Requisitos: Docker, Compose e imagem compatível com a arquitetura da máquina. A imagem fixada foi validada em arm64. Os exemplos usam contexto `orbstack`; substitua explicitamente se sua engine usa outro contexto. Execute na raiz deste repositório:

```bash
cp .env.example .env
chmod 600 .env
# Edite as cinco senhas em .env antes de usar dados que não sejam demonstração.
docker --context orbstack compose --env-file .env -p fcg-local config --quiet
docker --context orbstack compose --env-file .env -p fcg-local up -d --wait postgres
docker --context orbstack compose --env-file .env -p fcg-local ps
```

Todos os valores versionados em `.env.example` são DEMO. As cinco senhas são obrigatórias e não podem ser vazias. O Compose rejeita ausência/vazio antes da criação e o entrypoint repete a validação em toda partida, mesmo com volume existente. Não publique `.env`, conexões com senha, dumps de variáveis ou a saída completa de `compose config`/`docker inspect`, pois podem conter credenciais.

Use valores entre aspas simples no `.env` para preservar `$` e espaços. Uma aspa simples interna é escapada com `\'`; uma barra invertida literal é preservada nesse formato. Senhas chegam ao SQL como parâmetros `psql --set` com quoting literal e `ON_ERROR_STOP`; não são concatenadas em SQL. A sessão de init desativa o registro de statements, inclusive no erro, para que uma colisão de role não revele o comando com senha. A mensagem de erro continua disponível.

`POSTGRES_PORT` é opcional, padrão `5432`. A porta é publicada apenas em `127.0.0.1`; por exemplo, use `POSTGRES_PORT=55432` no `.env` para evitar conflito local. Contêineres na mesma rede Compose usam host `postgres`, porta `5432`. Clientes no host usam `127.0.0.1` e a porta publicada. A conexão de cada aplicação usa seu próprio nome de banco/role e senha, resolvida no contrato `ConnectionStrings:Default`. A imagem oficial tem trust no socket/loopback interno para administração local; isso não substitui o teste de autenticação pela rede com SCRAM.

## Readiness e diagnóstico

`pg_isready` indica que o processo aceita conexões, mas não comprova os quatro bancos. O healthcheck aguarda o servidor TCP final e consulta roles, donos, flags, associações e CONNECT efetivo em todos os bancos, além de verificar a ausência de CONNECT público. Exige CREATE efetivo no banco e consulta USAGE/CREATE efetivos do schema `public` dentro de cada banco; essas permissões são distintas mesmo quando a role é dona do banco. A verificação usa apenas metadados, sem criar objetos durante o healthcheck. Não usa uma tabela de negócio ou marcador de conclusão. A inspeção usa administração pelo socket local e não confunde uma nova senha de ambiente com rotação de credencial persistida.

```bash
docker --context orbstack compose --env-file .env -p fcg-local exec postgres /opt/fcg/postgres/healthcheck.sh
docker --context orbstack compose --env-file .env -p fcg-local logs postgres
docker --context orbstack compose --env-file .env -p fcg-local exec postgres psql -X -U fcg_bootstrap -d postgres -c '\du'
docker --context orbstack compose --env-file .env -p fcg-local exec postgres psql -X -U fcg_bootstrap -d postgres -c '\l'
```

O health detecta provisionamento incompleto ou alteração incompatível nas permissões; não garante que uma migration de aplicação foi aplicada. Se um administrador criar outro banco com CONNECT público, o health falha porque as roles poderiam acessá-lo. Repare a permissão explicitamente após avaliar o banco e seus clientes.

## Persistência e inicialização parcial

O volume lógico `postgres_data` é isolado pelo projeto Compose. Use sempre o mesmo `-p` para recuperar os mesmos dados. Recriar o container preserva o volume:

```bash
docker --context orbstack compose --env-file .env -p fcg-local up -d --force-recreate --wait postgres
```

O entrypoint oficial executa o init somente com PGDATA vazio. Alterar uma senha no `.env` e recriar o container **não altera** a senha da role no volume existente. Para rotacionar, conecte como administrador e use o comando interativo `\password fcg_users` (ou a outra role) no psql, depois sincronize a configuração privada dos clientes. Não escreva a nova senha em comandos SQL salvos no histórico ou em logs. Atualize também o `.env` para representar o valor esperado, preservando o volume.

Se o init falhar depois que PGDATA foi criado, reiniciar não completa automaticamente o provisionamento. O processo pode responder `pg_isready`, mas ficará unhealthy. Preserve o volume, faça backup do que já existe e examine os erros/roles/bancos/ACL. Um administrador deve reparar explicitamente apenas os objetos faltantes e suas permissões, usando entrada segura de senha. O health deve ficar verde após o reparo. Não reaplique cegamente o init em dados existentes: `CREATE ROLE`/`CREATE DATABASE` falham em objetos que já existem. Não apague o volume como tentativa de recuperação.

Para parar mantendo dados, use `docker --context orbstack compose --env-file .env -p fcg-local stop postgres`. Remoção de volume é descarte deliberado de dados e deve identificar e conferir a posse dos recursos daquele projeto. Não use prune ou limpeza global.

## Testes reais

```bash
python3 tests/postgres/test_postgres.py --context orbstack
```

A suíte usa apenas a biblioteca padrão Python, projeto aleatório, porta loopback efêmera e volume próprios. Verifica autenticação com senha contendo aspas/barra/dólar/espaço, senha errada, quatro donos com DDL/DML, conexões cruzadas/manutenção negadas, flags de role, perda separada de CREATE no banco e CREATE/USAGE no schema `public` (com recuperação explícita e DDL/DML autenticados nos quatro bancos), senhas vazias, persistência na recriação, ausência de rotação por ambiente e provisionamento parcial. Um segundo ambiente força falha real de init com role já existente e verifica que stdout/stderr/logs não expõem senha, enquanto o diagnóstico e dados parciais permanecem.

`--case provision` roda a primeira verificação; `--case acl` foca as permissões de banco/schema; `--case init-errors` foca o erro seguro. `--evidence-dir <diretório>` preserva os IDs/labels capturados e logs redigidos, sem gravar configurações de container com senhas. A limpeza confere identidade e labels antes de remover cada recurso capturado, incluindo os criados em falhas; não toca recursos preexistentes. Esses testes não provam a integração das APIs, RabbitMQ, migrations ou Kubernetes.
