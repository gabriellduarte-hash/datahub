"""Tempo-limite para o trabalho no DuckDB.

Uma thread espera o prazo e interrompe a conexão. No navegador (Pyodide) não
existem threads: lá o relógio não arma, e quem segura o tempo é o tamanho dos
arquivos aceitos.
"""

import sys
import threading
from contextlib import contextmanager

TEM_THREADS = sys.platform != "emscripten"


@contextmanager
def tempo_limite(con, segundos: float):
    """Interrompe `con` se o bloco passar de `segundos` (DuckDB levanta InterruptException)."""
    if not TEM_THREADS:
        yield
        return
    relogio = threading.Timer(segundos, con.interrupt)
    relogio.start()
    try:
        yield
    finally:
        relogio.cancel()
