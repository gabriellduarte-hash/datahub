"""Bloco 1: arquivos reais chegam de vários jeitos."""

from datetime import date

import duckdb
import pytest
from openpyxl import Workbook

from engine.consultas import grao_automatico, serie
from engine.leitura import ArquivoInvalido, ler
from engine.normalizacao import aplicar, sugerir
from engine.perfil import perfilar_tabela
from engine.sql import colunas_de


def leia(caminho, **opcoes):
    con = duckdb.connect()
    return con, ler(con, caminho, **opcoes)


def test_csv_em_latin1(tmp_path):
    arquivo = tmp_path / "excel.csv"
    arquivo.write_text("nome;cidade\nJoão;São Paulo\nÁgata;Maceió\n", encoding="cp1252")
    con, leitura = leia(arquivo)
    assert leitura.codificacao == "latin-1"
    assert con.execute("SELECT cidade FROM bruta ORDER BY cidade").fetchall() == [("Maceió",), ("São Paulo",)]


def test_csv_com_virgula_e_texto_entre_aspas(tmp_path):
    arquivo = tmp_path / "virgula.csv"
    arquivo.write_text('produto,preco\n"Mesa, grande",10\nCadeira,20\n', encoding="utf-8")
    con, leitura = leia(arquivo)
    assert leitura.separador == ","
    assert con.execute("SELECT produto FROM bruta ORDER BY 1").fetchall() == [("Cadeira",), ("Mesa, grande",)]


def test_tsv(tmp_path):
    arquivo = tmp_path / "dados.tsv"
    arquivo.write_text("a\tb\n1\t2\n3\t4\n", encoding="utf-8")
    _, leitura = leia(arquivo)
    assert leitura.colunas == 2 and leitura.linhas == 2


def test_linhas_com_colunas_a_menos_nao_quebram(tmp_path):
    arquivo = tmp_path / "torto.csv"
    arquivo.write_text("a;b;c\n1;2;3\n4;5\n7;8;9\n", encoding="utf-8")
    _, leitura = leia(arquivo)
    assert leitura.linhas == 3


def test_valor_estranho_no_fim_nao_quebra_a_leitura(tmp_path):
    arquivo = tmp_path / "grande.csv"
    corpo = "".join(f"{i};2024-01-01\n" for i in range(30000))
    arquivo.write_text("id;data\n" + corpo + "texto;nao e data\n", encoding="utf-8")
    con, leitura = leia(arquivo)
    assert leitura.linhas == 30001
    assert con.execute("SELECT id FROM bruta WHERE id = 'texto'").fetchone() == ("texto",)


def test_aspas_que_so_aparecem_no_fim_nao_perdem_linhas(tmp_path):
    # Caso real: uma base de COVID com "Korea, South" na linha 75.890.
    arquivo = tmp_path / "paises.csv"
    corpo = "".join(f"2020-01-01,Brasil,{i}\n" for i in range(30000))
    arquivo.write_text("data,pais,casos\n" + corpo + '2020-01-01,"Korea, South",1\n', encoding="utf-8")
    con, leitura = leia(arquivo)
    assert leitura.linhas == 30001 and leitura.colunas == 3
    assert con.execute("SELECT pais FROM bruta WHERE casos = '1' AND pais LIKE 'Korea%'").fetchone() == ("Korea, South",)


def test_excel_com_titulo_acima_do_cabecalho(tmp_path):
    livro = Workbook()
    aba = livro.active
    aba.append(["Relatório de vendas 2024"])
    aba.append([])
    aba.append(["mes", "receita", "custo"])
    aba.append(["janeiro", 100, 50])
    arquivo = tmp_path / "relatorio.xlsx"
    livro.save(arquivo)
    con, leitura = leia(arquivo)
    assert [nome for nome, _ in colunas_de(con, "bruta")] == ["mes", "receita", "custo"]
    assert con.execute("SELECT __linha, mes FROM bruta").fetchall() == [(1, "janeiro")]
    assert leitura.linhas == 1 and "Pulei 2 linhas" in leitura.avisos[0]


