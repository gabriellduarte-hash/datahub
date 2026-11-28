"""Sandbox: o que a planilha faz com os dados.

Duas famílias, com regras diferentes:

- Mudar dados (editar célula, apagar linhas, filtrar para valer, renomear,
  fórmula) vira passo da receita. Assim o resultado continua reproduzível, o
  SQL continua visível, e desfazer é tirar o último passo.
- Ver dados (página, filtro de visão, ordem, contar um termo) é só consulta.
  Não muda a tabela nem a análise.

Toda linha carrega o número que tinha no arquivo (`__linha`). Editar "a linha
4.512" acerta a mesma linha depois de ordenar, filtrar ou remover duplicatas.
"""

from engine.sql import LINHA, eh_interna, eh_nulo, ident, literal
from engine.tipos import FALSOS, VERDADEIROS

OPERADORES = {
    "igual": "=", "diferente": None, "maior": ">", "menor": "<", "maior_igual": ">=", "menor_igual": "<=",
    "contem": None, "nao_contem": None, "vazio": None, "preenchido": None,
}
NOMES_OPERADOR = {
    "igual": "=", "diferente": "≠", "maior": ">", "menor": "<", "maior_igual": "≥", "menor_igual": "≤",
    "contem": "contém", "nao_contem": "não contém", "vazio": "vazio", "preenchido": "preenchido",
}
TIPOS_INTEIROS = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"}
MAX_POR_PAGINA = 500


class PassoInvalido(ValueError):
    """O passo não faz sentido para esta tabela. A mensagem é para o usuário."""


def eh_numerico(tipo: str) -> bool:
    return tipo in TIPOS_INTEIROS or tipo in {"DOUBLE", "FLOAT"} or tipo.startswith("DECIMAL")


# ----- valores digitados na tela -----

def texto_no_tipo(expressao: str, tipo: str) -> str:
    """SQL que converte um texto digitado para o tipo da coluna. O que não converte vira nulo."""
    if tipo == "VARCHAR":
        return expressao
    if tipo == "BOOLEAN":
        sim = ", ".join(literal(v) for v in sorted(VERDADEIROS))
        nao = ", ".join(literal(v) for v in sorted(FALSOS))
        v = f"lower(trim({expressao}))"
        return f"CASE WHEN {v} IN ({sim}) THEN true WHEN {v} IN ({nao}) THEN false END"
    if eh_numerico(tipo):
        # "22,5" e "1.234,5" são formato brasileiro; "22.5" passa como está.
        v = f"trim({expressao})"
        normal = f"CASE WHEN contains({v}, ',') THEN replace(replace({v}, '.', ''), ',', '.') ELSE {v} END"
        return f"TRY_CAST({normal} AS {tipo})"
    return f"TRY_CAST(trim({expressao}) AS {tipo})"


def condicao(filtro: dict, tipos: dict[str, str]) -> str:
    """Um filtro da tela vira uma condição SQL. Nome e valor nunca entram crus."""
    coluna = filtro.get("coluna")
    if coluna not in tipos or eh_interna(coluna):
        raise PassoInvalido(f"A coluna {coluna} não existe.")
    operador = filtro.get("operador")
    if operador not in OPERADORES:
        raise PassoInvalido(f"Operador desconhecido: {operador}.")
    c, tipo = ident(coluna), tipos[coluna]
    if operador == "vazio":
        return eh_nulo(coluna, tipo)
    if operador == "preenchido":
        return f"NOT {eh_nulo(coluna, tipo)}"
    valor = str(filtro.get("valor") if filtro.get("valor") is not None else "")
    if operador in {"contem", "nao_contem"}:
        achou = f"contains(lower(CAST({c} AS VARCHAR)), lower({literal(valor)}))"
        return achou if operador == "contem" else f"NOT coalesce({achou}, false)"
    v = texto_no_tipo(literal(valor), tipo)
    if operador == "diferente":
        return f"{c} IS DISTINCT FROM {v}"
    return f"{c} {OPERADORES[operador]} {v}"


def onde(filtros: list[dict] | None, tipos: dict[str, str]) -> str:
    if not filtros:
        return "true"
    return " AND ".join(f"({condicao(filtro, tipos)})" for filtro in filtros)


def descrever_filtros(filtros: list[dict]) -> str:
    partes = []
    for filtro in filtros:
        operador = NOMES_OPERADOR.get(filtro.get("operador"), filtro.get("operador"))
        if filtro.get("operador") in {"vazio", "preenchido"}:
            partes.append(f"{filtro.get('coluna')} {operador}")
        else:
            partes.append(f"{filtro.get('coluna')} {operador} {filtro.get('valor')}")
    return " e ".join(partes)


# ----- passos que mudam dados -----

