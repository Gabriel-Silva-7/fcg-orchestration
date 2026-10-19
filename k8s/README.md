# Kubernetes do ambiente integrado

A infraestrutura global de PostgreSQL, RabbitMQ, configuração e composição do ambiente ficará neste diretório. Cada serviço manterá seus próprios recursos de implantação no diretório `k8s` do respectivo repositório.

Ainda não há manifestos executáveis. Kind é o alvo local previsto; versão, criação do cluster, armazenamento, carregamento de imagens e procedimentos de recuperação serão validados antes de documentar comandos de uso.

A implantação deverá usar imagens identificadas e configuração explícita. Secrets reais permanecem fora do Git; credenciais públicas de demonstração serão claramente identificadas. Persistência precisa sobreviver ao cenário de reinício validado, sem confundir recriação de dados vazios com recuperação.

Os manifestos e o roteiro de execução entrarão juntos, com verificação de rollout, readiness, comunicação e dados. Esta página não representa um cluster instalado ou uma aplicação já implantada.
