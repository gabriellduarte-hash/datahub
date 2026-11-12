"""Reconhece o tipo real de uma coluna de texto, entendendo formato brasileiro.

"R$ 1.234,56", "1.234,56" e "1234.56" são o mesmo tipo de dado escrito de
jeitos diferentes. A detecção olha uma amostra; quem conta os valores que não
convertem é o bloco de normalização, no arquivo inteiro.
"""

from dataclasses import dataclass, field

import duckdb

from engine.sql import ident, literal

# Um tipo vale para a coluna quando esta fração dos valores preenchidos encaixa.
FRACAO_MINIMA = 0.90

RE_INTEIRO = r"[+-]?[0-9]+"
RE_MOEDA = r"-?\s*R\$\s*-?\s*[0-9][0-9.,]*"
RE_BR = r"[+-]?([0-9]{1,3}(\.[0-9]{3})+(,[0-9]+)?|[0-9]+,[0-9]+)"
RE_US = r"[+-]?([0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?|[0-9]+\.[0-9]+)"
RE_PERCENTUAL = r"[+-]?[0-9][0-9.,]*\s*%"
# Inteiro com separador de milhar: "1.884" ou "1,884". Começa sem zero, senão
# "0.125" viraria 125.
RE_MILHAR = r"[+-]?[1-9][0-9]{0,2}([.,][0-9]{3})+"

# Unidade escrita depois do número: "28 yrs", "1.7 hrs", "5 kg". A lista é
# fechada de propósito: "1.5M" (milhão) ou um código como "123AB" não podem
# perder letras. "m" sozinho fica de fora porque pode ser metro ou milhão.
RE_UNIDADE = (
    r"\s*(anos?|yrs?|years?|hrs?|hours?|horas?|h|mins?|minutes?|minutos?|secs?|seconds?|"
    r"segundos?|seg|s|dias?|days?|meses|months?|kg|g|mg|lbs?|km|cm|mm|ml|un|units?|pcs?|"
    r"pts?|pontos?|points?|vezes|times|x)\.?$"
)
# Símbolo de moeda estrangeira no começo: "$1,884", "€ 20".
RE_SIMBOLO = r"^(US\$|\$|€|£)\s*"