def compilar(passo: dict, colunas: list[tuple[str, str]], origem: str) -> str:
    """O SELECT de um passo da sandbox. `colunas` inclui as internas."""
    op = passo["op"]
    tipos = dict(colunas)
    if op == "editar_celulas":
        return _editar(passo, tipos, origem)
    if op == "remover_linhas":
        linhas = sorted({int(linha) for linha in passo.get("linhas") or []})
        if not linhas:
            raise PassoInvalido("Marque ao menos uma linha para remover.")
        _exigir_numero_de_linha(tipos)
        return f"SELECT * FROM {origem} WHERE {ident(LINHA)} NOT IN ({', '.join(map(str, linhas))})"
    if op == "filtrar_linhas":
        filtros = passo.get("filtros") or []
        if not filtros:
            raise PassoInvalido("Escolha ao menos um filtro.")
        condicoes = onde(filtros, tipos)
        if passo.get("modo", "manter") == "remover":
            return f"SELECT * FROM {origem} WHERE NOT coalesce(({condicoes}), false)"
        return f"SELECT * FROM {origem} WHERE coalesce(({condicoes}), false)"
    if op == "renomear_coluna":
        return _renomear(passo, colunas, origem)
    if op == "mover_coluna":
        return _mover(passo, colunas, origem)
    if op == "agrupar":
        return _agrupar(passo, colunas, origem)
    raise PassoInvalido(f"Passo desconhecido: {op}")


def _exigir_numero_de_linha(tipos: dict[str, str]) -> None:
    if LINHA not in tipos:
        raise PassoInvalido("Esta tabela não tem número de linha, então não dá para apontar linhas nela.")


def _editar(passo: dict, tipos: dict[str, str], origem: str) -> str:
    _exigir_numero_de_linha(tipos)
    por_coluna: dict[str, dict[int, str | None]] = {}
    for edicao in passo.get("edicoes") or []:
        coluna = edicao.get("coluna")
        if coluna not in tipos or eh_interna(coluna):
            raise PassoInvalido(f"A coluna {coluna} não existe.")
        valor = edicao.get("valor")
        por_coluna.setdefault(coluna, {})[int(edicao["linha"])] = None if valor in (None, "") else str(valor)
    if not por_coluna:
        raise PassoInvalido("Nenhuma célula foi editada.")

    juncoes, trocas = [], []
    for indice, (coluna, valores) in enumerate(por_coluna.items()):
        apelido = f"__e{indice}"
        linhas = ", ".join(
            f"({linha}, {literal(valor) if valor is not None else 'NULL'})" for linha, valor in valores.items()
        )
        juncoes.append(
            f"LEFT JOIN (SELECT CAST(l AS BIGINT) AS l, CAST(v AS VARCHAR) AS v FROM (VALUES {linhas}) t(l, v)) "
            f"{apelido} ON {apelido}.l = __o.{ident(LINHA)}"
        )
        novo = texto_no_tipo(f"{apelido}.v", tipos[coluna])
        trocas.append(f"CASE WHEN {apelido}.l IS NOT NULL THEN {novo} ELSE __o.{ident(coluna)} END AS {ident(coluna)}")
    # A junção embaralha as linhas; a ordem do arquivo volta pelo número de cada uma.
    return (f"SELECT __o.* REPLACE ({', '.join(trocas)}) FROM {origem} __o {' '.join(juncoes)} "
            f"ORDER BY __o.{ident(LINHA)}")


def _renomear(passo: dict, colunas: list[tuple[str, str]], origem: str) -> str:
    antigo = passo.get("coluna")
    novo = str(passo.get("nome") or "").strip()
    nomes = [nome for nome, _ in colunas]
    if antigo not in nomes or eh_interna(antigo):
        raise PassoInvalido(f"A coluna {antigo} não existe.")
    if not novo:
        raise PassoInvalido("Escreva o nome novo da coluna.")
    if eh_interna(novo):
        raise PassoInvalido('Nomes que começam com "__" são do próprio hub.')
    # O DuckDB não diferencia maiúsculas em nomes de coluna: "Churn" e "churn" colidem.
    repetida = next((nome for nome in nomes if nome.lower() == novo.lower() and nome != antigo), None)
    if repetida:
        raise PassoInvalido(f"Já existe uma coluna chamada {repetida}.")
    lista = ", ".join(f"{ident(nome)} AS {ident(novo)}" if nome == antigo else ident(nome) for nome in nomes)
    return f"SELECT {lista} FROM {origem}"


