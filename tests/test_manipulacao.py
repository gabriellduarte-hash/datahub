"""Fase 1: a sandbox muda os dados por passos da receita, e cada passo pode ser desfeito."""

import duckdb
import pytest

from engine import area as areas
from engine.manipulacao import PassoInvalido, contar, pagina
from engine.sql import colunas_de

LINHAS = 200


def escrever_clientes(pasta, nome="clientes.csv"):
    linhas = ["cliente_id;regiao;plano;meses;mensalidade;cancelou"]
    for i in range(1, LINHAS + 1):
        regiao = ["Norte", "Sul", "Leste", "Oeste"][i % 4]
        plano = "Mensal" if i % 3 else "Anual"
        linhas.append(f"{i};{regiao};{plano};{i % 37 + 1};{50 + i % 20},{i % 10}0;{'sim' if i % 5 == 0 else 'não'}")
    linhas.append("7;Oeste;Mensal;8;57,70;não")     # cópia exata da linha do cliente 7
    caminho = pasta / nome
    caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return caminho


def nova_area(pasta):
    con = duckdb.connect()
    area = areas.nova("teste")
    item = areas.adicionar_arquivo(con, area, escrever_clientes(pasta), "clientes.csv")
    receita = [s["passo"] for s in item["sugestoes"] if s["marcada"]]
    areas.normalizar(con, area, item["id"], receita, {})
    return con, area, item


def tabela_atual(con, item):
    return con.execute(f'SELECT * FROM "{areas.nomes(item["id"])[1]}" ORDER BY __linha').fetchall()


def test_cada_linha_tem_o_numero_do_arquivo(tmp_path):
    con, area, item = nova_area(tmp_path)
    bruta = areas.nomes(item["id"])[0]
    assert con.execute(f'SELECT __linha, cliente_id FROM "{bruta}" WHERE __linha IN (1, 2) ORDER BY 1').fetchall() == [
        (1, "1"), (2, "2")]
    assert "__linha" not in [nome for nome, _ in colunas_de(con, bruta)]
    assert "__linha" not in [linha[0] for linha in con.execute('DESCRIBE "clientes"').fetchall()]


def test_duplicata_some_e_a_primeira_linha_fica(tmp_path):
    con, area, item = nova_area(tmp_path)
    assert item["relatorio"]["linhas_depois"] == LINHAS
    linhas_do_7 = con.execute(f'SELECT __linha FROM "{areas.nomes(item["id"])[1]}" WHERE cliente_id = 7').fetchall()
    assert linhas_do_7 == [(7,)], "a cópia na linha 201 sai, a original fica"


def test_editar_uma_celula(tmp_path):
    con, area, item = nova_area(tmp_path)
    antes = tabela_atual(con, item)
    resultado = areas.acrescentar_passo(con, area, item["id"], {
        "op": "editar_celulas", "edicoes": [{"linha": 3, "coluna": "meses", "valor": "99"}]})
    depois = tabela_atual(con, item)
    assert resultado["alterados"] == 1 and resultado["perdidos"] == 0
    mudou = [i for i, (a, b) in enumerate(zip(antes, depois)) if a != b]
    assert mudou == [2] and depois[2][4] == 99


def test_valor_que_nao_cabe_no_tipo_vira_vazio_e_e_contado(tmp_path):
    con, area, item = nova_area(tmp_path)
    resultado = areas.acrescentar_passo(con, area, item["id"], {
        "op": "editar_celulas", "edicoes": [{"linha": 4, "coluna": "meses", "valor": "muitos"},
                                            {"linha": 5, "coluna": "mensalidade", "valor": "1.234,5"}]})
    assert resultado["perdidos"] == 1
    linha_4, linha_5 = tabela_atual(con, item)[3:5]
    assert linha_4[4] is None and float(linha_5[5]) == pytest.approx(1234.5)


def test_edicoes_seguidas_viram_um_passo_e_saem_juntas(tmp_path):
    con, area, item = nova_area(tmp_path)
    original = tabela_atual(con, item)
    for linha in (3, 4, 5):
        areas.acrescentar_passo(con, area, item["id"], {
            "op": "editar_celulas", "edicoes": [{"linha": linha, "coluna": "regiao", "valor": "Centro"}]})
    assert len(item["manipulacao"]) == 1 and len(item["manipulacao"][0]["edicoes"]) == 3
    areas.desfazer(con, area, item["id"])
    assert tabela_atual(con, item) == original


def test_remover_linhas_marcadas(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "remover_linhas", "linhas": [1, 2, 3]})
    numeros = [linha[0] for linha in tabela_atual(con, item)]
    assert numeros[:2] == [4, 5] and len(numeros) == LINHAS - 3
    assert item["relatorio"]["passos"][-1]["alterados"] == 3


def test_filtro_de_visao_nao_muda_dados_e_aplicar_muda(tmp_path):
    con, area, item = nova_area(tmp_path)
    limpa = areas.nomes(item["id"])[1]
    filtro = [{"coluna": "regiao", "operador": "igual", "valor": "Sul"}]
    vista = pagina(con, limpa, 0, 10, filtros=filtro)
    assert vista["filtradas"] == 50 and vista["total"] == LINHAS
    assert all(linha[1] == "Sul" for linha in vista["linhas"])
    assert len(tabela_atual(con, item)) == LINHAS
    areas.acrescentar_passo(con, area, item["id"], {"op": "filtrar_linhas", "filtros": filtro})
    assert len(tabela_atual(con, item)) == 50
    areas.acrescentar_passo(con, area, item["id"], {
        "op": "filtrar_linhas", "modo": "remover", "filtros": [{"coluna": "meses", "operador": "maior", "valor": "20"}]})
    assert all(linha[4] <= 20 for linha in tabela_atual(con, item))