# Forma do texto -> formato de data. Olhar a forma antes evita que "14/12/24"
# seja lido como o ano 24 pelo formato de quatro dígitos.
FORMATOS_DATA = [
    (r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{2}", "%d/%m/%y"),
    (r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4}", "%d/%m/%Y"),
    (r"[0-9]{4}-[0-9]{2}-[0-9]{2}", "%Y-%m-%d"),
    (r"[0-9]{1,2}-[0-9]{1,2}-[0-9]{4}", "%d-%m-%Y"),
    (r"[0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4}", "%d.%m.%Y"),
    (r"[0-9]{4}/[0-9]{1,2}/[0-9]{1,2}", "%Y/%m/%d"),
    (r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4} [0-9]{1,2}:[0-9]{2}:[0-9]{2}", "%d/%m/%Y %H:%M:%S"),
    (r"[0-9]{1,2}/[0-9]{1,2}/[0-9]{4} [0-9]{1,2}:[0-9]{2}", "%d/%m/%Y %H:%M"),
    (r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", "%Y-%m-%d %H:%M:%S"),
    (r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}", "%Y-%m-%dT%H:%M:%S"),
    # ISO 8601 de APIs e exportações: "2017-11-13T17:13:01.000Z".
    (r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{1,6}Z", "%Y-%m-%dT%H:%M:%S.%fZ"),
    (r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", "%Y-%m-%dT%H:%M:%SZ"),
]

VERDADEIROS = {"sim", "s", "true", "verdadeiro", "yes", "y"}
FALSOS = {"nao", "não", "n", "false", "falso", "no"}


@dataclass
class TipoDetectado:
    tipo: str                     # inteiro, decimal, moeda, percentual, data, booleano, texto
    formatos: list[str] = field(default_factory=list)   # só para data
    milhar_ponto: bool = True     # só para números: "1.234" é mil e não 1,234
    misto: bool = False           # a coluna mistura mais de um jeito de escrever


def detectar(con: duckdb.DuckDBPyConnection, tabela: str, coluna: str, filtro: str) -> TipoDetectado:
    """Tipo de uma coluna de texto. `filtro` é a condição SQL dos valores preenchidos."""
    v = f"trim({ident(coluna)})"
    # O número sem a unidade e sem o símbolo de moeda estrangeira.
    n = f"regexp_replace(regexp_replace({v}, {literal(RE_UNIDADE)}, '', 'i'), {literal(RE_SIMBOLO)}, '')"
    casa = lambda texto, padrao: f"regexp_full_match({texto}, {literal(padrao)})"
    linha = con.execute(f"""
        WITH valores AS (
            SELECT {v} AS v, {n} AS n FROM {ident(tabela)} WHERE NOT {filtro}
        ), marcados AS (
            SELECT v, n,
                   {casa("n", RE_INTEIRO)} AS inteiro,
                   {casa("v", RE_MOEDA)} AS moeda,
                   {casa("n", RE_BR)} AS br,
                   {casa("n", RE_US)} AS us,
                   {casa("n", RE_PERCENTUAL)} AS percentual,
                   {casa("n", RE_MILHAR)} AS milhar
            FROM valores
        )
        SELECT count(*),
               count(*) FILTER (inteiro),
               count(*) FILTER (moeda),
               count(*) FILTER (br),
               count(*) FILTER (us),
               count(*) FILTER (percentual),
               count(*) FILTER (inteiro OR moeda OR br OR us OR percentual),
               count(*) FILTER (inteiro OR milhar),
               count(*) FILTER (n <> v),
               count(*) FILTER ({casa("v", "0[0-9]+")}),
               count(DISTINCT lower(v))
        FROM marcados
    """).fetchone()
    total, inteiros, moedas, br, us, percentuais, numericos, sem_casas, com_unidade, com_zero, distintos = linha
    if not total:
        return TipoDetectado("texto")

    # Códigos como CEP e matrícula têm zero à esquerda: são texto, não número.
    if com_zero / total > 0.05:
        return TipoDetectado("texto")

    if numericos / total >= FRACAO_MINIMA:
        milhar_ponto = moedas + br >= us
        escritas = sum(1 for k in (inteiros, moedas, br, us, percentuais, com_unidade) if k / total >= 0.02)
        if moedas / total >= 0.2:
            return TipoDetectado("moeda", milhar_ponto=milhar_ponto, misto=escritas > 1)
        if percentuais / total >= FRACAO_MINIMA:
            return TipoDetectado("percentual", milhar_ponto=milhar_ponto)
        # "1,884" numa coluna de inteiros é mil oitocentos e oitenta e quatro.
        if sem_casas == numericos and not moedas and not percentuais:
            return TipoDetectado("inteiro", misto=escritas > 1)
        return TipoDetectado("decimal", milhar_ponto=milhar_ponto, misto=escritas > 1)

    formatos = _formatos_de_data(con, tabela, v, filtro, total)
    if formatos:
        return TipoDetectado("data", formatos=formatos, misto=len(formatos) > 1)

    if distintos <= 2:
        valores = {
            linha[0] for linha in con.execute(
                f"SELECT DISTINCT lower({v}) FROM {ident(tabela)} WHERE NOT {filtro}"
            ).fetchall()
        }
        if valores <= VERDADEIROS | FALSOS and valores & VERDADEIROS and valores & FALSOS:
            return TipoDetectado("booleano")

    return TipoDetectado("texto")


def _formatos_de_data(con, tabela, v, filtro, total) -> list[str]:
    contagens = ", ".join(
        f"count(*) FILTER (regexp_full_match({v}, {literal(forma)}) "
        f"AND try_strptime({v}, {literal(formato)}) IS NOT NULL)"
        for forma, formato in FORMATOS_DATA
    )
    linha = con.execute(f"SELECT {contagens} FROM {ident(tabela)} WHERE NOT {filtro}").fetchone()
    achados = [(n, FORMATOS_DATA[i][1]) for i, n in enumerate(linha) if n]
    if sum(n for n, _ in achados) / total < FRACAO_MINIMA:
        return []

    formatos = [formato for _, formato in sorted(achados, key=lambda item: -item[0])]
    if "%d/%m/%Y" in formatos and _parece_data_americana(con, tabela, v, filtro):
        formatos[formatos.index("%d/%m/%Y")] = "%m/%d/%Y"
    # "%y" precisa vir antes de "%Y": o formato de quatro dígitos também aceita "24".
    return sorted(formatos, key=lambda formato: "%y" not in formato)


def _parece_data_americana(con, tabela, v, filtro) -> bool:
    """Em 03/25/2024 o segundo número passa de 12: o mês veio primeiro."""
    primeiro, segundo = con.execute(f"""
        SELECT count(*) FILTER (TRY_CAST(split_part({v}, '/', 1) AS INT) > 12),
               count(*) FILTER (TRY_CAST(split_part({v}, '/', 2) AS INT) > 12)
        FROM {ident(tabela)} WHERE NOT {filtro}
    """).fetchone()
    return segundo > 0 and primeiro == 0


def tipo_de_coluna_tipada(tipo_duckdb: str) -> str:
    """Tipo lógico de uma coluna que já tem tipo no DuckDB."""
    if tipo_duckdb in {"DATE", "TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMP_NS", "TIMESTAMP_MS", "TIMESTAMP_S"}:
        return "data"
    if tipo_duckdb == "BOOLEAN":
        return "booleano"
    if tipo_duckdb in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
                       "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT"}:
        return "inteiro"
    if tipo_duckdb in {"FLOAT", "DOUBLE"} or tipo_duckdb.startswith("DECIMAL"):
        return "decimal"
    return "texto"
