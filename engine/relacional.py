"""Várias tabelas como um banco: relações, empilhamento e a visão que junta tudo.

Uma relação liga uma coluna de uma tabela à coluna-chave de outra:
`pedidos.cliente_id → clientes.cliente_id`. O lado da seta precisa ter a chave
única; senão cada pedido encontraria vários clientes.

A visão unificada parte de uma tabela base e traz colunas das outras pelas
relações. A regra que protege a análise:

- trazer colunas do lado único é seguro: cada linha da base ganha no máximo
  uma linha da outra tabela, e nenhuma linha se repete;
- trazer linhas do lado múltiplo repetiria a base (um cliente com 3 pedidos
  viraria 3 linhas, e a soma do limite dele contaria 3 vezes). Isso é
  recusado. No lugar, o hub traz um resumo: quantas linhas e a soma de cada
  número, por chave.

Empilhar é diferente: tabelas com as mesmas colunas (vendas de janeiro e de
fevereiro) viram uma só, com uma coluna dizendo de onde veio cada linha.
"""

from engine.manipulacao import eh_numerico
from engine.sql import colunas_de, ident, literal, nome_simples

NOMES_DE_CHAVE = {"id", "cod", "codigo", "code", "chave", "key"}
MIN_COBERTURA_SUGESTAO = 0.5


class JuncaoRecusada(ValueError):
    """A junção repetiria linhas ou não faz sentido. A mensagem é para o usuário."""


def _tabela(area: dict, tabela_id: str) -> dict:
    from engine.area import tabela
    return tabela(area, tabela_id)


def _relacao(area: dict, relacao_id: str) -> dict:
    for relacao in area["relacoes"]:
        if relacao["id"] == relacao_id:
            return relacao
    from engine.area import NaoEncontrado
    raise NaoEncontrado(f"Relação {relacao_id} não encontrada.")


def _tipos(con, nome: str) -> dict[str, str]:
    return dict(colunas_de(con, nome))


def _familia(tipo: str) -> str:
    if eh_numerico(tipo):
        return "numero"
    if tipo.startswith("DATE") or tipo.startswith("TIMESTAMP"):
        return "data"
    return "texto"


def chave_unica(con, tabela: str, coluna: str) -> bool:
    """A coluna identifica cada linha: nenhum valor preenchido se repete."""
    preenchidos, distintos = con.execute(
        f"SELECT count({ident(coluna)}), count(DISTINCT {ident(coluna)}) FROM {ident(tabela)}"
    ).fetchone()
    return preenchidos > 0 and preenchidos == distintos


def cobertura(con, tabela_a: str, coluna_a: str, tabela_b: str, coluna_b: str) -> float:
    """Fração dos valores de A que existem em B. Perto de 1, a relação é boa."""
    a, b = ident(coluna_a), ident(coluna_b)
    valor = con.execute(f"""
        WITH valores AS (SELECT DISTINCT CAST({a} AS VARCHAR) AS v FROM {ident(tabela_a)} WHERE {a} IS NOT NULL),
             chaves AS (SELECT DISTINCT CAST({b} AS VARCHAR) AS v FROM {ident(tabela_b)} WHERE {b} IS NOT NULL)
        SELECT avg(CASE WHEN chaves.v IS NOT NULL THEN 1.0 ELSE 0.0 END)
        FROM valores LEFT JOIN chaves USING (v)
    """).fetchone()[0]
    return float(valor or 0)


def _nomes_combinam(tabela_a: str, coluna_a: str, tabela_b: str, coluna_b: str) -> bool:
    a, b = nome_simples(coluna_a), nome_simples(coluna_b)
    if a == b:
        return True
    tb = nome_simples(tabela_b)
    singular = tb[:-1] if tb.endswith("s") else tb
    if b in NOMES_DE_CHAVE:
        return a in {f"{tb}_{b}", f"{singular}_{b}", f"{b}_{tb}", f"{b}_{singular}"}
    return False


