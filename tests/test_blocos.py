"""Blocos 4 e 5: as respostas plantadas aparecem e o ruído não vira descoberta."""

import duckdb
import pytest

from engine import graficos, insights
from engine.leitura import ler
from engine.limites import LOCAL
from engine.normalizacao import aplicar, colunas_derivadas, sugerir, unidades_da_receita
from engine.perfil import perfilar_tabela
from samples.gerar_vendas import gerar


@pytest.fixture(scope="module")
def vendas(tmp_path_factory):
    caminho, gabarito = gerar(tmp_path_factory.mktemp("vendas"), linhas=20000)
    con = duckdb.connect()
    ler(con, caminho)
    perfil_bruto = perfilar_tabela(con, "bruta")
    receita = [s.passo for s in sugerir(con, "bruta", perfil_bruto) if s.marcada]
    relatorio = aplicar(con, receita)
    perfil = perfilar_tabela(con, "limpa")
    unidades = unidades_da_receita(receita)
    resultado = insights.gerar(con, "limpa", perfil, unidades, LOCAL, relatorio, colunas_derivadas(receita))
    return con, perfil, unidades, resultado, gabarito


def dados_de_ruido(linhas: int, semente: int, com_data: bool = True):
    """Dados sem nenhum efeito: nada aqui merece ser chamado de descoberta.

    Sem data, as linhas viram clientes (métricas por média); as colunas de
    sim/não testam os analisadores de taxa.
    """
    con = duckdb.connect()
    con.execute("SET threads = 1")
    con.execute(f"SELECT setseed({semente / 20})")
    data = "DATE '2023-01-01' + CAST(floor(random() * 730) AS INT) AS data," if com_data else ""
    con.execute(f"""
        CREATE TABLE limpa AS
        SELECT i AS pedido_id, {data}
               'grupo_' || CAST(floor(random() * 6) AS INT) AS categoria,
               'regiao_' || CAST(floor(random() * 4) AS INT) AS regiao,
               100 + 15 * sqrt(-2 * ln(random())) * cos(2 * pi() * random()) AS valor,
               50 + 10 * sqrt(-2 * ln(random())) * cos(2 * pi() * random()) AS custo,
               random() < 0.3 AS cancelou,
               CAST(random() < 0.1 AS INT) AS reclamou
        FROM range({linhas}) t(i)
    """)
    return con


def analisar_vendas(vendas_csv, com_extremos):
    con = duckdb.connect()
    ler(con, vendas_csv)
    perfil_bruto = perfilar_tabela(con, "bruta")
    sugestoes = sugerir(con, "bruta", perfil_bruto)
    receita = [s.passo for s in sugestoes if s.marcada or (com_extremos and s.grupo == "linhas")]
    relatorio = aplicar(con, receita)
    perfil = perfilar_tabela(con, "limpa")
    unidades = unidades_da_receita(receita)
    return insights.gerar(con, "limpa", perfil, unidades, LOCAL, relatorio, colunas_derivadas(receita))


def test_remover_os_extremos_revela_a_tendencia(tmp_path):
    caminho, _ = gerar(tmp_path, linhas=5000)
    sem = analisar_vendas(caminho, com_extremos=False)
    com = analisar_vendas(caminho, com_extremos=True)
    assert not any(i.tipo == "tendencia" and "valor_total" in i.colunas for i in sem.insights)
    assert any(i.tipo == "tendencia" and "valor_total" in i.colunas and "cresce" in i.texto for i in com.insights)


def test_pico_causado_por_uma_linha_e_explicado(tmp_path):
    caminho, _ = gerar(tmp_path, linhas=5000)
    achados = [i for i in analisar_vendas(caminho, com_extremos=False).insights if i.tipo == "pico_temporal"]
    assert achados and any("Uma única linha" in i.texto for i in achados)
    assert len({i.titulo for i in achados}) == len(achados), "cada período aparece uma vez"


def test_todos_os_analisadores_rodam(vendas):
    *_, resultado, _ = vendas
    assert [e.analisador for e in resultado.execucoes if e.status == "falhou"] == []


def test_acha_o_mes_de_destaque_e_quem_puxou(vendas):
    *_, resultado, gabarito = vendas
    achado = next(i for i in resultado.insights if i.tipo == "variacao_periodo" and "valor_total" in i.titulo)
    assert "agosto/2025" in achado.texto
    assert "subiu" in achado.texto
    assert gabarito["efeitos"]["categoria_destaque"] in achado.texto


def test_avisa_que_setembro_ficou_de_fora(vendas):
    *_, resultado, _ = vendas
    achado = next(i for i in resultado.insights if i.tipo == "variacao_periodo" and "valor_total" in i.titulo)
    assert any("Setembro/2025" in r and "incompleto" in r for r in achado.ressalvas)


def test_acha_o_crescimento(vendas):
    *_, resultado, _ = vendas
    assert any(i.tipo == "tendencia" and "cresce" in i.texto for i in resultado.insights)


