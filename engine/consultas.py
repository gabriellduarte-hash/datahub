"""Consultas usadas por gráficos e insights: séries no tempo, rankings e grupos."""

import math
import re
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import duckdb

from engine.perfil import Perfil, PerfilColuna
from engine.sql import colunas_de, eh_nulo, ident, literal, nome_simples
from engine.tipos import FALSOS, VERDADEIROS

GRAOS = {"dia": "day", "semana": "week", "mes": "month", "trimestre": "quarter", "ano": "year"}
AGREGACOES = {
    "soma": "sum({c})",
    "media": "avg({c})",
    "mediana": "median({c})",
    "minimo": "min({c})",
    "maximo": "max({c})",
    "contagem": "count(*)",
    "taxa": None,   # % de "sim" numa coluna de sim/não: ver expressao_sim
}
# Um período coberto abaixo disso é incompleto e não entra em comparações.
COBERTURA_MINIMA = 0.9
NOME_DE_VALOR = re.compile(
    r"valor|receita|venda|fatur|total|custo|preco|lucro|salario|price|sales|revenue|amount|cost|profit|gdp|value|fare"
)
# Volume de uso: o segundo melhor candidato a métrica principal, depois de dinheiro.
NOME_DE_VOLUME = re.compile(
    r"stream|views|visualiza|plays|downloads|likes|clicks|cliques|acessos|visitas|pedidos|orders|units|unidades"
)
# Colunas de sim/não que costumam ser a pergunta da planilha: quem cancelou,
# quem sobreviveu, quem comprou. Vêm primeiro nos insights.
NOME_ALVO = re.compile(
    r"churn|cancel|evas|evadiu|inadimpl|default|fraud|convert|conversao|survived|sobreviv|target|alvo|"
    r"outcome|resultado|aprovad|approved|attrition|exited|desist|abandon|comprou|purchased|clicked|clicou"
)
MAX_INDICADORES = 10


@dataclass
class Sim:
    """Como ler uma coluna de sim/não: 1 para sim, 0 para não, NULL sem valor."""
    expressao: str
    sim: str            # "Churn = sim"
    nao: str            # "Churn = não"


def expressao_sim(con: duckdb.DuckDBPyConnection, tabela: str, coluna: str) -> Sim:
    """Vale para colunas verdadeiro/falso, 0/1 (ou dois números quaisquer, o maior é "sim"),
    "sim"/"não" ainda em texto e um valor só com vazios ("X" ou nada)."""
    tipo = dict(colunas_de(con, tabela))[coluna]
    c = ident(coluna)
    if tipo == "BOOLEAN":
        return Sim(f"CAST({c} AS INT)", f"{coluna} = sim", f"{coluna} = não")

    if tipo == "VARCHAR":
        nulo = eh_nulo(coluna, tipo)
        valores = con.execute(
            f"SELECT lower(trim({c})), min(trim({c})) FROM {ident(tabela)} WHERE NOT {nulo} GROUP BY 1 LIMIT 3"
        ).fetchall()
        minusculos = {v for v, _ in valores}
        if len(valores) == 2 and minusculos <= VERDADEIROS | FALSOS and minusculos & VERDADEIROS:
            sim = ", ".join(literal(v) for v in sorted(VERDADEIROS))
            expressao = f"CASE WHEN {nulo} THEN NULL WHEN lower(trim({c})) IN ({sim}) THEN 1 ELSE 0 END"
            return Sim(expressao, f"{coluna} = sim", f"{coluna} = não")
        if len(valores) == 1:
            return Sim(f"CASE WHEN {nulo} THEN 0 ELSE 1 END", f"{coluna} = {valores[0][1]}", f"{coluna} vazio")
        raise ValueError(f"{coluna} não é uma coluna de sim/não.")

    valores = con.execute(
        f"SELECT DISTINCT {c}, CAST({c} AS VARCHAR) FROM {ident(tabela)} WHERE {c} IS NOT NULL ORDER BY 1 LIMIT 3"
    ).fetchall()
    if len(valores) != 2:
        raise ValueError(f"{coluna} não é uma coluna de sim/não.")
    (_, menor), (_, maior) = valores
    expressao = f"CASE WHEN CAST({c} AS VARCHAR) = {literal(maior)} THEN 1 WHEN {c} IS NOT NULL THEN 0 END"
    return Sim(expressao, f"{coluna} = {maior}", f"{coluna} = {menor}")


