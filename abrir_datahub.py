"""Abre o datahub no seu computador: sobe a API, que também entrega a interface, e abre o navegador.

É o ponto de entrada do executável (veja datahub.spec) e também roda direto,
depois de exportar a interface:

    cd web && DATAHUB_EXPORTAR=1 NEXT_PUBLIC_API_URL= npm run build && cd ..
    .venv/bin/python abrir_datahub.py
"""

import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PORTA_PREFERIDA = 8765  # fixa quando possível: o navegador guarda a sessão por endereço
EMPACOTADO = hasattr(sys, "_MEIPASS")


def pasta_da_interface() -> Path:
    # No executável, o PyInstaller descompacta tudo numa pasta temporária (sys._MEIPASS).
    if EMPACOTADO:
        return Path(sys._MEIPASS) / "web"
    return Path(__file__).parent / "web" / "out"


def porta_livre() -> int:
    for porta in (PORTA_PREFERIDA, 0):  # 0 = o sistema escolhe uma livre
        with socket.socket() as teste:
            try:
                teste.bind(("127.0.0.1", porta))
            except OSError:
                continue
            return teste.getsockname()[1]
    raise SystemExit("Não achei uma porta livre no computador.")


def abrir_quando_pronto(endereco: str) -> None:
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{endereco}/api/saude", timeout=1)
        except OSError:
            time.sleep(0.2)
            continue
        webbrowser.open(endereco)
        return


def main() -> None:
    interface = pasta_da_interface()
    if not (interface / "index.html").is_file():
        raise SystemExit(f"A interface não está em {interface}. Exporte antes (veja o topo deste arquivo).")

    # Lidos quando a API é importada, então vêm antes do import.
    os.environ.setdefault("DATAHUB_WEB", str(interface))
    os.environ.setdefault("DATAHUB_SESSOES", str(Path(tempfile.gettempdir()) / "datahub-sessoes"))

    import uvicorn

    from api.main import app

    porta = porta_livre()
    endereco = f"http://127.0.0.1:{porta}"
    print(f"\n  datahub rodando em {endereco}\n"
          "  O navegador abre sozinho. Para encerrar, feche esta janela ou aperte Ctrl+C.\n"
          "  Seus arquivos não saem deste computador.\n", flush=True)
    threading.Thread(target=abrir_quando_pronto, args=(endereco,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=porta, log_level="warning")


if __name__ == "__main__":
    main()