def test_acha_os_outliers_de_quantidade(vendas):
    *_, resultado, _ = vendas
    achado = next(i for i in resultado.insights if i.tipo == "valores_extremos" and "quantidade" in i.colunas)
    assert "999" in achado.texto


def test_acha_a_relacao_entre_preco_e_quantidade(vendas):
    *_, resultado, _ = vendas
    achado = next(i for i in resultado.insights if i.tipo == "correlacao")
    assert {"quantidade", "valor_unitario"} == set(achado.colunas)
    assert "cair" in achado.texto


def test_nao_correlaciona_coluna_com_a_propria_origem(vendas):
    *_, resultado, _ = vendas
    for insight in resultado.insights:
        if insight.tipo == "correlacao":
            assert "valor_total" not in insight.colunas


def test_valores_formatados_em_reais(vendas):
    *_, resultado, _ = vendas
    achado = next(i for i in resultado.insights if i.tipo == "variacao_periodo" and "valor_total" in i.titulo)
    assert "R$ " in achado.texto


def test_ruido_quase_nunca_vira_descoberta():
    achados = []
    for com_data in (True, False):
        for linhas in (300, 2000, 20000):
            for semente in range(8):
                con = dados_de_ruido(linhas, semente, com_data)
                perfil = perfilar_tabela(con, "limpa")
                resultado = insights.gerar(con, "limpa", perfil, {}, LOCAL)
                assert not [e for e in resultado.execucoes if e.status == "falhou"]
                achados += [i.texto for i in resultado.insights]
    # 48 conjuntos de puro ruído, com 10 analisadores em cada um.
    assert len(achados) <= 2, achados


def test_pico_no_meio_da_serie_e_encontrado():
    con = dados_de_ruido(20000, 1)
    con.execute("UPDATE limpa SET valor = valor * 3 WHERE data BETWEEN DATE '2023-09-01' AND DATE '2023-09-30'")
    perfil = perfilar_tabela(con, "limpa")
    resultado = insights.gerar(con, "limpa", perfil, {}, LOCAL)
    assert any(i.tipo == "pico_temporal" and "setembro/2023" in i.texto for i in resultado.insights)


def test_poucas_linhas_nao_geram_estatistica():
    con = dados_de_ruido(20, 1)
    perfil = perfilar_tabela(con, "limpa")
    resultado = insights.gerar(con, "limpa", perfil, {}, LOCAL)
    assert resultado.insights == []


def test_analisador_que_quebra_nao_derruba_os_outros(vendas):
    con, perfil, unidades, *_ = vendas

    def quebra(ctx):
        raise RuntimeError("falha de teste")
        yield

    def funciona(ctx):
        yield insights.Insight("teste", "ok", "ok", 1)

    resultado = insights.gerar(con, "limpa", perfil, unidades, catalogo=[("quebra", quebra, None), ("funciona", funciona, None)])
    assert [e.status for e in resultado.execucoes] == ["falhou", "ok"]
    assert len(resultado.insights) == 1


def test_graficos_sugeridos(vendas):
    con, perfil, unidades, *_ = vendas
    lista = graficos.sugerir(con, "limpa", perfil, unidades)
    assert {"linha", "barras", "histograma", "dispersao"} <= {g.tipo for g in lista}
    linha = next(g for g in lista if g.tipo == "linha" and g.y == "valor_total")
    assert linha.grao == "mes" and linha.x == "data_pedido"
    assert linha.dados[-1]["incompleto"] and "setembro/2025" in linha.aviso
    assert not linha.dados[-2]["incompleto"]


def test_dispersao_nunca_compara_coluna_com_ela_mesma(vendas):
    con, perfil, unidades, *_ = vendas
    derivadas = {"valor_total": {"quantidade", "valor_unitario"}}
    lista = graficos.sugerir(con, "limpa", perfil, unidades, derivadas=derivadas)
    dispersao = next(g for g in lista if g.tipo == "dispersao")
    assert dispersao.x != dispersao.y
    assert {dispersao.x, dispersao.y} == {"quantidade", "valor_unitario"}


def test_barras_agrupam_o_resto_em_outros(vendas):
    con, _, unidades, *_ = vendas
    pedido = {"tipo": "barras", "x": "produto", "y": "valor_total", "agregacao": "soma"}
    grafico = graficos.calcular(con, "limpa", unidades, LOCAL, pedido)
    assert len(grafico.dados) == LOCAL.max_categorias_grafico + 1
    assert grafico.dados[-1]["x"].startswith("Outros")


def test_grafico_nunca_entrega_muitos_pontos(vendas):
    con, _, unidades, *_ = vendas
    pedido = {"tipo": "dispersao", "x": "quantidade", "y": "valor_total"}
    grafico = graficos.calcular(con, "limpa", unidades, LOCAL, pedido)
    assert len(grafico.dados) <= graficos.PONTOS_DISPERSAO
    assert "Amostra" in grafico.aviso
    assert max(ponto["x"] for ponto in grafico.dados) < 999, "o extremo não pode achatar o gráfico"
