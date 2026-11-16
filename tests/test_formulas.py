"""Fase 3: fórmulas de coluna em português e o conversor de moedas."""

from datetime import date

import duckdb
import pytest

from engine import area as areas
from engine import cambio, formulas
from engine.formulas import FormulaInvalida


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute("""
        CREATE TABLE t AS SELECT * FROM (VALUES
            (1, 'Mensal',  1, CAST(29.85 AS DOUBLE), DATE '2024-01-15', 'SP', 'Ana Souza'),
            (2, 'Anual',  34, 56.95, DATE '2024-03-02', 'RJ', 'Bia Lima'),
            (3, 'Mensal',  2, 53.85, DATE '2024-03-30', 'SP', NULL),
            (4, 'Bienal', 45, 42.30, NULL,              'MG', 'Caio')
        ) v(__linha, contrato, tenure, mensalidade, inicio, uf, "nome completo")
    """)
    return con


def valores(con, formula, nome="nova"):
    sql = formulas.compilar_passo({"op": "formula", "nome": nome, "formula": formula},
                                  [(d[0], d[1]) for d in con.execute("DESCRIBE t").fetchall()], "t")
    return [linha[-1] for linha in con.execute(f"SELECT * FROM ({sql}) ORDER BY __linha").fetchall()]


@pytest.mark.parametrize("formula, esperado", [
    ("= mensalidade * 12", [358.2, 683.4, 646.2, 507.6]),
    ("= tenure + 1", [2, 35, 3, 46]),
    ("=ARRED(mensalidade * 2; 0)", [60.0, 114.0, 108.0, 85.0]),
    ("=ROUND(mensalidade * 2, 0)", [60.0, 114.0, 108.0, 85.0]),
    ('=SE(tenure < 12; "novo"; "antigo")', ["novo", "antigo", "novo", "antigo"]),
    ('=IF(tenure < 12, "novo", "antigo")', ["novo", "antigo", "novo", "antigo"]),
    ("=mensalidade * 1,5", [44.775, 85.425, 80.775, 63.45]),
    ("=ARRED(mensalidade * 1,5; 1)", [44.8, 85.4, 80.8, 63.5]),
    ('=contrato & " - " & uf', ["Mensal - SP", "Anual - RJ", "Mensal - SP", "Bienal - MG"]),
    ("=SOMA(tenure)", [82, 82, 82, 82]),
    ("=SOMA(tenure; 10; 1)", [12, 45, 13, 56]),
    ("=MÉDIA(tenure)", [20.5] * 4),
    ('=CONT.SE(uf; "SP")', [2, 2, 2, 2]),
    ('=CONT.SE(tenure; ">10")', [2, 2, 2, 2]),
    ('=SOMASE(uf; "SP"; mensalidade)', [83.7] * 4),
    ("=tenure / SOMA(tenure)", [1 / 82, 34 / 82, 2 / 82, 45 / 82]),
    ("=ANO(inicio)", [2024, 2024, 2024, None]),
    ("=MES(inicio)", [1, 3, 3, None]),
    ("=inicio + 30", [date(2024, 2, 14), date(2024, 4, 1), date(2024, 4, 29), None]),
    ('=NUM.CARACT([nome completo])', [9, 8, None, 4]),
    ('=MAIUSCULA(ESQUERDA(contrato; 3))', ["MEN", "ANU", "MEN", "BIE"]),
    ('=SEVAZIO([nome completo]; "sem nome")', ["Ana Souza", "Bia Lima", "sem nome", "Caio"]),
    ('=E(tenure > 1; uf = "SP")', [False, False, True, False]),
    ("=TENURE * 2", [2, 68, 4, 90]),
    ("=tenure / 0", [None, None, None, None]),
])
def test_formula(con, formula, esperado):
    for obtido, certo in zip(valores(con, formula), esperado, strict=True):
        if isinstance(certo, float):
            assert obtido == pytest.approx(certo)
        else:
            assert obtido == certo


@pytest.mark.parametrize("formula, mensagem", [
    ("", "vazia"),
    ("= tenur * 2", "Você quis dizer tenure"),
    ("= nome completo", "colchetes"),
    ("= SOMAR(tenure)", "Você quis dizer SOMA"),
    ("= SE(tenure > 1)", "SE recebe de 2 a 3 argumentos"),
    ("= RAIZ(tenure * 1,5)", "decimal com vírgula"),
    ("= (tenure * 2", "Faltou fechar"),
    ("= tenure * 2)", "a mais"),
    ("= tenure * ", "terminou"),
    ("= tenure # 2", "Não entendi"),
])
def test_erros_explicam_o_que_corrigir(con, formula, mensagem):
    with pytest.raises(FormulaInvalida, match=mensagem):
        valores(con, formula)


def test_texto_do_usuario_nunca_vira_sql(con):
    resultado = valores(con, "=\"x'); DROP TABLE t; --\"")
    assert resultado == ["x'); DROP TABLE t; --"] * 4
    assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 4
    with pytest.raises(FormulaInvalida):
        valores(con, "=tenure; DROP TABLE t")


