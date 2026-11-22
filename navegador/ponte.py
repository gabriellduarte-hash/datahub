"""A ponte do navegador: a mesma API do datahub, chamada sem rede.

No site estático não há servidor. Um Web Worker roda o Pyodide (o Python do
navegador) com o motor e esta ponte, e cada pedido da interface vira uma
chamada ASGI direto na aplicação FastAPI de api/main.py. As rotas são as
mesmas do servidor; só o transporte muda.
"""

import asyncio
import os

# Lidos quando a API é importada, então vêm antes do import.
os.environ.setdefault("DATAHUB_MODO", "navegador")
os.environ.setdefault("DATAHUB_SESSOES", "/sessoes")

import anyio.to_thread  # noqa: E402


async def _na_mesma_thread(funcao, *argumentos, abandon_on_cancel=False, cancellable=None, limiter=None):
    """O navegador não tem threads: o que o FastAPI mandaria para uma thread roda aqui mesmo."""
    return funcao(*argumentos)


anyio.to_thread.run_sync = _na_mesma_thread

from api.main import app  # noqa: E402


async def pedir(metodo: str, endereco: str, cabecalhos, corpo=None) -> dict:
    """Um pedido HTTP feito à aplicação, sem rede. Devolve status, cabeçalhos e corpo em bytes."""
    if hasattr(cabecalhos, "to_py"):  # lista vinda do JavaScript
        cabecalhos = cabecalhos.to_py()
    if hasattr(corpo, "to_bytes"):    # Uint8Array vindo do JavaScript
        corpo = corpo.to_bytes()
    caminho, _, consulta = endereco.partition("?")
    escopo = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},  # 2.4: o Starlette não fica escutando desconexão
        "http_version": "1.1",
        "method": metodo.upper(),
        "scheme": "http",
        "path": caminho,
        "raw_path": caminho.encode(),
        "query_string": consulta.encode(),
        "root_path": "",
        "headers": [(str(k).lower().encode("latin-1"), str(v).encode("latin-1")) for k, v in (cabecalhos or [])],
        "client": ("navegador", 0),
        "server": ("datahub", 80),
    }
    entregue = False

    async def receber():
        nonlocal entregue
        if not entregue:
            entregue = True
            return {"type": "http.request", "body": corpo or b"", "more_body": False}
        await asyncio.Event().wait()  # o corpo já foi; a "conexão" fica aberta até a resposta acabar

    resposta = {"status": 0, "cabecalhos": [], "corpo": bytearray()}

    async def enviar(mensagem):
        if mensagem["type"] == "http.response.start":
            resposta["status"] = mensagem["status"]
            resposta["cabecalhos"] = [[k.decode("latin-1"), v.decode("latin-1")] for k, v in mensagem.get("headers", [])]
        elif mensagem["type"] == "http.response.body":
            resposta["corpo"] += mensagem.get("body", b"")

    try:
        await app(escopo, receber, enviar)
    except Exception as erro:  # o Starlette já respondeu 500 e repassa o erro; aqui só vira texto
        if not resposta["status"]:
            resposta.update(status=500, cabecalhos=[["content-type", "application/json"]],
                            corpo=bytearray(b'{"erro": "Erro inesperado no motor: ' + str(erro)[:200].encode() + b'"}'))
    return {"status": resposta["status"], "cabecalhos": resposta["cabecalhos"], "corpo": bytes(resposta["corpo"])}