def test_excel_com_datas_numeros_e_linhas_em_branco_no_topo(tmp_path):
    from datetime import datetime

    livro = Workbook()
    aba = livro.active
    aba.title = "Vendas"
    aba.append([])
    aba.append(["data", "valor", "ativo"])
    aba.append([datetime(2025, 3, 14), 1234.5, True])
    aba.append([datetime(2025, 3, 15), 10, False])
    livro.create_sheet("Outra").append(["x"])
    arquivo = tmp_path / "planilha.xlsx"
    livro.save(arquivo)

    con, leitura = leia(arquivo)
    assert leitura.abas == ["Vendas", "Outra"] and leitura.aba == "Vendas"
    assert leitura.linhas == 2
    perfil = perfilar_tabela(con, "bruta")
    assert [c.tipo for c in perfil.colunas] == ["data", "decimal", "booleano"]

    con2, leitura2 = leia(arquivo, aba="Outra")
    assert leitura2.aba == "Outra"


def test_parquet_mantem_os_tipos(tmp_path):
    arquivo = tmp_path / "dados.parquet"
    con = duckdb.connect()
    con.execute(f"COPY (SELECT 1 AS a, DATE '2025-01-01' AS d) TO '{arquivo}' (FORMAT parquet)")
    con2, leitura = leia(arquivo)
    assert leitura.formato == "parquet"
    perfil = perfilar_tabela(con2, "bruta")
    assert [c.tipo for c in perfil.colunas] == ["inteiro", "data"]


@pytest.mark.parametrize("nome, conteudo", [
    ("vazio.csv", b""),
    ("antigo.xls", b"qualquer coisa"),
    ("foto.png", b"qualquer coisa"),
    ("falso.xlsx", b"nao sou um excel"),
])
def test_arquivo_invalido_explica_o_motivo(tmp_path, nome, conteudo):
    arquivo = tmp_path / nome
    arquivo.write_bytes(conteudo)
    with pytest.raises(ArquivoInvalido):
        leia(arquivo)


def perfil_de(tmp_path, cabecalho, linhas):
    arquivo = tmp_path / "dados.csv"
    arquivo.write_text(cabecalho + "\n" + "".join(linha + "\n" for linha in linhas), encoding="utf-8")
    con, _ = leia(arquivo)
    return con, perfilar_tabela(con, "bruta")


def test_arquivo_de_uma_linha_nao_e_constante(tmp_path):
    _, perfil = perfil_de(tmp_path, "cidade;valor", ["Recife;10"])
    assert [c.vazio for c in perfil.colunas] == [None, None]


def test_linhas_repetidas_nao_sugerem_remover_todas_as_colunas(tmp_path):
    con, perfil = perfil_de(tmp_path, "a;b", ["1;2"] * 40)
    assert all(s.passo["op"] != "remover_colunas" for s in sugerir(con, "bruta", perfil))


def test_coluna_de_ano_vira_data(tmp_path):
    con, perfil = perfil_de(tmp_path, "Year;Value", [f"{1990 + i};{i * 10}" for i in range(30)])
    assert perfil.colunas[0].tipo == "data" and perfil.colunas[0].papel == "data"
    receita = [s.passo for s in sugerir(con, "bruta", perfil) if s.marcada]
    aplicar(con, receita)
    assert con.execute('SELECT min("Year") FROM limpa').fetchone()[0] == date(1990, 1, 1)
    assert grao_automatico(con, "limpa", "Year") == "ano"
    assert not any(p.incompleto for p in serie(con, "limpa", "Year", "soma", "Value", "ano"))


def test_datas_de_primeiro_do_mes_nao_sao_periodo_incompleto(tmp_path):
    linhas = [f"2024-{mes:02d}-01;{mes * 10}" for mes in range(1, 13)]
    con, perfil = perfil_de(tmp_path, "mes_ref;valor", linhas)
    aplicar(con, [s.passo for s in sugerir(con, "bruta", perfil) if s.marcada])
    assert grao_automatico(con, "limpa", "mes_ref") == "mes"
    assert not any(p.incompleto for p in serie(con, "limpa", "mes_ref", "soma", "valor", "mes"))


