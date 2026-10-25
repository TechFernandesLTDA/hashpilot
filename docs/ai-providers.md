# IA para rigs com CPU simples

Abra **Benchmark / IA → Configurar IA**, ou **Configurar → Escolher provedor de IA**. A IA é opcional. Sensores, benchmark, validação de ajustes e proteção térmica são locais e continuam funcionando sem modelo ou conexão.

| Opção | Processamento | Cadastro e condições |
| --- | --- | --- |
| OpenRouter Cloud Free | Nuvem; recomendado para CPU simples | Conta e chave API próprias. `openrouter/free` ou variante `:free`, com preço zero confirmado no catálogo e teto zero na requisição. Há cotas. |
| OpenCode Cloud Free | Nuvem, usando o cliente oficial | Instale o cliente e faça login conforme a documentação. Selecione `opencode/...-free`. Promoções e disponibilidade podem mudar. |
| Ollama local | CPU/RAM do computador | Runtime e modelo locais opcionais; sem API paga. A GPU de mineração não é usada para inferência. |

**Ollama Cloud:** a oferta atual inclui créditos iniciais e cobrança por uso. O HashPilot não a apresenta como gratuidade contínua nem a usa como fallback automático.

## Configuração

1. Escolha o provedor e o identificador do modelo. As sugestões de nomes não garantem disponibilidade; o catálogo é consultado antes de inferência cloud.
2. No OpenRouter, crie uma chave na página oficial e informe-a somente no campo protegido do aplicativo. A chave fica no **Gerenciador de Credenciais do Windows**, fora de configurações, histórico, exportações e GitHub. OpenCode usa o login do seu cliente; o HashPilot não lê seu arquivo de autenticação.
3. Para cloud, marque a autorização de envio de telemetria resumida e ative as sugestões de IA.
4. Use **Testar conexão**. Uma consulta de teste também usa a cota cloud local. Sem chave/login, a integração informa indisponibilidade; não abre contas nem adiciona créditos.
5. Salve. Supervisores desta versão leem a escolha durante a execução; controladores antigos precisam ser reiniciados pela operação normal do rig.

OpenCode Free exige o cliente OpenCode nas condições atuais. O HashPilot usa `opencode.exe --pure run` em diretório temporário, um agente de uma etapa com todas as ferramentas negadas e o mesmo modelo gratuito para tarefas auxiliares. Não usa a API direta como atalho. Cliente ausente, incompatível, sem login ou modelo removido resultam em indisponibilidade.

## Cotas, dados e ajustes

- Teto local compartilhado de **40 consultas cloud por dia UTC em todo o rig**, incluindo testes e tentativas que falham após reservar uma consulta. A reserva SQLite é transacional e persiste após reinícios. O provedor pode impor uma cota menor.
- Consultas simultâneas respeitam intervalo global mínimo de 60 segundos. Após resposta válida, cada supervisor espera pelo menos uma hora para nova consulta cloud. As respostas só podem orientar decisões por até 90 segundos; não são tratadas como telemetria atual.
- Somente medidas numéricas de sensores e estatísticas resumidas do benchmark entram no prompt. Não são enviados carteira, UUID, serial, caminhos, logs ou credenciais. A chave API identifica a requisição no provedor e é enviada pelo cabeçalho de autenticação HTTPS.
- A nuvem só retorna uma ação enumerada e uma justificativa. Ela não executa comandos nem define valores arbitrários. O controlador valida temperatura, capacidade do driver, estabilidade e posse da GPU antes de aplicar qualquer proposta.
- Sem fallback para modelos pagos. Modelos removidos, respostas inválidas, autenticação recusada e cotas esgotadas preservam a política adaptativa local.

## Indicação opcional

`data/ai-referrals.json` aceita um link HTTPS público por provedor. Quando existe, o assistente mostra **Indicação do autor · opcional**, mantendo o acesso oficial. O cadastro não depende desse link. O arquivo nasce vazio; não inventa um código de indicação nem usa cookies de afiliado silenciosamente.

## Fontes verificadas em 25/10/2026

- [OpenRouter: modelos gratuitos](https://openrouter.ai/docs/guides/routing/model-variants/free), [limites](https://openrouter.ai/docs/api_reference/limits) e [teto de preço](https://openrouter.ai/docs/guides/routing/provider-selection).
- [OpenCode: modelos e condições Free](https://opencode.ai/v2/docs/console/models/), [CLI](https://opencode.ai/docs/cli/) e [permissões](https://opencode.ai/docs/permissions/).
- [Ollama: Cloud](https://docs.ollama.com/cloud) e [preços](https://ollama.com/pricing).
