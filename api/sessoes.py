"""Uma pasta por sessão: o arquivo enviado, o banco DuckDB e o estado em JSON.

As sessões não têm conta nem login, e somem depois de algumas horas.
"""

import json
import os
import re
import secrets
import shutil
import threading
import time
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

import duckdb
from fastapi.encoders import jsonable_encoder

from engine.limites import LOCAL, PUBLICO

PASTA = Path(os.environ.get("DATAHUB_SESSOES", Path(__file__).parent.parent / ".sessoes"))
LIMITES = PUBLICO if os.environ.get("DATAHUB_MODO") == "publico" else LOCAL
HORAS_DE_VIDA = 24
FORMATO_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")

_travas = defaultdict(threading.Lock)


class SessaoNaoEncontrada(Exception):
    pass


def criar() -> str:
    limpar_antigas()
    sessao = secrets.token_urlsafe(18)
    (PASTA / sessao).mkdir(parents=True)
    return sessao


def pasta(sessao: str) -> Path:
    """Valida o id antes de virar caminho: ele vem da URL."""
    if not FORMATO_ID.fullmatch(sessao) or not (PASTA / sessao).is_dir():
        raise SessaoNaoEncontrada(sessao)
    return PASTA / sessao


@contextmanager
def abrir(sessao: str, acesso_a_arquivos: bool = False):
    """Conexão com o banco da sessão, uma por vez.

    Fora da leitura e da exportação, o DuckDB fica sem acesso ao disco. Todo o
    SQL é montado com identificadores protegidos; isto é uma segunda barreira.
    """
    configuracao = {"memory_limit": LIMITES.memoria_max}
    if not acesso_a_arquivos:
        configuracao["enable_external_access"] = "false"
    with _travas[sessao]:
        con = duckdb.connect(str(pasta(sessao) / "dados.duckdb"), config=configuracao)
        try:
            yield con
        finally:
            con.close()


def ler_estado(sessao: str) -> dict:
    return json.loads((pasta(sessao) / "estado.json").read_text(encoding="utf-8"))


def salvar_estado(sessao: str, estado: dict) -> None:
    salvar_json(pasta(sessao) / "estado.json", estado)


def salvar_json(caminho: Path, dados) -> None:
    caminho.write_text(json.dumps(jsonable_encoder(dados), ensure_ascii=False, indent=1), encoding="utf-8")


def apagar(sessao: str) -> None:
    shutil.rmtree(PASTA / sessao, ignore_errors=True)
    _travas.pop(sessao, None)


def limpar_antigas() -> None:
    if not PASTA.is_dir():
        return
    limite = time.time() - HORAS_DE_VIDA * 3600
    for item in PASTA.iterdir():
        if item.is_dir() and item.stat().st_mtime < limite:
            apagar(item.name)
