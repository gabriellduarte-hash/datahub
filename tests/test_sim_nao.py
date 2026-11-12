"""Colunas de sim/não: a pergunta de muitas planilhas (quem cancelou, quem comprou).

Os dados são fabricados com efeitos plantados, como no arquivo de churn que
motivou estes analisadores: cancelamento muito maior no contrato mensal e
clientes que cancelam com menos meses de casa.
"""

import duckdb
import pytest

from engine import graficos, insights
from engine.consultas import expressao_sim
from engine.limites import LOCAL
from engine.perfil import perfilar_tabela


def clientes(linhas: int = 3000, semente: int = 1, com_efeito: bool = True):
    con = duckdb.connect()
    con.execute("SET threads = 1")
    con.execute(f"SELECT setseed({semente / 100})")
    taxa = "CASE contrato WHEN 'Mensal' THEN 0.40 WHEN 'Anual' THEN 0.12 ELSE 0.03 END" if com_efeito else "0.2"
    meses = "CASE WHEN cancelou THEN 8 ELSE 30 END" if com_efeito else "20"
    con.execute(f"""
        CREATE TABLE limpa AS
        WITH base AS (
            SELECT i AS cliente_id,
                   ['Mensal', 'Mensal', 'Anual', 'Bienal'][1 + CAST(floor(random() * 4) AS INT)] AS contrato,
                   ['Norte', 'Sul', 'Leste'][1 + CAST(floor(random() * 3) AS INT)] AS regiao,
                   random() < 0.9 AS tem_telefone,
                   random() AS sorteio,
                   random() AS u1, random() AS u2
            FROM range({linhas}) t(i)
        ), com_churn AS (
            SELECT *, sorteio < {taxa} AS cancelou FROM base
        )
        SELECT cliente_id, contrato, regiao, tem_telefone,
               CASE WHEN NOT tem_telefone THEN 'Sem telefone'
                    WHEN u1 < 0.5 THEN 'Uma linha' ELSE 'Várias linhas' END AS linhas_telefone,
               cancelou,
               greatest(1, round({meses} + 6 * sqrt(-2 * ln(u1)) * cos(2 * pi() * u2)))::INT AS meses_de_casa,
               round(70 + 20 * sqrt(-2 * ln(u2)) * sin(2 * pi() * u1), 2) AS mensalidade
        FROM com_churn
    """)
    return con


@pytest.fixture(scope="module")
def churn():
    con = clientes()
    perfil = perfilar_tabela(con, "limpa")
    return con, perfil, insights.gerar(con, "limpa", perfil, {}, LOCAL)


def test_os_analisadores_de_sim_nao_rodam(churn):
    *_, resultado = churn
    status = {e.analisador: e.status for e in resultado.execucoes}
    assert status["taxa_por_grupo"] == "ok" and status["metrica_por_indicador"] == "ok"


def test_acha_o_cancelamento_por_contrato_em_primeiro(churn):
    *_, resultado = churn
    primeiro = resultado.insights[0]
    assert primeiro.tipo == "taxa_por_grupo" and primeiro.colunas == ["cancelou", "contrato"]
    assert "Mensal" in primeiro.texto and "No geral" in primeiro.texto


def test_acha_que_quem_cancela_tem_menos_meses_de_casa(churn):
    *_, resultado = churn
    achado = next(i for i in resultado.insights if i.tipo == "metrica_por_indicador")
    assert achado.colunas == ["cancelou", "meses_de_casa"]
    assert "menor quando cancelou = sim" in achado.titulo


def test_regra_de_cadastro_nao_vira_descoberta(churn):
    """Quem não tem telefone sempre está em "Sem telefone": isso é definição."""
    *_, resultado = churn
    assert not any({"tem_telefone", "linhas_telefone"} <= set(i.colunas) for i in resultado.insights)


def test_sem_efeito_plantado_nada_aparece():
    for semente in range(5):
        con = clientes(semente=semente, com_efeito=False)
        perfil = perfilar_tabela(con, "limpa")
        achados = insights.gerar(con, "limpa", perfil, {}, LOCAL).insights
        assert not [i.texto for i in achados if i.tipo in {"taxa_por_grupo", "metrica_por_indicador"}]


def test_grafico_da_taxa_de_cancelamento(churn):
    con, perfil, _ = churn
    lista = graficos.sugerir(con, "limpa", perfil, {})
    taxa = next(g for g in lista if g.agregacao == "taxa")
    assert (taxa.x, taxa.y, taxa.unidade) == ("contrato", "cancelou", "%")
    valores = {ponto["x"]: ponto["y"] for ponto in taxa.dados}
    assert valores["Mensal"] > 30 > 5 > valores["Bienal"]


def test_taxa_so_vale_para_sim_nao(churn):
    con, *_ = churn
    pedido = {"tipo": "barras", "x": "contrato", "y": "mensalidade", "agregacao": "taxa"}
    with pytest.raises(ValueError, match="sim/não"):
        graficos.calcular(con, "limpa", {}, LOCAL, pedido)


@pytest.mark.parametrize("valores, sim", [
    (["Yes", "No", "yes"], "x = sim"),
    (["0", "1", "1"], "x = 1"),
    (["SIM", None, None], "x = SIM"),
])
def test_jeitos_de_escrever_sim_e_nao(valores, sim):
    con = duckdb.connect()
    con.execute("CREATE TABLE t (x VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [[v] for v in valores])
    if valores[0] == "0":
        con.execute("CREATE OR REPLACE TABLE t AS SELECT CAST(x AS INT) AS x FROM t")
    leitura = expressao_sim(con, "t", "x")
    assert leitura.sim == sim
    assert con.execute(f"SELECT sum({leitura.expressao}) FROM t").fetchone()[0] == 2 - (valores[0] == "SIM")
