"""API do hub. Fina de propósito: valida o pedido, chama a engine e devolve JSON."""

import math
import os
import re
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from api import sessoes
from api.sessoes import LIMITES, SessaoNaoEncontrada
from engine import graficos, pipeline
from engine.leitura import ArquivoInvalido
from engine.limites import LimiteExcedido, numero_br
from engine.normalizacao import unidades_da_receita
from engine.sql import colunas_de, ident
from samples.gerar_vendas import gerar

app = FastAPI(title="datahub")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("DATAHUB_ORIGENS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

TABELAS = {"bruta", "limpa"}
MAX_POR_PAGINA = 200
LINHAS_EXEMPLO = 5_000
PEDACO_UPLOAD = 1024 * 1024
NOMES_LIMITE = {
    "bytes_arquivo": "O arquivo tem {valor} bytes, e o máximo é {maximo}.",
    "colunas": "O arquivo tem {valor} colunas, e o máximo é {maximo}.",
    "linhas": "O arquivo tem {valor} linhas, e o máximo é {maximo}.",
    "celulas": "O arquivo tem {valor} células (linhas × colunas), e o máximo é {maximo}. "
               "Importe menos colunas.",
}


class PedidoNormalizar(BaseModel):
    receita: list[dict]
    papeis: dict[str, str] = {}


class PedidoPapeis(BaseModel):
    papeis: dict[str, str]


class PedidoAba(BaseModel):
    aba: str


class PedidoGrafico(BaseModel):
    tipo: str
    x: str | None = None
    y: str | None = None
    agregacao: str | None = None
    grao: str | None = None


@app.exception_handler(SessaoNaoEncontrada)
def sessao_nao_encontrada(_: Request, __: SessaoNaoEncontrada):
    return JSONResponse({"erro": "Sessão não encontrada. Ela pode ter expirado: envie o arquivo de novo."},
                        status_code=404)


@app.exception_handler(ArquivoInvalido)
def arquivo_invalido(_: Request, erro: ArquivoInvalido):
    return JSONResponse({"erro": str(erro)}, status_code=422)


@app.exception_handler(LimiteExcedido)
def limite_excedido(_: Request, erro: LimiteExcedido):
    mensagem = NOMES_LIMITE[erro.limite].format(valor=numero_br(erro.valor), maximo=numero_br(erro.maximo))
    return JSONResponse({"erro": mensagem, "limite": erro.limite}, status_code=413)


@app.exception_handler(HTTPException)
def erro_http(_: Request, erro: HTTPException):
    return JSONResponse({"erro": erro.detail}, status_code=erro.status_code)


@app.get("/api/saude")
def saude():
    return {"ok": True, "limites": {
        "max_bytes_arquivo": LIMITES.max_bytes_arquivo,
        "max_linhas": LIMITES.max_linhas,
        "max_colunas": LIMITES.max_colunas,
        "max_celulas": LIMITES.max_celulas,
    }}


@app.post("/api/sessoes")
def enviar_arquivo(arquivo: UploadFile = File(...), aba: str | None = Form(None)):
    """Blocos 1 a 3. O arquivo é gravado em pedaços para o limite valer durante o envio."""
    sessao = sessoes.criar()
    nome = Path(arquivo.filename or "arquivo").name
    destino = sessoes.pasta(sessao) / ("original" + Path(nome).suffix.lower())
    try:
        _gravar(arquivo, destino)
        return _abrir(sessao, destino, nome, aba)
    except Exception:
        sessoes.apagar(sessao)
        raise


@app.post("/api/sessoes/exemplo")
def abrir_exemplo():
    sessao = sessoes.criar()
    caminho, _ = gerar(sessoes.pasta(sessao), linhas=LINHAS_EXEMPLO)
    return _abrir(sessao, caminho, caminho.name, None)


def _gravar(arquivo: UploadFile, destino: Path) -> None:
    maximo = LIMITES.max_bytes_arquivo
    escritos = 0
    with destino.open("wb") as saida:
        while pedaco := arquivo.file.read(PEDACO_UPLOAD):
            escritos += len(pedaco)
            if maximo is not None and escritos > maximo:
                raise LimiteExcedido("bytes_arquivo", escritos, maximo)
            saida.write(pedaco)


def _abrir(sessao: str, caminho: Path, nome: str, aba: str | None) -> dict:
    with sessoes.abrir(sessao, acesso_a_arquivos=True) as con:
        resultado = pipeline.abrir(con, caminho, LIMITES, aba, nome)
    estado = {"sessao": sessao, "papeis": {}, **resultado}
    sessoes.salvar_estado(sessao, estado)
    return estado


@app.get("/api/sessoes/{sessao}")
def ver_sessao(sessao: str):
    return sessoes.ler_estado(sessao)


@app.post("/api/sessoes/{sessao}/aba")
def trocar_aba(sessao: str, pedido: PedidoAba):
    """Relê o Excel guardado na sessão. O que veio da aba anterior é descartado."""
    estado = sessoes.ler_estado(sessao)
    caminho = sessoes.pasta(sessao) / "original.xlsx"
    if not caminho.exists():
        raise HTTPException(409, "Só planilhas Excel têm abas.")
    return _abrir(sessao, caminho, estado["arquivo"]["arquivo"], pedido.aba)


@app.post("/api/sessoes/{sessao}/normalizar")
def normalizar(sessao: str, pedido: PedidoNormalizar):
    """Aplica a receita e refaz os blocos 4 e 5."""
    estado = sessoes.ler_estado(sessao)
    with sessoes.abrir(sessao) as con:
        resultado = pipeline.normalizar(con, pedido.receita, pedido.papeis, LIMITES, estado["blocos"])
    estado.update(resultado, papeis=pedido.papeis)
    sessoes.salvar_estado(sessao, estado)
    return estado


@app.post("/api/sessoes/{sessao}/papeis")
def trocar_papeis(sessao: str, pedido: PedidoPapeis):
    """Refaz gráficos e insights sem repetir a limpeza."""
    estado = sessoes.ler_estado(sessao)
    if not estado.get("relatorio"):
        raise HTTPException(409, "Aplique a normalização antes de trocar os papéis.")
    with sessoes.abrir(sessao) as con:
        resultado = pipeline.reanalisar(con, estado["receita"], estado["relatorio"], pedido.papeis,
                                        LIMITES, estado["blocos"])
    estado.update(resultado, papeis=pedido.papeis)
    sessoes.salvar_estado(sessao, estado)
    return estado


@app.post("/api/sessoes/{sessao}/grafico")
def calcular_grafico(sessao: str, pedido: PedidoGrafico):
    estado = sessoes.ler_estado(sessao)
    if not estado.get("relatorio"):
        raise HTTPException(409, "Aplique a normalização antes de editar gráficos.")
    unidades = unidades_da_receita(estado["receita"])
    with sessoes.abrir(sessao) as con:
        nomes = {nome for nome, _ in colunas_de(con, "limpa")}
        for coluna in (pedido.x, pedido.y):
            if coluna is not None and coluna not in nomes:
                raise HTTPException(422, f"A coluna {coluna} não existe.")
        try:
            return graficos.calcular(con, "limpa", unidades, LIMITES, pedido.model_dump(), "editado")
        except (ValueError, duckdb.Error) as erro:
            if pedido.agregacao == "taxa":
                raise HTTPException(422, "O cálculo % de sim só vale para colunas de sim/não.") from erro
            raise HTTPException(422, "Não consegui calcular esse gráfico com essas colunas.") from erro


@app.get("/api/sessoes/{sessao}/linhas")
def ver_linhas(sessao: str, tabela: str = "bruta", pagina: int = Query(1, ge=1),
               por_pagina: int = Query(50, ge=1, le=MAX_POR_PAGINA), ordenar: str | None = None,
               decrescente: bool = False):
    """Uma página de linhas. A interface nunca recebe a tabela inteira."""
    if tabela not in TABELAS:
        raise HTTPException(422, "Tabela inválida.")
    with sessoes.abrir(sessao) as con:
        if tabela == "limpa" and not con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'limpa'"
        ).fetchone()[0]:
            raise HTTPException(409, "Aplique a normalização primeiro.")
        colunas = colunas_de(con, tabela)
        ordem = ""
        if ordenar is not None:
            if ordenar not in {nome for nome, _ in colunas}:
                raise HTTPException(422, f"A coluna {ordenar} não existe.")
            ordem = f"ORDER BY {ident(ordenar)} {'DESC' if decrescente else 'ASC'} NULLS LAST"
        total = con.execute(f"SELECT count(*) FROM {tabela}").fetchone()[0]
        linhas = con.execute(
            f"SELECT * FROM {tabela} {ordem} LIMIT ? OFFSET ?", [por_pagina, (pagina - 1) * por_pagina]
        ).fetchall()
    return {
        "colunas": [{"nome": nome, "tipo": tipo} for nome, tipo in colunas],
        "linhas": [[_para_json(valor) for valor in linha] for linha in linhas],
        "total": total,
        "pagina": pagina,
        "por_pagina": por_pagina,
    }


