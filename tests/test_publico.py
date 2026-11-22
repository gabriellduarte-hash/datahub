"""As proteções do site público: tamanho do envio, pedidos por visitante, fila e teto de sessões."""

import os
import threading
import time

import pytest
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.testclient import TestClient

from api import main, sessoes
from api.publico import Janela, Protecao

MB = 1024**2


def test_janela_deixa_passar_o_maximo_e_depois_desliza():
    janela = Janela(maximo=3, segundos=60)
    assert [janela.permitir("ip", agora=t) for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert janela.permitir("outro", agora=3)
    assert janela.permitir("ip", agora=61)  # o pedido do segundo 0 saiu da janela


@pytest.fixture
def protegido():
    """Uma API mínima atrás da Protecao, com limites pequenos para o teste caber em segundos."""
    app = FastAPI()
    app.add_exception_handler(HTTPException, main.erro_http)  # o mesmo formato de erro da API de verdade

    @app.post("/api/envio")
    def envio(arquivo: UploadFile = File(...)):
        return {"bytes": len(arquivo.file.read())}

    @app.post("/api/sessoes/exemplo")
    def exemplo():
        return {"ok": True}

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.get("/pagina")
    def pagina():
        return {"ok": True}

    app.add_middleware(Protecao, max_arquivo=1 * MB, pedidos_por_minuto=5, sessoes_por_hora=2,
                       cabecalho_ip="fly-client-ip")
    return TestClient(app)


def test_envio_dentro_do_limite_passa(protegido):
    resposta = protegido.post("/api/envio", files={"arquivo": ("a.csv", b"x" * 1000)})
    assert resposta.status_code == 200 and resposta.json() == {"bytes": 1000}


def test_envio_grande_e_recusado_pelo_tamanho_declarado(protegido):
    resposta = protegido.post("/api/envio", files={"arquivo": ("a.csv", b"x" * (3 * MB))})
    assert resposta.status_code == 413
    assert "1 MB" in resposta.json()["erro"]


def test_envio_grande_sem_tamanho_declarado_e_cortado_enquanto_chega(protegido):
    """Sem Content-Length (envio em pedaços), o corte acontece ao passar do limite."""
    pedacos = (b"x" * (256 * 1024) for _ in range(12))  # 3 MB em pedaços
    resposta = protegido.post("/api/envio", content=pedacos,
                              headers={"content-type": "multipart/form-data; boundary=fronteira"})
    assert resposta.status_code == 413
    assert "1 MB" in resposta.json()["erro"]


def test_muitos_pedidos_do_mesmo_visitante_esperam(protegido):
    respostas = [protegido.get("/api/ping", headers={"fly-client-ip": "1.1.1.1"}).status_code for _ in range(6)]
    assert respostas == [200] * 5 + [429]
    assert protegido.get("/api/ping", headers={"fly-client-ip": "2.2.2.2"}).status_code == 200
    assert protegido.get("/pagina", headers={"fly-client-ip": "1.1.1.1"}).status_code == 200  # só /api/ conta


def test_sessoes_novas_tem_limite_por_hora(protegido):
    cabecalho = {"fly-client-ip": "3.3.3.3"}
    respostas = [protegido.post("/api/sessoes/exemplo", headers=cabecalho).status_code for _ in range(3)]
    assert respostas == [200, 200, 429]
    assert "sessões" in protegido.post("/api/sessoes/exemplo", headers=cabecalho).json()["erro"]


def test_respostas_saem_com_cabecalhos_de_seguranca(protegido):
    resposta = protegido.get("/pagina")
    assert resposta.headers["x-content-type-options"] == "nosniff"
    assert resposta.headers["referrer-policy"] == "same-origin"


def test_fila_recusa_quem_espera_demais_e_aceita_o_mesmo_pedido_de_novo(monkeypatch):
    monkeypatch.setattr(sessoes, "ESPERA_S", 0.2)
    fila = sessoes._Fila(1)
    ocupou, liberar = threading.Event(), threading.Event()

    def segurar():
        with fila.vaga():
            ocupou.set()
            liberar.wait(5)

    outro = threading.Thread(target=segurar)
    outro.start()
    ocupou.wait(5)
    with pytest.raises(sessoes.ServidorOcupado):
        with fila.vaga():
            pass
    liberar.set()
    outro.join()
    with fila.vaga(), fila.vaga():  # o mesmo pedido abrindo o banco duas vezes usa uma vaga só
        pass


def test_teto_de_sessoes_libera_as_paradas_e_depois_recusa(monkeypatch, tmp_path):
    monkeypatch.setattr(sessoes, "PASTA", tmp_path)
    monkeypatch.setattr(sessoes, "MAX_SESSOES", 2)
    primeira, segunda = sessoes.criar(), sessoes.criar()
    with pytest.raises(sessoes.ServidorOcupado):
        sessoes.criar()

    parada_ha = time.time() - sessoes.OCIOSA_S - 60
    os.utime(tmp_path / primeira, (parada_ha, parada_ha))
    terceira = sessoes.criar()
    assert not (tmp_path / primeira).exists()
    assert (tmp_path / segunda).is_dir() and (tmp_path / terceira).is_dir()


def test_api_explica_quando_esta_cheia(monkeypatch):
    def cheio():
        raise sessoes.ServidorOcupado("sessoes")

    monkeypatch.setattr(sessoes, "criar", cheio)
    resposta = TestClient(main.app).post("/api/sessoes/exemplo")
    assert resposta.status_code == 503
    assert "cheio" in resposta.json()["erro"] and resposta.headers["retry-after"] == "30"


def test_saude_diz_o_modo():
    dados = TestClient(main.app).get("/api/saude").json()
    assert dados["modo"] == "local" and dados["horas_de_vida"] == 24
