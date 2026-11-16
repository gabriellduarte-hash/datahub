# datahub

Normalização, sandbox, gráficos e insights de planilhas, sem IA: regras, estatística e SQL.

O hub tem três etapas:

1. **Triagem**: lê o arquivo e mostra o perfil de cada coluna.
2. **Normalização**: sugere uma receita de limpeza; você marca os passos.
3. **Sandbox**: uma tábua com a planilha no meio, gráficos ao lado e insights embaixo.

Uma sessão pode ter várias tabelas: arquivos, abas do Excel, consultas salvas e arquivos empilhados.

## Blocos da engine

| Bloco | O que faz | Código |
|---|---|---|
| Arquivo | Lê CSV, TSV, TXT, XLSX ou Parquet, numera as linhas e aplica os limites | `engine/leitura.py` |
| Perfil | Descobre tipos, papéis, vazios e duplicatas | `engine/perfil.py`, `engine/tipos.py` |
| Normalização | Sugere passos e aplica a receita, gerando SQL | `engine/normalizacao.py` |
| Sandbox | Edita células, apaga e filtra linhas, renomeia, cria colunas por fórmula | `engine/manipulacao.py`, `engine/formulas.py` |
| Gráficos | Recomenda gráficos e calcula os dados no DuckDB | `engine/graficos.py` |
| Insights | Catálogo de analisadores com evidências e ressalvas | `engine/insights.py` |

Outros módulos:

- `engine/area.py`: a sessão com várias tabelas e a base de análise.
- `engine/relacional.py`: relações, a visão que junta tabelas e o empilhamento.
- `engine/consulta.py`: a vista SQL, só de consulta.
- `engine/cambio.py`: as cotações para a fórmula `CONVERTER`.
- `engine/pipeline.py`: liga os blocos.
- `api/`: expõe a engine.
- `web/`: a interface.

## Regras que protegem os dados

- **Receita:** toda mudança de dados vira um passo da receita, que continua reproduzível e mostra o SQL. Desfazer tira o último passo.
- **Número de linha:** cada linha leva o número que tinha no arquivo (`__linha`), e edições apontam para ele.
- **Fórmulas:** valem para a coluna inteira. Fórmula por célula, como `=A2*B7`, não existe de propósito.
- **Vista SQL:**
  - roda só `SELECT` e `WITH`, um comando por vez, sem acesso ao disco e com tempo limite;
  - um resultado pode ser salvo como tabela nova ou como arquivo `.sql`.
- **Base única:** gráficos e insights olham para uma base só, seja uma tabela ou uma visão que junta tabelas.
  - A visão só traz colunas do lado onde a chave é única.
  - Uma junção que repetiria linhas é recusada; no lugar, a visão traz um resumo (contagem e somas por chave).

## Fórmulas

Escritas como no Excel em português. Argumentos são separados por `;`, e a vírgula pode ser decimal. Colunas com espaço vão entre colchetes, como `[Average Cost for two]`.

| Tipo | Funções |
|---|---|
| Lógica | `SE`, `E`, `OU`, `NAO`, `SEVAZIO`, `EVAZIO` |
| Números | `ARRED`, `ABS`, `RAIZ`, `POTENCIA`, `LN`, `INT`, `MOD`, `VALOR` |
| Totais | `SOMA`, `MEDIA`, `MAXIMO`, `MINIMO`: com um argumento, o total da coluna; com vários, a conta na linha |
| Totais por critério | `MEDIANA`, `CONT`, `CONT.SE`, `SOMASE` |
| Texto | `MAIUSCULA`, `MINUSCULA`, `ARRUMAR`, `NUM.CARACT`, `ESQUERDA`, `DIREITA`, `EXT.TEXTO`, `SUBSTITUIR`, `CONCATENAR`, `CONTEM`, `TEXTO` |
| Datas | `ANO`, `MES`, `DIA`, `DIA.DA.SEMANA`, `HOJE`, `DATA`, `DIAS` |
| Moedas | `CONVERTER(valor; moeda; destino [; data ou ano])` |

Os nomes em inglês também funcionam, como `IF`, `ROUND`, `SUM` e `COUNTIF`.

`CONVERTER` tem três modos:

- sem data, usa a cotação mais recente;
- com uma data, usa a cotação daquele dia;
- com um ano, como `ANO(data)`, usa a média do ano.

As cotações são do Banco Central Europeu e ficam em `engine/dados/`.

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

Para atualizar as cotações de câmbio:

```bash
.venv/bin/python -m engine.cambio atualizar
```

## Testar

```bash
.venv/bin/pytest
.venv/bin/python -m samples.benchmark 100000 1000000
```

`samples/gerar_vendas.py` fabrica um CSV com sujeira e efeitos de negócio plantados, e um gabarito do que plantou. Os testes usam esse gabarito para conferir se o hub acertou, e dados de puro ruído para conferir que ele não inventa descobertas.

## Limites

Ficam em `engine/limites.py`.

- **Carga:** protegem a máquina. São 10 milhões de linhas, 500 colunas e um teto de células.
- **Análise:** protegem os insights. Bases de até 200 mil linhas se reanalisam sozinhas depois de uma edição.
- **Site público:** `DATAHUB_MODO=publico` liga os limites próprios dele.
