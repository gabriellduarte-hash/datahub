"""A área de trabalho de uma sessão: várias tabelas, a sandbox e a base de análise.

Cada tabela vive no banco com três nomes:

- `__bruta_t1`: o arquivo como chegou, com o número de cada linha. Nunca muda.
- `__limpa_t1`: o estado atual, depois da normalização e dos passos da sandbox.
- o nome visível (`it_customer_churn`): uma visão sem as colunas internas. É
  o nome que o usuário escreve no SQL e vê no esquema.

A receita de cada tabela tem duas partes: a da normalização, que o usuário
marca na lista de sugestões, e a da sandbox, que cresce a cada edição. Um passo
novo da sandbox é aplicado sobre a tabela atual, e a anterior fica guardada
como `__anterior_t1`: desfazer o último passo é só trocar uma pela outra.

Gráficos e insights olham para uma base só: uma tabela ou uma visão que junta
várias (veja engine/relacional.py). O estado inteiro é um dicionário simples,
que a API guarda em JSON.
"""

from dataclasses import asdict
from pathlib import Path

import duckdb

from engine import graficos, normalizacao, pipeline
from engine.limites import LOCAL, Limites
from engine.manipulacao import PassoInvalido
from engine.perfil import perfilar_tabela
from engine.sql import LINHA, colunas_de, ident, nome_simples


class NaoEncontrado(LookupError):
    """Tabela, visão ou consulta que não existe nesta sessão."""


def nova(sessao: str) -> dict:
    return {
        "sessao": sessao,
        "tabelas": [],
        "ativa": None,
        "base": None,          # {"tipo": "tabela" | "visao", "id": ...}
        "analise": None,
        "relacoes": [],
        "visoes": [],
        "consultas": [],
        "contador": 0,
    }


def nomes(tabela_id: str) -> tuple[str, str, str]:
    """(bruta, limpa, anterior) no banco."""
    return f"__bruta_{tabela_id}", f"__limpa_{tabela_id}", f"__anterior_{tabela_id}"


def tabela(area: dict, tabela_id: str) -> dict:
    for item in area["tabelas"]:
        if item["id"] == tabela_id:
            return item
    raise NaoEncontrado(f"Tabela {tabela_id} não encontrada nesta sessão.")


def _proximo_id(area: dict, prefixo: str) -> str:
    area["contador"] += 1
    return f"{prefixo}{area['contador']}"


def nome_livre(area: dict, desejado: str, ignorar: str | None = None) -> str:
    """Um nome que o usuário possa escrever no SQL sem aspas e que ninguém usa ainda."""
    base = nome_simples(desejado) or "tabela"
    if base[0].isdigit():
        base = f"t_{base}"
    usados = {t["nome"] for t in area["tabelas"] if t["id"] != ignorar}
    usados |= {v["nome"] for v in area["visoes"] if v["id"] != ignorar}
    nome, n = base, 2
    while nome in usados:
        nome, n = f"{base}_{n}", n + 1
    return nome


def tabela_fisica(item: dict) -> str:
    """A tabela que guarda o estado atual: a limpa, ou a bruta antes da normalização."""
    bruta, limpa, _ = nomes(item["id"])
    return limpa if item.get("relatorio") else bruta


def existe(con, nome: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [nome]
    ).fetchone()[0])


# ----- entrada de dados -----

def adicionar_arquivo(con: duckdb.DuckDBPyConnection, area: dict, caminho: Path, nome_arquivo: str,
                      limites: Limites = LOCAL, aba: str | None = None) -> dict:
    """Lê um arquivo para uma tabela nova da sessão (blocos 1 a 3)."""
    tabela_id = _proximo_id(area, "t")
    bruta, _, _ = nomes(tabela_id)
    resultado = pipeline.abrir(con, caminho, limites, aba, nome_arquivo, tabela=bruta)
    leitura = resultado["arquivo"]
    titulo = nome_arquivo
    desejado = Path(nome_arquivo).stem
    if leitura and len(leitura.get("abas") or []) > 1:
        titulo = f"{nome_arquivo} · {leitura['aba']}"
        desejado = f"{desejado}_{leitura['aba']}"
    item = _nova_tabela(area, tabela_id, desejado, titulo, "arquivo", resultado)
    item["caminho"] = Path(caminho).name
    _publicar(con, item)
    return item


