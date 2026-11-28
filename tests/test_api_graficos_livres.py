"""Pela API: apagar todas as linhas de um filtro e gráficos montados pelo usuário."""

import json

import pytest
from fastapi.testclient import TestClient

from api import main, sessoes


# ----- pela API: apagar o filtro inteiro e gráficos sob demanda -----

@pytest.fixture(scope="module")
def sessao(tmp_path_factory):
    sessoes.PASTA = tmp_path_factory.mktemp("sessoes")
    cliente = TestClient(main.app, raise_server_exceptions=False)
    aberto = cliente.post("/api/sessoes/exemplo").json()
    tabela = aberto["tabelas"][0]
    receita = [s["passo"] for s in tabela["sugestoes"] if s["marcada"]]
    estado = cliente.post(f"/api/sessoes/{aberto['sessao']}/tabelas/{tabela['id']}/normalizar", json={"receita": receita}).json()
    return cliente, estado


def test_selecionar_todas_da_visao_filtrada_e_apagar(sessao):
    """Com tudo marcado e um filtro na visão, apagar remove exatamente as linhas do filtro."""
    cliente, estado = sessao
    rota = f"/api/sessoes/{estado['sessao']}/tabelas/t1"
    filtro = [{"coluna": "uf", "operador": "igual", "valor": "SP"}]
    antes = cliente.get(f"{rota}/linhas", params={"quantas": 1, "filtros": json.dumps(filtro)}).json()
    assert antes["filtradas"] > 0
    depois = cliente.post(f"{rota}/passos", json={"passo": {"op": "filtrar_linhas", "modo": "remover", "filtros": filtro}}).json()
    assert depois["ultimo_passo"]["linhas_depois"] == antes["total"] - antes["filtradas"]
    cliente.post(f"{rota}/desfazer")


def test_grafico_sob_demanda_fica_na_sessao_e_acompanha_a_base(sessao):
    cliente, estado = sessao
    rota = f"/api/sessoes/{estado['sessao']}/graficos-livres"
    criado = cliente.post(rota, json={"tipo": "rosca", "x": "categoria", "y": "valor_total", "agregacao": "soma"}).json()
    livre = criado["analise"]["livres"][-1]
    assert livre["grafico"]["tipo"] == "rosca" and livre["grafico"]["dados"]
    assert criado["graficos_livres"][-1]["id"] == livre["id"]

    trocado = cliente.put(f"{rota}/{livre['id']}", json={"tipo": "barras", "x": "uf", "y": None, "agregacao": "contagem"}).json()
    assert trocado["analise"]["livres"][-1]["grafico"]["x"] == "uf"

    refeito = cliente.post(f"/api/sessoes/{estado['sessao']}/reanalisar").json()
    assert refeito["analise"]["livres"][-1]["grafico"]["tipo"] == "barras", "a reanálise recalcula o gráfico"

    apagado = cliente.delete(f"{rota}/{livre['id']}").json()
    assert all(item["id"] != livre["id"] for item in apagado["analise"]["livres"])


@pytest.mark.parametrize("pedido, erro", [
    ({"tipo": "rosca", "x": "categoria", "y": "valor_total", "agregacao": "media"}, "soma ou contagem"),
    ({"tipo": "pizza3d", "x": "categoria"}, "Tipo de gráfico desconhecido"),
    ({"tipo": "barras", "x": "nao_existe"}, "não existe"),
])
def test_grafico_sem_sentido_nao_e_guardado(sessao, pedido, erro):
    cliente, estado = sessao
    resposta = cliente.post(f"/api/sessoes/{estado['sessao']}/graficos-livres", json=pedido)
    assert resposta.status_code == 422 and erro in resposta.json()["erro"]


def test_trocar_o_que_cada_linha_representa_e_voltar_ao_detectado(sessao):
    cliente, estado = sessao
    assert estado["analise"]["grao"]["tipo"] == "evento", "vendas: cada linha é um pedido"
    rota = f"/api/sessoes/{estado['sessao']}/grao"
    trocado = cliente.post(rota, json={"grao": "entidade"}).json()
    assert trocado["analise"]["grao"]["tipo"] == "entidade" and trocado["analise"]["grao"]["escolhido"]
    metricas = [c for c in trocado["analise"]["perfil_limpo"]["colunas"] if c["papel"] == "metrica"]
    assert all(c["agregacao"] != "soma" for c in metricas)
    de_volta = cliente.post(rota, json={"grao": None}).json()
    assert de_volta["analise"]["grao"] == {**de_volta["analise"]["grao"], "tipo": "evento", "escolhido": False}
    assert cliente.post(rota, json={"grao": "nada"}).status_code == 422
