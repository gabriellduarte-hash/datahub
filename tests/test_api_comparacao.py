"""Pela API: comparar dois arquivos, copiar a limpeza de um para o outro e combinar sem duplicatas."""

import pytest
from fastapi.testclient import TestClient

from api import main, sessoes
from tests.test_comparacao import FEVEREIRO, JANEIRO


@pytest.fixture
def cliente(tmp_path):
    sessoes.PASTA = tmp_path / "sessoes"
    sessoes.PASTA.mkdir()
    return TestClient(main.app, raise_server_exceptions=False)


def test_comparar_copiar_e_combinar(cliente):
    estado = cliente.post("/api/sessoes", files={"arquivo": ("janeiro.csv", JANEIRO.encode(), "text/csv")}).json()
    sessao = estado["sessao"]
    estado = cliente.post(f"/api/sessoes/{sessao}/tabelas", files={"arquivo": ("fevereiro.csv", FEVEREIRO.encode(), "text/csv")}).json()
    jan, fev = estado["tabelas"]

    comparacao = cliente.get(f"/api/sessoes/{sessao}/comparacao").json()
    assert comparacao["pares"][0]["iguais"] == 3
    assert {c["chave"]: c["situacao"] for c in comparacao["colunas"]}["valor_total"] == "nome_diferente"
    assert comparacao["planos"] == []  # nenhum dos dois tem limpeza ainda

    rota = f"/api/sessoes/{sessao}/tabelas/{jan['id']}"
    cliente.post(f"{rota}/normalizar", json={"receita": []})
    cliente.post(f"{rota}/passos", json={"passo": {"op": "renomear_coluna", "coluna": "Valor Total", "nome": "valor"}})
    plano = next(p for p in cliente.get(f"/api/sessoes/{sessao}/comparacao").json()["planos"] if p["destino"] == fev["id"])
    assert plano["trocas"] == [{"de": "Valor Total", "para": "valor_total"}]

    copiado = cliente.post(f"/api/sessoes/{sessao}/tabelas/{fev['id']}/copiar-receita", json={"origem": jan["id"]})
    assert copiado.status_code == 200, copiado.text
    assert copiado.json()["copia"]["aplicados"] == 1
    plano = next(p for p in cliente.get(f"/api/sessoes/{sessao}/comparacao").json()["planos"] if p["destino"] == fev["id"])
    assert plano["aplicado"] is True
    de_novo = cliente.post(f"/api/sessoes/{sessao}/tabelas/{fev['id']}/copiar-receita", json={"origem": jan["id"]})
    assert de_novo.status_code == 422 and "nada mudaria" in de_novo.json()["erro"]
    recusado = cliente.post(f"/api/sessoes/{sessao}/tabelas/{fev['id']}/copiar-receita", json={"origem": fev["id"]})
    assert recusado.status_code == 422 and "diferentes" in recusado.json()["erro"]

    combinado = cliente.post(f"/api/sessoes/{sessao}/combinar",
                             json={"tabelas": [jan["id"], fev["id"]], "nome": "tudo", "sem_duplicatas": True}).json()
    nova = combinado["tabelas"][-1]
    assert nova["arquivo"]["linhas"] == 11 - 4
