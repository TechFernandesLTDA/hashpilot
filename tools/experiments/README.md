# Experimentos de throughput INT8 — tensor cores da RTX 3070

Bancadas de **medição**, para testar hipóteses de desempenho antes de escrever
um kernel de minerador. **Nenhuma delas minera, altera clocks, fala com a pool
ou envia shares.** Seguem o padrão de `ampere_int8_probe.py`: restritas à SM86,
recusam rodar com um minerador ativo, compilam PTX pelo JIT do driver NVIDIA (sem
nvcc) e conferem cada saída contra uma referência inteira de CPU.

Contexto e limites estão em `docs/pearl-rtx3070-investigacao-2026-11-02.txt`. O
resumo honesto: o SRBMiner já entrega ~78,9 TH/s, cerca de 96% do pico denso
teórico da placa (~82 TMAC/s). O ganho possível é pequeno, e mesmo +10% vale uns
US$ 0,12–0,18/dia nesta GPU. Vale como pesquisa, não como renda.

Antes de rodar: o controle térmico do painel não deve estar atuando na GPU, e
nenhum minerador pode estar ativo (os probes recusam, mas confira). A saída é um
JSON em `dist/` (ignorado pelo git). Compare sempre com o mesmo clock/memória.

## 1. Co-emissão DP4A + tensor core — `dp4a_coissue_probe.py`

**Hipótese:** a instrução DP4A (4 MACs INT8 exatos por thread, nos núcleos CUDA)
pode somar throughput enquanto o tensor core faz MMA, cobrindo os ciclos em que o
escalonador do warp fica ocioso entre dois `mma`.

```text
mise exec -- python tools/experiments/dp4a_coissue_probe.py --output dist/dp4a-coissue.json
# Linux/driver NVIDIA: python3 tools/experiments/dp4a_coissue_probe.py --output dist/dp4a-coissue-linux.json
```

**Como ler** (campo `median_total_tmac_s`, por `waves`):

- `both` ≈ `max(mma, dp4a)` → há sobreposição; a ideia tem fundamento.
- `both` ≈ `mma + dp4a` → serializado; sem ganho.
- `both` < `mma` → a DP4A está atrapalhando o MMA; abandone.

**Portão de decisão:** só continua para um kernel real se `both` superar `mma`
sozinho **e** a temperatura não disparar o recuo de clock. Lembre que, no
minerador de verdade, o INT32 também é usado pelo BLAKE3 e há pressão de
carga/armazenamento que este probe não tem — o ganho real é menor que o medido
aqui. Rode também o `ampere_int8_probe.py` como linha de base.

**Resultado na RTX 3070 (teste real, 08/11/2026; driver 621.14, sem minerador, clocks de fábrica): portão fechado.** Todas as saídas conferiram com a referência inteira da CPU.

| Medição (`median_total_tmac_s`) | 1 onda | 4 ondas |
|---|---|---|
| `mma` | 76,9 | 82,4 |
| `dp4a` | 13,0 | 18,6 |
| `both` | 76,3 | 75,6 |
| `ampere_int8_probe.py` (melhor cadeia) | 77,2 | 82,5 |

`both` ficou abaixo de `mma` nas duas cargas: a DP4A atrapalha o MMA, e a ideia foi abandonada. O pico de ~82,5 TMAC/s confirma que o SRBMiner, a ~78,5 TH/s, já opera perto de 95% do teto desta placa.

## 2. Viabilidade do Strassen de 1 nível em INT8 — `strassen_int8_feasibility.py`

Roda em **qualquer sistema** (só CPU, biblioteca padrão). Já executado:

```text
python3 tools/experiments/strassen_int8_feasibility.py --output dist/strassen.json
```

Resultado confirmado localmente (400 tentativas por tamanho):

- O Strassen ideal de 1 nível é **exato** sobre inteiros (7 produtos em vez de 8,
  teto de **+14,3%**).
- Os operandos somados ficam em [−128, +128]. O **único** valor fora do INT8 é
  **+128**, em ~0,007% das posições, e a correção de posto 1 (gravar 127 e somar
  o resto em INT32) **restaura a exatidão**.

**Portão de decisão — o bloqueio real não é a aritmética, é o protocolo.** O
Pearl grava no transcript BLAKE3 os produtos intermediários por fatia de K. Se
cada fatia precisar do produto do bloco **antes** da recombinação do Strassen,
não dá para trocar o GEMM do bloco por Strassen e ainda produzir o transcript
correto. Isso **tem de ser resolvido lendo o código oficial** (`noisy_gemm`,
`inner_hash`; ver a investigação) antes de escrever qualquer kernel. Se o
transcript exigir os intermediários, a ideia 2 morre aqui, com matemática
bonita e inaplicável.

**Atualização (leitura do código oficial, `zk-pow/src/ffi/mine.rs` no commit
`d7c517d`):** o transcript faz XOR do acumulador **de todo o tile** ao fim de cada
fatia de `rank` posições de K, e não de cada instrução `mma`. Com `rank = 128` (o
valor do teste oficial), um nível de Strassen que divide K em 64 + 64 **dentro** da
fatia preserva exatamente a soma de cada fatia, ou seja, o transcript. O portão do
protocolo abre, desde que o `rank` do job da pool seja pelo menos 64 (ele vem na
configuração de cada trabalho; confira antes). O custo das somas extras e da
correção do +128 continua sendo o que decide se compensa.

## 3. Reaproveitar o produto sem ruído — **não implementado, de propósito**

A ideia de calcular A·B uma vez e reaproveitá-lo entre tentativas, somando só as
correções baratas do ruído de baixo rank, é **exatamente o atalho que o desenho
do protocolo e a prova de segurança do Pearl existem para impedir**. Construir
isso seria contornar a integridade do PoW, não otimizar mineração.

Se, ao estudar os probes acima, aparecer evidência de que esse reaproveitamento
passa pela verificação, trate como **vulnerabilidade** e reporte à Pearl Research
Labs — não como vantagem de mineração. Este repositório não traz código para
isso.

## Depois dos probes

Se algum portão abrir, o próximo passo barato é um **A/B dos mineradores abertos
já existentes** (FreeForGemPear, pearl-gpu-miner) contra o SRBMiner, medindo
TH/s líquido de taxa, watts e shares aceitas pelo agente do painel — uma taxa de
desenvolvedor menor pode render mais que um kernel novo. Aplicar a técnica a um
fork de um minerador aberto (revisando a licença) é mais seguro que começar do
zero.
