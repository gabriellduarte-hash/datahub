"""Fases 4 e 5: SQL só de consulta, várias tabelas, relações e a base única de análise."""

import duckdb
import pytest

from engine import area as areas
from engine import consulta, relacional, relogio
from engine.consulta import ConsultaRecusada
from engine.relacional import JuncaoRecusada


@pytest.fixture
def loja(tmp_path):
    """100 clientes com limite de 500 ou 1.500 (100 mil no total), e 300 pedidos: 3 por cliente."""
    clientes = tmp_path / "clientes.csv"
    clientes.write_text("cliente_id;nome;limite;regiao\n" + "".join(
        f"{i};Cliente {i};{500 + (i % 2) * 1000};{['Norte', 'Sul'][i % 2]}\n" for i in range(1, 101)), encoding="utf-8")
    pedidos = tmp_path / "pedidos.csv"
    pedidos.write_text("pedido_id;cliente_id;valor;canal\n" + "".join(
        f"{i};{(i - 1) % 100 + 1};{50 + i % 7}0;{['loja', 'site'][i % 2]}\n" for i in range(1, 301)), encoding="utf-8")
    con = duckdb.connect()
    area = areas.nova("teste")
    for caminho in (clientes, pedidos):
        item = areas.adicionar_arquivo(con, area, caminho, caminho.name)
        areas.normalizar(con, area, item["id"], [s["passo"] for s in item["sugestoes"] if s["marcada"]], {})
    return con, area


def ids(area):
    return {t["nome"]: t["id"] for t in area["tabelas"]}


# ----- SQL -----

@pytest.mark.parametrize("sql", ["SELECT 1", "with a as (select 1 x) select * from a", "  SELECT 2 ;  "])
def test_consulta_aceita(sql):
    assert consulta.validar(sql)


@pytest.mark.parametrize("sql, motivo", [
    ("", "Escreva"),
    ("DROP TABLE clientes", "DROP"),
    ("SELECT 1; DROP TABLE clientes", "um comando por vez"),
    ("COPY (SELECT 1) TO 'x.csv'", "COPY"),
    ("ATTACH 'outro.db'", "ATTACH"),
    ("SET enable_external_access = true", "SET"),
    ("INSTALL httpfs", "LOAD"),
    ("CREATE TABLE x AS SELECT 1", "CREATE"),
    ("SELEC 1", "Não entendi"),
])
def test_consulta_recusa(sql, motivo):
    with pytest.raises(ConsultaRecusada, match=motivo):
        consulta.validar(sql)


def test_consulta_roda_pagina_e_reaproveita(loja):
    con, area = loja
    sql = "SELECT regiao, count(*) AS n FROM clientes GROUP BY 1 ORDER BY 1"
    primeira = consulta.executar(con, sql, "v1", None, 0, 1)
    assert primeira["total"] == 2 and primeira["linhas"] == [["Norte", 50]]
    segunda = consulta.executar(con, sql, "v1", primeira["chave"], 1, 1)
    assert segunda["reaproveitada"] and segunda["linhas"] == [["Sul", 50]]
    nova = consulta.executar(con, sql, "v2", primeira["chave"], 0, 10)
    assert not nova["reaproveitada"], "mudou uma tabela: a consulta roda de novo"


@pytest.mark.skipif(not relogio.TEM_THREADS, reason="sem threads (navegador), não há relógio para interromper")
def test_consulta_lenta_e_interrompida(loja):
    con, _ = loja
    with pytest.raises(ConsultaRecusada, match="interrompida"):
        consulta.executar(con, "SELECT count(*) FROM range(1000000000000) a", "v", None, tempo_limite=1)


def test_consulta_nao_le_arquivos(tmp_path):
    con = duckdb.connect(config={"enable_external_access": "false"})
    with pytest.raises(ConsultaRecusada, match="não leem arquivos"):
        consulta.executar(con, "SELECT * FROM read_csv('/etc/passwd')", "v", None)


def test_tabela_inexistente_lista_as_que_existem(loja):
    con, _ = loja
    with pytest.raises(ConsultaRecusada, match="clientes, pedidos"):
        consulta.executar(con, "SELECT * FROM vendas", "v", None)


