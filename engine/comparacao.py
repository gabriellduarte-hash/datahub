"""Comparar os arquivos de uma sessão antes de juntar.

O caso típico: o mesmo relatório exportado em meses diferentes, ou por filiais
diferentes. Antes de combinar os arquivos, é preciso saber três coisas:

- se as colunas batem: o mesmo nome (ignorando maiúsculas, acentos e
  espaços, então "Valor Total" e "valor_total" são a mesma coluna) e o mesmo
  tipo;
- se há duplicatas entre os arquivos (a mesma linha nos dois), comparando as
  colunas em comum sem ligar para maiúsculas e espaços nas pontas;
- quais passos da limpeza de um arquivo servem para o outro, e por que os
  outros ficam de fora.
"""

import difflib
import json
from itertools import combinations

from engine import formulas, normalizacao
from engine.area import nomes, tabela, tabela_fisica
from engine.relacional import _familia
from engine.sql import LINHA, colunas_de, eh_interna, ident, nome_simples

class CopiaRecusada(ValueError):
    """A limpeza não pode ser copiada. A mensagem é para o usuário."""


MAX_TABELAS = 6
EXEMPLOS = 5
VAZIO = "␀"  # marca o vazio dentro da chave da linha, para vazio bater com vazio
PARECIDO = 0.82

# Passos que fazem sentido em outro arquivo. Editar ou apagar linhas escolhidas
# a dedo aponta para linhas do arquivo de origem: no outro, seriam outras linhas.
SANDBOX_TRANSFERIVEL = {"renomear_coluna", "mover_coluna", "remover_colunas", "formula", "filtrar_linhas", "agrupar"}
DE_LINHAS = {"editar_celulas", "remover_linhas"}
# Converter depende de como o arquivo escreve os valores ("1.234,56" ou "1,234.56", "10%"):
# no outro arquivo vale a conversão que a normalização de lá detectou para a mesma coluna.
CONVERSOES = {"converter_numero", "converter_data", "converter_booleano"}


def _visiveis(con, fisica: str) -> dict[str, str]:
    return {nome: tipo for nome, tipo in colunas_de(con, fisica, internas=True) if not eh_interna(nome)}


def _papeis(item: dict) -> dict[str, str]:
    perfil = (item.get("perfil_limpo") if item.get("relatorio") else item.get("perfil")) or {}
    papeis = {c["nome"]: c["papel"] for c in perfil.get("colunas", [])}
    papeis.update(item.get("papeis") or {})
    return papeis


def _alinhar(colunas: dict[str, dict[str, str]], ordem: list[str]) -> dict[str, dict[str, str]]:
    """Chave simples da coluna -> nome dela em cada tabela."""
    chaves: dict[str, dict[str, str]] = {}
    for tid in ordem:
        for nome in colunas[tid]:
            base = nome_simples(nome) or nome
            chave, n = base, 2
            while tid in chaves.get(chave, {}):  # duas colunas da mesma tabela com o mesmo nome simples
                chave, n = f"{base}#{n}", n + 1
            chaves.setdefault(chave, {})[tid] = nome
    return chaves


# O tipo que o perfil enxerga, mesmo antes da normalização, quando tudo ainda é texto.
GRUPOS = {"inteiro": "numero", "decimal": "numero", "moeda": "numero", "percentual": "percentual",
          "data": "data", "booleano": "booleano", "texto": "texto"}


def _detectados(con, item: dict, tabela_sql: str, colunas: dict[str, str]) -> dict[str, str]:
    """O tipo de cada coluna pelo perfil. Coluna que o perfil não conhece (criada ou renomeada
    na sandbox) é medida nos dados: texto em que todo valor preenchido vira número é número."""
    perfil = (item.get("perfil_limpo") if item.get("relatorio") else item.get("perfil")) or {}
    tipos = {c["nome"]: c.get("tipo") for c in perfil.get("colunas", [])}
    grupos = {nome: GRUPOS.get(tipos.get(nome) or "", _familia(tipo)) for nome, tipo in colunas.items()}
    medir = [nome for nome, tipo in colunas.items() if nome not in tipos and _familia(tipo) == "texto"]
    if medir:
        testes = ", ".join(
            f"bool_and(TRY_CAST(trim({ident(n)}) AS DOUBLE) IS NOT NULL) FILTER (WHERE nullif(trim({ident(n)}), '') IS NOT NULL)"
            for n in medir)
        for nome, numerica in zip(medir, con.execute(f"SELECT {testes} FROM {ident(tabela_sql)}").fetchone()):
            if numerica:
                grupos[nome] = "numero"
    return grupos


