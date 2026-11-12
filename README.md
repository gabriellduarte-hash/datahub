# datahub

Normalização, gráficos e insights de planilhas, sem IA: regras, estatística e SQL.

O hub trabalha em blocos, e cada um entrega o resultado para o próximo:

| Bloco | O que faz | Código |
|---|---|---|
| 1. Arquivo | Lê CSV, TSV, TXT, XLSX ou Parquet e aplica os limites | `engine/leitura.py` |
| 2. Perfil | Descobre tipos, papéis, vazios e duplicatas | `engine/perfil.py`, `engine/tipos.py` |
| 3. Normalização | Sugere passos e aplica a receita, gerando SQL | `engine/normalizacao.py` |
| 4. Gráficos | Recomenda gráficos e calcula os dados no DuckDB | `engine/graficos.py` |
| 5. Insights | Catálogo de analisadores com evidências e ressalvas | `engine/insights.py` |

`engine/pipeline.py` liga os blocos, `api/` expõe a engine e `web/` é a interface.

## Rodar

Só na primeira vez:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Para abrir o hub (API na porta 8000 e interface na 3000):

```bash
./dev.sh
```

Depois acesse http://localhost:3000. `Ctrl+C` encerra tudo.

## Testar

```bash
.venv/bin/pytest
.venv/bin/python -m samples.benchmark 100000 1000000
```

`samples/gerar_vendas.py` fabrica um CSV com sujeira e efeitos de negócio plantados, e um gabarito do que plantou. Os testes usam esse gabarito para conferir se o hub acertou, e dados de puro ruído para conferir que ele não inventa descobertas.

## Limites

Ficam em `engine/limites.py`. Os de carga protegem a máquina: 10 milhões de linhas, 500 colunas e um teto de células. Os de análise protegem os insights. `DATAHUB_MODO=publico` liga os limites do site público.