def test_salvar_consulta_como_tabela(loja):
    con, area = loja
    sql = "SELECT canal, sum(valor) AS total FROM pedidos GROUP BY 1"
    nova = areas.adicionar_tabela_pronta(con, area, consulta.selecao_salvavel(sql), "por_canal",
                                         "Consulta: por canal", "consulta", definicao={"sql": sql})
    assert nova["nome"] == "por_canal" and nova["arquivo"]["linhas"] == 2
    assert con.execute(f'SELECT count(*) FROM "{areas.nomes(nova["id"])[0]}" WHERE __linha IN (1, 2)').fetchone()[0] == 2
    assert nova["sugestoes"] is not None and nova["perfil"]["linhas"] == 2


# ----- relações e visão unificada -----

def test_sugere_a_relacao_de_pedidos_para_clientes(loja):
    con, area = loja
    t = ids(area)
    sugestoes = relacional.sugerir_relacoes(con, area)
    assert {"de": {"tabela": t["pedidos"], "coluna": "cliente_id"},
            "para": {"tabela": t["clientes"], "coluna": "cliente_id"}, "cobertura": 1.0} in sugestoes
    assert not any(s["para"]["tabela"] == t["pedidos"] and s["para"]["coluna"] == "cliente_id" for s in sugestoes)


def test_relacao_para_chave_repetida_e_recusada(loja):
    con, area = loja
    t = ids(area)
    with pytest.raises(JuncaoRecusada, match="se repete"):
        relacional.criar_relacao(con, area, {"tabela": t["clientes"], "coluna": "cliente_id"},
                                 {"tabela": t["pedidos"], "coluna": "cliente_id"})


def relacao_padrao(con, area):
    t = ids(area)
    return relacional.criar_relacao(con, area, {"tabela": t["pedidos"], "coluna": "cliente_id"},
                                    {"tabela": t["clientes"], "coluna": "cliente_id"})


def test_juntar_pelo_lado_que_repete_e_recusado(loja):
    con, area = loja
    relacao = relacao_padrao(con, area)
    with pytest.raises(JuncaoRecusada, match="repetiria"):
        relacional.criar_visao(con, area, "clientes_e_pedidos", ids(area)["clientes"],
                               [{"relacao": relacao["id"], "modo": "colunas"}])


def test_resumo_mantem_o_limite_certo(loja):
    """O teste do plano: juntando pedidos, a soma do limite não pode virar 300 mil."""
    con, area = loja
    relacao = relacao_padrao(con, area)
    visao = relacional.criar_visao(con, area, "clientes_com_pedidos", ids(area)["clientes"],
                                   [{"relacao": relacao["id"], "modo": "resumo"}])
    soma, linhas, pedidos = con.execute(
        f'SELECT sum(limite), count(*), sum(pedidos_linhas) FROM "{visao["nome"]}"').fetchone()
    assert (soma, linhas, pedidos) == (100_000, 100, 300)
    assert "pedidos_soma_valor" in visao["origens"]


def test_pedidos_ganham_as_colunas_do_cliente(loja):
    con, area = loja
    relacao = relacao_padrao(con, area)
    visao = relacional.criar_visao(con, area, "pedidos_completos", ids(area)["pedidos"],
                                   [{"relacao": relacao["id"]}])
    assert visao["linhas"] == 300
    regioes = dict(con.execute(f'SELECT clientes_regiao, count(*) FROM "{visao["nome"]}" GROUP BY 1').fetchall())
    assert regioes == {"Norte": 150, "Sul": 150}


def test_analise_na_visao_tira_media_das_colunas_emprestadas(loja):
    con, area = loja
    relacao = relacao_padrao(con, area)
    visao = relacional.criar_visao(con, area, "pedidos_completos", ids(area)["pedidos"],
                                   [{"relacao": relacao["id"]}])
    areas.escolher_base(area, {"tipo": "visao", "id": visao["id"]})
    analise = areas.analisar(con, area)
    papeis = {c["nome"]: c for c in analise["perfil_limpo"]["colunas"]}
    assert papeis["clientes_limite"]["agregacao"] == "media", "o limite é do cliente: somar por pedido repete"
    assert analise["rotulo"] == "pedidos_completos" and analise["linhas"] == 300
    assert {b["bloco"]: b["status"] for b in analise["blocos"]}["insights"] == "ok"