def sugerir_relacoes(con, area: dict) -> list[dict]:
    """Pares de colunas com nomes que combinam, chave única do lado B e valores de A achados em B."""
    prontas = [t for t in area["tabelas"]]
    existentes = {(r["de"]["tabela"], r["de"]["coluna"], r["para"]["tabela"], r["para"]["coluna"])
                  for r in area["relacoes"]}
    sugestoes = []
    tipos = {t["id"]: _tipos(con, t["nome"]) for t in prontas}
    for a in prontas:
        for b in prontas:
            if a["id"] == b["id"]:
                continue
            for coluna_a, tipo_a in tipos[a["id"]].items():
                for coluna_b, tipo_b in tipos[b["id"]].items():
                    if (a["id"], coluna_a, b["id"], coluna_b) in existentes:
                        continue
                    if _familia(tipo_a) != _familia(tipo_b) or _familia(tipo_a) == "data":
                        continue
                    if not _nomes_combinam(a["nome"], coluna_a, b["nome"], coluna_b):
                        continue
                    if not chave_unica(con, b["nome"], coluna_b):
                        continue
                    fracao = cobertura(con, a["nome"], coluna_a, b["nome"], coluna_b)
                    # Se as duas colunas são únicas, a relação é de um para um: basta sugerir num sentido.
                    if fracao >= MIN_COBERTURA_SUGESTAO and not (
                            chave_unica(con, a["nome"], coluna_a) and a["id"] > b["id"]):
                        sugestoes.append({
                            "de": {"tabela": a["id"], "coluna": coluna_a},
                            "para": {"tabela": b["id"], "coluna": coluna_b},
                            "cobertura": round(fracao, 4),
                        })
    return sorted(sugestoes, key=lambda s: -s["cobertura"])


def criar_relacao(con, area: dict, de: dict, para: dict) -> dict:
    """Valida e guarda uma relação. A coluna de destino precisa ser chave única."""
    a, b = _tabela(area, de["tabela"]), _tabela(area, para["tabela"])
    if a["id"] == b["id"]:
        raise JuncaoRecusada("Escolha duas tabelas diferentes.")
    tipos_a, tipos_b = _tipos(con, a["nome"]), _tipos(con, b["nome"])
    if de["coluna"] not in tipos_a:
        raise JuncaoRecusada(f"A coluna {de['coluna']} não existe em {a['nome']}.")
    if para["coluna"] not in tipos_b:
        raise JuncaoRecusada(f"A coluna {para['coluna']} não existe em {b['nome']}.")
    if _familia(tipos_a[de["coluna"]]) != _familia(tipos_b[para["coluna"]]):
        raise JuncaoRecusada(f"{de['coluna']} é {_familia(tipos_a[de['coluna']])} e {para['coluna']} é "
                             f"{_familia(tipos_b[para['coluna']])}: os valores nunca vão bater.")
    if not chave_unica(con, b["nome"], para["coluna"]):
        raise JuncaoRecusada(
            f"{para['coluna']} se repete em {b['nome']}: cada linha de {a['nome']} encontraria várias. "
            f"A seta aponta para a tabela onde a chave é única; tente o sentido contrário."
        )
    if any(r["de"] == de and r["para"] == para for r in area["relacoes"]):
        raise JuncaoRecusada("Essa relação já existe.")
    area["contador"] += 1
    relacao = {
        "id": f"r{area['contador']}",
        "de": {"tabela": a["id"], "coluna": de["coluna"]},
        "para": {"tabela": b["id"], "coluna": para["coluna"]},
        "cobertura": round(cobertura(con, a["nome"], de["coluna"], b["nome"], para["coluna"]), 4),
    }
    area["relacoes"].append(relacao)
    return relacao


