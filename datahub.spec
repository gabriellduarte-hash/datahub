# Receita do PyInstaller: um executável só, com Python, o motor, a API e a interface.
#
#   pyinstaller datahub.spec      →  dist/datahub (ou dist/datahub.exe no Windows)
#
# Antes, exporte a interface para web/out (veja abrir_datahub.py).
# O GitHub Actions faz isso para Windows, macOS e Linux: .github/workflows/executavel.yml

from PyInstaller.utils.hooks import collect_submodules

analise = Analysis(
    ["abrir_datahub.py"],
    pathex=["."],
    datas=[
        ("web/out", "web"),                 # a interface exportada
        ("engine/dados", "engine/dados"),   # cotações do BCE para o CONVERTER
    ],
    # O uvicorn carrega partes dele pelo nome, em texto; o PyInstaller não as acha sozinho.
    hiddenimports=collect_submodules("uvicorn"),
    excludes=["tkinter", "pytest", "IPython"],
)
pacote = PYZ(analise.pure)
executavel = EXE(
    pacote,
    analise.scripts,
    analise.binaries,
    analise.datas,
    name="datahub",
    console=True,   # a janela mostra o endereço e encerra o hub ao ser fechada
    upx=False,
)
