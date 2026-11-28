"""Comparar arquivos da mesma sessão: colunas, duplicatas e a mesma limpeza nos dois.

Os arquivos imitam o mesmo relatório exportado em dois meses por pessoas
diferentes: nomes de coluna escritos de outro jeito, uma coluna a mais, um tipo
que mudou e três pedidos que aparecem nos dois meses.
"""

import duckdb
import pytest

from engine import area as areas
from engine import comparacao, relacional
from engine.limites import LOCAL

JANEIRO = """Pedido,Cliente,Valor Total,Cidade,Desconto
1,Ana,100.50,São Paulo,5
2,Bruno,80.00,Rio de Janeiro,0
3,Carla,45.90,Recife,10
4,Davi,300.00,Curitiba,0
5,Eva,12.00,Salvador,0
"""
FEVEREIRO = """pedido,cliente,valor_total,Cidade,Desconto,Vendedor
3,carla,45.9,  Recife ,10%,Rui
4,Davi,300,CURITIBA,0%,Rui
5,Eva,12.0,Salvador,0%,Lia
6,Fábio,99.99,Natal,0%,Lia
7,Gil,10.00,Belém,0%,Rui
7,Gil,10.00,Belém,0%,Rui
"""


@pytest.fixture
def dois_meses(tmp_path):
    con = duckdb.connect()
    area = areas.nova("teste")
    caminhos = []
    for nome, texto in (("janeiro.csv", JANEIRO), ("fevereiro.csv", FEVEREIRO)):
        caminho = tmp_path / nome
        caminho.write_text(texto, encoding="utf-8")
        caminhos.append(areas.adicionar_arquivo(con, area, caminho, nome))
    jan, fev = caminhos
    return con, area, jan, fev


def coluna(resultado, chave):
    return next(c for c in resultado["colunas"] if c["chave"] == chave)


def test_colunas_batem_pelo_nome_simples_e_apontam_o_que_difere(dois_meses):
    con, area, jan, fev = dois_meses
    resultado = comparacao.comparar(con, area)
    total = coluna(resultado, "valor_total")
    assert total["situacao"] == "nome_diferente"
    assert total["nomes"] == {jan["id"]: "Valor Total", fev["id"]: "valor_total"}
    assert coluna(resultado, "cidade")["situacao"] == "igual"
    assert coluna(resultado, "vendedor")["situacao"] == "falta"
    assert coluna(resultado, "desconto")["situacao"] == "tipo_diferente"  # 5 contra "10%"


def test_duplicatas_entre_e_dentro_dos_arquivos(dois_meses):
    con, area, jan, fev = dois_meses
    resultado = comparacao.comparar(con, area)
    duplicatas = {t["id"]: t["duplicatas_dentro"] for t in resultado["tabelas"]}
    assert duplicatas == {jan["id"]: 0, fev["id"]: 1}

    par = resultado["pares"][0]
    assert {par["a"], par["b"]} == {jan["id"], fev["id"]}
    # Desconto mudou de tipo ("5" contra "10%"): com ele, nenhuma linha bateria. Fica fora da conta, e a tela diz.
    assert par["fora"] == ["desconto"]
    # Ainda sem normalizar: "45.90" bate com "45.9", "Carla" com "carla", "  Recife " com "Recife".
    assert par["iguais"] == 3 and par["b_em_a"] == 3 and par["a_em_b"] == 3
    assert [e["linha_a"] for e in par["exemplos"]] == [3, 4, 5]
    assert [e["linha_b"] for e in par["exemplos"]] == [1, 2, 3]


def test_identificador_em_comum_conta_registros_nos_dois(dois_meses):
    con, area, jan, fev = dois_meses
    jan["papeis"]["Pedido"] = "id"
    par = comparacao.comparar(con, area)["pares"][0]
    assert par["chave"] == {"coluna": "pedido", "ambos": 3, "so_a": 2, "so_b": 2}