def rotulo_sql(nome: str, tipo: str) -> str:
    """O valor de uma coluna como texto para mostrar: verdadeiro/falso vira sim/não."""
    c = ident(nome)
    if tipo == "BOOLEAN":
        return f"CASE WHEN {c} THEN 'sim' WHEN NOT {c} THEN 'não' END"
    return f"CAST({c} AS VARCHAR)"


def por_grupo(con: duckdb.DuckDBPyConnection, fonte: str, grupos: list[tuple[str, str]],
              campos: dict[str, str], contas: dict[str, str]) -> dict[str, list[tuple[str, dict]]]:
    """As `contas` para cada valor de cada coluna de `grupos`, numa varredura só.

    `grupos` é [(nome, tipo)]. `campos` (apelido -> expressão) entram no SELECT
    interno e podem ser usados nas `contas` (apelido -> agregação). Devolve
    {coluna: [(valor, {apelido: resultado})]}, sem o grupo dos vazios.
    """
    if not grupos:
        return {}
    k = len(grupos)
    interno = [f"{rotulo_sql(nome, tipo)} AS __g{j}" for j, (nome, tipo) in enumerate(grupos)]
    interno += [f"{expressao} AS {apelido}" for apelido, expressao in campos.items()]
    linhas = con.execute(f"""
        SELECT {", ".join(f"grouping(__g{j})" for j in range(k))},
               {", ".join(f"__g{j}" for j in range(k))},
               {", ".join(contas.values())}
        FROM (SELECT {", ".join(interno)} FROM {fonte})
        GROUP BY GROUPING SETS ({", ".join(f"(__g{j})" for j in range(k))})
    """).fetchall()
    resultado = {nome: [] for nome, _ in grupos}
    for linha in linhas:
        j = list(linha[:k]).index(0)
        valor = linha[k + j]
        if valor is not None:
            resultado[grupos[j][0]].append((valor, dict(zip(contas, linha[2 * k:]))))
    return resultado


@dataclass
class Ponto:
    inicio: date
    valor: float
    linhas: int
    incompleto: bool = False


def agregado(con: duckdb.DuckDBPyConnection, tabela: str, agregacao: str, coluna: str | None) -> str:
    if agregacao not in AGREGACOES:
        raise ValueError(f"Agregação inválida: {agregacao}")
    if agregacao == "contagem":
        return "count(*)"
    if agregacao == "taxa":
        return f"100.0 * avg({expressao_sim(con, tabela, coluna).expressao})"
    # A coluna pode ter ficado como texto se o usuário não converteu.
    c = ident(coluna)
    if dict(colunas_de(con, tabela))[coluna] == "VARCHAR":
        c = f"TRY_CAST({c} AS DOUBLE)"
    return AGREGACOES[agregacao].format(c=c)


def faixa_normal(con: duckdb.DuckDBPyConnection, fonte: str, expressao: str) -> tuple[float, float, int] | None:
    """(mínimo, máximo, total) da faixa normal de uma coluna numérica.

    Quanto mais linhas, mais natural é aparecer um valor raro: o limite cresce
    com o tamanho, e sobra uma folga para pegar só o que é mesmo anormal.
    """
    q1, mediana, q3, total = con.execute(
        f"SELECT quantile_cont({expressao}, 0.25), median({expressao}), "
        f"quantile_cont({expressao}, 0.75), count({expressao}) FROM {fonte}"
    ).fetchone()
    if q1 is None or q3 <= q1:
        return None
    margem = (math.sqrt(2 * math.log(total)) + 2) * (q3 - q1) / 1.349
    return mediana - margem, mediana + margem, total


def grao_automatico(con: duckdb.DuckDBPyConnection, tabela: str, data: str) -> str:
    d = ident(data)
    inicio, fim, so_anos, so_meses = con.execute(f"""
        SELECT min({d})::DATE, max({d})::DATE,
               bool_and(month({d}) = 1 AND day({d}) = 1), bool_and(day({d}) = 1)
        FROM {ident(tabela)}
    """).fetchone()
    if inicio is None:
        return "mes"
    dias = (fim - inicio).days
    # Dados já resumidos por ano ou mês têm uma data por período.
    if so_anos:
        return "ano"
    if so_meses and dias <= 365 * 6:
        return "mes"
    if dias <= 60:
        return "dia"
    if dias <= 365 * 4:
        return "mes"
    if dias <= 365 * 12:
        return "trimestre"
    return "ano"


