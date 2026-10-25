# Catálogo de GPUs NVIDIA GeForce GTX e RTX

O [dataset legível por máquinas](../data/nvidia-gpus.json) identifica **158 modelos únicos** das séries GTX 400 até RTX 50, incluindo desktop, notebook e all-in-one. Os nomes e identificadores PCI foram extraídos do apêndice oficial da NVIDIA; nomes alternativos como `750Ti`/`750 Ti` e `3050Ti`/`3050 Ti` foram consolidados e não contam como placas adicionais.

## O que foi pesquisado

| Informação | Cobertura e significado |
| --- | --- |
| Nomes, família, aliases e PCI | Todos os 158 registros; fonte primária NVIDIA por registro |
| Formato físico | 79 desktop, 62 notebook, 4 all-in-one e 13 com nome ambíguo |
| Memória e potência de referência | 44 registros com variantes publicadas nas tabelas oficiais; demais campos sem evidência ficam `null` |
| CUDA compute capability | 60 correspondências exatas com a tabela oficial de produtos legados |
| Arquitetura | Famílias modernas sustentadas pelos comparativos NVIDIA; variantes excepcionais e modelos antigos sem evidência direta ficam `null` |
| Overclock, desempenho Pearl e estabilidade | Somente a unidade RTX 3070 do autor foi medida; o catálogo não contém presets de overclock |

## Como interpretar os dados

- `sources` e `field_sources` registram a fonte de cada fato, com o escopo da evidência. `metadata.checked_at` informa quando a pesquisa foi atualizada.
- `pci_device_ids` guarda o identificador do chip. `pci_identifiers` conserva também os identificadores de subsistema quando publicados. Um chip pode aparecer em projetos diferentes; não basta o PCI de quatro dígitos para garantir memória, formato ou limites elétricos.
- `form_factor: unspecified` significa que a NVIDIA usa o mesmo nome em produtos desktop e notebook. Por exemplo, uma GTX 1060 identificada apenas pelo nome não autoriza aplicar limites de uma placa desktop.
- `memory_gb` e `published_power_w` só recebem um número quando a identidade e a fonte permitem um valor único. `reference_variants` preserva as alternativas publicadas. Nos nomes ambíguos, variantes de desktop ficam explicitamente separadas.
- A potência publicada é uma especificação do projeto de referência, não o limite configurável da placa detectada. A RTX 3070 de referência tem 220 W na tabela NVIDIA, enquanto a unidade do autor informa 240 W de limite de fábrica. O controlador deve consultar o hardware.
- `tuning_validated: true` na RTX 3070 representa uma única unidade desktop medida pelo autor. Não valida todos os fabricantes, BIOS, notebooks ou outras unidades do mesmo modelo.
- A inclusão no catálogo não garante suporte no driver atual, em uma versão do CUDA, no SRBMiner ou no algoritmo Pearl. A fonte do driver contém também seções de produtos legados.

## Benchmark e ajuste dinâmico

O catálogo serve à identificação. A decisão de aplicar um ajuste depende das capacidades e limites informados pelo driver da GPU real, do algoritmo executado e das medições do benchmark local. Modelos conhecidos sem suporte de escrita devem continuar em observação. Não há hashrate presumido nem preset universal de clock, temperatura ou potência nesta pesquisa.

## Reproduzir a pesquisa e validar

```powershell
mise exec -- python tools/validate_gpu_catalog.py
mise exec -- python tools/validate_gpu_catalog.py --refresh
```

O primeiro comando valida o JSON local sem rede. O segundo busca novamente as tabelas oficiais de identificação e compute capability, reconstrói o dataset com as especificações de referência curadas e atualiza esta página. A revisão humana das tabelas de comparação continua necessária quando essas especificações mudarem. A soma SHA256 dos documentos de identificação fica em `sources` para rastreabilidade; não é um checksum de firmware ou de programa executável.

## Fontes primárias