def test_mesma_limpeza_alinha_os_arquivos_e_as_duplicatas_aparecem(dois_meses):
    con, area, jan, fev = dois_meses
    receita = [s["passo"] for s in jan["sugestoes"] if s["marcada"]]
    areas.normalizar(con, area, jan["id"], receita, {})
    areas.acrescentar_passo(con, area, jan["id"], {"op": "renomear_coluna", "coluna": "Valor Total", "nome": "valor"})
    areas.acrescentar_passo(con, area, jan["id"], {"op": "remover_colunas", "colunas": ["Desconto"]})
    areas.acrescentar_passo(con, area, jan["id"], {"op": "formula", "nome": "valor_com_frete", "formula": "valor + 10"})
    areas.acrescentar_passo(con, area, jan["id"], {"op": "editar_celulas", "edicoes": [{"linha": 1, "coluna": "Cliente", "valor": "Ana Paula"}]})

    bruta_fev = areas.nomes(fev["id"])[0]
    plano = comparacao.planejar(jan, {linha[0]: linha[1] for linha in con.execute(f'DESCRIBE "{bruta_fev}"').fetchall()
                                      if not linha[0].startswith("__")}, fev)
    titulos = [p["titulo"] for p in plano["aplicaveis"]]
    assert any("valor_com_frete" in t for t in titulos), titulos
    assert {"de": "Valor Total", "para": "valor_total"} in plano["trocas"]
    assert {"de": "Desconto", "para": "Desconto"} not in plano["trocas"]
    assert [p["motivo"] for p in plano["pulados"]] == ["mexe em linhas escolhidas a dedo no outro arquivo"]

    resultado = comparacao.copiar_receita(con, area, jan["id"], fev["id"], LOCAL)
    assert resultado["aplicados"] == len(plano["aplicaveis"])
    colunas_fev = [linha[0] for linha in con.execute(f'DESCRIBE "{fev["nome"]}"').fetchall()]
    assert "valor" in colunas_fev and "valor_com_frete" in colunas_fev and "Desconto" not in colunas_fev

    par = comparacao.comparar(con, area)["pares"][0]
    assert "desconto" not in par["colunas"]
    # 3 (carla/Recife com espaços e minúsculas), 4 (CURITIBA) e 5 aparecem nos dois meses
    assert par["iguais"] == 3
    assert par["b_em_a"] == 3
    assert [e["linha_a"] for e in par["exemplos"]] == [3, 4, 5]


def test_combinar_sem_duplicatas_usa_o_mesmo_criterio(dois_meses):
    con, area, jan, fev = dois_meses
    for item, coluna_valor in ((jan, "Valor Total"), (fev, "valor_total")):
        areas.normalizar(con, area, item["id"], [], {})
        areas.acrescentar_passo(con, area, item["id"], {"op": "renomear_coluna", "coluna": coluna_valor, "nome": "valor"})
        areas.acrescentar_passo(con, area, item["id"], {"op": "remover_colunas", "colunas": ["Desconto"]})

    tudo = relacional.combinar(con, area, [jan["id"], fev["id"]], "tudo")
    sem = relacional.combinar(con, area, [jan["id"], fev["id"]], "sem", sem_duplicatas=True)
    contar = lambda item: con.execute(f'SELECT count(*) FROM "{item["nome"]}"').fetchone()[0]
    assert contar(tudo) == 11
    assert contar(sem) == 11 - 3 - 1  # três pedidos nos dois meses e o Gil repetido em fevereiro
    assert any("4 duplicatas removidas" in aviso for aviso in sem["arquivo"]["avisos"])
    # "Pedido" e "pedido" viraram uma coluna só: só Vendedor falta em parte dos arquivos
    assert any(aviso.endswith("ficaram vazias nas outras: Vendedor.") for aviso in sem["arquivo"]["avisos"])
    # fica a primeira aparição: a de janeiro
    origem = con.execute(f'SELECT origem FROM "{sem["nome"]}" WHERE lower(trim(Cidade)) = \'recife\'').fetchall()
    assert origem == [(jan["nome"],)]


def test_tabela_combinada_fica_fora_da_comparacao(dois_meses):
    con, area, jan, fev = dois_meses
    relacional.combinar(con, area, [jan["id"], fev["id"]], "tudo")
    assert [t["id"] for t in comparacao.comparar(con, area)["tabelas"]] == [jan["id"], fev["id"]]