def test_filtro_com_aspas_e_so_texto(tmp_path):
    con, area, item = nova_area(tmp_path)
    limpa = areas.nomes(item["id"])[1]
    malicioso = [{"coluna": "regiao", "operador": "igual", "valor": "Sul' OR 1=1 --"}]
    assert pagina(con, limpa, 0, 10, filtros=malicioso)["filtradas"] == 0
    with pytest.raises(PassoInvalido):
        pagina(con, limpa, 0, 10, filtros=[{"coluna": 'x" OR 1=1 --', "operador": "igual", "valor": "1"}])


def test_renomear_coluna_leva_o_papel_junto(tmp_path):
    con, area, item = nova_area(tmp_path)
    item["papeis"] = {"plano": "categoria"}
    areas.acrescentar_passo(con, area, item["id"], {"op": "renomear_coluna", "coluna": "plano", "nome": "contrato"})
    nomes = [nome for nome, _ in colunas_de(con, "clientes")]
    assert "contrato" in nomes and "plano" not in nomes
    assert item["papeis"] == {"contrato": "categoria"}
    with pytest.raises(PassoInvalido, match="Já existe"):
        areas.acrescentar_passo(con, area, item["id"], {"op": "renomear_coluna", "coluna": "regiao", "nome": "CONTRATO"})


def test_passo_que_falha_nao_muda_nada(tmp_path):
    con, area, item = nova_area(tmp_path)
    antes, receita = tabela_atual(con, item), list(item["manipulacao"])
    with pytest.raises(PassoInvalido):
        areas.acrescentar_passo(con, area, item["id"], {"op": "remover_linhas", "linhas": []})
    with pytest.raises(PassoInvalido):
        areas.acrescentar_passo(con, area, item["id"], {
            "op": "editar_celulas", "edicoes": [{"linha": 1, "coluna": "nao_existe", "valor": "1"}]})
    assert tabela_atual(con, item) == antes and item["manipulacao"] == receita


def test_desfazer_rapido_e_refazer_tudo_dao_a_mesma_tabela(tmp_path):
    con, area, item = nova_area(tmp_path)
    passos = [
        {"op": "remover_linhas", "linhas": [10, 11]},
        {"op": "editar_celulas", "edicoes": [{"linha": 12, "coluna": "plano", "valor": "Bienal"}]},
        {"op": "renomear_coluna", "coluna": "meses", "nome": "tenure"},
    ]
    estados = [tabela_atual(con, item)]
    for passo in passos:
        areas.acrescentar_passo(con, area, item["id"], passo)
        estados.append(tabela_atual(con, item))
    for esperado in reversed(estados[:-1]):
        areas.desfazer(con, area, item["id"])
        assert tabela_atual(con, item) == esperado
    with pytest.raises(PassoInvalido):
        areas.desfazer(con, area, item["id"])


def test_sql_da_receita_reproduz_a_tabela(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "remover_linhas", "linhas": [1]})
    areas.acrescentar_passo(con, area, item["id"], {
        "op": "editar_celulas", "edicoes": [{"linha": 2, "coluna": "regiao", "valor": "Centro"}]})
    sql = item["relatorio"]["sql"].rstrip(";")
    assert sorted(con.execute(sql).fetchall()) == sorted(tabela_atual(con, item))


def test_reaplicar_a_normalizacao_mantem_a_sandbox(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "remover_linhas", "linhas": [1, 2]})
    areas.normalizar(con, area, item["id"], item["receita"], {})
    assert len(tabela_atual(con, item)) == LINHAS - 2


def test_contar_termo(tmp_path):
    con, area, item = nova_area(tmp_path)
    limpa = areas.nomes(item["id"])[1]
    assert contar(con, limpa, "regiao", "or")["encontradas"] == 50, "Norte tem 'or'; Oeste e Leste não"
    assert contar(con, limpa, "regiao", "sul", inteiro=True)["encontradas"] == 50


def test_pagina_ordenada_traz_o_numero_da_linha(tmp_path):
    con, area, item = nova_area(tmp_path)
    limpa = areas.nomes(item["id"])[1]
    vista = pagina(con, limpa, 0, 5, ordenar="meses", decrescente=True)
    assert [linha[3] for linha in vista["linhas"]] == [37] * 5
    assert all(isinstance(n, int) for n in vista["numeros"])
    assert pagina(con, limpa, 195, 10)["numeros"] == [196, 197, 198, 199, 200]


def test_linhas_continuam_na_ordem_do_arquivo_depois_de_editar(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {
        "op": "editar_celulas", "edicoes": [{"linha": 150, "coluna": "regiao", "valor": "Centro"}]})
    limpa = areas.nomes(item["id"])[1]
    numeros = [linha[0] for linha in con.execute(f'SELECT __linha FROM "{limpa}"').fetchall()]
    assert numeros == sorted(numeros)