def _mover(passo: dict, colunas: list[tuple[str, str]], origem: str) -> str:
    """Muda a coluna de lugar: logo depois (`apos`) ou logo antes (`antes`) de outra.

    A referência é outra coluna, não uma posição: o passo continua certo se uma
    coluna for criada ou apagada antes dele, e serve para outro arquivo.
    """
    nomes = [nome for nome, _ in colunas]
    coluna, apos, antes = passo.get("coluna"), passo.get("apos"), passo.get("antes")
    if coluna not in nomes or eh_interna(coluna):
        raise PassoInvalido(f"A coluna {coluna} não existe.")
    if bool(apos) == bool(antes):
        raise PassoInvalido("Diga para onde a coluna vai: depois ou antes de outra.")
    alvo = apos or antes
    if alvo == coluna:
        raise PassoInvalido("A coluna não pode ir para antes ou depois dela mesma.")
    if alvo not in nomes or eh_interna(alvo):
        raise PassoInvalido(f"A coluna {alvo} não existe.")
    lista = [nome for nome in nomes if nome != coluna]
    lista.insert(lista.index(alvo) + (1 if apos else 0), coluna)
    return f"SELECT {', '.join(ident(nome) for nome in lista)} FROM {origem}"


FUNCOES_AGRUPAR = {"soma": "sum", "media": "avg", "minimo": "min", "maximo": "max"}
NOMES_FUNCAO = {"soma": "soma", "media": "média", "minimo": "mínimo", "maximo": "máximo"}


def _agrupar(passo: dict, colunas: list[tuple[str, str]], origem: str) -> str:
    """Uma linha por combinação dos valores das colunas escolhidas, com quantas linhas caíram nela.

    Cada grupo herda o número da primeira linha que o formou, como na remoção
    de duplicatas: a planilha continua apontando para o arquivo original.
    """
    tipos = dict(colunas)
    chaves = list(dict.fromkeys(passo.get("colunas") or []))
    if not chaves:
        raise PassoInvalido("Escolha ao menos uma coluna para agrupar.")
    for chave in chaves:
        if chave not in tipos or eh_interna(chave):
            raise PassoInvalido(f"A coluna {chave} não existe.")
    contagem = str(passo.get("contagem") or "quantidade").strip()
    if eh_interna(contagem):
        raise PassoInvalido('Nomes que começam com "__" são do próprio hub.')
    usados = {chave.lower() for chave in chaves}
    if contagem.lower() in usados:
        raise PassoInvalido(f"A coluna de contagem não pode se chamar {contagem}: já é uma das colunas agrupadas.")
    usados.add(contagem.lower())

    lista = [ident(chave) for chave in chaves]
    if LINHA in tipos:
        lista.insert(0, f"min({ident(LINHA)}) AS {ident(LINHA)}")
    lista.append(f"count(*) AS {ident(contagem)}")
    for item in passo.get("agregacoes") or []:
        coluna, funcao = item.get("coluna"), item.get("funcao")
        if coluna not in tipos or eh_interna(coluna):
            raise PassoInvalido(f"A coluna {coluna} não existe.")
        if funcao not in FUNCOES_AGRUPAR:
            raise PassoInvalido(f"Cálculo desconhecido: {funcao}.")
        if funcao in {"soma", "media"} and not eh_numerico(tipos[coluna]):
            raise PassoInvalido(f"{coluna} não é numérica, então não dá para calcular a {NOMES_FUNCAO[funcao]}.")
        nome = f"{funcao}_{coluna}"
        if nome.lower() in usados:
            raise PassoInvalido(f"Duas colunas sairiam com o nome {nome}.")
        usados.add(nome.lower())
        lista.append(f"{FUNCOES_AGRUPAR[funcao]}({ident(coluna)}) AS {ident(nome)}")
    ordem = f"ORDER BY {ident(LINHA)}" if LINHA in tipos else ""
    return f"SELECT {', '.join(lista)} FROM {origem} GROUP BY {', '.join(ident(c) for c in chaves)} {ordem}"


def descrever(passo: dict) -> str:
    op = passo.get("op")
    if op == "agrupar":
        extras = [f"{NOMES_FUNCAO.get(a.get('funcao'), a.get('funcao'))} de {a.get('coluna')}" for a in passo.get("agregacoes") or []]
        texto = f"Agrupar por {', '.join(passo.get('colunas') or [])}, contando em {passo.get('contagem') or 'quantidade'}"
        return texto + (f" e com {', '.join(extras)}" if extras else "")
    if op == "editar_celulas":
        edicoes = passo.get("edicoes") or []
        colunas = sorted({str(e.get("coluna")) for e in edicoes})
        quantas = len({(e.get("linha"), e.get("coluna")) for e in edicoes})
        return f"Editar {quantas} {'célula' if quantas == 1 else 'células'}: {', '.join(colunas)}"
    if op == "remover_linhas":
        quantas = len(set(passo.get("linhas") or []))
        return f"Remover {quantas} {'linha marcada' if quantas == 1 else 'linhas marcadas'}"
    if op == "filtrar_linhas":
        verbo = "Remover as linhas com" if passo.get("modo") == "remover" else "Manter só as linhas com"
        return f"{verbo} {descrever_filtros(passo.get('filtros') or [])}"
    if op == "renomear_coluna":
        return f"Renomear {passo.get('coluna')} para {passo.get('nome')}"
    if op == "mover_coluna":
        lugar = f"depois de {passo['apos']}" if passo.get("apos") else f"antes de {passo.get('antes')}"
        return f"Mover {passo.get('coluna')} para {lugar}"
    return str(op)