def _normalizada(coluna: str, familia: str, outra: str, numero_em_texto: bool = False) -> str:
    """O valor como texto comparável.

    Número com número e data com data comparam o valor; texto compara sem
    maiúsculas e sem espaços nas pontas. Número guardado como texto, comum antes
    da normalização, vira número quando dá: "45.90" bate com 45.9.
    """
    c = ident(coluna)
    if familia == outra == "numero":
        return f"CAST(CAST({c} AS DOUBLE) AS VARCHAR)"
    if familia == outra == "data":
        return f"CAST(CAST({c} AS TIMESTAMP) AS VARCHAR)"
    texto = f"lower(trim(CAST({c} AS VARCHAR)))"
    if numero_em_texto:
        return f"coalesce(CAST(TRY_CAST(trim(CAST({c} AS VARCHAR)) AS DOUBLE) AS VARCHAR), {texto})"
    return texto


def _chave_da_linha(expressoes: list[str]) -> str:
    partes = ", ".join(f"coalesce({e}, '{VAZIO}')" for e in expressoes)
    return f"md5(concat_ws(chr(31), {partes}))"


def _texto(valor):
    return None if valor is None else str(valor)


def comparar(con, area: dict) -> dict:
    # Uma tabela combinada contém os próprios arquivos: compará-la com eles não diz nada.
    arquivos = [item for item in area["tabelas"] if item.get("origem") != "combinada"]
    itens = arquivos[:MAX_TABELAS]
    if len(itens) < 2:
        return {"tabelas": [], "colunas": [], "pares": [], "planos": [], "deixadas_de_fora": 0}
    ordem = [item["id"] for item in itens]
    fisicas = {item["id"]: tabela_fisica(item) for item in itens}
    colunas = {tid: _visiveis(con, fisicas[tid]) for tid in ordem}
    familias = {tid: {nome: _familia(tipo) for nome, tipo in colunas[tid].items()} for tid in ordem}
    detectados = {item["id"]: _detectados(con, item, fisicas[item["id"]], colunas[item["id"]]) for item in itens}
    papeis = {item["id"]: _papeis(item) for item in itens}
    chaves = _alinhar(colunas, ordem)

    tabelas = []
    for item in itens:
        tid = item["id"]
        fisica = ident(fisicas[tid])
        visiveis = ", ".join(ident(nome) for nome in colunas[tid])
        linhas, distintas = con.execute(
            f"SELECT (SELECT count(*) FROM {fisica}), (SELECT count(*) FROM (SELECT DISTINCT {visiveis} FROM {fisica}))"
        ).fetchone()
        tabelas.append({"id": tid, "nome": item["nome"], "titulo": item["titulo"], "linhas": linhas,
                        "duplicatas_dentro": linhas - distintas, "normalizada": bool(item.get("relatorio"))})

    linhas_colunas = []
    for chave, por_tabela in chaves.items():
        presentes = [tid for tid in ordem if tid in por_tabela]
        grupos = {detectados[tid][por_tabela[tid]] for tid in presentes}
        if len(grupos) > 1:
            situacao = "tipo_diferente"
        elif len(presentes) < len(ordem):
            situacao = "falta"
        elif len({por_tabela[tid] for tid in presentes}) > 1:
            situacao = "nome_diferente"
        else:
            situacao = "igual"
        linhas_colunas.append({
            "chave": chave,
            "nomes": {tid: por_tabela.get(tid) for tid in ordem},
            "tipos": {tid: detectados[tid][por_tabela[tid]] if tid in por_tabela else None for tid in ordem},
            "papeis": {tid: papeis[tid].get(por_tabela[tid]) if tid in por_tabela else None for tid in ordem},
            "situacao": situacao,
            "parecida_com": None,
        })
    _marcar_parecidas(linhas_colunas, ordem)

    pares = [_par(con, a, b, chaves, fisicas, familias, detectados, papeis) for a, b in combinations(ordem, 2)]
    planos = []
    for origem in itens:
        if not (origem.get("receita") or origem.get("manipulacao")):
            continue
        for destino in itens:
            if destino["id"] != origem["id"]:
                bruta = nomes(destino["id"])[0]
                plano = planejar(origem, _visiveis(con, bruta), destino)
                planos.append({"origem": origem["id"], "destino": destino["id"],
                               "aplicaveis": [p["titulo"] for p in plano["aplicaveis"]],
                               "pulados": plano["pulados"], "trocas": plano["trocas"],
                               "aplicado": ja_aplicado(plano, destino)})

    return {"tabelas": tabelas, "colunas": linhas_colunas, "pares": pares, "planos": planos,
            "deixadas_de_fora": max(0, len(arquivos) - MAX_TABELAS)}