def test_conversao_copiada_usa_o_formato_do_destino(dois_meses):
    """Desconto é inteiro em janeiro e percentual ("10%") em fevereiro: a mesma regra, cada um no seu formato."""
    con, area, jan, fev = dois_meses
    receita = [s["passo"] for s in jan["sugestoes"] if s["marcada"]]
    assert {"op": "converter_numero", "coluna": "Desconto"}.items() <= next(
        p for p in receita if p.get("coluna") == "Desconto").items()
    areas.normalizar(con, area, jan["id"], receita, {})

    comparacao.copiar_receita(con, area, jan["id"], fev["id"], LOCAL)
    passo = next(p for p in fev["receita"] if p.get("coluna") == "Desconto")
    assert passo["tipo"] == "percentual"
    vazios = con.execute(f'SELECT count(*) FROM "{fev["nome"]}" WHERE Desconto IS NULL').fetchone()[0]
    assert vazios == 0


def test_plano_diz_quando_a_limpeza_ja_foi_aplicada(dois_meses):
    """Depois de aplicar, aplicar de novo não mudaria nada: o plano avisa e a cópia é recusada."""
    con, area, jan, fev = dois_meses
    areas.normalizar(con, area, jan["id"], [s["passo"] for s in jan["sugestoes"] if s["marcada"]], {})
    areas.acrescentar_passo(con, area, jan["id"], {"op": "mover_coluna", "coluna": "Cidade", "antes": "Pedido"})
    plano = lambda o, d: next(p for p in comparacao.comparar(con, area)["planos"] if p["origem"] == o["id"] and p["destino"] == d["id"])
    assert plano(jan, fev)["aplicado"] is False
    assert any(t.startswith("Mover Cidade para antes de pedido") for t in plano(jan, fev)["aplicaveis"])

    comparacao.copiar_receita(con, area, jan["id"], fev["id"], LOCAL)
    assert plano(jan, fev)["aplicado"] is True
    assert [l[0] for l in con.execute(f'DESCRIBE "{fev["nome"]}"').fetchall()][0] == "Cidade"
    with pytest.raises(comparacao.CopiaRecusada, match="nada mudaria"):
        comparacao.copiar_receita(con, area, jan["id"], fev["id"], LOCAL)
    # o caminho de volta também já está feito: fevereiro tem a mesma limpeza que janeiro
    assert plano(fev, jan)["aplicado"] is True

    # mudar a limpeza da origem libera o botão de novo
    areas.acrescentar_passo(con, area, jan["id"], {"op": "renomear_coluna", "coluna": "Cliente", "nome": "nome_cliente"})
    assert plano(jan, fev)["aplicado"] is False


def test_combinar_guarda_a_versao_de_cada_arquivo(dois_meses):
    con, area, jan, fev = dois_meses
    nova = relacional.combinar(con, area, [jan["id"], fev["id"]], "tudo", sem_duplicatas=True)
    assert nova["origem"] == "combinada" and nova["titulo"] == "Combinada: janeiro + fevereiro"
    assert nova["definicao"] == {"tabelas": [jan["id"], fev["id"]], "sem_duplicatas": True,
                                 "versoes": {jan["id"]: jan["versao"], fev["id"]: fev["versao"]}}


def test_combinar_junta_colunas_com_o_mesmo_nome_simples(dois_meses):
    """A comparação diz que "Valor Total" e "valor_total" são a mesma coluna; combinar também as junta."""
    con, area, jan, fev = dois_meses
    nova = relacional.combinar(con, area, [jan["id"], fev["id"]], "tudo", sem_duplicatas=True)
    colunas = [linha[0] for linha in con.execute(f'DESCRIBE "{nova["nome"]}"').fetchall()]
    assert "Valor Total" in colunas and "valor_total" not in colunas
    vazios = con.execute(f'SELECT count(*) FROM "{nova["nome"]}" WHERE "Valor Total" IS NULL').fetchone()[0]
    assert vazios == 0
    assert any(a.startswith("Viraram uma coluna só") and "valor_total em Valor Total" in a for a in nova["arquivo"]["avisos"])
    # Desconto tem tipo diferente ("5" contra "10%"): fica fora da conta, e as 3 duplicatas entre os meses aparecem
    assert con.execute(f'SELECT count(*) FROM "{nova["nome"]}"').fetchone()[0] == 11 - 3 - 1
