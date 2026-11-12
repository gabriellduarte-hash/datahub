"""A API de ponta a ponta, do envio do arquivo à exportação."""

import csv
import io

import pytest
from fastapi.testclient import TestClient

from api import main, sessoes


@pytest.fixture(scope="module")
def cliente(tmp_path_factory):
    sessoes.PASTA = tmp_path_factory.mktemp("sessoes")
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def aberto(cliente):
    resposta = cliente.post("/api/sessoes/exemplo")
    assert resposta.status_code == 200
    return resposta.json()


@pytest.fixture(scope="module")
def normalizado(cliente, aberto):
    receita = [s["passo"] for s in aberto["sugestoes"] if s["marcada"]]
    resposta = cliente.post(f"/api/sessoes/{aberto['sessao']}/normalizar", json={"receita": receita})
    assert resposta.status_code == 200
    return resposta.json()


def test_abrir_arquivo_roda_os_tres_primeiros_blocos(aberto):
    status = {b["bloco"]: b["status"] for b in aberto["blocos"]}
    assert status == {"arquivo": "ok", "perfil": "ok", "normalizacao": "ok",
                      "graficos": "pendente", "insights": "pendente"}
    assert aberto["arquivo"]["linhas"] == 5055


def test_normalizar_roda_os_cinco_blocos(normalizado):
    assert {b["status"] for b in normalizado["blocos"]} == {"ok"}
    assert normalizado["relatorio"]["linhas_depois"] == 5000
    assert normalizado["graficos"] and normalizado["insights"]["insights"]


def test_paginas_de_linhas(cliente, normalizado):
    sessao = normalizado["sessao"]
    resposta = cliente.get(f"/api/sessoes/{sessao}/linhas", params={
        "tabela": "limpa", "pagina": 2, "por_pagina": 10, "ordenar": "valor_total", "decrescente": True,
    }).json()
    assert len(resposta["linhas"]) == 10 and resposta["total"] == 5000


def test_ordenar_por_coluna_inexistente_e_recusado(cliente, normalizado):
    resposta = cliente.get(f"/api/sessoes/{normalizado['sessao']}/linhas",
                           params={"ordenar": "1; DROP TABLE bruta"})
    assert resposta.status_code == 422


def test_trocar_o_papel_de_uma_coluna_refaz_os_graficos(cliente, normalizado):
    sessao = normalizado["sessao"]
    resposta = cliente.post(f"/api/sessoes/{sessao}/papeis", json={"papeis": {"valor_total": "ignorar"}}).json()
    assert all(g["y"] != "valor_total" for g in resposta["graficos"])
    cliente.post(f"/api/sessoes/{sessao}/papeis", json={"papeis": {}})


def test_editar_um_grafico(cliente, normalizado):
    resposta = cliente.post(f"/api/sessoes/{normalizado['sessao']}/grafico", json={
        "tipo": "linha", "x": "data_pedido", "y": "valor_total", "agregacao": "media", "grao": "trimestre",
    })
    assert resposta.status_code == 200
    assert resposta.json()["grao"] == "trimestre"


def test_grafico_com_coluna_inexistente_e_recusado(cliente, normalizado):
    resposta = cliente.post(f"/api/sessoes/{normalizado['sessao']}/grafico", json={"tipo": "barras", "x": "nao_existe"})
    assert resposta.status_code == 422


def test_taxa_de_coluna_que_nao_e_sim_nao_explica_o_erro(cliente, normalizado):
    resposta = cliente.post(f"/api/sessoes/{normalizado['sessao']}/grafico", json={
        "tipo": "barras", "x": "categoria", "y": "valor_total", "agregacao": "taxa",
    })
    assert resposta.status_code == 422
    assert "sim/não" in resposta.json()["erro"]


def test_coluna_de_texto_marcada_como_metrica_nao_derruba_nada(cliente, normalizado):
    sessao = normalizado["sessao"]
    resposta = cliente.post(f"/api/sessoes/{sessao}/papeis", json={"papeis": {"cliente": "metrica"}})
    assert resposta.status_code == 200
    assert {b["status"] for b in resposta.json()["blocos"]} == {"ok"}
    grafico = cliente.post(f"/api/sessoes/{sessao}/grafico", json={
        "tipo": "barras", "x": "categoria", "y": "cliente", "agregacao": "soma",
    })
    assert grafico.status_code == 200
    cliente.post(f"/api/sessoes/{sessao}/papeis", json={"papeis": {}})


def test_exportar_csv_para_excel_brasileiro(cliente, normalizado):
    resposta = cliente.get(f"/api/sessoes/{normalizado['sessao']}/exportar/csv")
    assert resposta.status_code == 200
    assert resposta.content.startswith(b"\xef\xbb\xbf")
    linhas = list(csv.DictReader(io.StringIO(resposta.content.decode("utf-8-sig")), delimiter=";"))
    assert len(linhas) == 5000
    assert "," in linhas[0]["valor_unitario"]


def test_exportar_parquet_e_receita(cliente, normalizado):
    sessao = normalizado["sessao"]
    assert cliente.get(f"/api/sessoes/{sessao}/exportar/parquet").content[:4] == b"PAR1"
    assert cliente.get(f"/api/sessoes/{sessao}/exportar/receita").json() == normalizado["receita"]


def test_envio_de_csv_real(cliente):
    conteudo = "nome;cidade;valor\nJoão;São Paulo;R$ 10,00\nAna;Recife;R$ 20,00\n".encode("cp1252")
    resposta = cliente.post("/api/sessoes", files={"arquivo": ("meus dados.csv", conteudo, "text/csv")})
    assert resposta.status_code == 200
    assert resposta.json()["arquivo"]["arquivo"] == "meus dados.csv"
    assert resposta.json()["arquivo"]["codificacao"] == "latin-1"


def test_arquivo_invalido_nao_deixa_sessao_para_tras(cliente):
    antes = len(list(sessoes.PASTA.iterdir()))
    resposta = cliente.post("/api/sessoes", files={"arquivo": ("foto.png", b"x", "image/png")})
    assert resposta.status_code == 422
    assert "Formato" in resposta.json()["erro"]
    assert len(list(sessoes.PASTA.iterdir())) == antes


def test_limite_de_tamanho_vale_durante_o_envio(cliente, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(main, "LIMITES", replace(main.LIMITES, max_bytes_arquivo=100))
    resposta = cliente.post("/api/sessoes", files={"arquivo": ("grande.csv", b"a;b\n" * 500, "text/csv")})
    assert resposta.status_code == 413
    assert resposta.json()["limite"] == "bytes_arquivo"


def test_id_de_sessao_nao_vira_caminho(cliente):
    assert cliente.get("/api/sessoes/..%2F..%2Fetc").status_code == 404
    assert cliente.get("/api/sessoes/sessao_que_nao_existe_000").status_code == 404


def test_sem_acesso_a_arquivos_durante_a_analise(aberto):
    with sessoes.abrir(aberto["sessao"]) as con, pytest.raises(Exception, match="disabled"):
        con.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()
