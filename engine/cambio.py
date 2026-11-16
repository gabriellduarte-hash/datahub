"""Câmbio para a fórmula CONVERTER, com o histórico do Banco Central Europeu.

O histórico (desde 1999, cerca de 30 moedas ativas) fica guardado no projeto em
`engine/dados/cambio_bce.csv.gz`, para o hub não depender de outro serviço no
meio de uma análise. Para atualizar:

    python -m engine.cambio atualizar

O BCE publica quanto vale 1 euro em cada moeda. Converter de uma moeda para
outra divide uma cotação pela outra, no mesmo dia. CONVERTER tem três modos:

- sem data: a cotação mais recente do arquivo;
- com uma data: a cotação daquele dia (fim de semana usa o último dia útil);
- com um ano: o valor dividido pela média do ano da cotação da moeda de origem
  em relação à de destino. É a convenção dos salários em dólar publicados com
  a média anual, como o `salary_in_usd` do ds_salaries.
"""

import gzip
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

from engine.sql import literal

ARQUIVO = Path(__file__).parent / "dados" / "cambio_bce.csv.gz"
ENDERECO = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
TABELA = "__cambio"


def garantir(con) -> None:
    """Carrega as cotações na sessão, uma vez. Precisa de uma conexão com acesso a arquivos."""
    existe = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [TABELA]
    ).fetchone()[0]
    if existe:
        return
    con.execute(f"""
        CREATE TABLE {TABELA} AS
        WITH largo AS (
            SELECT * FROM read_csv({literal(str(ARQUIVO))}, header = true, nullstr = 'N/A', all_varchar = true)
        ), longo AS (
            UNPIVOT largo ON COLUMNS(* EXCLUDE (Date)) INTO NAME moeda VALUE cotacao
        )
        SELECT CAST(Date AS DATE) AS __data, upper(trim(moeda)) AS __moeda, TRY_CAST(cotacao AS DOUBLE) AS __por_euro
        FROM longo WHERE TRY_CAST(cotacao AS DOUBLE) IS NOT NULL AND regexp_full_match(trim(moeda), '[A-Za-z]{{3}}')
        UNION ALL
        SELECT CAST(Date AS DATE), 'EUR', 1.0 FROM largo
        ORDER BY __moeda, __data
    """)


def moedas(con) -> list[str]:
    return [linha[0] for linha in con.execute(f"SELECT DISTINCT __moeda FROM {TABELA} ORDER BY 1").fetchall()]


def expressao(valor: str, moeda: str, destino: str, data: str | None, modo: str | None) -> str:
    """SQL da conversão. Moeda sem cotação no dia ou no ano dá vazio, que a receita conta como perdido."""
    # As colunas da tabela de câmbio começam com "__": dentro da subconsulta, uma
    # coluna do usuário chamada "moeda" ou "data" continua sendo a do usuário.
    origem = f"upper(trim({moeda}))"
    alvo = f"upper(trim({destino}))"
    juncao = (f"FROM {TABELA} __o JOIN {TABELA} __d ON __d.__data = __o.__data AND __d.__moeda = {alvo} "
              f"WHERE __o.__moeda = {origem}")
    if modo == "ano":
        media = f"(SELECT avg(__o.__por_euro / __d.__por_euro) {juncao} AND year(__o.__data) = {data})"
        return f"({valor} / nullif({media}, 0))"
    filtro = f" AND __o.__data <= CAST({data} AS DATE)" if modo == "dia" else ""
    fator = f"(SELECT arg_max(__d.__por_euro / __o.__por_euro, __o.__data) {juncao}{filtro})"
    return f"({valor} * {fator})"


def atualizar(destino: Path = ARQUIVO) -> int:
    """Baixa o histórico do BCE e guarda comprimido. Devolve quantos dias vieram."""
    with urllib.request.urlopen(ENDERECO, timeout=60) as resposta:
        compactado = resposta.read()
    with zipfile.ZipFile(io.BytesIO(compactado)) as pacote:
        nome = next(n for n in pacote.namelist() if n.endswith(".csv"))
        conteudo = pacote.read(nome)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(destino, "wb", compresslevel=9) as saida:
        saida.write(conteudo)
    return conteudo.count(b"\n") - 1


if __name__ == "__main__":
    if sys.argv[1:] == ["atualizar"]:
        dias = atualizar()
        print(f"Cotações do BCE atualizadas: {dias} dias em {ARQUIVO}")
    else:
        print("Uso: python -m engine.cambio atualizar")