def edicoes_perdidas(con, passo: dict, tipos: dict[str, str]) -> tuple[int, int]:
    """(células editadas, valores que não couberam no tipo da coluna e viraram vazio)."""
    total, perdidos = 0, 0
    vistos = {}
    for edicao in passo.get("edicoes") or []:
        vistos[(int(edicao["linha"]), edicao["coluna"])] = edicao.get("valor")
    for (_, coluna), valor in vistos.items():
        total += 1
        if valor in (None, "") or coluna not in tipos:
            continue
        convertido = con.execute(
            f"SELECT {texto_no_tipo('v', tipos[coluna])} FROM (SELECT CAST(? AS VARCHAR) AS v)", [str(valor)]
        ).fetchone()[0]
        perdidos += convertido is None
    return total, perdidos


# ----- consultas que só mostram -----

def pagina(con, tabela: str, inicio: int = 0, quantas: int = 100, ordenar: str | None = None,
           decrescente: bool = False, filtros: list[dict] | None = None) -> dict:
    """Uma faixa de linhas, com o número de cada uma. A planilha pede só o que está à vista."""
    from engine.sql import colunas_de

    todas = colunas_de(con, tabela, internas=True)
    tipos = dict(todas)
    visiveis = [(nome, tipo) for nome, tipo in todas if not eh_interna(nome)]
    quantas = max(1, min(int(quantas), MAX_POR_PAGINA))
    inicio = max(0, int(inicio))
    condicoes = onde(filtros, tipos)
    tem_numero = LINHA in tipos
    numero = ident(LINHA) if tem_numero else "NULL"
    ordem = []
    if ordenar is not None:
        if ordenar not in {nome for nome, _ in visiveis}:
            raise PassoInvalido(f"A coluna {ordenar} não existe.")
        ordem.append(f"{ident(ordenar)} {'DESC' if decrescente else 'ASC'} NULLS LAST")
    if tem_numero:
        ordem.append(ident(LINHA))
    ordem_sql = f"ORDER BY {', '.join(ordem)}" if ordem else ""
    lista = ", ".join(ident(nome) for nome, _ in visiveis)
    t = ident(tabela)
    total, filtradas = con.execute(
        f"SELECT count(*), count(*) FILTER (coalesce(({condicoes}), false)) FROM {t}"
    ).fetchone()
    linhas = con.execute(
        f"SELECT {numero}, {lista} FROM {t} WHERE coalesce(({condicoes}), false) {ordem_sql} LIMIT ? OFFSET ?",
        [quantas, inicio],
    ).fetchall()
    return {
        "colunas": [{"nome": nome, "tipo": tipo} for nome, tipo in visiveis],
        "numeros": [linha[0] for linha in linhas],
        "linhas": [list(linha[1:]) for linha in linhas],
        "inicio": inicio,
        "total": total,
        "filtradas": filtradas,
    }


def contar(con, tabela: str, coluna: str, termo: str, inteiro: bool = False,
           filtros: list[dict] | None = None) -> dict:
    """Quantas linhas têm o termo na coluna: contendo o texto ou exatamente igual."""
    from engine.sql import colunas_de

    tipos = dict(colunas_de(con, tabela, internas=True))
    if coluna not in tipos or eh_interna(coluna):
        raise PassoInvalido(f"A coluna {coluna} não existe.")
    if inteiro:
        teste = f"lower(trim(CAST({ident(coluna)} AS VARCHAR))) = lower(trim({literal(termo)}))"
    else:
        teste = f"contains(lower(CAST({ident(coluna)} AS VARCHAR)), lower({literal(termo)}))"
    condicoes = onde(filtros, tipos)
    linhas, achadas = con.execute(
        f"SELECT count(*), count(*) FILTER (coalesce({teste}, false)) FROM {ident(tabela)} "
        f"WHERE coalesce(({condicoes}), false)"
    ).fetchone()
    return {"coluna": coluna, "termo": termo, "inteiro": inteiro, "linhas": linhas, "encontradas": achadas}
