# Contribuir com o HashPilot

Comece pelo [índice da documentação](docs/index.md) e pela [arquitetura](docs/architecture.md). Para contexto de hardware, consulte os [scripts de referência](mining/windows/README.md).

O produto suporta somente Windows 10/11 x64. Valide mudanças no ambiente Windows e diferencie testes reais de simulações de layout ou hardware.

## Preparar o ambiente

Clone o repositório fora de pastas de sincronização de documentos e use o Python gerenciado pelo mise:

```text
mise install
mise exec -- python -m unittest discover -v
mise exec -- python monitor.py
```

Os testes comuns não precisam instalar ou executar um minerador. Não use o teste unitário como ponto de entrada para alterar clocks. Para mudanças de interface, a QA opt-in usa janelas próprias e diretórios temporários:

```text
mise exec -- python test_responsive_ui.py --run --native-dpi
mise exec -- python test_windowing.py --run --native-dpi
```

Informe as dimensões solicitadas e reais, área útil, DPI e se o caso 4K foi virtual ou físico. A validação normal deste repositório é local; não é necessário criar workflows de GitHub Actions.

## Regras de implementação

- Preserve a separação entre monitor, modelo financeiro, política pura e controle real da GPU.
- Não transforme dados ausentes em zero, estimativas em crédito realizado ou catálogo em overclock validado.
- Preserve a semântica de PRL/BRL/USD/BTC, períodos parciais e saldo confirmado/pendente.
- Migrações devem manter carteiras e sessões isoladas e preservar o histórico existente.
- Teste falhas e limites relevantes à mudança, além do caminho normal.
- Use assets oficiais e mantenha a origem documentada; não redesenhe ou relicencie a marca.
- O painel usa GPU0. Testes por UUID no motor não validam telemetria mult GPU nem permitem atribuir hashrate agregado a outra placa.
- Diferencie pesquisa de catálogo, benchmark observado e controle real; informe qual GPU foi fisicamente testada.

## Relatar um problema

Use o [template de bug](https://github.com/TechFernandesLTDA/hashpilot/issues/new?template=bug_report.yml). Inclua versão/revisão, sistema, GPU, passos, resultado esperado e observado. Para problemas visuais, inclua resolução/DPI; para coleta, a idade da amostra e o estado das fontes ajudam mais que uma estimativa isolada de lucro.

`latest.json` e `finance-latest.json` ficam no diretório local de dados. Revise seu conteúdo antes de anexar: podem identificar carteira, caminhos e histórico financeiro. Não anexe seed, chave privada, credenciais ou arquivos internos da carteira. Vulnerabilidades devem seguir [SECURITY.md](SECURITY.md).

## Enviar uma alteração

Abra uma branch com escopo definido e descreva o problema e o comportamento final. Inclua os comandos de validação realmente executados e suas limitações. Atualize a documentação quando mudar configuração, instalação, contratos de dados ou suporte de hardware.

O snapshot público do autor é intencional e não deve ser confundido com dados de teste. Não substitua `portable-data/` com dados pessoais ou de terceiros como efeito colateral de uma correção.

O código usa [GPL-3.0-only](LICENSE). Preserve os avisos e as obrigações dessa licença nas contribuições. A marca e os logos não são licenciados sob GPL: consulte [NOTICE](NOTICE) e [ORIGEM.md](assets/brand/ORIGEM.md).
