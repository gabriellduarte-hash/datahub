"""Bloco 3: sugestões, conversões e a receita reaplicada."""

import duckdb
import pytest

from engine.leitura import ler
from engine.normalizacao import _expr_numero, _variantes, aplicar, garantir_macros, sugerir
from engine.perfil import perfilar_tabela
from samples.gerar_vendas import gerar


@pytest.fixture(scope="module")
def sessao(tmp_path_factory):
    caminho, gabarito = gerar(tmp_path_factory.mktemp("vendas"), linhas=2000)
    con = duckdb.connect()
    ler(con, caminho)
    perfil = perfilar_tabela(con, "bruta")
    sugestoes = sugerir(con, "bruta", perfil)
    receita = [s.passo for s in sugestoes if s.marcada]
    relatorio = aplicar(con, receita)
    return con, perfil, sugestoes, receita, relatorio, gabarito


@pytest.mark.parametrize("texto, milhar_ponto, esperado", [
    ("R$ 1.234,56", True, 1234.56),
    ("1.234,56", True, 1234.56),
    ("1234.56", True, 1234.56),
    ("1,234.56", True, 1234.56),
    ("1.234.567,89", True, 1234567.89),
    ("89,90", True, 89.9),
    ("1.234", True, 1234),
    ("1.234", False, 1.234),
    ("1,234", False, 1234),
    ("-R$ 10,50", True, -10.5),
    ("R$ -10,50", True, -10.5),
    ("12,5%", True, 12.5),
    ("28 yrs", True, 28),
    ("1.7 hrs", False, 1.7),
    ("$528 ", True, 528),
])
def test_para_numero(texto, milhar_ponto, esperado):
    con = duckdb.connect()
    garantir_macros(con)
    valor = con.execute("SELECT TRY_CAST(para_numero(?, ?) AS DOUBLE)", [texto, milhar_ponto]).fetchone()[0]
    assert valor == pytest.approx(esperado)


def test_nenhum_passo_falha(sessao):
    *_, relatorio, _ = sessao
    assert [p.titulo for p in relatorio.passos if p.status != "ok"] == []


def test_remove_o_que_foi_plantado(sessao):
    con, perfil, _, _, relatorio, gabarito = sessao
    assert relatorio.linhas_antes == gabarito["linhas_total"]
    assert relatorio.linhas_depois == 2000
    nomes = [linha[0] for linha in con.execute("DESCRIBE limpa").fetchall()]
    assert "observacao" not in nomes and "pais" not in nomes and "column12" not in nomes
    assert "data_cancelamento" in nomes, "quase vazia não é removida sem o usuário pedir"
    assert "usou_cupom" in nomes, "indicador não é removido"


def test_datas_invalidas_viram_nulo_e_sao_contadas(sessao):
    con, _, _, _, relatorio, gabarito = sessao
    passo = next(p for p in relatorio.passos if p.titulo == "Converter para data: data_pedido")
    assert passo.perdidos == gabarito["sujeira"]["datas_invalidas"]
    tipo = dict((l[0], l[1]) for l in con.execute("DESCRIBE limpa").fetchall())["data_pedido"]
    assert tipo == "DATE"


def test_uf_padronizada_em_siglas(sessao):
    con, *_ = sessao
    valores = {linha[0] for linha in con.execute("SELECT DISTINCT uf FROM limpa").fetchall()}
    assert valores == {"SP", "RJ", "MG", "RS", "PR", "BA", "SC", "PE", "CE"}


def test_moeda_vira_numero_exato(sessao):
    con, *_ = sessao
    tipos = dict((l[0], l[1]) for l in con.execute("DESCRIBE limpa").fetchall())
    assert tipos["valor_unitario"] == "DECIMAL(18,2)"
    assert tipos["valor_total"] == "DECIMAL(18,2)"
    nulos = con.execute("SELECT count(*) FILTER (valor_unitario IS NULL) FROM limpa").fetchone()[0]
    assert nulos == 0


def test_valores_extremos_vem_desmarcados(sessao):
    _, _, sugestoes, *_ = sessao
    extremos = next(s for s in sugestoes if s.id == "extremos:quantidade")
    assert not extremos.marcada and extremos.afetados == 3
    assert "999" in extremos.motivo