def _marcar_parecidas(linhas: list[dict], ordem: list[str]) -> None:
    """Colunas que faltam em parte das tabelas, com nomes quase iguais em tabelas diferentes: talvez sejam a mesma."""
    faltando = [linha for linha in linhas if linha["situacao"] == "falta"]
    for i, linha in enumerate(faltando):
        onde = {tid for tid in ordem if linha["nomes"][tid]}
        for outra in faltando[i + 1:]:
            if onde & {tid for tid in ordem if outra["nomes"][tid]}:
                continue
            if difflib.SequenceMatcher(None, linha["chave"], outra["chave"]).ratio() >= PARECIDO:
                linha["parecida_com"] = linha["parecida_com"] or outra["chave"]
                outra["parecida_com"] = outra["parecida_com"] or linha["chave"]


def _par(con, a: str, b: str, chaves, fisicas, familias, detectados, papeis) -> dict:
    todas = [(chave, nomes_[a], nomes_[b]) for chave, nomes_ in chaves.items() if a in nomes_ and b in nomes_]
    # Uma coluna com tipo diferente nos dois ("5" contra "5%") não deixaria nenhuma linha bater: fica fora da conta.
    comuns = [(k, ca, cb) for k, ca, cb in todas if detectados[a][ca] == detectados[b][cb]] or todas
    par = {"a": a, "b": b, "colunas": [chave for chave, _, _ in comuns],
           "fora": [chave for chave, _, _ in todas if chave not in {k for k, _, _ in comuns}],
           "b_em_a": 0, "a_em_b": 0, "iguais": 0, "exemplos": [], "chave": None}
    if not comuns:
        return par

    def normalizar(tabela_id: str, outra_id: str, coluna: str, coluna_outra: str) -> str:
        numerica = detectados[tabela_id][coluna] == detectados[outra_id][coluna_outra] == "numero"
        return _normalizada(coluna, familias[tabela_id][coluna], familias[outra_id][coluna_outra], numerica)

    ta, tb = ident(fisicas[a]), ident(fisicas[b])
    expr_a = [normalizar(a, b, ca, cb) for _, ca, cb in comuns]
    expr_b = [normalizar(b, a, cb, ca) for _, ca, cb in comuns]
    chave_a, chave_b = _chave_da_linha(expr_a), _chave_da_linha(expr_b)
    numero = ident(LINHA)
    tem_numero_a = LINHA in dict(colunas_de(con, fisicas[a], internas=True))
    tem_numero_b = LINHA in dict(colunas_de(con, fisicas[b], internas=True))
    linha_a = numero if tem_numero_a else "rowid + 1"
    linha_b = numero if tem_numero_b else "rowid + 1"

    con.execute(f"CREATE OR REPLACE TEMP TABLE __cmp_a AS SELECT {linha_a} AS n, {chave_a} AS k FROM {ta}")
    con.execute(f"CREATE OR REPLACE TEMP TABLE __cmp_b AS SELECT {linha_b} AS n, {chave_b} AS k FROM {tb}")
    try:
        b_em_a, a_em_b, iguais = con.execute("""
            SELECT (SELECT count(*) FROM __cmp_b WHERE k IN (SELECT k FROM __cmp_a)),
                   (SELECT count(*) FROM __cmp_a WHERE k IN (SELECT k FROM __cmp_b)),
                   (SELECT count(*) FROM (SELECT k FROM __cmp_a INTERSECT SELECT k FROM __cmp_b))
        """).fetchone()
        par.update(b_em_a=b_em_a, a_em_b=a_em_b, iguais=iguais)
        if iguais:
            visiveis = ", ".join(f"t.{ident(ca)}" for _, ca, _ in comuns)
            exemplos = con.execute(f"""
                WITH achadas AS (
                    SELECT x.n AS linha_a, min(y.n) AS linha_b FROM __cmp_a x JOIN __cmp_b y USING (k)
                    GROUP BY x.n ORDER BY x.n LIMIT {EXEMPLOS})
                SELECT achadas.linha_a, achadas.linha_b, {visiveis}
                FROM achadas JOIN (SELECT {linha_a} AS __n, * FROM {ta}) t ON t.__n = achadas.linha_a
                ORDER BY achadas.linha_a
            """).fetchall()
            par["exemplos"] = [{"linha_a": linha[0], "linha_b": linha[1], "valores": [_texto(v) for v in linha[2:]]}
                               for linha in exemplos]
    finally:
        con.execute("DROP TABLE IF EXISTS __cmp_a")
        con.execute("DROP TABLE IF EXISTS __cmp_b")

    # Um identificador em comum diz quantos registros aparecem nos dois, mesmo com outros campos diferentes.
    for chave, ca, cb in comuns:
        if papeis[a].get(ca) == "id" or papeis[b].get(cb) == "id":
            va, vb = normalizar(a, b, ca, cb), normalizar(b, a, cb, ca)
            so_a, so_b, ambos = con.execute(f"""
                WITH x AS (SELECT DISTINCT {va} AS v FROM {ta} WHERE {ident(ca)} IS NOT NULL),
                     y AS (SELECT DISTINCT {vb} AS v FROM {tb} WHERE {ident(cb)} IS NOT NULL)
                SELECT (SELECT count(*) FROM x WHERE v NOT IN (SELECT v FROM y)),
                       (SELECT count(*) FROM y WHERE v NOT IN (SELECT v FROM x)),
                       (SELECT count(*) FROM x JOIN y USING (v))
            """).fetchone()
            par["chave"] = {"coluna": chave, "ambos": ambos, "so_a": so_a, "so_b": so_b}
            break
    return par