def adicionar_tabela_pronta(con: duckdb.DuckDBPyConnection, area: dict, origem_sql: str, nome: str, titulo: str,
                            tipo: str, limites: Limites = LOCAL, definicao: dict | None = None) -> dict:
    """Uma tabela nova a partir de um SELECT do próprio hub: consulta salva ou arquivos empilhados."""
    tabela_id = _proximo_id(area, "t")
    bruta, _, _ = nomes(tabela_id)
    temporaria = f"__nova_{tabela_id}"
    con.execute(f"CREATE OR REPLACE TABLE {ident(temporaria)} AS {origem_sql}")
    con.execute(f"CREATE OR REPLACE TABLE {ident(bruta)} AS SELECT rowid + 1 AS {ident(LINHA)}, * "
                f"FROM {ident(temporaria)}")
    con.execute(f"DROP TABLE {ident(temporaria)}")
    linhas = con.execute(f"SELECT count(*) FROM {ident(bruta)}").fetchone()[0]
    leitura = {"arquivo": titulo, "formato": tipo, "bytes": 0, "linhas": linhas,
               "colunas": len(colunas_de(con, bruta)), "separador": None, "codificacao": None,
               "aba": None, "abas": [], "avisos": []}
    resultado = {**pipeline.triar(con, bruta, limites), "arquivo": leitura}
    item = _nova_tabela(area, tabela_id, nome, titulo, tipo, resultado)
    item["definicao"] = definicao
    _publicar(con, item)
    return item


def _nova_tabela(area: dict, tabela_id: str, desejado: str, titulo: str, origem: str, resultado: dict) -> dict:
    item = {
        "id": tabela_id,
        "nome": nome_livre(area, desejado),
        "titulo": titulo,
        "origem": origem,
        **resultado,
        "papeis": {},
        "receita": None,
        "manipulacao": [],
        "relatorio": None,
        "perfil_limpo": None,
        "desfazer_rapido": False,
        "versao": 1,
    }
    area["tabelas"].append(item)
    area["ativa"] = tabela_id
    return item


def trocar_aba(con, area: dict, tabela_id: str, caminho: Path, aba: str, limites: Limites = LOCAL) -> dict:
    """Relê a tabela de outra aba do mesmo Excel. A receita anterior é descartada."""
    item = tabela(area, tabela_id)
    bruta, limpa, anterior = nomes(tabela_id)
    resultado = pipeline.abrir(con, caminho, limites, aba, item["arquivo"]["arquivo"], tabela=bruta)
    for nome in (limpa, anterior):
        con.execute(f"DROP TABLE IF EXISTS {ident(nome)}")
    item.update(resultado, receita=None, manipulacao=[], relatorio=None, perfil_limpo=None,
                desfazer_rapido=False, papeis={})
    item["titulo"] = f"{item['arquivo']['arquivo']} · {aba}"
    item["versao"] += 1
    _publicar(con, item)
    _mudou(con, area, tabela_id)
    return item


def remover_tabela(con, area: dict, tabela_id: str) -> None:
    item = tabela(area, tabela_id)
    for nome in nomes(tabela_id):
        con.execute(f"DROP TABLE IF EXISTS {ident(nome)}")
    con.execute(f"DROP VIEW IF EXISTS {ident(item['nome'])}")
    area["tabelas"] = [t for t in area["tabelas"] if t["id"] != tabela_id]
    area["relacoes"] = [r for r in area["relacoes"] if tabela_id not in (r["de"]["tabela"], r["para"]["tabela"])]
    for visao in [v for v in area["visoes"] if tabela_id in dependencias(area, {"tipo": "visao", "id": v["id"]})]:
        con.execute(f"DROP VIEW IF EXISTS {ident(visao['nome'])}")
        area["visoes"].remove(visao)
    if area["base"] and not _base_existe(area, area["base"]):
        area["base"], area["analise"] = None, None
    if area["ativa"] == tabela_id:
        area["ativa"] = area["tabelas"][0]["id"] if area["tabelas"] else None


