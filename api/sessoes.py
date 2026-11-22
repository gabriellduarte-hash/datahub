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

from dataclasses import replace

import duckdb
from fastapi.encoders import jsonable_encoder

from engine.limites import LOCAL, NAVEGADOR, PUBLICO


def _numero(nome: str, padrao: int) -> int:
    valor = os.environ.get(nome, "").strip()
    return int(valor) if valor else padrao


# Na sua máquina nada disso aperta. No site público (DATAHUB_MODO=publico) a
# máquina é pequena e dividida entre visitantes: arquivos menores, sessões mais
# curtas, poucas análises ao mesmo tempo. Cada número pode ser trocado por uma
# variável de ambiente, para caber na máquina contratada.
MODO = os.environ.get("DATAHUB_MODO", "local")  # local, publico ou navegador (navegador/ponte.py)
PUBLICO_ATIVO = MODO == "publico"
PASTA = Path(os.environ.get("DATAHUB_SESSOES", Path(__file__).parent.parent / ".sessoes"))
LIMITES = {"publico": PUBLICO, "navegador": NAVEGADOR}.get(MODO, LOCAL)
if os.environ.get("DATAHUB_MEMORIA"):
    LIMITES = replace(LIMITES, memoria_max=os.environ["DATAHUB_MEMORIA"])
HORAS_DE_VIDA = _numero("DATAHUB_HORAS", 6 if PUBLICO_ATIVO else 24)
MAX_SESSOES = _numero("DATAHUB_MAX_SESSOES", 150 if PUBLICO_ATIVO else 0)  # 0 = sem teto
TRABALHOS = _numero("DATAHUB_TRABALHOS", 2 if PUBLICO_ATIVO else 0)       # operações no DuckDB ao mesmo tempo; 0 = sem fila
THREADS = _numero("DATAHUB_THREADS", 2 if PUBLICO_ATIVO else 0)           # 0 = o DuckDB decide
ESPERA_S = _numero("DATAHUB_ESPERA", 30)                                  # quanto um pedido espera na fila
OCIOSA_S = 30 * 60  # com o teto cheio, sessões paradas há mais que isso dão lugar às novas
FORMATO_ID = re.compile(r"[A-Za-z0-9_-]{16,64}")

_travas = defaultdict(threading.RLock)


class SessaoNaoEncontrada(Exception):
    pass


class ServidorOcupado(Exception):
    """Fila cheia ou sessões demais. `motivo` é "fila" ou "sessoes"."""

    def __init__(self, motivo: str):
        self.motivo = motivo
        super().__init__(motivo)


class _Fila:
    """Quantas operações pesadas rodam juntas. Vale uma vaga por pedido, mesmo que ele abra o banco duas vezes."""

    def __init__(self, vagas: int):
        self.vagas = threading.BoundedSemaphore(vagas) if vagas else None
        self.local = threading.local()

    @contextmanager
    def vaga(self):
        profundidade = getattr(self.local, "profundidade", 0)
        if self.vagas is not None and profundidade == 0 and not self.vagas.acquire(timeout=ESPERA_S):
            raise ServidorOcupado("fila")
        self.local.profundidade = profundidade + 1
        try:
            yield
        finally:
            self.local.profundidade = profundidade
            if self.vagas is not None and profundidade == 0:
                self.vagas.release()


_fila = _Fila(TRABALHOS)


def criar() -> str:
    limpar_antigas()
    if MAX_SESSOES and _contar() >= MAX_SESSOES:
        _liberar_ociosas()
        if _contar() >= MAX_SESSOES:
            raise ServidorOcupado("sessoes")
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
    if THREADS:
        configuracao["threads"] = THREADS
    if not acesso_a_arquivos:
        configuracao["enable_external_access"] = "false"
    # Travada, a configuração não muda mais: nem um SQL do usuário religa o acesso ao disco.
    configuracao["lock_configuration"] = "true"
    with _fila.vaga(), _travas[sessao]:
        con = duckdb.connect(str(pasta(sessao) / "dados.duckdb"), config=configuracao)
        try:
            yield con
        finally:
            con.close()


@contextmanager
def trabalhar(sessao: str, acesso_a_arquivos: bool = False, salvar: bool = True):
    """Uma operação inteira: trava a sessão, abre o banco, lê o estado e salva no fim.

    Se a operação falha no meio, o estado não é salvo: a sessão fica como estava.
    """
    # Sempre a vaga na fila antes da trava da sessão, nos dois lugares: na ordem
    # inversa, dois pedidos podiam ficar esperando um pelo outro.
    with _fila.vaga(), _travas[sessao]:
        with abrir(sessao, acesso_a_arquivos) as con:
            estado = ler_estado(sessao)
            yield con, estado
            if salvar:
                salvar_estado(sessao, estado)


def ler_estado(sessao: str) -> dict:
    return json.loads((pasta(sessao) / "estado.json").read_text(encoding="utf-8"))


def salvar_estado(sessao: str, estado: dict) -> None:
    salvar_json(pasta(sessao) / "estado.json", estado)


def salvar_json(caminho: Path, dados) -> None:
    """Grava num arquivo ao lado e troca de uma vez: quem lê nunca pega o JSON pela metade."""
    temporario = caminho.with_name(f".{caminho.name}.{secrets.token_hex(4)}")
    temporario.write_text(json.dumps(jsonable_encoder(dados), ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temporario, caminho)


def apagar(sessao: str) -> None:
    shutil.rmtree(PASTA / sessao, ignore_errors=True)
    _travas.pop(sessao, None)


def _apagar_se_livre(sessao: str) -> bool:
    """Não apaga uma sessão no meio de uma operação."""
    trava = _travas[sessao]
    if not trava.acquire(blocking=False):
        return False
    try:
        apagar(sessao)
        return True
    finally:
        trava.release()


def _pastas() -> list[Path]:
    return [item for item in PASTA.iterdir() if item.is_dir()] if PASTA.is_dir() else []


def _contar() -> int:
    return len(_pastas())


def _usos() -> list[tuple[float, str]]:
    """(último uso, sessão), da mais parada para a mais recente.

    A pasta muda a cada estado salvo, então a data dela diz quando a sessão foi usada.
    """
    usos = []
    for item in _pastas():
        try:
            usos.append((item.stat().st_mtime, item.name))
        except FileNotFoundError:  # apagada por outro pedido no meio da listagem
            pass
    return sorted(usos)


def limpar_antigas() -> None:
    limite = time.time() - HORAS_DE_VIDA * 3600
    for usada_em, sessao in _usos():
        if usada_em < limite:
            _apagar_se_livre(sessao)


def _liberar_ociosas() -> None:
    limite = time.time() - OCIOSA_S
    for usada_em, sessao in _usos():
        if usada_em >= limite or _contar() < MAX_SESSOES:
            return
        _apagar_se_livre(sessao)