def _para_json(valor):
    if isinstance(valor, float) and not math.isfinite(valor):
        return None
    if isinstance(valor, Decimal):
        return float(valor)
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, (int, float, str, bool)) or valor is None:
        return valor
    return str(valor)


@app.get("/api/sessoes/{sessao}/exportar/{formato}")
def exportar(sessao: str, formato: str):
    estado = sessoes.ler_estado(sessao)
    if not estado.get("relatorio"):
        raise HTTPException(409, "Aplique a normalização antes de exportar.")

    base = re.sub(r"[^\w.-]+", "_", Path(estado["arquivo"]["arquivo"]).stem) or "dados"
    pasta = sessoes.pasta(sessao)
    if formato == "receita":
        destino = pasta / "receita.json"
        sessoes.salvar_json(destino, estado["receita"])
        return FileResponse(destino, media_type="application/json", filename=f"{base}_receita.json")

    if formato not in {"csv", "parquet"}:
        raise HTTPException(422, "Formato inválido.")
    destino = pasta / f"exportacao.{formato}"
    with sessoes.abrir(sessao, acesso_a_arquivos=True) as con:
        if formato == "parquet":
            con.execute(f"COPY limpa TO '{_sem_aspas(destino)}' (FORMAT parquet)")
        else:
            _exportar_csv(con, destino)
    tipo = "text/csv" if formato == "csv" else "application/octet-stream"
    return FileResponse(destino, media_type=tipo, filename=f"{base}_limpo.{formato}")


def _exportar_csv(con, destino: Path) -> None:
    """CSV do jeito que o Excel brasileiro abre: ";", vírgula decimal e marca de UTF-8."""
    partes = []
    for nome, tipo in colunas_de(con, "limpa"):
        c = ident(nome)
        if tipo.startswith("DECIMAL") or tipo in {"DOUBLE", "FLOAT"}:
            partes.append(f"replace(CAST({c} AS VARCHAR), '.', ',') AS {c}")
        else:
            partes.append(c)
    temporario = destino.with_suffix(".tmp")
    con.execute(
        f"COPY (SELECT {', '.join(partes)} FROM limpa) TO '{_sem_aspas(temporario)}' "
        "(FORMAT csv, HEADER, DELIMITER ';')"
    )
    with destino.open("wb") as saida, temporario.open("rb") as entrada:
        saida.write(b"\xef\xbb\xbf")
        shutil.copyfileobj(entrada, saida)
    temporario.unlink()


def _sem_aspas(caminho: Path) -> str:
    return str(caminho).replace("'", "''")


@app.delete("/api/sessoes/{sessao}")
def encerrar(sessao: str):
    sessoes.pasta(sessao)
    sessoes.apagar(sessao)
    return {"ok": True}