def renomear_tabela(con, area: dict, tabela_id: str, novo: str) -> dict:
    item = tabela(area, tabela_id)
    nome = nome_livre(area, novo, ignorar=tabela_id)
    con.execute(f"DROP VIEW IF EXISTS {ident(item['nome'])}")
    item["nome"] = nome
    _publicar(con, item)
    recriar_visoes(con, area)
    return item


# ----- normalização e sandbox -----

def normalizar(con, area: dict, tabela_id: str, receita: list[dict], papeis: dict | None,
               limites: Limites = LOCAL) -> dict:
    """Aplica a receita da normalização e, por cima, os passos que a sandbox já tinha."""
    item = tabela(area, tabela_id)
    item["receita"] = receita
    if papeis is not None:
        item["papeis"] = papeis
    _reaplicar(con, item, limites)
    if area["base"] is None:
        area["base"] = {"tipo": "tabela", "id": tabela_id}
    _mudou(con, area, tabela_id)
    return item


def _reaplicar(con, item: dict, limites: Limites) -> None:
    bruta, limpa, anterior = nomes(item["id"])
    estados = pipeline._estados_de(item.get("blocos"))
    relatorio = pipeline._rodar(
        "normalizacao",
        lambda: normalizacao.aplicar(con, (item["receita"] or []) + item["manipulacao"], origem=bruta, destino=limpa),
        estados,
    )
    item["blocos"] = pipeline._lista(estados)
    con.execute(f"DROP TABLE IF EXISTS {ident(anterior)}")
    item["desfazer_rapido"] = False
    item["relatorio"] = asdict(relatorio) if relatorio else None
    if relatorio:
        perfil = perfilar_tabela(con, limpa, limites)
        pipeline.aplicar_papeis(perfil, item["papeis"])
        item["perfil_limpo"] = asdict(perfil)
    item["versao"] += 1
    _publicar(con, item)


def acrescentar_passo(con, area: dict, tabela_id: str, passo: dict) -> dict:
    """Um passo da sandbox sobre a tabela atual. Se o passo não serve, nada muda.

    Edições de célula seguidas viram um passo só, para a receita não crescer
    uma linha por tecla.
    """
    item = tabela(area, tabela_id)
    if not item.get("relatorio"):
        raise PassoInvalido("Aplique a normalização desta tabela antes de editar.")
    bruta, limpa, anterior = nomes(tabela_id)
    novo = f"__nova_{tabela_id}"
    relatorio = pipeline.relatorio_de(item["relatorio"])
    manipulacao = item["manipulacao"]
    juntar = (passo.get("op") == "editar_celulas" and manipulacao and manipulacao[-1].get("op") == "editar_celulas"
              and item["desfazer_rapido"] and existe(con, anterior))

    if juntar:
        passo = {"op": "editar_celulas", "edicoes": manipulacao[-1]["edicoes"] + list(passo.get("edicoes") or [])}
        indice = relatorio.passos[-1].indice
        sql_anterior = relatorio.partes[-2][2] if len(relatorio.partes) >= 2 else bruta
        resultado, parte = normalizacao.aplicar_passo(con, passo, indice, anterior, novo, sql_anterior)
        if resultado.status != "ok":
            raise PassoInvalido(resultado.erro or "Não consegui aplicar a edição.")
        con.execute(f"DROP TABLE {ident(limpa)}")
        con.execute(f"ALTER TABLE {ident(novo)} RENAME TO {ident(limpa)}")
        manipulacao[-1] = passo
        relatorio.passos[-1] = resultado
        relatorio.partes[-1] = parte
    else:
        indice = len(item["receita"] or []) + len(manipulacao) + 1
        sql_anterior = relatorio.partes[-1][2] if relatorio.partes else bruta
        resultado, parte = normalizacao.aplicar_passo(con, passo, indice, limpa, novo, sql_anterior)
        if resultado.status != "ok":
            raise PassoInvalido(resultado.erro or "Não consegui aplicar o passo.")
        con.execute(f"DROP TABLE IF EXISTS {ident(anterior)}")
        con.execute(f"ALTER TABLE {ident(limpa)} RENAME TO {ident(anterior)}")
        con.execute(f"ALTER TABLE {ident(novo)} RENAME TO {ident(limpa)}")
        manipulacao.append(passo)
        relatorio.passos.append(resultado)
        relatorio.partes.append(parte)

    if passo.get("op") == "renomear_coluna" and passo.get("coluna") in item["papeis"]:
        item["papeis"][passo["nome"]] = item["papeis"].pop(passo["coluna"])
    _fechar_relatorio(con, item, relatorio)
    item["desfazer_rapido"] = True
    _mudou(con, area, tabela_id)
    return asdict(resultado)