def serie(con: duckdb.DuckDBPyConnection, tabela: str, data: str, agregacao: str,
          metrica: str | None, grao: str) -> list[Ponto]:
    """Uma métrica ao longo do tempo. Marca o primeiro e o último período se estiverem incompletos."""
    d = ident(data)
    linhas = con.execute(f"""
        SELECT date_trunc('{GRAOS[grao]}', {d})::DATE AS periodo,
               {agregado(con, tabela, agregacao, metrica)}, count(*), min({d})::DATE, max({d})::DATE,
               count(DISTINCT {d})
        FROM {ident(tabela)} WHERE {d} IS NOT NULL
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    pontos = [Ponto(linha[0], float(linha[1] or 0), linha[2]) for linha in linhas]
    # Com uma data só por período, os dados já vieram resumidos: nada está incompleto.
    resumida = bool(linhas) and statistics.median(linha[5] for linha in linhas) <= 1
    if len(pontos) >= 2 and not resumida:
        pontos[-1].incompleto = _cobertura(linhas[-1][0], linhas[-1][4], grao) < COBERTURA_MINIMA
        pontos[0].incompleto = _cobertura_inicio(linhas[0][0], linhas[0][3], grao) < COBERTURA_MINIMA
    return pontos


def _fim_do_periodo(inicio: date, grao: str) -> date:
    """Primeiro dia do período seguinte."""
    if grao == "dia":
        return inicio + timedelta(days=1)
    if grao == "semana":
        return inicio + timedelta(days=7)
    meses = {"mes": 1, "trimestre": 3, "ano": 12}[grao]
    total = inicio.month - 1 + meses
    return date(inicio.year + total // 12, total % 12 + 1, 1)


def _cobertura(inicio: date, ultimo_dia: date, grao: str) -> float:
    fim = _fim_do_periodo(inicio, grao)
    return ((ultimo_dia - inicio).days + 1) / (fim - inicio).days


def _cobertura_inicio(inicio: date, primeiro_dia: date, grao: str) -> float:
    fim = _fim_do_periodo(inicio, grao)
    return (fim - primeiro_dia).days / (fim - inicio).days


def ranking(con: duckdb.DuckDBPyConnection, tabela: str, categoria: str, agregacao: str,
            metrica: str | None) -> list[tuple[str, float, int]]:
    """(categoria, valor, linhas), do maior para o menor."""
    c = ident(categoria)
    rotulo = rotulo_sql(categoria, dict(colunas_de(con, tabela))[categoria])
    linhas = con.execute(f"""
        SELECT {rotulo}, {agregado(con, tabela, agregacao, metrica)}, count(*)
        FROM {ident(tabela)} WHERE {c} IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC NULLS LAST, 1 LIMIT 5000
    """).fetchall()
    return [(linha[0], float(linha[1] or 0), linha[2]) for linha in linhas]


def metricas(perfil: Perfil, unidades: dict[str, str]) -> list[PerfilColuna]:
    """Métricas da mais para a menos importante: dinheiro somável primeiro."""
    colunas = [c for c in perfil.colunas if c.papel == "metrica" and c.tipo_duckdb != "VARCHAR"]
    return sorted(colunas, key=lambda c: (
        unidades.get(c.nome) != "BRL" or c.agregacao != "soma",
        not NOME_DE_VALOR.search(nome_simples(c.nome)),
        not NOME_DE_VOLUME.search(nome_simples(c.nome)),
        c.agregacao != "soma",
        -c.posicao if unidades.get(c.nome) == "BRL" else c.posicao,
    ))


def categorias(perfil: Perfil) -> list[PerfilColuna]:
    """Categorias com poucos grupos primeiro: dão comparações mais legíveis."""
    colunas = [c for c in perfil.colunas if c.papel == "categoria" and c.distintos >= 2]
    return sorted(colunas, key=lambda c: (c.distintos > 20, c.nulos, c.distintos))


def indicadores(perfil: Perfil) -> list[PerfilColuna]:
    """Colunas de sim/não. As que parecem ser a pergunta da planilha vêm primeiro."""
    colunas = [c for c in perfil.colunas if c.papel == "indicador"]
    return sorted(colunas, key=lambda c: (not eh_alvo(c), c.posicao))[:MAX_INDICADORES]


def eh_alvo(coluna: PerfilColuna) -> bool:
    return bool(NOME_ALVO.search(nome_simples(coluna.nome)))


def datas(perfil: Perfil) -> list[PerfilColuna]:
    """Datas mais preenchidas primeiro: data_pedido vem antes de data_cancelamento."""
    return sorted((c for c in perfil.colunas if c.papel == "data"), key=lambda c: c.nulos)
