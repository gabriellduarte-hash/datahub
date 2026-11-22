"""Prepara o motor para o site estático: o código Python num zip e as bibliotecas que o Pyodide não traz.

    python navegador/empacotar.py      →  web/public/motor/{manifesto.json, datahub-<hash>.zip, rodas/*.whl}

O Web Worker (web/public/motor/motor.js) lê o manifesto, baixa o zip e as
rodas e importa navegador/ponte.py. O nome do zip leva um resumo do conteúdo:
quando o código muda, o navegador baixa o novo em vez de usar o guardado.
"""

import hashlib
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "web" / "public" / "motor"
PASTAS = ["api", "engine", "samples", "navegador"]
# Puras em Python e fora do Pyodide 314.0.7; o resto (duckdb, fastapi, pydantic...) vem dele.
RODAS = ["python-multipart==0.0.33", "openpyxl==3.1.5", "et_xmlfile==2.0.0"]


def arquivos():
    for pasta in PASTAS:
        for caminho in sorted((RAIZ / pasta).rglob("*")):
            relativo = caminho.relative_to(RAIZ)
            if caminho.is_file() and "__pycache__" not in relativo.parts and caminho.suffix in {".py", ".gz"}:
                if relativo.parts[0] == "samples" and caminho.name not in {"__init__.py", "gerar_vendas.py"}:
                    continue  # benchmark e dados grandes ficam de fora
                yield caminho, relativo


def main() -> None:
    for antigo in DESTINO.glob("datahub-*.zip"):
        antigo.unlink()
    shutil.rmtree(DESTINO / "rodas", ignore_errors=True)

    pacote = io.BytesIO()
    with zipfile.ZipFile(pacote, "w", zipfile.ZIP_DEFLATED) as zip_:
        for caminho, relativo in arquivos():
            info = zipfile.ZipInfo(relativo.as_posix(), date_time=(2026, 1, 1, 0, 0, 0))  # data fixa: o resumo só muda com o conteúdo
            info.compress_type = zipfile.ZIP_DEFLATED
            zip_.writestr(info, caminho.read_bytes())
    conteudo = pacote.getvalue()
    nome_zip = f"datahub-{hashlib.sha256(conteudo).hexdigest()[:12]}.zip"
    DESTINO.mkdir(parents=True, exist_ok=True)
    (DESTINO / nome_zip).write_bytes(conteudo)

    subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--no-deps", "--only-binary=:all:",
                    "--dest", str(DESTINO / "rodas"), *RODAS], check=True)
    rodas = sorted(f"rodas/{roda.name}" for roda in (DESTINO / "rodas").glob("*.whl"))
    (DESTINO / "manifesto.json").write_text(json.dumps({"codigo": nome_zip, "rodas": rodas}, indent=1))
    print(f"{nome_zip}: {len(conteudo) // 1024} KB; rodas: {', '.join(r.split('/')[1] for r in rodas)}")


if __name__ == "__main__":
    main()