def desfazer(con, area: dict, tabela_id: str, limites: Limites = LOCAL) -> dict:
    """Tira o último passo da sandbox. Com o ponto de retorno guardado, é instantâneo."""
    item = tabela(area, tabela_id)
    if not item["manipulacao"]:
        raise PassoInvalido("Não há passo da sandbox para desfazer.")
    bruta, limpa, anterior = nomes(tabela_id)
    item["manipulacao"].pop()
    if item["desfazer_rapido"] and existe(con, anterior):
        con.execute(f"DROP TABLE {ident(limpa)}")
        con.execute(f"ALTER TABLE {ident(anterior)} RENAME TO {ident(limpa)}")
        relatorio = pipeline.relatorio_de(item["relatorio"])
        relatorio.passos.pop()
        relatorio.partes.pop()
        _fechar_relatorio(con, item, relatorio)
        item["desfazer_rapido"] = False
    else:
        _reaplicar(con, item, limites)
    _mudou(con, area, tabela_id)
    return item


def _fechar_relatorio(con, item: dict, relatorio) -> None:
    bruta, limpa, _ = nomes(item["id"])
    relatorio.linhas_depois = con.execute(f"SELECT count(*) FROM {ident(limpa)}").fetchone()[0]
    relatorio.colunas_depois = len(colunas_de(con, limpa))
    relatorio.sql = normalizacao.montar_sql(relatorio.partes, bruta)
    item["relatorio"] = asdict(relatorio)
    item["versao"] += 1
    _publicar(con, item)


# ----- nomes visíveis e visões -----

def _publicar(con, item: dict) -> None:
    """O nome visível da tabela: o que o usuário escreve no SQL, sem as colunas internas."""
    fisica = tabela_fisica(item)
    if not existe(con, fisica):
        return
    tem_numero = LINHA in dict(colunas_de(con, fisica, internas=True))
    selecao = f"SELECT * EXCLUDE ({ident(LINHA)}) FROM {ident(fisica)}" if tem_numero else f"SELECT * FROM {ident(fisica)}"
    con.execute(f"CREATE OR REPLACE VIEW {ident(item['nome'])} AS {selecao}")


def recriar_visoes(con, area: dict) -> None:
    """As visões que juntam tabelas são refeitas a partir da definição guardada."""
    from engine import relacional

    for visao in area["visoes"]:
        try:
            relacional.criar_sql_da_visao(con, area, visao)
            visao["erro"] = None
        except (ValueError, duckdb.Error) as erro:
            visao["erro"] = str(erro).strip().splitlines()[0][:200]


def _mudou(con, area: dict, tabela_id: str) -> None:
    """Uma tabela mudou: visões que dependem dela são refeitas e a análise fica desatualizada."""
    recriar_visoes(con, area)
    analise = area.get("analise")
    if analise and tabela_id in dependencias(area, analise["base"]):
        analise["desatualizada"] = True


# ----- base de análise -----