# ----- as mesmas regras em outro arquivo -----

def _colunas_do_passo(passo: dict) -> list[tuple[str, object]]:
    """Onde o passo cita colunas: (campo, valor). Usado para conferir e para trocar o nome pelo do outro arquivo."""
    citadas = []
    for campo in ("coluna", "a", "b", "apos", "antes"):
        if passo.get(campo):
            citadas.append((campo, passo[campo]))
    if passo.get("op") in {"remover_colunas", "agrupar"}:
        citadas.append(("colunas", list(passo.get("colunas") or [])))
    for filtro in passo.get("filtros") or []:
        citadas.append(("filtro", filtro.get("coluna")))
    for conta in passo.get("agregacoes") or []:
        citadas.append(("agregacao", conta.get("coluna")))
    return citadas


def _depois(passo: dict, presentes: list[str]) -> list[str]:
    """As colunas que existem depois do passo, para conferir os passos seguintes."""
    op = passo.get("op")
    if op == "renomear_coluna":
        return [passo["nome"] if nome == passo["coluna"] else nome for nome in presentes]
    if op == "remover_colunas":
        return [nome for nome in presentes if nome not in passo["colunas"]]
    if op in {"formula", "coluna_calculada"} and passo.get("nome") not in presentes:
        return presentes + [passo["nome"]]
    if op == "agrupar":
        contas = [f"{c['funcao']}_{c['coluna']}" for c in passo.get("agregacoes") or []]
        return list(passo["colunas"]) + [passo.get("contagem") or "quantidade"] + contas
    return presentes


