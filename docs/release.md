# Identificação e reprodução de uma release

Há scripts executáveis de preparação, Compose, Kubernetes e jornada integrada. Eles registram fontes e imagens da execução local. Uma evidência de release deve incluir a reprodução a partir de cinco clones limpos e identificar a origem desses clones (bundles locais ou GitHub). Testes em worktrees locais não comprovam reprodução pelo GitHub; publicação e integração em `main` exigem registros próprios.

## Revisões de origem

Uma release precisa identificar separadamente Users, Catalog, Payments, Notifications e a própria orquestração. Para cada repositório, registrar:

- URL de origem verificada.
- SHA completo do commit utilizado.
- Tag legível, quando existir, como referência auxiliar.
- Imagem produzida e seu digest, além da plataforma testada.

Não usar `main`, `latest` ou só um nome de tag como prova de imutabilidade. A origem precisa permitir reconstruir o código e a imagem usada. Um SHA de um serviço não identifica os outros quatro repositórios.

## Contextos de build

Desenvolvimento pode selecionar diretórios locais, incluindo worktrees. Para reprodução por quem avalia, o pacote de reprodução deve funcionar a partir da orquestração e de fontes verificáveis fixadas por revisão, sem depender do diretório pessoal do autor ou de arquivos não versionados essenciais.

As bases de imagens e dependências também precisam estar fixadas. Qualquer override local deve ser explícito e registrado na evidência; um build com overrides não demonstra o comportamento do padrão sem eles.

## Evidência de integração

Antes de chamar uma combinação de release, registrar os comandos executados e seus resultados: build, inicialização, readiness real das dependências, jornada de cadastro e compra, biblioteca, notificações simuladas e recuperação após indisponibilidade. Executar também a reprodução a partir de fontes limpas.

O registro deve distinguir testes locais dos serviços, execução integrada em Compose e execução em Kubernetes. Nenhuma dessas provas substitui automaticamente as outras.

Publicar repositórios, imagens ou tags e submeter a entrega são ações separadas da preparação local. Essa etapa somente será documentada como concluída após sua execução e verificação.

## Pacote local de fontes para revisão

`scripts/release.py` permite reproduzir uma combinação de commits locais
sem depender da disponibilidade das refs remotas. É um
pacote de fontes local, não uma release publicada. Requer Python3 e Git; cada
origem precisa estar limpa e ter URL HTTPS sem credenciais. O script da orquestração
também precisa estar commitado, pois seu próprio HEAD integra o pacote.

```sh
python3 scripts/release.py pack \
  --users ../fcg-users-api --catalog ../fcg-catalog-api \
  --payments ../fcg-payments-api --notifications ../fcg-notifications-api \
  --output /caminho/privado/pacote-novo
python3 scripts/release.py restore \
  --manifest /caminho/privado/pacote-novo/sources.json \
  --output /caminho/privado/clones-novos
```

O pacote contém `sources.json` e cinco bundles Git autossuficientes, com o HEAD
completo e sua história alcançável. Não copia arquivos ignorados, `.env`, chaves,
artefatos de build ou diretórios pessoais. O manifesto registra URL, SHA completo,
nome de bundle e SHA256; ele deve ser obtido por um canal confiável. Hash confirma
integridade em relação ao manifesto, não autentica um manifesto alterado junto
com os bundles. Revise também o conteúdo já commitado antes de compartilhar.

Restore confere todos os hashes/heads antes de criar o destino, faz clones novos
com checkout detached e origem URL restabelecida. Recusa destinos existentes e
fontes sujas, sem limpar arquivos de outro trabalho. Uma falha intermediária pode
deixar um diretório parcial para diagnóstico; inspecione-o e use outro destino,
sem tratar falha como autorização de apagar dados. `RESTORED.json` identifica a
combinação clonada, sem caminhos absolutos no manifesto de distribuição.

## Ensaio a partir dos clones

Entre no clone novo de `fcg-orchestration`. As quatro pastas irmãs restauradas são
os contextos de build. Execute exatamente a preparação e a jornada do
[guia Compose](compose.md), preservando `sources.json` e as saídas dos dois E2Es.
Pare os containers do projeto antes de preparar o cluster, pois a validação local
não pressupõe capacidade para executar as duas stacks completas simultaneamente.

Para Kind, gere **outro** ambiente com `prepare-local.py --output .local/kind-demo`,
usando os mesmos quatro clones. Prepare ferramentas e renderize como no
[guia Kubernetes](../k8s/README.md), com `--applications`. Execute `deploy.py up`,
confira nodes/Pods/readiness e rode duas jornadas `scripts/e2e.sh --target k8s`
com saídas distintas. Remova somente o cluster identificado pelo próprio helper.

Registre hashes do manifesto e dos bundles, SHAs dos cinco clones, comandos,
resultados, plataforma, fontes e cadeia das imagens usadas. Os E2Es precisam
corresponder à combinação que será gravada no vídeo. Dependências públicas e
imagens base podem usar cache local; nenhum `.env` anterior ou imagem privada de
aplicação pode ser requisito para executar o procedimento.

Os testes de seleção e integridade do pacote usam Git real e diretórios descartáveis:

```sh
python3 -m unittest discover -s tests/release -v
```

Acesso do avaliador aos repositórios/commits, publicação de imagens, vídeo e envio
do relatório dependem de execução e conferência separadas, após autorização.