def dependencias(area: dict, base: dict | None) -> set[str]:
    if not base:
        return set()
    if base["tipo"] == "tabela":
        return {base["id"]}
    visao = next((v for v in area["visoes"] if v["id"] == base["id"]), None)
    if not visao:
        return set()
    return {visao["tabela"]} | {j["tabela"] for j in visao["juncoes"]}


def _base_existe(area: dict, base: dict) -> bool:
    if base["tipo"] == "tabela":
        return any(t["id"] == base["id"] and t.get("relatorio") for t in area["tabelas"])
    return any(v["id"] == base["id"] for v in area["visoes"])


def escolher_base(area: dict, base: dict) -> None:
    if base.get("tipo") not in {"tabela", "visao"} or not _base_existe(area, base):
        raise NaoEncontrado("Essa base não existe, ou a tabela ainda não foi normalizada.")
    area["base"] = {"tipo": base["tipo"], "id": base["id"]}
    if area["analise"] and area["analise"]["base"] != area["base"]:
        area["analise"]["desatualizada"] = True


def descrever_base(area: dict) -> dict:
    """O que a análise precisa saber da base: nome no banco, unidades, receita, papéis."""
    base = area["base"]
    if not base:
        raise NaoEncontrado("Escolha uma base de análise.")
    if base["tipo"] == "tabela":
        item = tabela(area, base["id"])
        receita = (item["receita"] or []) + item["manipulacao"]
        return {
            "nome": nomes(item["id"])[1],
            "rotulo": item["nome"],
            "unidades": normalizacao.unidades_da_receita(receita),
            "derivadas": normalizacao.colunas_derivadas(receita),
            "relatorio": pipeline.relatorio_de(item["relatorio"]) if item.get("relatorio") else None,
            "papeis": item["papeis"],
            "linhas": item["relatorio"]["linhas_depois"] if item.get("relatorio") else 0,
        }
    visao = next(v for v in area["visoes"] if v["id"] == base["id"])
    unidades = {}
    for apelido, (tabela_id, coluna) in visao["origens"].items():
        item = tabela(area, tabela_id)
        unidade = normalizacao.unidades_da_receita((item["receita"] or []) + item["manipulacao"]).get(coluna)
        if unidade:
            unidades[apelido] = unidade
    emprestadas, vinculadas = set(), {}
    for juncao in visao["juncoes"]:
        da_tabela = {apelido for apelido, (tabela_id, _) in visao["origens"].items() if tabela_id == juncao["tabela"]}
        if juncao["modo"] != "resumo":
            emprestadas |= da_tabela
        # Região e população vêm da mesma tabela de UFs: compará-las descreve o cadastro, não os dados.
        for apelido in da_tabela:
            vinculadas.setdefault(apelido, set()).update(da_tabela - {apelido})
    return {
        "nome": visao["nome"],
        "rotulo": visao["nome"],
        "unidades": unidades,
        "derivadas": vinculadas,
        "relatorio": None,
        "papeis": visao.setdefault("papeis", {}),
        "linhas": visao.get("linhas", 0),
        "so_media": emprestadas,
    }


def analisar(con, area: dict, limites: Limites = LOCAL) -> dict:
    """Perfil, gráficos e insights da base escolhida."""
    base = descrever_base(area)
    estados = {}
    resultado = pipeline.analisar(con, base["nome"], base["unidades"], base["derivadas"], base["relatorio"],
                                  base["papeis"], limites, estados, base.get("so_media"))
    area["analise"] = {
        "base": dict(area["base"]),
        "rotulo": base["rotulo"],
        "linhas": con.execute(f"SELECT count(*) FROM {ident(base['nome'])}").fetchone()[0],
        "desatualizada": False,
        **resultado,
        "blocos": [b for b in resultado["blocos"] if b["bloco"] in {"graficos", "insights"}],
    }
    if area["base"]["tipo"] == "tabela":
        tabela(area, area["base"]["id"])["perfil_limpo"] = resultado["perfil_limpo"]
    area["analise"]["livres"] = [_calcular_livre(con, base, item, limites) for item in area.setdefault("graficos_livres", [])]
    return area["analise"]