1. [NVIDIA — Supported GPU Products, driver 580.95.05](https://download.nvidia.com/XFree86/Linux-x86_64/580.95.05/README/supportedchips.html): nomes e PCI, inclusive produtos legados.
2. [NVIDIA — comparação GeForce desktop](https://www.nvidia.com/en-us/geforce/graphics-cards/compare/): arquitetura das famílias recentes, memória e potência dos projetos de referência.
3. [NVIDIA — comparação GeForce notebooks](https://www.nvidia.com/en-us/geforce/laptops/compare/): arquitetura das famílias recentes e variantes RTX 50 para notebook.
4. [NVIDIA Developer — compute capability legado](https://developer.nvidia.com/cuda/gpus/legacy): capacidade CUDA por nome exato.

## Índice dos modelos

Valores `—` indicam informação não estabelecida para a identidade exata. A coluna de memória apresenta variantes de referência quando disponíveis, com o formato explicitado no JSON.

| Modelo | Família | Formato | Memória (GB) | PCI IDs |
| --- | --- | --- | --- | --- |
| GTX 1050 | GTX 10 Series | Ambíguo | — | 1C22, 1C62, 1C81, 1C83, 1C8D, 1C91, 1C92 |
| GTX 1050 Ti | GTX 10 Series | Desktop | — | 1C21, 1C61, 1C82, 1C8C, 1C8F |
| GTX 1050 Ti with Max-Q Design | GTX 10 Series | Notebook | — | 1C8C, 1C8F |
| GTX 1050 with Max-Q Design | GTX 10 Series | Notebook | — | 1C8D, 1C91, 1C92 |
| GTX 1060 | GTX 10 Series | Ambíguo | — | 1C20, 1C23, 1C60 |
| GTX 1060 3GB | GTX 10 Series | Desktop | 3 | 1B84, 1C02 |
| GTX 1060 5GB | GTX 10 Series | Desktop | 5 | 1C04 |
| GTX 1060 6GB | GTX 10 Series | Desktop | 6 | 1B83, 1C03, 1C06 |
| GTX 1060 with Max-Q Design | GTX 10 Series | Notebook | — | 1C20, 1C60 |
| GTX 1070 | GTX 10 Series | Ambíguo | — | 1B81, 1BA1, 1BA2, 1BE1 |
| GTX 1070 Ti | GTX 10 Series | Desktop | — | 1B82 |
| GTX 1070 with Max-Q Design | GTX 10 Series | Notebook | — | 1BA1, 1BE1 |
| GTX 1080 | GTX 10 Series | Ambíguo | — | 1B80, 1BA0, 1BE0 |
| GTX 1080 Ti | GTX 10 Series | Desktop | — | 1B06 |
| GTX 1080 with Max-Q Design | GTX 10 Series | Notebook | — | 1BA0, 1BE0 |
| GTX 1630 | GTX 16 Series | Desktop | 4 | 1F83 |
| GTX 1650 | GTX 16 Series | Ambíguo | 4 | 1F0A, 1F82, 1F91, 1F96, 1F99, 1F9D, 1FDD, 2188 |
| GTX 1650 SUPER | GTX 16 Series | Desktop | 4 | 2187 |
| GTX 1650 Ti | GTX 16 Series | Ambíguo | — | 1F95, 2192 |
| GTX 1650 Ti with Max-Q Design | GTX 16 Series | Notebook | — | 1F95 |
| GTX 1650 with Max-Q Design | GTX 16 Series | Notebook | — | 1F91, 1F96, 1F99, 1F9D |
| GTX 1660 | GTX 16 Series | Desktop | 6 | 2184 |
| GTX 1660 SUPER | GTX 16 Series | Desktop | 6 | 21C4 |
| GTX 1660 Ti | GTX 16 Series | Ambíguo | 6 | 2182, 2191, 21D1 |
| GTX 1660 Ti with Max-Q Design | GTX 16 Series | Notebook | — | 2191 |
| GTX 460 | GTX 400 Series | Desktop | — | 0E22, 0E24 |
| GTX 460 SE | GTX 400 Series | Desktop | — | 0E23 |
| GTX 460 SE v2 | GTX 400 Series | Desktop | — | 1203 |
| GTX 460 v2 | GTX 400 Series | Desktop | — | 1205 |
| GTX 460M | GTX 400 Series | Notebook | — | 0DD1 |
| GTX 465 | GTX 400 Series | Desktop | — | 06C4 |
| GTX 470 | GTX 400 Series | Desktop | — | 06CD |
| GTX 470M | GTX 400 Series | Notebook | — | 0E30 |
| GTX 480 | GTX 400 Series | Desktop | — | 06C0 |
| GTX 480M | GTX 400 Series | Notebook | — | 06CA |
| GTX 485M | GTX 400 Series | Notebook | — | 0E31 |
| GTX 550 Ti | GTX 500 Series | Desktop | — | 1244 |
| GTX 555 | GTX 500 Series | Desktop | — | 1206 |
| GTX 560 | GTX 500 Series | Desktop | — | 1084, 1201 |
| GTX 560 SE | GTX 500 Series | Desktop | — | 1208 |
| GTX 560 Ti | GTX 500 Series | Desktop | — | 1082, 1087, 1200 |
| GTX 560M | GTX 500 Series | Notebook | — | 1251 |
| GTX 570 | GTX 500 Series | Desktop | — | 1081, 1086 |
| GTX 570M | GTX 500 Series | Notebook | — | 1210 |
| GTX 580 | GTX 500 Series | Desktop | — | 1080, 1089, 108B |
| GTX 580M | GTX 500 Series | Notebook | — | 1211 |
| GTX 590 | GTX 500 Series | Desktop | — | 1088 |
| GTX 645 | GTX 600 Series | Desktop | — | 11C4 |
| GTX 650 | GTX 600 Series | Desktop | — | 0FC6, 11C8 |
| GTX 650 Ti | GTX 600 Series | Desktop | — | 11C3, 11C6 |
| GTX 650 Ti BOOST | GTX 600 Series | Desktop | — | 11C2 |
| GTX 660 | GTX 600 Series | Desktop | — | 1185, 1195, 11C0 |
| GTX 660 Ti | GTX 600 Series | Desktop | — | 1183 |
| GTX 660M | GTX 600 Series | Notebook | — | 0FD4, 0FE0 |
| GTX 670 | GTX 600 Series | Desktop | — | 1189 |
| GTX 670M | GTX 600 Series | Notebook | — | 1213 |
| GTX 670MX | GTX 600 Series | Notebook | — | 11A1 |
| GTX 675M | GTX 600 Series | Notebook | — | 1212 |
| GTX 675MX | GTX 600 Series | Notebook | — | 11A2, 11A7 |
| GTX 680 | GTX 600 Series | Desktop | — | 1180 |
| GTX 680M | GTX 600 Series | Notebook | — | 11A0 |
| GTX 680MX | GTX 600 Series | Notebook | — | 11A3 |
| GTX 690 | GTX 600 Series | Desktop | — | 1188 |
| GTX 745 | GTX 700 Series | Desktop | — | 1382 |
| GTX 750 | GTX 700 Series | Desktop | — | 1381, 1407 |
| GTX 750 Ti | GTX 700 Series | Desktop | — | 1380, 1392, 139B, 139D |
| GTX 760 | GTX 700 Series | Desktop | — | 1185, 1187, 1199 |
| GTX 760 (192-bit) | GTX 700 Series | Desktop | — | 118E |
| GTX 760 Ti OEM | GTX 700 Series | Desktop | — | 1189, 1193 |
| GTX 760A | GTX 700 Series | All-in-one | — | 11E3 |
| GTX 760M | GTX 700 Series | Notebook | — | 11E3 |
| GTX 765M | GTX 700 Series | Notebook | — | 11E1, 11E2 |
| GTX 770 | GTX 700 Series | Desktop | — | 1184 |
| GTX 770M | GTX 700 Series | Notebook | — | 11E0 |
| GTX 775M | GTX 700 Series | Notebook | — | 119D |
| GTX 780 | GTX 700 Series | Desktop | — | 1004, 1007 |
| GTX 780 Ti | GTX 700 Series | Desktop | — | 1008, 100A |
| GTX 780M | GTX 700 Series | Notebook | — | 119E, 119F |
| GTX 850A | GTX 800 Series | All-in-one | — | 1391 |
| GTX 850M | GTX 800 Series | Notebook | — | 1391 |
| GTX 860M | GTX 800 Series | Notebook | — | 119A, 1392, 139B |
| GTX 870M | GTX 800 Series | Notebook | — | 1199 |
| GTX 880M | GTX 800 Series | Notebook | — | 1198 |
| GTX 950 | GTX 900 Series | Desktop | — | 1402, 1427 |
| GTX 950A | GTX 900 Series | All-in-one | — | 139A |
| GTX 950M | GTX 900 Series | Notebook | — | 139A |
| GTX 960 | GTX 900 Series | Desktop | — | 13D8, 1401, 1406 |
| GTX 960A | GTX 900 Series | All-in-one | — | 139B |
| GTX 960M | GTX 900 Series | Notebook | — | 139B |
| GTX 965M | GTX 900 Series | Notebook | — | 13D9, 1427, 1619, 1667 |
| GTX 970 | GTX 900 Series | Desktop | — | 13C2 |
| GTX 970M | GTX 900 Series | Notebook | — | 13D8, 1618 |
| GTX 980 | GTX 900 Series | Desktop | — | 13C0, 13DA, 161A |
| GTX 980 Ti | GTX 900 Series | Desktop | — | 17C8 |
| GTX 980M | GTX 900 Series | Notebook | — | 13D7, 1617 |
| RTX 2050 | RTX 20 Series | Ambíguo | — | 25A7, 25A9, 25AD, 25ED |
| RTX 2060 | RTX 20 Series | Ambíguo | 6 / 12 | 1E89, 1F03, 1F08, 1F11, 1F12, 1F15, 1F51, 1F55 |
| RTX 2060 SUPER | RTX 20 Series | Desktop | 8 | 1F06, 1F42, 1F47 |
| RTX 2060 with Max-Q Design | RTX 20 Series | Notebook | — | 1F12 |
| RTX 2070 | RTX 20 Series | Ambíguo | 8 | 1F02, 1F07, 1F10, 1F14, 1F50, 1F54 |
| RTX 2070 SUPER | RTX 20 Series | Ambíguo | 8 | 1E84, 1E91, 1EC2, 1EC7, 1ED1 |
| RTX 2070 SUPER with Max-Q Design | RTX 20 Series | Notebook | — | 1E91, 1ED1 |
| RTX 2070 with Max-Q Design | RTX 20 Series | Notebook | — | 1F10, 1F14, 1F50, 1F54 |
| RTX 2080 | RTX 20 Series | Ambíguo | 8 | 1E82, 1E87, 1E90, 1ED0 |
| RTX 2080 SUPER | RTX 20 Series | Ambíguo | 8 | 1E81, 1E93, 1ED3 |
| RTX 2080 SUPER with Max-Q Design | RTX 20 Series | Notebook | — | 1E93, 1ED3 |
| RTX 2080 Ti | RTX 20 Series | Desktop | 11 | 1E04, 1E07 |
| RTX 2080 with Max-Q Design | RTX 20 Series | Notebook | — | 1E90, 1ED0 |
| RTX 3050 | RTX 30 Series | Desktop | 6 / 8 | 2507, 2582, 2584 |
| RTX 3050 4GB Laptop GPU | RTX 30 Series | Notebook | 4 | 25AB |
| RTX 3050 6GB Laptop GPU | RTX 30 Series | Notebook | 6 | 25AC, 25EC |
| RTX 3050 A Laptop GPU | RTX 30 Series | Notebook | — | 2822, 28A3, 28E3 |
| RTX 3050 Laptop GPU | RTX 30 Series | Notebook | — | 25A2, 25A5, 25E2, 25E5 |
| RTX 3050 OEM | RTX 30 Series | Desktop | — | 2508 |
| RTX 3050 Ti Laptop GPU | RTX 30 Series | Notebook | — | 2523, 2563, 25A0, 25A2, 25E0 |
| RTX 3060 | RTX 30 Series | Desktop | 8 / 12 | 2487, 24C7, 2503, 2504, 2544 |
| RTX 3060 Laptop GPU | RTX 30 Series | Notebook | — | 249C, 24A0, 2520, 2521, 2560, 25A0, 25A2 |
| RTX 3060 Ti | RTX 30 Series | Desktop | 8 | 2414, 2486, 2489, 24C9 |
| RTX 3070 | RTX 30 Series | Desktop | 8 | 2484, 2488 |
| RTX 3070 Laptop GPU | RTX 30 Series | Notebook | — | 249D, 24DD |
| RTX 3070 Ti | RTX 30 Series | Desktop | 8 | 2207, 2482 |
| RTX 3070 Ti Laptop GPU | RTX 30 Series | Notebook | — | 24A0, 24E0 |
| RTX 3080 | RTX 30 Series | Desktop | 10 / 12 | 2206, 220A, 2216 |
| RTX 3080 Laptop GPU | RTX 30 Series | Notebook | — | 249C, 24DC |
| RTX 3080 Ti | RTX 30 Series | Desktop | 12 | 2208 |
| RTX 3080 Ti Laptop GPU | RTX 30 Series | Notebook | — | 2420, 2460 |
| RTX 3090 | RTX 30 Series | Desktop | 24 | 2204 |
| RTX 3090 Ti | RTX 30 Series | Desktop | 24 | 2203 |
| RTX 4050 Laptop GPU | RTX 40 Series | Notebook | — | 28A1, 28E1 |
| RTX 4060 | RTX 40 Series | Desktop | 8 | 2808, 2882 |
| RTX 4060 Laptop GPU | RTX 40 Series | Notebook | — | 28A0, 28E0 |
| RTX 4060 Ti | RTX 40 Series | Desktop | 8 / 16 | 2788, 2803, 2805 |
| RTX 4070 | RTX 40 Series | Desktop | 12 | 2709, 2786 |
| RTX 4070 Laptop GPU | RTX 40 Series | Notebook | — | 2820, 2860 |
| RTX 4070 SUPER | RTX 40 Series | Desktop | 12 | 2783 |
| RTX 4070 Ti | RTX 40 Series | Desktop | 12 | 2782 |
| RTX 4070 Ti SUPER | RTX 40 Series | Desktop | 16 | 2689, 2705 |
| RTX 4080 | RTX 40 Series | Desktop | 16 | 2704 |
| RTX 4080 Laptop GPU | RTX 40 Series | Notebook | — | 27A0, 27E0 |
| RTX 4080 SUPER | RTX 40 Series | Desktop | 16 | 2702 |
| RTX 4090 | RTX 40 Series | Desktop | 24 | 2684 |
| RTX 4090 D | RTX 40 Series | Desktop | — | 2685 |
| RTX 4090 Laptop GPU | RTX 40 Series | Notebook | — | 2717, 2757 |
| RTX 5050 | RTX 50 Series | Desktop | 8 | 2D83 |
| RTX 5050 Laptop GPU | RTX 50 Series | Notebook | 8 | 2D98, 2DD8 |
| RTX 5060 | RTX 50 Series | Desktop | 8 | 2D05 |
| RTX 5060 Laptop GPU | RTX 50 Series | Notebook | 8 | 2D19, 2D59 |
| RTX 5060 Ti | RTX 50 Series | Desktop | 8 / 16 | 2D04 |
| RTX 5070 | RTX 50 Series | Desktop | 12 | 2F04 |
| RTX 5070 Laptop GPU | RTX 50 Series | Notebook | 8 / 12 | 2D18, 2D58 |
| RTX 5070 Ti | RTX 50 Series | Desktop | 16 | 2C05 |
| RTX 5070 Ti Laptop GPU | RTX 50 Series | Notebook | 12 | 2F18, 2F58 |
| RTX 5080 | RTX 50 Series | Desktop | 16 | 2C02 |
| RTX 5080 Laptop GPU | RTX 50 Series | Notebook | 16 | 2C19, 2C59 |
| RTX 5090 | RTX 50 Series | Desktop | 32 | 2B85 |
| RTX 5090 D | RTX 50 Series | Desktop | — | 2B87 |
| RTX 5090 D v2 | RTX 50 Series | Desktop | — | 2B8C |
| RTX 5090 Laptop GPU | RTX 50 Series | Notebook | 24 | 2C18, 2C58 |
