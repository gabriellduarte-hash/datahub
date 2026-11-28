"""API do hub. Fina de propósito: valida o pedido, chama a engine e devolve JSON.

Uma sessão é uma área de trabalho com várias tabelas (engine/area.py). Toda
rota que muda alguma coisa devolve o estado inteiro da sessão, para a tela se
redesenhar com uma resposta só.
"""

import json
import math
import os
import re
import secrets
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api import sessoes
from api.publico import Protecao
from api.sessoes import LIMITES, SessaoNaoEncontrada, ServidorOcupado
from engine import area as areas
from engine import cambio, comparacao, consulta, formulas, graficos, relacional
from engine.area import NaoEncontrado
from engine.comparacao import CopiaRecusada
from engine.consulta import ConsultaRecusada
from engine.formulas import FormulaInvalida
from engine.leitura import ArquivoInvalido
from engine.limites import LimiteExcedido, numero_br
from engine.manipulacao import PassoInvalido, contar, pagina
from engine.relacional import JuncaoRecusada
from engine.sql import LINHA, colunas_de, ident
from samples.gerar_vendas import gerar

app = FastAPI(title="Tabula rasa")
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("DATAHUB_ORIGENS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)
if sessoes.PUBLICO_ATIVO:
    app.add_middleware(
        Protecao,
        max_arquivo=LIMITES.max_bytes_arquivo,
        pedidos_por_minuto=sessoes._numero("DATAHUB_PEDIDOS_MINUTO", 600),
        sessoes_por_hora=sessoes._numero("DATAHUB_SESSOES_HORA", 20),
        cabecalho_ip=os.environ.get("DATAHUB_IP_CABECALHO"),
    )

LINHAS_EXEMPLO = 5_000
PEDACO_UPLOAD = 1024 * 1024
NOMES_LIMITE = {
    "bytes_arquivo": "O arquivo tem {valor} bytes, e o máximo é {maximo}.",
    "colunas": "O arquivo tem {valor} colunas, e o máximo é {maximo}.",
    "linhas": "O arquivo tem {valor} linhas, e o máximo é {maximo}.",
    "celulas": "O arquivo tem {valor} células (linhas × colunas), e o máximo é {maximo}. "
               "Importe menos colunas.",
}


# ----- pedidos -----

class PedidoNormalizar(BaseModel):
    receita: list[dict]
    papeis: dict[str, str] = {}


class PedidoPasso(BaseModel):
    passo: dict


class PedidoFormula(BaseModel):
    nome: str
    formula: str = ""
    substituir: bool = False
    vazia: bool = False


class PedidoPapeis(BaseModel):
    papeis: dict[str, str]


class PedidoAba(BaseModel):
    aba: str


class PedidoNome(BaseModel):
    nome: str


class PedidoAtiva(BaseModel):
    tabela: str


class PedidoBase(BaseModel):
    tipo: str
    id: str


class PedidoGrafico(BaseModel):
    tipo: str
    x: str | None = None
    y: str | None = None
    agregacao: str | None = None
    grao: str | None = None


class PedidoSql(BaseModel):
    sql: str
    inicio: int = 0
    quantas: int = 100


class PedidoSalvarSql(BaseModel):
    sql: str
    nome: str


class PedidoConsulta(BaseModel):
    nome: str
    sql: str
    id: str | None = None


class PontaRelacao(BaseModel):
    tabela: str
    coluna: str


class PedidoRelacao(BaseModel):
    de: PontaRelacao
    para: PontaRelacao


class PedidoVisao(BaseModel):
    nome: str
    tabela: str
    juncoes: list[dict]


class PedidoCombinar(BaseModel):
    tabelas: list[str]
    nome: str = "combinada"
    sem_duplicatas: bool = False


class PedidoCopiarReceita(BaseModel):
    origem: str


# ----- erros -----

@app.exception_handler(SessaoNaoEncontrada)
def sessao_nao_encontrada(_: Request, __: SessaoNaoEncontrada):
    return JSONResponse({"erro": "Sessão não encontrada. Ela pode ter expirado: envie o arquivo de novo."},
                        status_code=404)


@app.exception_handler(ServidorOcupado)
def servidor_ocupado(_: Request, erro: ServidorOcupado):
    mensagem = ("O hub está cheio agora, com muitas sessões abertas. Tente de novo em alguns minutos."
                if erro.motivo == "sessoes" else
                "O servidor está ocupado com outras análises. Tente de novo em alguns segundos.")
    return JSONResponse({"erro": mensagem}, status_code=503, headers={"Retry-After": "30"})


@app.exception_handler(NaoEncontrado)
def nao_encontrado(_: Request, erro: NaoEncontrado):
    return JSONResponse({"erro": str(erro)}, status_code=404)


@app.exception_handler(ArquivoInvalido)
def arquivo_invalido(_: Request, erro: ArquivoInvalido):
    return JSONResponse({"erro": str(erro)}, status_code=422)


@app.exception_handler(LimiteExcedido)
def limite_excedido(_: Request, erro: LimiteExcedido):
    mensagem = NOMES_LIMITE[erro.limite].format(valor=numero_br(erro.valor), maximo=numero_br(erro.maximo))
    return JSONResponse({"erro": mensagem, "limite": erro.limite}, status_code=413)


@app.exception_handler(PassoInvalido)
@app.exception_handler(FormulaInvalida)
@app.exception_handler(ConsultaRecusada)
@app.exception_handler(JuncaoRecusada)
@app.exception_handler(CopiaRecusada)
def pedido_recusado(_: Request, erro: ValueError):
    return JSONResponse({"erro": str(erro)}, status_code=422)


@app.exception_handler(HTTPException)
def erro_http(_: Request, erro: HTTPException):
    return JSONResponse({"erro": erro.detail}, status_code=erro.status_code)


@app.get("/api/saude")
def saude():
    """Também diz à tela em que modo o hub roda, para ela mostrar os limites certos."""
    return {"ok": True, "modo": sessoes.MODO,
            "horas_de_vida": sessoes.HORAS_DE_VIDA, "limites": {
        "max_bytes_arquivo": LIMITES.max_bytes_arquivo,
        "max_linhas": LIMITES.max_linhas,
        "max_colunas": LIMITES.max_colunas,
        "max_celulas": LIMITES.max_celulas,
    }}


# ----- sessão e arquivos -----

def _gravar(arquivo: UploadFile, destino: Path) -> None:
    """Grava em pedaços para o limite de tamanho valer durante o envio."""
    maximo = LIMITES.max_bytes_arquivo
    escritos = 0
    with destino.open("wb") as saida:
        while pedaco := arquivo.file.read(PEDACO_UPLOAD):
            escritos += len(pedaco)
            if maximo is not None and escritos > maximo:
                raise LimiteExcedido("bytes_arquivo", escritos, maximo)
            saida.write(pedaco)


def _guardar_upload(sessao: str, arquivo: UploadFile) -> tuple[Path, str]:
    nome = Path(arquivo.filename or "arquivo").name
    destino = sessoes.pasta(sessao) / f"arquivo_{secrets.token_hex(4)}{Path(nome).suffix.lower()}"
    try:
        _gravar(arquivo, destino)
    except Exception:
        destino.unlink(missing_ok=True)
        raise
    return destino, nome


def _criar_sessao_com(adicionar) -> dict:
    sessao = sessoes.criar()
    try:
        estado = areas.nova(sessao)
        sessoes.salvar_estado(sessao, estado)
        with sessoes.trabalhar(sessao, acesso_a_arquivos=True) as (con, estado):
            adicionar(con, estado)
            return estado
    except Exception:
        sessoes.apagar(sessao)
        raise


@app.post("/api/sessoes")
def enviar_arquivo(arquivo: UploadFile = File(...), aba: str | None = Form(None)):
    """Sessão nova com um arquivo (blocos 1 a 3)."""
    def adicionar(con, estado):
        destino, nome = _guardar_upload(estado["sessao"], arquivo)
        areas.adicionar_arquivo(con, estado, destino, nome, LIMITES, aba)
    return _criar_sessao_com(adicionar)


@app.post("/api/sessoes/exemplo")
def abrir_exemplo():
    def adicionar(con, estado):
        caminho, _ = gerar(sessoes.pasta(estado["sessao"]), linhas=LINHAS_EXEMPLO)
        areas.adicionar_arquivo(con, estado, caminho, caminho.name, LIMITES)
    return _criar_sessao_com(adicionar)


@app.get("/api/sessoes/{sessao}")
def ver_sessao(sessao: str):
    return sessoes.ler_estado(sessao)


@app.delete("/api/sessoes/{sessao}")
def encerrar(sessao: str):
    sessoes.pasta(sessao)
    sessoes.apagar(sessao)
    return {"ok": True}


@app.post("/api/sessoes/{sessao}/tabelas")
def adicionar_arquivo(sessao: str, arquivo: UploadFile = File(...), aba: str | None = Form(None)):
    """Mais um arquivo na mesma sessão: vira uma tabela nova."""
    destino, nome = _guardar_upload(sessao, arquivo)
    try:
        with sessoes.trabalhar(sessao, acesso_a_arquivos=True) as (con, estado):
            areas.adicionar_arquivo(con, estado, destino, nome, LIMITES, aba)
            return estado
    except Exception:
        destino.unlink(missing_ok=True)
        raise


@app.post("/api/sessoes/{sessao}/ativa")
def escolher_ativa(sessao: str, pedido: PedidoAtiva):
    with sessoes.trabalhar(sessao) as (_, estado):
        areas.tabela(estado, pedido.tabela)
        estado["ativa"] = pedido.tabela
        return estado


@app.post("/api/sessoes/{sessao}/tabelas/{tabela}/nome")
def renomear_tabela(sessao: str, tabela: str, pedido: PedidoNome):
    with sessoes.trabalhar(sessao) as (con, estado):
        areas.renomear_tabela(con, estado, tabela, pedido.nome)
        return estado


@app.delete("/api/sessoes/{sessao}/tabelas/{tabela}")
def remover_tabela(sessao: str, tabela: str):
    with sessoes.trabalhar(sessao) as (con, estado):
        item = areas.tabela(estado, tabela)
        areas.remover_tabela(con, estado, tabela)
        if item.get("caminho") and not any(t.get("caminho") == item["caminho"] for t in estado["tabelas"]):
            (sessoes.pasta(sessao) / item["caminho"]).unlink(missing_ok=True)
        return estado


def _arquivo_da_tabela(sessao: str, item: dict) -> Path:
    if not item.get("caminho") or not item["caminho"].lower().endswith(".xlsx"):
        raise HTTPException(409, "Só planilhas Excel têm abas.")
    return sessoes.pasta(sessao) / item["caminho"]


@app.post("/api/sessoes/{sessao}/tabelas/{tabela}/aba")
def trocar_aba(sessao: str, tabela: str, pedido: PedidoAba):
    """Relê a tabela de outra aba do mesmo Excel. O que veio da aba anterior é descartado."""
    with sessoes.trabalhar(sessao, acesso_a_arquivos=True) as (con, estado):
        item = areas.tabela(estado, tabela)
        areas.trocar_aba(con, estado, tabela, _arquivo_da_tabela(sessao, item), pedido.aba, LIMITES)
        return estado


@app.post("/api/sessoes/{sessao}/tabelas/{tabela}/abas")
def carregar_abas(sessao: str, tabela: str):
    """Cada aba do Excel que ainda não foi lida vira uma tabela."""
    with sessoes.trabalhar(sessao, acesso_a_arquivos=True) as (con, estado):
        item = areas.tabela(estado, tabela)
        caminho = _arquivo_da_tabela(sessao, item)
        lidas = {t["arquivo"].get("aba") for t in estado["tabelas"] if t.get("caminho") == item["caminho"]}
        for aba in item["arquivo"].get("abas") or []:
            if aba not in lidas:
                areas.adicionar_arquivo(con, estado, caminho, item["arquivo"]["arquivo"], LIMITES, aba)
        estado["ativa"] = tabela
        return estado


# ----- normalização e sandbox -----

def _precisa_do_cambio(passos) -> bool:
    return any(re.search(r"CONVERT", str(p.get("formula") or ""), re.I) for p in passos)


def _reanalisar_se_pequena(con, estado: dict) -> None:
    """Base pequena se reanalisa sozinha; a grande espera o clique em Reanalisar."""
    if estado.get("base") and (estado.get("analise") is None or areas.pode_reanalisar_sozinho(estado, LIMITES)):
        areas.analisar(con, estado, LIMITES)


@app.post("/api/sessoes/{sessao}/tabelas/{tabela}/normalizar")
def normalizar(sessao: str, tabela: str, pedido: PedidoNormalizar):
    """Aplica a receita marcada e refaz a análise se a base depende desta tabela."""
    with sessoes.trabalhar(sessao, acesso_a_arquivos=_precisa_do_cambio(pedido.receita)) as (con, estado):
        areas.normalizar(con, estado, tabela, pedido.receita, pedido.papeis, LIMITES)
        if tabela in areas.dependencias(estado, estado["base"]):
            areas.analisar(con, estado, LIMITES)
        return estado


@app.post("/api/sessoes/{sessao}/tabelas/{tabela}/passos")
def acrescentar_passo(sessao: str, tabela: str, pedido: PedidoPasso):
    """Um passo da sandbox: editar, apagar, filtrar para valer, renomear, fórmula."""
    cambio_necessario = _precisa_do_cambio([pedido.passo])
    with sessoes.trabalhar(sessao, acesso_a_arquivos=cambio_necessario) as (con, estado):
        if cambio_necessario:
            cambio.garantir(con)
        resultado = areas.acrescentar_passo(con, estado, tabela, pedido.passo)
        _reanalisar_se_pequena(con, estado)
        return {**estado, "ultimo_passo": resultado}


@app.post("/api/sessoes/{sessao}/tabelas/{tabela}/desfazer")
def desfazer(sessao: str, tabela: str):
    with sessoes.trabalhar(sessao) as (con, estado):
        areas.desfazer(con, estado, tabela, LIMITES)
        _reanalisar_se_pequena(con, estado)
        return estado


@app.post("/api/sessoes/{sessao}/tabelas/{tabela}/formula")
def previa_da_formula(sessao: str, tabela: str, pedido: PedidoFormula):
    """Mostra o resultado da fórmula nas primeiras linhas, sem mudar nada."""
    cambio_necessario = _precisa_do_cambio([pedido.model_dump()])
    with sessoes.trabalhar(sessao, acesso_a_arquivos=cambio_necessario, salvar=False) as (con, estado):
        if cambio_necessario:
            cambio.garantir(con)
        item = areas.tabela(estado, tabela)
        return formulas.previa(con, areas.tabela_fisica(item), pedido.model_dump())


def _filtros(texto: str | None) -> list[dict] | None:
    if not texto:
        return None
    try:
        filtros = json.loads(texto)
    except json.JSONDecodeError as erro:
        raise HTTPException(422, "Filtros inválidos.") from erro
    if not isinstance(filtros, list) or not all(isinstance(f, dict) for f in filtros):
        raise HTTPException(422, "Filtros inválidos.")
    return filtros


@app.get("/api/sessoes/{sessao}/tabelas/{tabela}/linhas")
def ver_linhas(sessao: str, tabela: str, inicio: int = Query(0, ge=0), quantas: int = Query(100, ge=1, le=500),
               ordenar: str | None = None, decrescente: bool = False, filtros: str | None = None,
               bruta: bool = False):
    """Uma faixa de linhas, com o número de cada uma. A planilha nunca recebe a tabela inteira."""
    with sessoes.trabalhar(sessao, salvar=False) as (con, estado):
        item = areas.tabela(estado, tabela)
        fonte = areas.nomes(tabela)[0] if bruta else areas.tabela_fisica(item)
        resposta = pagina(con, fonte, inicio, quantas, ordenar, decrescente, _filtros(filtros))
    resposta["linhas"] = [[_para_json(valor) for valor in linha] for linha in resposta["linhas"]]
    return resposta


@app.get("/api/sessoes/{sessao}/tabelas/{tabela}/contar")
def contar_termo(sessao: str, tabela: str, coluna: str, termo: str, inteiro: bool = False,
                 filtros: str | None = None):
    with sessoes.trabalhar(sessao, salvar=False) as (con, estado):
        item = areas.tabela(estado, tabela)
        return contar(con, areas.tabela_fisica(item), coluna, termo, inteiro, _filtros(filtros))


# ----- base de análise -----

@app.post("/api/sessoes/{sessao}/base")
def escolher_base(sessao: str, pedido: PedidoBase):
    with sessoes.trabalhar(sessao) as (con, estado):
        areas.escolher_base(estado, pedido.model_dump())
        areas.analisar(con, estado, LIMITES)
        return estado


@app.post("/api/sessoes/{sessao}/reanalisar")
def reanalisar(sessao: str):
    with sessoes.trabalhar(sessao) as (con, estado):
        areas.analisar(con, estado, LIMITES)
        return estado


class PedidoGrao(BaseModel):
    grao: str | None = None


@app.post("/api/sessoes/{sessao}/grao")
def trocar_grao(sessao: str, pedido: PedidoGrao):
    """O que cada linha representa (acontecimento, pessoa ou coisa, retrato repetido); None volta ao detectado."""
    with sessoes.trabalhar(sessao) as (con, estado):
        areas.trocar_grao(estado, pedido.grao)
        areas.analisar(con, estado, LIMITES)
        return estado


@app.post("/api/sessoes/{sessao}/papeis")
def trocar_papeis(sessao: str, pedido: PedidoPapeis):
    """Refaz gráficos e insights com os papéis escolhidos para a base."""
    with sessoes.trabalhar(sessao) as (con, estado):
        areas.trocar_papeis(estado, pedido.papeis)
        areas.analisar(con, estado, LIMITES)
        return estado


@app.post("/api/sessoes/{sessao}/grafico")
def calcular_grafico(sessao: str, pedido: PedidoGrafico):
    with sessoes.trabalhar(sessao, salvar=False) as (con, estado):
        if not estado.get("analise"):
            raise HTTPException(409, "Escolha uma base e analise antes de editar gráficos.")
        base = areas.descrever_base(estado)
        nomes = {nome for nome, _ in colunas_de(con, base["nome"])}
        for coluna in (pedido.x, pedido.y):
            if coluna is not None and coluna not in nomes:
                raise HTTPException(422, f"A coluna {coluna} não existe.")
        try:
            return graficos.calcular(con, base["nome"], base["unidades"], LIMITES, pedido.model_dump(), "editado")
        except (ValueError, duckdb.Error) as erro:
            if pedido.agregacao == "taxa":
                raise HTTPException(422, "O cálculo % de sim só vale para colunas de sim/não.") from erro
            raise HTTPException(422, "Não consegui calcular esse gráfico com essas colunas.") from erro


# ----- gráficos que o usuário monta -----

@app.post("/api/sessoes/{sessao}/graficos-livres")
def criar_grafico_livre(sessao: str, pedido: PedidoGrafico):
    with sessoes.trabalhar(sessao) as (con, estado):
        areas.salvar_grafico_livre(con, estado, pedido.model_dump(), LIMITES)
        return estado


@app.put("/api/sessoes/{sessao}/graficos-livres/{grafico_id}")
def trocar_grafico_livre(sessao: str, grafico_id: str, pedido: PedidoGrafico):
    with sessoes.trabalhar(sessao) as (con, estado):
        if not any(item["id"] == grafico_id for item in estado.get("graficos_livres", [])):
            raise HTTPException(404, "Esse gráfico não existe mais.")
        areas.salvar_grafico_livre(con, estado, pedido.model_dump(), LIMITES, grafico_id)
        return estado


@app.delete("/api/sessoes/{sessao}/graficos-livres/{grafico_id}")
def remover_grafico_livre(sessao: str, grafico_id: str):
    with sessoes.trabalhar(sessao) as (_, estado):
        areas.remover_grafico_livre(estado, grafico_id)
        return estado


# ----- SQL -----

def _versao_dos_dados(estado: dict) -> str:
    return ";".join(f"{t['id']}:{t['versao']}" for t in estado["tabelas"]) + f";v{len(estado['visoes'])}"


@app.post("/api/sessoes/{sessao}/sql")
def rodar_sql(sessao: str, pedido: PedidoSql):
    """Roda uma consulta (só SELECT) e devolve uma página do resultado."""
    with sessoes.trabalhar(sessao) as (con, estado):
        guardada = (estado.get("ultima_consulta") or {}).get("chave")
        resultado = consulta.executar(con, pedido.sql, _versao_dos_dados(estado), guardada, pedido.inicio,
                                      pedido.quantas, LIMITES.timeout_consulta_s, LIMITES.max_linhas_consulta)
        estado["ultima_consulta"] = {"chave": resultado["chave"], "sql": pedido.sql}
    resultado["linhas"] = [[_para_json(valor) for valor in linha] for linha in resultado["linhas"]]
    return resultado


@app.post("/api/sessoes/{sessao}/sql/tabela")
def salvar_sql_como_tabela(sessao: str, pedido: PedidoSalvarSql):
    """O resultado da consulta vira uma tabela nova, que passa pela triagem como um arquivo."""
    with sessoes.trabalhar(sessao) as (con, estado):
        texto = consulta.validar(pedido.sql)
        try:
            areas.adicionar_tabela_pronta(con, estado, consulta.selecao_salvavel(texto), pedido.nome,
                                          f"Consulta: {pedido.nome}", "consulta", LIMITES, {"sql": texto})
        except duckdb.Error as erro:
            raise ConsultaRecusada("O banco recusou a consulta: " + str(erro).strip().splitlines()[0][:200]) from erro
        return estado


@app.post("/api/sessoes/{sessao}/consultas")
def salvar_consulta(sessao: str, pedido: PedidoConsulta):
    """Guarda uma consulta como arquivo .sql da sessão. Com `id`, atualiza a existente."""
    with sessoes.trabalhar(sessao) as (_, estado):
        texto = consulta.validar(pedido.sql)
        nome = re.sub(r"[^\w.-]+", "_", pedido.nome.strip()) or "consulta"
        existente = next((c for c in estado["consultas"] if c["id"] == pedido.id), None) if pedido.id else None
        if existente:
            existente.update(nome=nome, sql=texto)
        else:
            estado["contador"] += 1
            estado["consultas"].append({"id": f"c{estado['contador']}", "nome": nome, "sql": texto})
        return estado


@app.delete("/api/sessoes/{sessao}/consultas/{consulta_id}")
def apagar_consulta(sessao: str, consulta_id: str):
    with sessoes.trabalhar(sessao) as (_, estado):
        estado["consultas"] = [c for c in estado["consultas"] if c["id"] != consulta_id]
        return estado


@app.get("/api/sessoes/{sessao}/consultas/{consulta_id}/arquivo")
def baixar_consulta(sessao: str, consulta_id: str):
    estado = sessoes.ler_estado(sessao)
    item = next((c for c in estado["consultas"] if c["id"] == consulta_id), None)
    if not item:
        raise HTTPException(404, "Consulta não encontrada.")
    return PlainTextResponse(consulta.salvar_como_sql(item["sql"]), media_type="application/sql",
                             headers={"Content-Disposition": f'attachment; filename="{item["nome"]}.sql"'})


# ----- várias tabelas -----

@app.get("/api/sessoes/{sessao}/esquema")
def ver_esquema(sessao: str):
    with sessoes.trabalhar(sessao, salvar=False) as (con, estado):
        return relacional.esquema(con, estado)


@app.post("/api/sessoes/{sessao}/relacoes")
def criar_relacao(sessao: str, pedido: PedidoRelacao):
    with sessoes.trabalhar(sessao) as (con, estado):
        relacional.criar_relacao(con, estado, pedido.de.model_dump(), pedido.para.model_dump())
        return estado


@app.delete("/api/sessoes/{sessao}/relacoes/{relacao_id}")
def apagar_relacao(sessao: str, relacao_id: str):
    with sessoes.trabalhar(sessao) as (_, estado):
        if any(j["relacao"] == relacao_id for v in estado["visoes"] for j in v["juncoes"]):
            raise HTTPException(409, "Uma visão usa essa relação. Apague a visão antes.")
        estado["relacoes"] = [r for r in estado["relacoes"] if r["id"] != relacao_id]
        return estado


@app.post("/api/sessoes/{sessao}/visoes")
def criar_visao(sessao: str, pedido: PedidoVisao):
    """A visão unificada vira a base de análise, e gráficos e insights são refeitos."""
    with sessoes.trabalhar(sessao) as (con, estado):
        visao = relacional.criar_visao(con, estado, pedido.nome, pedido.tabela, pedido.juncoes)
        areas.escolher_base(estado, {"tipo": "visao", "id": visao["id"]})
        areas.analisar(con, estado, LIMITES)
        return estado


@app.delete("/api/sessoes/{sessao}/visoes/{visao_id}")
def apagar_visao(sessao: str, visao_id: str):
    with sessoes.trabalhar(sessao) as (con, estado):
        visao = next((v for v in estado["visoes"] if v["id"] == visao_id), None)
        if not visao:
            raise HTTPException(404, "Visão não encontrada.")
        con.execute(f"DROP VIEW IF EXISTS {ident(visao['nome'])}")
        estado["visoes"].remove(visao)
        if estado["base"] == {"tipo": "visao", "id": visao_id}:
            estado["base"], estado["analise"] = None, None
        return estado


@app.post("/api/sessoes/{sessao}/combinar")
def combinar(sessao: str, pedido: PedidoCombinar):
    """Arquivos com as mesmas colunas, um embaixo do outro, numa tabela nova."""
    with sessoes.trabalhar(sessao) as (con, estado):
        relacional.combinar(con, estado, pedido.tabelas, pedido.nome, pedido.sem_duplicatas)
        return estado


# ----- comparar arquivos -----

@app.get("/api/sessoes/{sessao}/comparacao")
def comparar_arquivos(sessao: str):
    """Colunas que batem, duplicatas entre os arquivos e que limpeza de um serve para o outro."""
    with sessoes.trabalhar(sessao, salvar=False) as (con, estado):
        return comparacao.comparar(con, estado)


@app.post("/api/sessoes/{sessao}/tabelas/{tabela}/copiar-receita")
def copiar_receita(sessao: str, tabela: str, pedido: PedidoCopiarReceita):
    """A tabela passa a ter a mesma limpeza da origem; os passos que não servem ficam de fora, com o motivo."""
    origem = areas.tabela(sessoes.ler_estado(sessao), pedido.origem)
    cambio_necessario = _precisa_do_cambio((origem.get("receita") or []) + origem["manipulacao"])
    with sessoes.trabalhar(sessao, acesso_a_arquivos=cambio_necessario) as (con, estado):
        if cambio_necessario:
            cambio.garantir(con)
        resultado = comparacao.copiar_receita(con, estado, pedido.origem, tabela, LIMITES)
        if tabela in areas.dependencias(estado, estado["base"]):
            areas.analisar(con, estado, LIMITES)
        return {**estado, "copia": resultado}


# ----- exportação -----

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


@app.get("/api/sessoes/{sessao}/tabelas/{tabela}/exportar/{formato}")
def exportar(sessao: str, tabela: str, formato: str):
    estado = sessoes.ler_estado(sessao)
    item = areas.tabela(estado, tabela)
    if not item.get("relatorio"):
        raise HTTPException(409, "Aplique a normalização antes de exportar.")

    base = re.sub(r"[^\w.-]+", "_", item["nome"]) or "dados"
    pasta = sessoes.pasta(sessao)
    if formato == "receita":
        destino = pasta / f"receita_{tabela}.json"
        sessoes.salvar_json(destino, (item["receita"] or []) + item["manipulacao"])
        return FileResponse(destino, media_type="application/json", filename=f"{base}_receita.json")

    if formato not in {"csv", "parquet"}:
        raise HTTPException(422, "Formato inválido.")
    destino = pasta / f"exportacao_{tabela}.{formato}"
    limpa = areas.nomes(tabela)[1]
    with sessoes.abrir(sessao, acesso_a_arquivos=True) as con:
        if formato == "parquet":
            selecao = _selecao_exportada(con, limpa, None)
            con.execute(f"COPY ({selecao}) TO '{_sem_aspas(destino)}' (FORMAT parquet)")
        else:
            _exportar_csv(con, limpa, destino)
    tipo = "text/csv" if formato == "csv" else "application/octet-stream"
    return FileResponse(destino, media_type=tipo, filename=f"{base}_limpo.{formato}")


def _selecao_exportada(con, tabela: str, partes: list[str] | None) -> str:
    """As colunas do usuário, na ordem do arquivo original."""
    colunas = partes or [ident(nome) for nome, _ in colunas_de(con, tabela)]
    ordem = f" ORDER BY {ident(LINHA)}" if LINHA in dict(colunas_de(con, tabela, internas=True)) else ""
    return f"SELECT {', '.join(colunas)} FROM {ident(tabela)}{ordem}"


def _exportar_csv(con, tabela: str, destino: Path) -> None:
    """CSV do jeito que o Excel brasileiro abre: ";", vírgula decimal e marca de UTF-8."""
    partes = []
    for nome, tipo in colunas_de(con, tabela):
        c = ident(nome)
        if tipo.startswith("DECIMAL") or tipo in {"DOUBLE", "FLOAT"}:
            partes.append(f"replace(CAST({c} AS VARCHAR), '.', ',') AS {c}")
        else:
            partes.append(c)
    temporario = destino.with_suffix(".tmp")
    con.execute(
        f"COPY ({_selecao_exportada(con, tabela, partes)}) TO '{_sem_aspas(temporario)}' "
        "(FORMAT csv, HEADER, DELIMITER ';')"
    )
    with destino.open("wb") as saida, temporario.open("rb") as entrada:
        saida.write(b"\xef\xbb\xbf")
        shutil.copyfileobj(entrada, saida)
    temporario.unlink()


def _sem_aspas(caminho: Path) -> str:
    return str(caminho).replace("'", "''")


# ----- a interface -----
# Na imagem de produção, a interface vira arquivos estáticos (next build com
# DATAHUB_EXPORTAR=1) e a própria API os serve: um endereço só, sem CORS.
# Montada por último, para as rotas /api/ terem a vez.
PASTA_WEB = os.environ.get("DATAHUB_WEB")
if PASTA_WEB and (Path(PASTA_WEB) / "index.html").is_file():
    app.mount("/", StaticFiles(directory=PASTA_WEB, html=True), name="web")