# ----- gráficos que o usuário monta -----
# Ficam na sessão como pedidos (tipo, colunas, cálculo) e são recalculados a
# cada análise: acompanham a base quando ela muda ou é editada.

TIPOS_LIVRES = {"barras", "rosca", "linha", "histograma", "dispersao"}


def _calcular_livre(con, base: dict, item: dict, limites: Limites) -> dict:
    try:
        grafico = asdict(graficos.calcular(con, base["nome"], base["unidades"], limites, item["pedido"], item["id"]))
        return {"id": item["id"], "pedido": item["pedido"], "grafico": grafico, "erro": None}
    except (ValueError, KeyError, duckdb.Error) as erro:
        motivo = str(erro) if isinstance(erro, ValueError) else "As colunas deste gráfico não existem mais na base."
        return {"id": item["id"], "pedido": item["pedido"], "grafico": None, "erro": motivo}


def _pedido_livre(con, area: dict, pedido: dict) -> tuple[dict, dict]:
    if not area.get("analise"):
        raise PassoInvalido("Escolha uma base e analise antes de montar gráficos.")
    if pedido.get("tipo") not in TIPOS_LIVRES:
        raise PassoInvalido(f"Tipo de gráfico desconhecido: {pedido.get('tipo')}.")
    base = descrever_base(area)
    nomes = {nome for nome, _ in colunas_de(con, base["nome"])}
    for coluna in (pedido.get("x"), pedido.get("y")):
        if coluna is not None and coluna not in nomes:
            raise PassoInvalido(f"A coluna {coluna} não existe na base.")
    limpo = {chave: pedido.get(chave) for chave in ("tipo", "x", "y", "agregacao", "grao")}
    return base, limpo


def salvar_grafico_livre(con, area: dict, pedido: dict, limites: Limites = LOCAL, id_: str | None = None) -> dict:
    """Cria (sem id) ou troca um gráfico do usuário. Só guarda se o gráfico sai: um pedido sem sentido explica o porquê."""
    base, limpo = _pedido_livre(con, area, pedido)
    livres = area.setdefault("graficos_livres", [])
    if id_ is None:
        id_ = f"l{max((int(item['id'][1:]) for item in livres), default=0) + 1}"
    resultado = _calcular_livre(con, base, {"id": id_, "pedido": limpo}, limites)
    if resultado["erro"]:
        raise PassoInvalido(resultado["erro"])
    posicao = next((i for i, item in enumerate(livres) if item["id"] == id_), None)
    if posicao is None:
        livres.append({"id": id_, "pedido": limpo})
    else:
        livres[posicao] = {"id": id_, "pedido": limpo}
    lista = area["analise"].setdefault("livres", [])
    lista[:] = [item for item in lista if item["id"] != id_]
    lista.insert(posicao if posicao is not None else len(lista), resultado)
    return resultado


def remover_grafico_livre(area: dict, id_: str) -> None:
    area["graficos_livres"] = [item for item in area.get("graficos_livres", []) if item["id"] != id_]
    if area.get("analise"):
        area["analise"]["livres"] = [item for item in area["analise"].get("livres", []) if item["id"] != id_]


def trocar_papeis(area: dict, papeis: dict[str, str]) -> None:
    base = area["base"]
    if not base:
        raise NaoEncontrado("Escolha uma base de análise.")
    if base["tipo"] == "tabela":
        tabela(area, base["id"])["papeis"] = papeis
    else:
        next(v for v in area["visoes"] if v["id"] == base["id"])["papeis"] = papeis


def pode_reanalisar_sozinho(area: dict, limites: Limites = LOCAL) -> bool:
    """Bases pequenas se reanalisam sozinhas depois de uma edição; as grandes esperam o clique."""
    analise = area.get("analise")
    return bool(analise and analise.get("desatualizada") and analise.get("linhas", 0) <= limites.linhas_reanalise_automatica)