def test_visao_acompanha_edicoes_nas_tabelas(loja):
    con, area = loja
    relacao = relacao_padrao(con, area)
    visao = relacional.criar_visao(con, area, "pedidos_completos", ids(area)["pedidos"],
                                   [{"relacao": relacao["id"]}])
    areas.escolher_base(area, {"tipo": "visao", "id": visao["id"]})
    areas.analisar(con, area)
    areas.acrescentar_passo(con, area, ids(area)["clientes"], {
        "op": "editar_celulas", "edicoes": [{"linha": 1, "coluna": "regiao", "valor": "Centro"}]})
    assert area["analise"]["desatualizada"]
    assert con.execute(f'SELECT count(*) FROM "{visao["nome"]}" WHERE clientes_regiao = \'Centro\'').fetchone()[0] == 3


def test_combinar_tabelas_parecidas(tmp_path):
    con = duckdb.connect()
    area = areas.nova("teste")
    for mes, linhas in (("janeiro", 3), ("fevereiro", 4)):
        caminho = tmp_path / f"vendas_{mes}.csv"
        extra = ";cupom" if mes == "fevereiro" else ""
        corpo = "".join(f"{i};{i * 10}{';X' if extra else ''}\n" for i in range(linhas))
        caminho.write_text(f"produto;valor{extra}\n{corpo}", encoding="utf-8")
        areas.adicionar_arquivo(con, area, caminho, caminho.name)
    nova = relacional.combinar(con, area, [t["id"] for t in area["tabelas"]], "vendas")
    assert nova["arquivo"]["linhas"] == 7
    origens = dict(con.execute(f'SELECT origem, count(*) FROM "{nova["nome"]}" GROUP BY 1').fetchall())
    assert origens == {"vendas_janeiro": 3, "vendas_fevereiro": 4}
    assert "cupom" in nova["arquivo"]["avisos"][-1]


def test_remover_tabela_leva_relacoes_visoes_e_base(loja):
    con, area = loja
    relacao = relacao_padrao(con, area)
    visao = relacional.criar_visao(con, area, "pedidos_completos", ids(area)["pedidos"], [{"relacao": relacao["id"]}])
    areas.escolher_base(area, {"tipo": "visao", "id": visao["id"]})
    areas.remover_tabela(con, area, ids(area)["clientes"])
    assert area["relacoes"] == [] and area["visoes"] == [] and area["base"] is None
    nomes = [linha[0] for linha in con.execute("SELECT table_name FROM information_schema.tables").fetchall()]
    assert "clientes" not in nomes and "pedidos_completos" not in nomes


def test_esquema_desenha_tabelas_e_chaves(loja):
    con, area = loja
    relacao_padrao(con, area)
    desenho = relacional.esquema(con, area)
    clientes = next(t for t in desenho["tabelas"] if t["nome"] == "clientes")
    assert clientes["linhas"] == 100 and clientes["normalizada"]
    assert next(c for c in clientes["colunas"] if c["nome"] == "cliente_id")["chave"]
    assert desenho["sugestoes"] == [], "a relação sugerida já foi criada"


def test_colunas_da_mesma_tabela_emprestada_nao_viram_insight(loja):
    """Região e limite são do cadastro do cliente: comparar os dois numa visão de pedidos não é descoberta."""
    con, area = loja
    relacao = relacao_padrao(con, area)
    visao = relacional.criar_visao(con, area, "pedidos_completos", ids(area)["pedidos"], [{"relacao": relacao["id"]}])
    areas.escolher_base(area, {"tipo": "visao", "id": visao["id"]})
    analise = areas.analisar(con, area)
    emprestadas = {"clientes_nome", "clientes_limite", "clientes_regiao"}
    assert not [i["titulo"] for i in analise["insights"]["insights"] if set(i["colunas"]) <= emprestadas and len(i["colunas"]) > 1]