def criar_visao(con, area: dict, nome: str, tabela_base: str, juncoes: list[dict]) -> dict:
    """Uma visão que parte de `tabela_base` e traz colunas ou resumos pelas relações."""
    from engine.area import nome_livre

    _tabela(area, tabela_base)
    if not juncoes:
        raise JuncaoRecusada("Escolha ao menos uma relação para juntar.")
    area["contador"] += 1
    visao = {
        "id": f"v{area['contador']}",
        "nome": nome_livre(area, nome or "visao"),
        "tabela": tabela_base,
        "juncoes": [],
        "origens": {},
        "papeis": {},
        "linhas": 0,
        "erro": None,
    }
    for juncao in juncoes:
        relacao = _relacao(area, juncao["relacao"])
        if tabela_base == relacao["de"]["tabela"]:
            outra = relacao["para"]["tabela"]
        elif tabela_base == relacao["para"]["tabela"]:
            outra = relacao["de"]["tabela"]
        else:
            raise JuncaoRecusada("Essa relação não passa pela tabela base.")
        visao["juncoes"].append({"relacao": relacao["id"], "tabela": outra, "modo": juncao.get("modo", "colunas")})
    criar_sql_da_visao(con, area, visao)
    area["visoes"].append(visao)
    return visao


def criar_sql_da_visao(con, area: dict, visao: dict) -> None:
    """Monta e cria a visão. Confere no fim que ela tem exatamente as linhas da base."""
    base = _tabela(area, visao["tabela"])
    tipos_base = _tipos(con, base["nome"])
    selecoes = ["__b.*"]
    origens = {coluna: (base["id"], coluna) for coluna in tipos_base}
    usados = {coluna.lower() for coluna in tipos_base}
    trechos = []

    def apelido(desejado: str) -> str:
        nome, n = desejado, 2
        while nome.lower() in usados:
            nome, n = f"{desejado}_{n}", n + 1
        usados.add(nome.lower())
        return nome

    for indice, juncao in enumerate(visao["juncoes"]):
        relacao = _relacao(area, juncao["relacao"])
        outra = _tabela(area, juncao["tabela"])
        j = f"__j{indice}"
        if relacao["de"]["tabela"] == base["id"]:
            # A base aponta para a outra: lado único, trazer colunas é seguro.
            chave = relacao["para"]["coluna"]
            if not chave_unica(con, outra["nome"], chave):
                raise JuncaoRecusada(f"{chave} deixou de ser única em {outra['nome']}: a junção repetiria linhas.")
            trechos.append(f"LEFT JOIN {ident(outra['nome'])} {j} "
                           f"ON __b.{ident(relacao['de']['coluna'])} = {j}.{ident(chave)}")
            for coluna in _tipos(con, outra["nome"]):
                if coluna == chave:
                    continue
                nome = apelido(f"{outra['nome']}_{coluna}")
                selecoes.append(f"{j}.{ident(coluna)} AS {ident(nome)}")
                origens[nome] = (outra["id"], coluna)
        else:
            # A outra aponta para a base: cada linha da base tem várias na outra.
            if juncao["modo"] != "resumo":
                raise JuncaoRecusada(
                    f"Cada linha de {base['nome']} tem várias em {outra['nome']}. Trazer essas linhas repetiria "
                    f"{base['nome']} e somaria os valores dela mais de uma vez. Use o resumo: quantas linhas e "
                    f"a soma de cada número, por {relacao['para']['coluna']}."
                )
            chave_externa = relacao["de"]["coluna"]
            numericas = [c for c, t in _tipos(con, outra["nome"]).items()
                         if eh_numerico(t) and c != chave_externa and not nome_simples(c).endswith("id")]
            contagem = apelido(f"{outra['nome']}_linhas")
            resumos = [f"count(*) AS {ident(contagem)}"]
            selecoes.append(f"coalesce({j}.{ident(contagem)}, 0) AS {ident(contagem)}")
            origens[contagem] = (outra["id"], contagem)
            for coluna in numericas:
                nome = apelido(f"{outra['nome']}_soma_{coluna}")
                resumos.append(f"sum({ident(coluna)}) AS {ident(nome)}")
                selecoes.append(f"{j}.{ident(nome)} AS {ident(nome)}")
                origens[nome] = (outra["id"], coluna)
            trechos.append(
                f"LEFT JOIN (SELECT {ident(chave_externa)} AS __chave, {', '.join(resumos)} "
                f"FROM {ident(outra['nome'])} GROUP BY 1) {j} ON __b.{ident(relacao['para']['coluna'])} = {j}.__chave"
            )

    sql = f"SELECT {', '.join(selecoes)} FROM {ident(base['nome'])} __b {' '.join(trechos)}"
    con.execute(f"CREATE OR REPLACE VIEW {ident(visao['nome'])} AS {sql}")
    linhas = con.execute(f"SELECT count(*) FROM {ident(visao['nome'])}").fetchone()[0]
    linhas_base = con.execute(f"SELECT count(*) FROM {ident(base['nome'])}").fetchone()[0]
    if linhas != linhas_base:
        con.execute(f"DROP VIEW {ident(visao['nome'])}")
        raise JuncaoRecusada(f"A visão teria {linhas} linhas e a base tem {linhas_base}: alguma junção repete linhas.")
    visao["origens"] = origens
    visao["linhas"] = linhas
    visao["sql"] = sql