def test_remover_extremos_tira_so_as_linhas_de_fora(sessao):
    con, _, sugestoes, receita, _, _ = sessao
    extremos = next(s for s in sugestoes if s.id == "extremos:quantidade")
    aplicar(con, [*receita, extremos.passo])
    assert con.execute("SELECT count(*), max(quantidade) FROM limpa").fetchone() == (1997, 10)
    aplicar(con, receita)


def test_quase_vazia_vem_desmarcada(sessao):
    _, _, sugestoes, *_ = sessao
    quase = next(s for s in sugestoes if s.id == "quase_vazia:data_cancelamento")
    assert not quase.marcada


def test_receita_reaplicada_da_o_mesmo_resultado(sessao):
    con, _, _, receita, relatorio, _ = sessao
    antes = con.execute("SELECT * FROM limpa ORDER BY ALL").fetchall()
    aplicar(con, receita)
    assert con.execute("SELECT * FROM limpa ORDER BY ALL").fetchall() == antes


def test_sql_gerado_reproduz_a_tabela(sessao):
    con, _, _, _, relatorio, _ = sessao
    com_sql = con.execute(relatorio.sql.rstrip(";") + " ORDER BY ALL").fetchall()
    assert com_sql == con.execute("SELECT * FROM limpa ORDER BY ALL").fetchall()


def test_passo_que_falha_nao_derruba_a_receita():
    con = duckdb.connect()
    con.execute("CREATE TABLE bruta AS SELECT ' a ' AS x")
    relatorio = aplicar(con, [
        {"op": "remover_colunas", "colunas": ["nao_existe"]},
        {"op": "aparar_espacos", "coluna": "x"},
    ])
    assert [p.status for p in relatorio.passos] == ["falhou", "ok"]
    assert con.execute("SELECT x FROM limpa").fetchone()[0] == "a"


def test_nome_de_coluna_malicioso_nao_vira_sql():
    con = duckdb.connect()
    nome = 'x"; DROP TABLE bruta; --'
    con.execute(f'CREATE TABLE bruta AS SELECT \' a \' AS "{nome.replace(chr(34), chr(34) * 2)}"')
    aplicar(con, [{"op": "aparar_espacos", "coluna": nome}])
    assert con.execute("SELECT count(*) FROM bruta").fetchone()[0] == 1


@pytest.mark.parametrize("texto, esperado", [
    ("$1,884 ", 1884),        # milhar americano numa coluna de inteiros: era convertido para 2
    ("1.884", 1884),
    ("28 yrs", 28),
    ("3.0", 3),
    ("2,5", None),            # não arredonda para 3 em silêncio: vira nulo e é contado
    ("1.5M", None),           # milhão não é unidade: não perde o M
    ("123AB", None),
])
def test_conversao_para_inteiro(texto, esperado):
    con = duckdb.connect()
    garantir_macros(con)
    expressao = _expr_numero({"coluna": "x", "tipo": "inteiro", "milhar_ponto": True})
    assert con.execute(f"SELECT {expressao} FROM (SELECT ? AS x)", [texto]).fetchone()[0] == esperado


def mapa_de(valores: list[str]) -> dict[str, str]:
    con = duckdb.connect()
    con.execute("CREATE TABLE t (v VARCHAR)")
    con.executemany("INSERT INTO t VALUES (?)", [[v] for v in valores])
    perfil = perfilar_tabela(con, "t")
    return _variantes(con, "t", perfil.colunas[0])[0]


def test_padroniza_genero_abreviado():
    mapa = mapa_de(["Male"] * 5 + ["Female"] * 5 + ["M", "F", "MALE", "female", "Other", "other"])
    assert mapa == {"M": "Male", "MALE": "Male", "F": "Female", "female": "Female", "other": "Other"}


def test_espaco_e_pontuacao_nao_separam_valores():
    mapa = mapa_de(["ChatGPT"] * 5 + ["Chat GPT", "Master's", "Master's", "Masters", "Gemini", "gemini"])
    assert mapa == {"Chat GPT": "ChatGPT", "Masters": "Master's", "gemini": "Gemini"}


def test_letra_solta_fora_de_genero_nao_vira_palavra():
    assert mapa_de(["P", "M", "G", "GG", "M"]) == {}
    assert mapa_de(["A", "B", "AB", "O", "Other", "A"]) == {}