def planejar(origem: dict, colunas_destino: dict[str, str], destino: dict | None = None) -> dict:
    """Quais passos da origem servem para um arquivo com estas colunas (nome -> tipo no banco).

    Uma coluna com o mesmo nome simples conta como a mesma ("Valor Total" e
    "valor_total"), e o passo copiado passa a usar o nome do destino. Fórmulas
    precisam do nome exato: trocar nomes dentro do texto da fórmula seria
    adivinhar demais. Conversões usam o formato detectado no destino.
    """
    presentes = list(colunas_destino)
    nome_destino = destino["nome"] if destino else "o outro arquivo"
    sugeridas = [s["passo"] for s in (destino or {}).get("sugestoes") or []]
    aplicaveis, pulados, trocas = [], [], []
    partes = [("receita", origem.get("receita") or []), ("manipulacao", origem.get("manipulacao") or [])]
    for parte, passos in partes:
        for passo in passos:
            titulo = normalizacao.descrever(passo)
            op = passo.get("op")
            if op in DE_LINHAS:
                pulados.append({"titulo": titulo, "motivo": "mexe em linhas escolhidas a dedo no outro arquivo"})
                continue
            if parte == "manipulacao" and op not in SANDBOX_TRANSFERIVEL:
                pulados.append({"titulo": titulo, "motivo": "passo da sandbox que só vale para o outro arquivo"})
                continue
            por_simples = {nome_simples(nome): nome for nome in presentes}

            def achar(nome):
                if nome in presentes:
                    return nome
                return por_simples.get(nome_simples(str(nome)))

            novo, faltando = json.loads(json.dumps(passo)), []
            for campo, valor in _colunas_do_passo(passo):
                if campo == "colunas":
                    achados = [achar(v) for v in valor]
                    faltando += [v for v, achado in zip(valor, achados) if not achado]
                    novo["colunas"] = [achado or v for v, achado in zip(valor, achados)]
                    trocas += [{"de": v, "para": achado} for v, achado in zip(valor, achados) if achado and achado != v]
                    continue
                achado = achar(valor)
                if not achado:
                    # Na coluna nova, a posição é só preferência: sem a coluna, ela vai para o fim.
                    # Para mover uma coluna, a referência é o próprio passo.
                    if campo not in {"apos", "antes"} or op == "mover_coluna":
                        faltando.append(valor)
                    else:
                        novo.pop(campo, None)
                    continue
                if achado != valor:
                    trocas.append({"de": valor, "para": achado})
                    if campo in {"filtro", "agregacao"}:
                        lista = "filtros" if campo == "filtro" else "agregacoes"
                        for item in novo[lista]:
                            if item.get("coluna") == valor:
                                item["coluna"] = achado
                    else:
                        novo[campo] = achado
            if op == "formula" and not novo.get("vazia"):
                faltando += sorted(c for c in formulas.colunas_usadas(novo) if c not in presentes)
            if faltando:
                pulados.append({"titulo": titulo, "motivo": "não tem a coluna " + ", ".join(dict.fromkeys(map(str, faltando)))})
                continue
            if op in CONVERSOES and destino is not None:
                propria = next((s for s in sugeridas if s.get("op") == op and s.get("coluna") == novo["coluna"]), None)
                if propria is None:
                    tipo = colunas_destino.get(novo["coluna"], "VARCHAR")
                    motivo = (f"em {nome_destino}, {novo['coluna']} já está convertida" if tipo != "VARCHAR"
                              else f"em {nome_destino}, os valores de {novo['coluna']} não parecem desse tipo")
                    pulados.append({"titulo": titulo, "motivo": motivo})
                    continue
                novo = json.loads(json.dumps(propria))
                titulo = normalizacao.descrever(novo)
            aplicaveis.append({"parte": parte, "passo": novo, "titulo": normalizacao.descrever(novo)})
            presentes = _depois(novo, presentes)
    unicas = list({(t["de"], t["para"]): t for t in trocas}.values())
    return {"aplicaveis": aplicaveis, "pulados": pulados, "trocas": unicas}


def _chave(passo: dict) -> str:
    return json.dumps(passo, sort_keys=True)


def ja_aplicado(plano: dict, destino: dict) -> bool:
    """O destino já tem exatamente esta limpeza: aplicar de novo não mudaria nada."""
    receita = [_chave(p["passo"]) for p in plano["aplicaveis"] if p["parte"] == "receita"]
    sandbox = {_chave(p["passo"]) for p in plano["aplicaveis"] if p["parte"] == "manipulacao"}
    return (destino.get("relatorio") is not None
            and receita == [_chave(p) for p in destino.get("receita") or []]
            and sandbox <= {_chave(p) for p in destino["manipulacao"]})


def copiar_receita(con, area: dict, origem_id: str, destino_id: str, limites) -> dict:
    """O destino passa a ter a limpeza da origem: a receita é trocada, e os passos da sandbox que servem entram no fim."""
    from engine import area as areas

    if origem_id == destino_id:
        raise CopiaRecusada("Escolha dois arquivos diferentes.")
    origem, destino = tabela(area, origem_id), tabela(area, destino_id)
    plano = planejar(origem, _visiveis(con, nomes(destino_id)[0]), destino)
    if not plano["aplicaveis"]:
        raise CopiaRecusada(f"Nenhum passo de {origem['nome']} serve para {destino['nome']}.")
    if ja_aplicado(plano, destino):
        raise CopiaRecusada(f"{destino['nome']} já tem a limpeza de {origem['nome']}: nada mudaria.")
    receita = [p["passo"] for p in plano["aplicaveis"] if p["parte"] == "receita"]
    ja_tem = {_chave(p) for p in destino["manipulacao"]}
    novos = [p["passo"] for p in plano["aplicaveis"] if p["parte"] == "manipulacao" and _chave(p["passo"]) not in ja_tem]
    destino["manipulacao"] = destino["manipulacao"] + novos
    areas.normalizar(con, area, destino_id, receita, None, limites)
    return {"aplicados": len(plano["aplicaveis"]), "pulados": plano["pulados"], "trocas": plano["trocas"]}
