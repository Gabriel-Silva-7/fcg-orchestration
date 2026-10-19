# Identificação e reprodução de uma release

Ainda não existe release integrada ou manifesto executável neste repositório. Esta página define os dados que deverão acompanhar a primeira entrega reproduzível.

## Revisões de origem

Uma release precisa identificar separadamente Users, Catalog, Payments, Notifications e a própria orquestração. Para cada repositório, registrar:

- URL de origem verificada.
- SHA completo do commit utilizado.
- Tag legível, quando existir, como referência auxiliar.
- Imagem produzida e seu digest, além da plataforma testada.

Não usar `main`, `latest` ou só um nome de tag como prova de imutabilidade. A origem precisa permitir reconstruir o código e a imagem usada. Um SHA de um serviço não identifica os outros quatro repositórios.

## Contextos de build

Desenvolvimento pode selecionar diretórios locais, incluindo worktrees. Para reprodução por quem avalia, o futuro ambiente deve funcionar a partir da orquestração e de fontes verificáveis fixadas por revisão, sem depender do diretório pessoal do autor ou de arquivos não versionados essenciais.

As bases de imagens e dependências também precisam estar fixadas. Qualquer override local deve ser explícito e registrado na evidência; um build com overrides não demonstra o comportamento do padrão sem eles.

## Evidência de integração

Antes de chamar uma combinação de release, registrar os comandos executados e seus resultados: build, inicialização, readiness real das dependências, jornada de cadastro e compra, biblioteca, notificações simuladas e recuperação após indisponibilidade. Executar também a reprodução a partir de fontes limpas.

O registro deve distinguir testes locais dos serviços, execução integrada em Compose e execução em Kubernetes. Nenhuma dessas provas substitui automaticamente as outras.

Publicar repositórios, imagens ou tags e submeter a entrega são ações separadas da preparação local. Essa etapa somente será documentada como concluída após sua execução e verificação.