def test_inteiros_todos_diferentes_so_sao_id_se_forem_sequencia(tmp_path):
    _, perfil = perfil_de(tmp_path, "id_interno;vendas", [f"{i};{i * 137 + 50}" for i in range(100)])
    assert perfil.colunas[0].papel == "id"
    _, perfil = perfil_de(tmp_path, "numero;vendas", [f"{i + 1};{i * 137 + 50}" for i in range(100)])
    assert perfil.colunas[0].papel == "id", "1, 2, 3... é um identificador mesmo sem o nome dizer"
    assert perfil.colunas[1].papel == "metrica"


def test_codigo_de_poucos_inteiros_e_categoria(tmp_path):
    _, perfil = perfil_de(tmp_path, "classe;qtd", [f"{1 + i % 3};{1 + i % 3}" for i in range(60)])
    assert perfil.colunas[0].papel == "categoria"
    assert perfil.colunas[1].papel == "metrica", "o nome diz que é quantidade"


@pytest.mark.parametrize("nome", ["Age", "idade", "taxa_juros", "price", "nota_final"])
def test_coluna_que_nao_se_soma_usa_media(tmp_path, nome):
    _, perfil = perfil_de(tmp_path, f"{nome};x", [f"{10 + i};a" for i in range(50)])
    assert perfil.colunas[0].agregacao == "media"


def test_reais_no_nome_da_coluna_viram_moeda(tmp_path):
    _, perfil = perfil_de(tmp_path, "Salário (R$);x", [f"{1000 + i},50;a" for i in range(50)])
    assert perfil.colunas[0].tipo == "moeda"


@pytest.mark.parametrize("texto, tipo", [
    (["R$ 10,00", "R$ 1.200,50", "R$ 3,00"], "moeda"),
    (["10", "20", "30"], "inteiro"),
    (["1,5", "2,5", "1.000,25"], "decimal"),
    (["10%", "12,5%", "3%"], "percentual"),
    (["14/12/24", "01/02/25", "31/01/24"], "data"),
    (["sim", "não", "sim"], "booleano"),
    (["01310-100", "04538-133", "20040-020"], "texto"),
    (["00123", "00456", "07890"], "texto"),
    (["Ana", "Bia", "Caio"], "texto"),
    (["28 yrs", "30", "25 yrs"], "inteiro"),
    (["$1,884", "528", "98"], "inteiro"),
    (["1.7 hrs", "2.5", "0.9 hrs"], "decimal"),
    (["123AB", "456CD", "789EF"], "texto"),
])
def test_tipos_detectados(tmp_path, texto, tipo):
    arquivo = tmp_path / "coluna.csv"
    arquivo.write_text("x;y\n" + "".join(f"{v};1\n" for v in texto), encoding="utf-8")
    con, _ = leia(arquivo)
    assert perfilar_tabela(con, "bruta").colunas[0].tipo == tipo


def test_texto_longo_quase_todo_diferente_e_texto_e_nao_id(tmp_path):
    tarefas = [f"Revisar o capítulo {i} do relatório e corrigir a gramática" for i in range(60)]
    _, perfil = perfil_de(tmp_path, "tarefas;codigo", [f"{t};U{i:04d}" for i, t in enumerate(tarefas)])
    assert perfil.colunas[0].papel == "texto"
    assert perfil.colunas[1].papel == "id"


def test_nota_de_1_a_5_e_metrica_e_nao_categoria(tmp_path):
    _, perfil = perfil_de(tmp_path, "Accuracy_Rating;x", [f"{1 + i % 4};a" for i in range(60)])
    assert perfil.colunas[0].papel == "metrica"
    assert perfil.colunas[0].agregacao == "media"


def test_sem_data_cada_linha_e_uma_pessoa_e_a_metrica_usa_media(tmp_path):
    _, perfil = perfil_de(tmp_path, "total_sleep_hours;grupo", [f"{5 + i % 4};g{i % 3}" for i in range(60)])
    assert perfil.colunas[0].agregacao == "media", "somar horas de sono de pessoas não diz nada"
    linhas = [f"2024-01-{1 + i % 28:02d};{5 + i % 4};g{i % 3}" for i in range(60)]
    _, perfil = perfil_de(tmp_path, "data;vendas;grupo", linhas)
    assert perfil.colunas[1].agregacao == "soma", "com data, as linhas são eventos e somam"
