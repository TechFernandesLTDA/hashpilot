# Pearl na RTX 3070: usar mais memória realmente ajuda?

## Medição local de 6 de novembro de 2026

- VRAM total: 8192 MiB; ocupada na GPU inteira: 2083 MiB; livre reportada: 5935 MiB. Há memória reservada pelo sistema além dessas duas últimas parcelas.
- Atividade da GPU: 100%; atividade de memória reportada: 5%. Esses indicadores não medem ocupação dos Tensor Cores nem percentual de capacidade da VRAM em uso.
- RAM do processo SRBMiner: aproximadamente 317 MiB de working set e 466 MiB privados. É memória do computador, diferente da VRAM.
- O driver Windows/WDDM retornou `N/A` para VRAM por processo. Logo, a medição não permite atribuir os 2083 MiB exclusivamente ao minerador.

Fontes sobre as métricas: [NVIDIA-SMI](https://docs.nvidia.com/deploy/nvidia-smi/index.html) e [ocupação CUDA](https://docs.nvidia.com/cuda/cuda-c-best-practices-guide/#occupancy). Uma GPU pode estar ocupada sem usar toda a capacidade de cálculo. O limitante pode estar em instruções, dependências, registradores, memória compartilhada, cache ou movimentação de dados. Preencher a VRAM não identifica nem resolve automaticamente esse limitante.

## Uma alavanca real encontrada em código

No [host do FreeForGemPear](https://github.com/pepsykolya/FreeForGemPear/blob/main/host/src/mining.rs), `FFGP_M` altera as linhas processadas por tentativa. O override aceita múltiplos de 128 entre 1024 e 1048576. Isso não é um parâmetro do SRBMiner.

Há uma diferença importante entre exemplo e implementação: o README cita 47104 linhas, mas `device_m` no commit `0065ac7` tenta fatores 16, 12, 8, 4 e 2, limitando o orçamento de `a_base`. Com 46 SMs e K=8192, o primeiro valor é `128 × 46 × 16 = 94208`: aproximadamente 736 MiB para `a_base` **na RAM do host**, dentro do teto automático de 768 MiB. Aumentar M não equivale, portanto, a alocar a mesma quantidade adicional de VRAM.

O comentário de desempenho no próprio código relata cerca de 0,2% de ganho sintético entre fatores 8 e 16, com saturação posterior. É resultado de terceiro, em TMAC/s, não um benchmark nosso de hashrate ou lucro.

A [interface da biblioteca GPU](https://github.com/pepsykolya/FreeForGemPear/blob/main/gpu/pearlgpu.h) já expõe dois slots assíncronos. Não seria correto apresentar double buffering como uma otimização inédita ainda ausente. A geração local de dados e a reutilização em cache podem reduzir tráfego externo melhor que simplesmente aumentar buffers globais. [Descrição do minerador e limites do código publicado](https://github.com/pepsykolya/FreeForGemPear#building-from-source).

O host é publicado, mas o kernel continua em biblioteca binária e tem licença própria. Não foi copiado para o HashPilot nem executado neste levantamento. O projeto aponta o sucessor [FreeForGem 1.7.6](https://github.com/pepsykolya/FreeForGem/releases/tag/v1.7.6); qualquer comparação deve fixar a versão efetivamente testada.

## Experimento que faria sentido

1. Medir uma referência do SRBMiner com núcleo, memória e condições estáveis, incluindo shares aceitas e rejeitadas.
2. Preparar um adaptador separado para executar o minerador alternativo completo, preservando sua taxa e licença. O HashPilot atual gerencia SRBMiner; integração com outro backend não está implementada por este relatório.
3. Parar a instância gerenciada antes de um teste alternativo; manter um único minerador e um único responsável pelos clocks na GPU.
4. Validar a implementação alternativa com seu self-test. Depois comparar os fatores de M 4, 8, 12, 16 e, experimentalmente, 24: 23552, 47104, 70656, 94208 e 141312 linhas. Os valores preservam divisão regular pelos SMs; o último excede o orçamento automático de RAM do host e exige observar custo de provas e latência.
5. Repetir os testes com mesmo núcleo, memória, pool e carteira; medir memória real, TH/s sustentados, watts, temperatura, erros, shares e impacto da taxa do desenvolvedor. Uma taxa menor anunciada ou GEMM sintético melhor não prova rendimento líquido maior.
6. Se os lotes não melhorarem o resultado, investigar tempo e ocupação dos kernels com um profiler apropriado, numa sessão controlada. Só então considerar um kernel independente ou outro arranjo de execução, sem presumir acesso ao código fechado do SRBMiner.

O alvo é mais trabalho válido por segundo, não 8 GB ocupados. Ganhos dessa estratégia ainda não foram demonstrados nesta máquina. A mineração de produção continua no SRBMiner enquanto o controle de clocks é validado; nenhum teste de concorrência entre dois mineradores foi usado como atalho.