def test_nome_da_coluna_nova(con):
    with pytest.raises(FormulaInvalida, match="Já existe"):
        valores(con, "=1", nome="Tenure")
    sql = formulas.compilar_passo({"op": "formula", "nome": "tenure", "formula": "=tenure * 10", "substituir": True},
                                  [(d[0], d[1]) for d in con.execute("DESCRIBE t").fetchall()], "t")
    assert [l[2] for l in con.execute(f"SELECT * FROM ({sql}) ORDER BY __linha").fetchall()] == [10, 340, 20, 450]


def test_previa_mostra_valores_e_vazios(con):
    previa = formulas.previa(con, "t", {"nome": "anos", "formula": "=ANO(inicio)"})
    assert previa["vazios"] == 1 and previa["linhas"] == 4
    assert [p["valor"] for p in previa["previa"]] == [2024, 2024, 2024, None]
    with pytest.raises(FormulaInvalida, match="tipos não combinam|recusou"):
        formulas.previa(con, "t", {"nome": "x", "formula": "=uf > DATA(2024; 1; 1)"})


# ----- moedas -----

# Linhas reais do ds_salaries: o autor converteu pela média anual do câmbio.
SALARIOS = [
    (2021, "BRL", 69600, 12901),
    (2021, "CAD", 68000, 54238),
    (2020, "EUR", 14000, 15966),
    (2021, "EUR", 8760, 10354),
    (2021, "GBP", 37456, 51519),
    (2021, "INR", 400000, 5409),
    (2020, "JPY", 4450000, 41689),
]


@pytest.fixture(scope="module")
def com_cambio():
    con = duckdb.connect()
    cambio.garantir(con)
    valores_sql = ", ".join(f"({i}, {ano}, '{moeda}', {salario}, {usd})"
                            for i, (ano, moeda, salario, usd) in enumerate(SALARIOS, 1))
    con.execute(f"CREATE TABLE s AS SELECT * FROM (VALUES {valores_sql}) v(__linha, ano, moeda, salario, usd)")
    return con


def test_converter_reproduz_o_salary_in_usd(com_cambio):
    sql = formulas.compilar_passo(
        {"op": "formula", "nome": "em_dolar", "formula": '=CONVERTER(salario; moeda; "USD"; ano)'},
        [(d[0], d[1]) for d in com_cambio.execute("DESCRIBE s").fetchall()], "s")
    for linha in com_cambio.execute(f"SELECT usd, em_dolar FROM ({sql}) ORDER BY __linha").fetchall():
        assert linha[1] == pytest.approx(linha[0], rel=0.002)


def test_converter_pelo_dia_e_pela_cotacao_mais_recente(com_cambio):
    sabado = com_cambio.execute(
        "SELECT (SELECT __por_euro FROM __cambio WHERE __moeda = 'USD' AND __data = DATE '2024-03-01')"
    ).fetchone()[0]
    sql = cambio.expressao("100.0", "'EUR'", "'USD'", "DATE '2024-03-02'", "dia")
    assert com_cambio.execute(f"SELECT {sql}").fetchone()[0] == pytest.approx(100 * sabado)
    recente = com_cambio.execute(f"SELECT {cambio.expressao('1.0', chr(39) + 'USD' + chr(39), chr(39) + 'BRL' + chr(39), None, None)}").fetchone()[0]
    assert 3 < recente < 10


def test_moeda_sem_cotacao_vira_vazio(com_cambio):
    sql = cambio.expressao("100.0", "'CLP'", "'USD'", "2021", "ano")
    assert com_cambio.execute(f"SELECT {sql}").fetchone()[0] is None


def test_converter_pede_data_ou_ano(com_cambio):
    with pytest.raises(FormulaInvalida, match="data .* ou um ano"):
        formulas.traduzir('=CONVERTER(salario; moeda; "USD"; moeda)', {"salario": "BIGINT", "moeda": "VARCHAR"})


def test_coluna_convertida_para_real_fica_em_reais(tmp_path):
    from engine.normalizacao import unidades_da_receita
    receita = [{"op": "formula", "nome": "em_reais", "formula": '=CONVERTER(valor; moeda; "BRL")'}]
    assert unidades_da_receita(receita) == {"em_reais": "BRL"}


def test_formula_como_passo_da_sandbox(tmp_path):
    from tests.test_manipulacao import nova_area

    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "formula", "nome": "fase",
                                                     "formula": '=SE(meses < 12; "novo"; "antigo")'})
    contagem = dict(con.execute('SELECT fase, count(*) FROM "clientes" GROUP BY 1').fetchall())
    novos = con.execute('SELECT count(*) FROM "clientes" WHERE meses < 12').fetchone()[0]
    assert contagem == {"novo": novos, "antigo": 200 - novos}
    areas.desfazer(con, area, item["id"])
    assert "fase" not in [l[0] for l in con.execute('DESCRIBE "clientes"').fetchall()]
