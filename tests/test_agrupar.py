"""Agrupar linhas: uma linha por grupo, com quantas linhas caíram em cada um."""

import pytest

from engine import area as areas
from engine.manipulacao import PassoInvalido
from tests.test_manipulacao import nova_area, tabela_atual


# ----- agrupar, no motor -----

def test_agrupar_conta_quantas_linhas_caem_em_cada_grupo(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "agrupar", "colunas": ["regiao", "plano"]})
    limpa = areas.nomes(item["id"])[1]
    grupos = con.execute(f'SELECT regiao, plano, quantidade FROM "{limpa}" ORDER BY 1, 2').fetchall()
    esperado = con.execute(
        'SELECT regiao, plano, count(*) FROM (SELECT * FROM "{}") GROUP BY 1, 2 ORDER BY 1, 2'.format(areas.nomes(item["id"])[2])
    ).fetchall()
    assert grupos == esperado and len(grupos) == 8
    assert sum(q for *_, q in grupos) == 200, "cada linha cai em exatamente um grupo"


def test_cada_grupo_herda_o_numero_da_primeira_linha(tmp_path):
    con, area, item = nova_area(tmp_path)
    areas.acrescentar_passo(con, area, item["id"], {"op": "agrupar", "colunas": ["regiao"]})
    numeros = [linha[0] for linha in tabela_atual(con, item)]
    assert numeros == [1, 2, 3, 4], "Sul, Leste, Oeste e Norte aparecem nas linhas 1 a 4"


def test_agrupar_com_soma_e_media_e_desfazer(tmp_path):
    con, area, item = nova_area(tmp_path)
    passo = {"op": "agrupar", "colunas": ["plano"], "contagem": "clientes",
             "agregacoes": [{"coluna": "mensalidade", "funcao": "soma"}, {"coluna": "meses", "funcao": "media"}]}
    areas.acrescentar_passo(con, area, item["id"], passo)
    limpa, anterior = areas.nomes(item["id"])[1], areas.nomes(item["id"])[2]
    linhas = dict(con.execute(f'SELECT plano, soma_mensalidade FROM "{limpa}"').fetchall())
    conferido = dict(con.execute(f'SELECT plano, sum(mensalidade) FROM "{anterior}" GROUP BY 1').fetchall())
    assert linhas == conferido
    assert item["relatorio"]["passos"][-1]["titulo"] == "Agrupar por plano, contando em clientes e com soma de mensalidade, média de meses"
    areas.desfazer(con, area, item["id"])
    assert len(tabela_atual(con, item)) == 200


@pytest.mark.parametrize("passo, erro", [
    ({"op": "agrupar", "colunas": []}, "ao menos uma coluna"),
    ({"op": "agrupar", "colunas": ["regiao"], "contagem": "regiao"}, "já é uma das colunas agrupadas"),
    ({"op": "agrupar", "colunas": ["regiao"], "agregacoes": [{"coluna": "plano", "funcao": "soma"}]}, "não é numérica"),
    ({"op": "agrupar", "colunas": ["nada"]}, "não existe"),
])
def test_agrupar_sem_sentido_explica(tmp_path, passo, erro):
    con, area, item = nova_area(tmp_path)
    with pytest.raises(PassoInvalido, match=erro):
        areas.acrescentar_passo(con, area, item["id"], passo)
