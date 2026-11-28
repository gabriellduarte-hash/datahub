"""Editar colunas como numa planilha: coluna em branco, inserir à direita, renomear e apagar."""

import pytest

from engine import area as areas
from engine.manipulacao import PassoInvalido
from engine.sql import colunas_de
from tests.test_manipulacao import nova_area


def nomes_visiveis(con, item):
    return [nome for nome, _ in colunas_de(con, areas.nomes(item["id"])[1])]


def test_coluna_nova_entra_logo_depois_da_escolhida(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "formula", "nome": "anual", "formula": "mensalidade * 12", "apos": "plano"})
    nomes = nomes_visiveis(con, item)
    assert nomes[nomes.index("plano") + 1] == "anual"
    assert con.execute(f'SELECT count(*) FROM "{areas.nomes(item["id"])[1]}" WHERE anual = mensalidade * 12').fetchone()[0] == 200


def test_inserir_a_esquerda_da_primeira_coluna(tmp_path):
    con, area, item = nova_area(tmp_path)
    primeira = nomes_visiveis(con, item)[0]
    areas.acrescentar_passo(con, area, item["id"], {"op": "formula", "nome": "nota", "vazia": True, "antes": primeira})
    assert nomes_visiveis(con, item)[:2] == ["nota", primeira]


def test_coluna_em_branco_para_preencher_e_editar(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "formula", "nome": "observacao", "vazia": True, "apos": "cliente_id"})
    assert nomes_visiveis(con, item)[1] == "observacao"
    areas.acrescentar_passo(con, area, item["id"], {"op": "editar_celulas", "edicoes": [{"linha": 3, "coluna": "observacao", "valor": "ligar"}]})
    limpa = areas.nomes(item["id"])[1]
    assert con.execute(f'SELECT observacao FROM "{limpa}" WHERE __linha = 3').fetchone()[0] == "ligar"
    assert con.execute(f'SELECT count(observacao) FROM "{limpa}"').fetchone()[0] == 1
    assert item["relatorio"]["passos"][-2]["titulo"] == "Criar a coluna observacao em branco"


def test_renomear_e_apagar_pelo_cabecalho_sao_passos_desfeitos_um_a_um(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "renomear_coluna", "coluna": "plano", "nome": "tipo_de_plano"})
    areas.acrescentar_passo(con, area, item["id"], {"op": "remover_colunas", "colunas": ["regiao"]})
    assert "tipo_de_plano" in nomes_visiveis(con, item) and "regiao" not in nomes_visiveis(con, item)
    areas.desfazer(con, area, item["id"])
    assert "regiao" in nomes_visiveis(con, item)


def test_mover_coluna_para_depois_e_para_antes_de_outra(tmp_path):
    con, area, item = nova_area(tmp_path)
    nomes = nomes_visiveis(con, item)
    primeira, ultima = nomes[0], nomes[-1]
    limpa = areas.nomes(item["id"])[1]
    valores = lambda: con.execute(f'SELECT "{primeira}", "{ultima}" FROM "{limpa}" ORDER BY __linha').fetchall()
    antes = valores()
    areas.acrescentar_passo(con, area, item["id"], {"op": "mover_coluna", "coluna": primeira, "apos": ultima})
    assert nomes_visiveis(con, item) == nomes[1:] + [primeira]
    assert valores() == antes  # os dados vão junto com a coluna, linha a linha
    areas.acrescentar_passo(con, area, item["id"], {"op": "mover_coluna", "coluna": primeira, "antes": nomes[1]})
    assert nomes_visiveis(con, item) == nomes
    assert item["relatorio"]["passos"][-1]["titulo"] == f"Mover {primeira} para antes de {nomes[1]}"
    areas.desfazer(con, area, item["id"])
    assert nomes_visiveis(con, item) == nomes[1:] + [primeira]


@pytest.mark.parametrize("passo, erro", [
    ({"coluna": "plano"}, "depois ou antes"),
    ({"coluna": "plano", "apos": "plano"}, "dela mesma"),
    ({"coluna": "plano", "apos": "nao_existe"}, "nao_existe não existe"),
    ({"coluna": "__linha", "apos": "plano"}, "não existe"),
])
def test_mover_coluna_recusa_pedido_sem_sentido(tmp_path, passo, erro):
    con, area, item = nova_area(tmp_path)
    antes = nomes_visiveis(con, item)
    with pytest.raises(PassoInvalido, match=erro):
        areas.acrescentar_passo(con, area, item["id"], {"op": "mover_coluna", **passo})
    assert nomes_visiveis(con, item) == antes
