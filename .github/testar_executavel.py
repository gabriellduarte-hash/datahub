"""Teste rápido de um executável recém-montado: abre, responde, cria a sessão de exemplo e fecha.

    python .github/testar_executavel.py dist/datahub
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

ENDERECO = "http://127.0.0.1:8765"


def pedir(caminho: str, metodo: str = "GET") -> tuple[int, bytes]:
    pedido = urllib.request.Request(ENDERECO + caminho, method=metodo)
    with urllib.request.urlopen(pedido, timeout=120) as resposta:
        return resposta.status, resposta.read()


def main(executavel: str) -> None:
    processo = subprocess.Popen([executavel], env={**os.environ, "BROWSER": "true"})
    try:
        for _ in range(120):
            try:
                pedir("/api/saude")
                break
            except OSError:
                time.sleep(1)
        else:
            raise SystemExit("O executável não respondeu em 2 minutos.")
        status, pagina = pedir("/")
        assert status == 200 and b"<title>datahub</title>" in pagina, "a interface não veio"
        status, corpo = pedir("/api/sessoes/exemplo", "POST")
        tabela = json.loads(corpo)["tabelas"][0]
        assert status == 200 and tabela["arquivo"]["linhas"] > 0, "a sessão de exemplo falhou"
        print(f"ok: interface e sessão de exemplo com {tabela['arquivo']['linhas']} linhas")
    finally:
        processo.terminate()
        processo.wait(timeout=30)


if __name__ == "__main__":
    main(sys.argv[1])