def empilhar(con, area: dict, tabela_ids: list[str], nome: str) -> dict:
    """Junta tabelas com as mesmas colunas, uma embaixo da outra, numa tabela nova."""
    from engine.area import adicionar_tabela_pronta

    if len(set(tabela_ids)) < 2:
        raise JuncaoRecusada("Escolha ao menos duas tabelas para empilhar.")
    itens = [_tabela(area, tabela_id) for tabela_id in tabela_ids]
    colunas = [set(_tipos(con, item["nome"])) for item in itens]
    comuns = set.intersection(*colunas)
    if not comuns:
        raise JuncaoRecusada("As tabelas não têm nenhuma coluna em comum.")
    origem = "origem" if not any("origem" in c for c in colunas) else "tabela_de_origem"
    partes = [f"SELECT {literal(item['nome'])} AS {ident(origem)}, * FROM {ident(item['nome'])}" for item in itens]
    sql = " UNION ALL BY NAME ".join(partes)
    titulo = "Empilhada: " + " + ".join(item["nome"] for item in itens)
    nova = adicionar_tabela_pronta(con, area, sql, nome or "empilhada", titulo, "empilhada",
                                   definicao={"tabelas": tabela_ids})
    faltando = sorted(set.union(*colunas) - comuns)
    if faltando:
        nova["arquivo"]["avisos"].append(
            "Colunas que só existem em parte das tabelas ficaram vazias nas outras: " + ", ".join(faltando) + "."
        )
    return nova


def esquema(con, area: dict) -> dict:
    """Tudo o que a vista de esquema desenha: tabelas, colunas, relações, visões e sugestões."""
    tabelas = []
    chaves = {(r["para"]["tabela"], r["para"]["coluna"]) for r in area["relacoes"]}
    estrangeiras = {(r["de"]["tabela"], r["de"]["coluna"]) for r in area["relacoes"]}
    for item in area["tabelas"]:
        tipos = _tipos(con, item["nome"])
        linhas = con.execute(f"SELECT count(*) FROM {ident(item['nome'])}").fetchone()[0]
        tabelas.append({
            "id": item["id"],
            "nome": item["nome"],
            "titulo": item["titulo"],
            "linhas": linhas,
            "normalizada": bool(item.get("relatorio")),
            "colunas": [
                {"nome": coluna, "tipo": tipo,
                 "chave": (item["id"], coluna) in chaves, "aponta": (item["id"], coluna) in estrangeiras}
                for coluna, tipo in tipos.items()
            ],
        })
    return {
        "tabelas": tabelas,
        "relacoes": area["relacoes"],
        "visoes": [{k: v for k, v in visao.items() if k != "origens"} for visao in area["visoes"]],
        "sugestoes": sugerir_relacoes(con, area),
    }
