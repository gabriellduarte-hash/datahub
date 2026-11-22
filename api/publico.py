"""Proteções do site público, na porta de entrada da API.

Rodam antes de qualquer rota, para um abuso custar pouco:

- um envio maior que o permitido é cortado enquanto chega, sem esperar o
  arquivo inteiro ficar no disco;
- cada visitante tem um limite de pedidos por minuto e de sessões novas por
  hora;
- toda resposta sai com dois cabeçalhos básicos de segurança.

Na sua máquina nada disso é ligado: veja `api/main.py`.
"""

import json
import time
from collections import deque

from fastapi import HTTPException

# Rotas que criam sessão: são as que gravam arquivo e rodam a análise inteira.
ROTAS_DE_SESSAO_NOVA = {"/api/sessoes", "/api/sessoes/exemplo"}
CABECALHOS_SEGURANCA = [(b"x-content-type-options", b"nosniff"), (b"referrer-policy", b"same-origin")]


class Janela:
    """Quantas vezes cada chave pode passar dentro de uma janela de tempo que desliza."""

    def __init__(self, maximo: int, segundos: float):
        self.maximo = maximo
        self.segundos = segundos
        self.registros: dict[str, deque] = {}

    def permitir(self, chave: str, agora: float | None = None) -> bool:
        agora = time.monotonic() if agora is None else agora
        fila = self.registros.setdefault(chave, deque())
        while fila and fila[0] <= agora - self.segundos:
            fila.popleft()
        if len(fila) >= self.maximo:
            return False
        fila.append(agora)
        if len(self.registros) > 10_000:
            self._esquecer_parados(agora)
        return True

    def _esquecer_parados(self, agora: float) -> None:
        for chave in [c for c, fila in self.registros.items() if not fila or fila[-1] <= agora - self.segundos]:
            del self.registros[chave]


class CorpoGrande(HTTPException):
    """É um HTTPException porque o FastAPI troca qualquer outro erro na leitura do corpo por um 400 genérico."""

    def __init__(self, maximo: int):
        super().__init__(status_code=413, detail=_mensagem_tamanho(maximo))


FOLGA_FORMULARIO = 1024**2  # o resto do formulário em volta do arquivo


class Protecao:
    """Middleware ASGI. `cabecalho_ip` é o cabeçalho em que a hospedagem põe o IP do visitante."""

    def __init__(self, app, max_arquivo: int | None, pedidos_por_minuto: int, sessoes_por_hora: int,
                 cabecalho_ip: str | None = None):
        self.app = app
        self.max_arquivo = max_arquivo
        self.max_corpo = max_arquivo + FOLGA_FORMULARIO if max_arquivo else None
        self.pedidos = Janela(pedidos_por_minuto, 60)
        self.sessoes = Janela(sessoes_por_hora, 3600)
        self.cabecalho_ip = cabecalho_ip.lower().encode() if cabecalho_ip else None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        caminho, metodo = scope["path"], scope["method"]
        if caminho.startswith("/api/"):
            ip = self._ip(scope)
            if not self.pedidos.permitir(ip):
                return await _responder(send, 429, "Muitos pedidos seguidos. Espere um minuto e tente de novo.")
            if metodo == "POST" and caminho in ROTAS_DE_SESSAO_NOVA and not self.sessoes.permitir(ip):
                return await _responder(send, 429, "Você abriu muitas sessões na última hora. "
                                                   "Continue na sessão aberta ou volte mais tarde.")

        if self.max_corpo is not None:
            declarado = _cabecalho(scope, b"content-length")
            if declarado and declarado.isdigit() and int(declarado) > self.max_corpo:
                return await _responder(send, 413, _mensagem_tamanho(self.max_arquivo))

        recebidos = 0
        respondeu = False

        async def receber():
            nonlocal recebidos
            mensagem = await receive()
            if self.max_corpo is not None and mensagem["type"] == "http.request":
                recebidos += len(mensagem.get("body", b""))
                if recebidos > self.max_corpo:
                    raise CorpoGrande(self.max_arquivo)
            return mensagem

        async def enviar(mensagem):
            nonlocal respondeu
            if mensagem["type"] == "http.response.start":
                respondeu = True
                mensagem = {**mensagem, "headers": [*mensagem.get("headers", []), *CABECALHOS_SEGURANCA]}
            await send(mensagem)

        try:
            await self.app(scope, receber, enviar)
        except CorpoGrande:
            if not respondeu:
                await _responder(send, 413, _mensagem_tamanho(self.max_arquivo))

    def _ip(self, scope) -> str:
        if self.cabecalho_ip:
            valor = _cabecalho(scope, self.cabecalho_ip)
            if valor:
                return valor.split(",")[0].strip()
        cliente = scope.get("client")
        return cliente[0] if cliente else "desconhecido"


def _cabecalho(scope, nome: bytes) -> str | None:
    for chave, valor in scope.get("headers", []):
        if chave == nome:
            return valor.decode("latin-1")
    return None


def _mensagem_tamanho(maximo: int) -> str:
    return f"O arquivo passa de {maximo // 1024**2} MB, o máximo do site público. Rode o hub na sua máquina para arquivos maiores."


async def _responder(send, status: int, erro: str) -> None:
    corpo = json.dumps({"erro": erro}, ensure_ascii=False).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(corpo)).encode()),
                            *CABECALHOS_SEGURANCA]})
    await send({"type": "http.response.body", "body": corpo})
