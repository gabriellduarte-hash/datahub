"""O que cada linha representa decide somar ou tirar a média.

Os casos imitam os arquivos reais medidos antes de implementar
(.build/experimento): músicas com ano de lançamento, itens de pedido, sessões
de app com hora e vídeos que reaparecem em vários dias de tendência.
"""

import duckdb
import pytest

from engine import grao, pipeline
from engine.limites import LOCAL
from engine.perfil import perfilar_tabela


def tabela(sql: str):
    con = duckdb.connect()
    con.execute("SELECT setseed(0.3)")
    con.execute(f"CREATE TABLE t AS {sql}")
    return con, perfilar_tabela(con, "t")


MUSICAS = """
    SELECT 'Música ' || i AS track_name, 1990 + (i % 34) AS released_year,
           80 + (i * 7) % 90 AS bpm, (i * 1237) % 900000 AS streams
    FROM range(600) r(i)"""
PEDIDOS = """
    SELECT DATE '2026-01-01' + CAST(floor(random() * 300) AS INT) AS data_pedido,
           ['Norte', 'Sul', 'Leste'][1 + i % 3] AS regiao, 1 + i % 4 AS quantidade, round(20 + random() * 300, 2) AS valor_total
    FROM range(2000) r(i)"""
SESSOES = """
    SELECT 's' || i AS session_id, TIMESTAMP '2026-01-01 00:00:00' + INTERVAL (i * 37) MINUTE AS started_at,
           ['ios', 'android', 'web'][1 + i % 3] AS device, 5 + (i * 13) % 3000 AS duration_sec
    FROM range(2000) r(i)"""
VIDEOS = """
    SELECT 'v' || (i % 300) AS video_id, DATE '2026-03-01' + CAST(i // 300 AS INT) AS trending_date,
           ['Música', 'Esporte', 'Jogos'][1 + (i % 300) % 3] AS categoria, 1000 * (i % 300) + 50 * (i // 300) AS views
    FROM range(1800) r(i)"""
CLIENTES = """
    SELECT i AS cliente_id, ['basico', 'pro'][1 + i % 2] AS plano, 18 + i % 50 AS idade, 49 + (i % 2) * 50 AS mensalidade
    FROM range(800) r(i)"""


@pytest.mark.parametrize("sql, esperado", [
    (MUSICAS, "entidade"),
    (PEDIDOS, "evento"),
    (SESSOES, "evento"),
    (VIDEOS, "foto"),
    (CLIENTES, "entidade"),
])
def test_detecta_o_que_cada_linha_representa(sql, esperado):
    con, perfil = tabela(sql)
    detectado = grao.detectar(con, "t", perfil)
    assert detectado.tipo == esperado, detectado.motivo
    assert detectado.motivo


def test_musicas_com_ano_passam_a_tirar_media_e_pedidos_continuam_somando():
    """O erro que motivou: 'Soma de bpm por ano' numa tabela de músicas."""
    con, _ = tabela(MUSICAS)
    musicas = pipeline.analisar(con, "t", {}, {}, None, None, LOCAL)
    assert musicas["grao"]["tipo"] == "entidade"
    agregacoes = {c["nome"]: c["agregacao"] for c in musicas["perfil_limpo"]["colunas"] if c["papel"] == "metrica"}
    assert agregacoes and set(agregacoes.values()) == {"media"}

    con, _ = tabela(PEDIDOS)
    pedidos = pipeline.analisar(con, "t", {}, {}, None, None, LOCAL)
    assert pedidos["grao"]["tipo"] == "evento"
    agregacoes = {c["nome"]: c["agregacao"] for c in pedidos["perfil_limpo"]["colunas"] if c["papel"] == "metrica"}
    assert agregacoes["valor_total"] == "soma"


def test_a_escolha_do_usuario_vale_mais_e_guarda_o_motivo_do_detectado():
    con, perfil = tabela(MUSICAS)
    escolha = grao.resolver(con, "t", perfil, "evento")
    assert escolha.tipo == "evento" and escolha.escolhido
    assert "uma pessoa ou coisa" in escolha.motivo
    assert not grao.resolver(con, "t", perfil, None).escolhido


def test_valor_da_nota_fiscal_e_dinheiro_que_soma():
    """'Nota' de escola não soma; 'Valor da Nota Fiscal' soma (export de faturamento)."""
    from engine.perfil import NOME_NAO_SOMAVEL
    from engine.sql import nome_simples

    assert not NOME_NAO_SOMAVEL.search(nome_simples("Valor da Nota Fiscal"))
    assert NOME_NAO_SOMAVEL.search(nome_simples("nota_final"))
