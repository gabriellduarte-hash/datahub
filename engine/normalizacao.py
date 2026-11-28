"""Bloco 3, normalização: sugere passos de limpeza e aplica os que o usuário escolher.

Cada passo é um dicionário simples (a "receita"), e cada um vira um SELECT. Por
isso o resultado é reproduzível: a mesma receita sobre o mesmo arquivo dá a
mesma tabela, e o SQL pode ser mostrado ao usuário. A tabela `bruta` nunca muda;
o resultado vai para `limpa`.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field

import duckdb

from engine.consultas import faixa_normal
from engine.limites import LOCAL, Limites
from engine.perfil import Perfil, PerfilColuna
from engine import manipulacao
from engine.sql import LINHA, LISTA_VAZIOS, colunas_de, eh_interna, eh_nulo, ident, literal, nome_simples
from engine.tipos import FALSOS, RE_MILHAR, RE_UNIDADE, VERDADEIROS

# "1.234" é mil ou um vírgula dois três quatro? Sozinho o texto não diz, então
# a coluna inteira decide (milhar_ponto). O separador que vem por último é
# sempre o decimal: em "1.234,56" é a vírgula, em "1,234.56" é o ponto.
# Numa coluna de inteiros não há dúvida: "1,884" e "1.884" são 1884. E um
# "2,5" no meio dos inteiros vira nulo, contado, em vez de ser arredondado
# para 3 sem ninguém ver.
MACROS = r"""
CREATE OR REPLACE TEMP MACRO numero_normalizado(s, milhar_ponto) AS CASE
    WHEN s LIKE '%.%' AND s LIKE '%,%' THEN
        CASE WHEN strpos(reverse(s), ',') < strpos(reverse(s), '.')
             THEN replace(replace(s, '.', ''), ',', '.')
             ELSE replace(s, ',', '') END
    WHEN s LIKE '%,%' THEN
        CASE WHEN length(s) - length(replace(s, ',', '')) > 1
                  OR (NOT milhar_ponto AND regexp_full_match(s, '[+-]?[0-9]{1,3},[0-9]{3}'))
             THEN replace(s, ',', '')
             ELSE replace(s, ',', '.') END
    WHEN s LIKE '%.%' THEN
        CASE WHEN length(s) - length(replace(s, '.', '')) > 1
                  OR (milhar_ponto AND regexp_full_match(s, '[+-]?[0-9]{1,3}\.[0-9]{3}'))
             THEN replace(s, '.', '')
             ELSE s END
    ELSE s
END;
CREATE OR REPLACE TEMP MACRO numero_limpo(texto) AS
    regexp_replace(regexp_replace(trim(texto), '__UNIDADE__', '', 'i'), '[R$€£%\s]', '', 'g');
CREATE OR REPLACE TEMP MACRO para_numero(texto, milhar_ponto) AS
    numero_normalizado(numero_limpo(texto), milhar_ponto);
CREATE OR REPLACE TEMP MACRO inteiro_de(s, milhar_ponto) AS TRY_CAST(regexp_extract(
    CASE WHEN regexp_full_match(s, '__MILHAR__') THEN regexp_replace(s, '[.,]', '', 'g')
         ELSE numero_normalizado(s, milhar_ponto) END,
    '^([+-]?[0-9]+)(\.0*)?$', 1) AS BIGINT);
""".replace("__UNIDADE__", RE_UNIDADE.replace("'", "''")).replace("__MILHAR__", RE_MILHAR)

TIPOS_SQL = {"moeda": "DECIMAL(18,2)", "inteiro": "BIGINT", "decimal": "DOUBLE", "percentual": "DOUBLE"}
TIPOS_CONVERTIVEIS = set(TIPOS_SQL)

UFS = {
    "AC": "acre", "AL": "alagoas", "AP": "amapa", "AM": "amazonas", "BA": "bahia", "CE": "ceara",
    "DF": "distrito_federal", "ES": "espirito_santo", "GO": "goias", "MA": "maranhao",
    "MT": "mato_grosso", "MS": "mato_grosso_do_sul", "MG": "minas_gerais", "PA": "para",
    "PB": "paraiba", "PR": "parana", "PE": "pernambuco", "PI": "piaui", "RJ": "rio_de_janeiro",
    "RN": "rio_grande_do_norte", "RS": "rio_grande_do_sul", "RO": "rondonia", "RR": "roraima",
    "SC": "santa_catarina", "SP": "sao_paulo", "SE": "sergipe", "TO": "tocantins",
}
CHAVES_UF = {sigla.lower(): sigla for sigla in UFS} | {nome: sigla for sigla, nome in UFS.items()}

MAX_DISTINTOS_VARIANTES = 5_000
ABREVIACOES_GENERO = {
    "m": ("male", "masculino", "man", "mulher"),
    "f": ("female", "feminino", "woman"),
    "h": ("homem",),
    "o": ("other", "outro", "outros", "outra"),
    "nb": ("nonbinary", "naobinario"),
}
FORMAS_DE_GENERO = {"male", "female", "masculino", "feminino", "homem", "mulher", "man", "woman"}
NOMES_QUANTIDADE = ("quantidade", "qtd", "qtde", "quant")
NOMES_PRECO = ("valor_unitario", "vlr_unitario", "preco_unitario", "preco", "valor_unit")
NOMES_TOTAL = ("valor_total", "vlr_total", "total", "receita", "faturamento")


@dataclass
class Sugestao:
    id: str
    grupo: str                # vazios, texto, tipos, linhas, colunas, calculo
    titulo: str
    motivo: str
    passo: dict
    marcada: bool             # vem marcada para aplicar?
    afetados: int = 0
    perdidos: int = 0         # valores que viram nulo porque não convertem
    exemplos: list[list] = field(default_factory=list)   # pares [antes, depois]


@dataclass
class ResultadoPasso:
    indice: int
    titulo: str
    op: str
    status: str               # ok ou falhou
    linhas_antes: int
    linhas_depois: int
    alterados: int = 0
    perdidos: int = 0
    erro: str | None = None


@dataclass
class Relatorio:
    linhas_antes: int
    linhas_depois: int
    colunas_antes: int
    colunas_depois: int
    passos: list[ResultadoPasso]
    sql: str
    # [título, SELECT, nome do trecho] de cada passo que deu certo. Guardar os
    # trechos deixa somar um passo novo ao SQL sem recompilar a receita toda.
    partes: list[list[str]] = field(default_factory=list)


def garantir_macros(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(MACROS)


# ----- de passo para SQL -----

def _texto(coluna: str) -> str:
    return f"trim(CAST({ident(coluna)} AS VARCHAR))"


def _expr_vazios(passo: dict) -> str:
    c = ident(passo["coluna"])
    return f"CASE WHEN lower(trim({c})) IN ({LISTA_VAZIOS}) THEN NULL ELSE {c} END"


def _expr_aparar(passo: dict) -> str:
    return rf"regexp_replace(trim({ident(passo['coluna'])}), '\s+', ' ', 'g')"


def _expr_padronizar(passo: dict) -> str:
    c = ident(passo["coluna"])
    casos = " ".join(
        f"WHEN {literal(de)} THEN {literal(para)}" for de, para in passo["mapa"].items()
    )
    return f"CASE {c} {casos} ELSE {c} END"


def _expr_numero(passo: dict) -> str:
    """`list_transform([x], lambda s: ...)[1]` calcula x uma vez só e o chama de s.

    Sem isso o DuckDB repete a limpeza do texto a cada vez que s aparece na
    conta: num WHERE, ficava 10 vezes mais lento.
    """
    tipo = TIPOS_SQL[passo["tipo"]]
    milhar = "true" if passo.get("milhar_ponto", True) else "false"
    if passo["tipo"] == "inteiro":
        conta = f"inteiro_de(s, {milhar})"
    else:
        conta = f"TRY_CAST(numero_normalizado(s, {milhar}) AS {tipo})"
    return f"list_transform([numero_limpo({_texto(passo['coluna'])})], lambda s: {conta})[1]"


def _expr_data(passo: dict) -> str:
    formatos = ", ".join(literal(f) for f in passo["formatos"])
    expressao = f"try_strptime({_texto(passo['coluna'])}, [{formatos}])"
    if any("%H" in f for f in passo["formatos"]):
        return expressao
    return f"CAST({expressao} AS DATE)"


def _expr_booleano(passo: dict) -> str:
    v = f"lower({_texto(passo['coluna'])})"
    sim = ", ".join(literal(x) for x in sorted(VERDADEIROS))
    nao = ", ".join(literal(x) for x in sorted(FALSOS))
    return f"CASE WHEN {v} IN ({sim}) THEN true WHEN {v} IN ({nao}) THEN false END"


EXPRESSOES = {
    "vazios_para_nulo": _expr_vazios,
    "aparar_espacos": _expr_aparar,
    "padronizar_valores": _expr_padronizar,
    "converter_numero": _expr_numero,
    "converter_data": _expr_data,
    "converter_booleano": _expr_booleano,
}


PASSOS_DA_SANDBOX = {"editar_celulas", "remover_linhas", "filtrar_linhas", "renomear_coluna", "mover_coluna", "agrupar"}


def compilar(passo: dict, colunas: list[tuple[str, str]], origem: str) -> str:
    """O SELECT que aplica um passo sobre `origem`.

    `colunas` pode trazer as colunas internas, como o número da linha: elas
    passam pelo passo sem serem vistas como dados.
    """
    op = passo.get("op")
    if op in PASSOS_DA_SANDBOX:
        return manipulacao.compilar(passo, colunas, origem)
    if op == "formula":
        from engine import formulas
        return formulas.compilar_passo(passo, colunas, origem)

    tem_numero = any(nome == LINHA for nome, _ in colunas)
    colunas = [(nome, tipo) for nome, tipo in colunas if not eh_interna(nome)]
    nomes = [nome for nome, _ in colunas]

    if op in EXPRESSOES:
        _exigir(passo["coluna"], nomes)
        return f"SELECT * REPLACE ({EXPRESSOES[op](passo)} AS {ident(passo['coluna'])}) FROM {origem}"

    if op == "remover_linhas_vazias":
        toda_vazia = " AND ".join(eh_nulo(nome, tipo) for nome, tipo in colunas)
        return f"SELECT * FROM {origem} WHERE NOT ({toda_vazia})"

    if op == "remover_duplicatas":
        if not tem_numero:
            return f"SELECT DISTINCT * FROM {origem}"
        # Fica a primeira aparição de cada linha repetida, com o número dela.
        lista = ", ".join(ident(nome) for nome in nomes)
        return (f"SELECT min({ident(LINHA)}) AS {ident(LINHA)}, {lista} FROM {origem} GROUP BY ALL "
                f"ORDER BY {ident(LINHA)}")

    if op == "remover_extremos":
        _exigir(passo["coluna"], nomes)
        c = ident(passo["coluna"])
        minimo, maximo = float(passo["minimo"]), float(passo["maximo"])
        return f"SELECT * FROM {origem} WHERE {c} IS NULL OR {c} BETWEEN {minimo} AND {maximo}"

    if op == "remover_colunas":
        for nome in passo["colunas"]:
            _exigir(nome, nomes)
        lista = ", ".join(ident(nome) for nome in passo["colunas"])
        return f"SELECT * EXCLUDE ({lista}) FROM {origem}"

    if op == "coluna_calculada":
        return _compilar_calculo(passo, nomes, origem)

    raise ValueError(f"Passo desconhecido: {op}")


def _compilar_calculo(passo: dict, nomes: list[str], origem: str) -> str:
    _exigir(passo["a"], nomes)
    _exigir(passo["b"], nomes)
    if passo["nome"] in nomes:
        raise ValueError(f"Já existe uma coluna chamada {passo['nome']}.")
    a, b = ident(passo["a"]), ident(passo["b"])
    operador = passo["operador"]
    if operador not in {"+", "-", "*", "/"}:
        raise ValueError(f"Operador inválido: {operador}")
    conta = f"{a} / nullif({b}, 0)" if operador == "/" else f"{a} {operador} {b}"
    return f"SELECT *, TRY_CAST({conta} AS DECIMAL(18,2)) AS {ident(passo['nome'])} FROM {origem}"


def _exigir(coluna: str, nomes: list[str]) -> None:
    if coluna not in nomes:
        raise ValueError(f"A coluna {coluna} não existe neste ponto da receita.")


def descrever(passo: dict) -> str:
    op = passo.get("op")
    coluna = passo.get("coluna")
    if op == "vazios_para_nulo":
        return f"Transformar vazios disfarçados em nulo: {coluna}"
    if op == "aparar_espacos":
        return f"Remover espaços sobrando: {coluna}"
    if op == "padronizar_valores":
        return f"Padronizar valores: {coluna}"
    if op == "converter_numero":
        return f"Converter para {passo['tipo']}: {coluna}"
    if op == "converter_data":
        return f"Converter para data: {coluna}"
    if op == "converter_booleano":
        return f"Converter para sim/não: {coluna}"
    if op == "remover_linhas_vazias":
        return "Remover linhas vazias"
    if op == "remover_duplicatas":
        return "Remover linhas duplicadas"
    if op == "remover_extremos":
        return f"Remover linhas com valores extremos: {coluna}"
    if op == "remover_colunas":
        return f"Remover colunas: {', '.join(passo['colunas'])}"
    if op == "coluna_calculada":
        return f"Calcular {passo['nome']} = {passo['a']} {passo['operador']} {passo['b']}"
    if op == "formula":
        if passo.get("vazia"):
            return f"Criar a coluna {passo.get('nome')} em branco"
        return f"Calcular {passo.get('nome')} = {passo.get('formula')}"
    if op in PASSOS_DA_SANDBOX:
        return manipulacao.descrever(passo)
    return str(op)


# ----- aplicar a receita -----

def aplicar(con: duckdb.DuckDBPyConnection, receita: list[dict],
            origem: str = "bruta", destino: str = "limpa") -> Relatorio:
    """Aplica um passo por vez. Um passo que falha é relatado e os outros seguem."""
    garantir_macros(con)
    alvo = ident(destino)
    con.execute(f"CREATE OR REPLACE TABLE {alvo} AS SELECT * FROM {ident(origem)}")
    linhas_antes = _linhas(con, destino)
    colunas_antes = len(colunas_de(con, destino))

    resultados, partes = [], []
    anterior = origem
    for indice, passo in enumerate(receita, 1):
        resultado, parte = aplicar_passo(con, passo, indice, destino, destino, anterior)
        resultados.append(resultado)
        if parte:
            partes.append(parte)
            anterior = parte[2]

    return Relatorio(linhas_antes, _linhas(con, destino), colunas_antes,
                     len(colunas_de(con, destino)), resultados, _montar_sql(partes, origem), partes)


def aplicar_passo(con: duckdb.DuckDBPyConnection, passo: dict, indice: int, origem: str, destino: str,
                  anterior_no_sql: str) -> tuple[ResultadoPasso, list[str] | None]:
    """Cria `destino` aplicando um passo sobre `origem` (podem ser a mesma tabela).

    Se o passo falha, `destino` não muda. Devolve o resultado e o trecho de SQL
    do passo, escrito sobre `anterior_no_sql`, para montar o SQL da receita.
    """
    garantir_macros(con)
    titulo = descrever(passo)
    colunas = colunas_de(con, origem, internas=True)
    antes = _linhas(con, origem)
    try:
        selecao = compilar(passo, colunas, ident(origem))
        alterados, perdidos = _medir(con, passo, origem)
        con.execute(f"CREATE OR REPLACE TABLE {ident(destino)} AS {selecao}")
    except (KeyError, TypeError, ValueError, duckdb.Error) as erro:
        mensagem = str(erro).strip().splitlines()[0][:200]
        return ResultadoPasso(indice, titulo, str(passo.get("op")), "falhou", antes, antes, erro=mensagem), None

    depois = _linhas(con, destino)
    if passo["op"] in {"remover_linhas", "filtrar_linhas"}:
        alterados = antes - depois
    parte = [titulo, compilar(passo, colunas, anterior_no_sql), f"passo_{indice}"]
    return ResultadoPasso(indice, titulo, passo["op"], "ok", antes, depois, alterados, perdidos), parte


def _medir(con, passo: dict, tabela: str) -> tuple[int, int]:
    """Quantos valores o passo muda e quantos ele perde, antes de executar."""
    op = passo["op"]
    if op == "editar_celulas":
        return manipulacao.edicoes_perdidas(con, passo, dict(colunas_de(con, tabela)))
    if op not in EXPRESSOES:
        return 0, 0
    nome = passo["coluna"]
    tipo = dict(colunas_de(con, tabela))[nome]
    c = ident(nome)
    nova = EXPRESSOES[op](passo)
    alterados, perdidos = con.execute(f"""
        SELECT count(*) FILTER (CAST({c} AS VARCHAR) IS DISTINCT FROM CAST({nova} AS VARCHAR)),
               count(*) FILTER (NOT {eh_nulo(nome, tipo)} AND ({nova}) IS NULL)
        FROM {ident(tabela)}
    """).fetchone()
    return alterados, perdidos


def montar_sql(partes: list[list[str]], origem: str) -> str:
    return _montar_sql(partes, origem)


def _montar_sql(partes: list[tuple[str, str, str]], origem: str) -> str:
    if not partes:
        return f"SELECT * FROM {origem};"
    blocos = [f"{nome} AS (\n  -- {titulo.replace(chr(10), ' ')}\n  {selecao}\n)" for titulo, selecao, nome in partes]
    return "WITH " + ",\n".join(blocos) + f"\nSELECT * FROM {partes[-1][2]};"


def _linhas(con, tabela: str) -> int:
    return con.execute(f"SELECT count(*) FROM {ident(tabela)}").fetchone()[0]


# ----- sugestões -----

def sugerir(con: duckdb.DuckDBPyConnection, tabela: str, perfil: Perfil,
            limites: Limites = LOCAL) -> list[Sugestao]:
    """Os passos que fazem sentido para este arquivo, na ordem em que devem rodar."""
    garantir_macros(con)
    ativas = [c for c in perfil.colunas if c.vazio not in {"vazia", "constante"}]
    sugestoes = []

    for coluna in ativas:
        if coluna.vazios_disfarcados:
            sugestoes.append(_sugerir_vazios(con, tabela, coluna))
    for coluna in ativas:
        if coluna.com_espacos and coluna.tipo == "texto":
            sugestoes.append(_sugerir_aparar(con, tabela, coluna))
    for coluna in ativas:
        if coluna.tipo == "texto" and coluna.distintos <= MAX_DISTINTOS_VARIANTES:
            sugestao = _sugerir_padronizar(con, tabela, coluna)
            if sugestao:
                sugestoes.append(sugestao)
    for coluna in ativas:
        sugestao = _sugerir_conversao(con, tabela, coluna)
        if sugestao:
            sugestoes.append(sugestao)

    sugestoes += _sugerir_linhas(perfil)
    for coluna in ativas:
        sugestao = _sugerir_extremos(con, tabela, coluna)
        if sugestao:
            sugestoes.append(sugestao)
    sugestoes += _sugerir_colunas(perfil, limites)
    calculo = _sugerir_calculo(perfil)
    if calculo:
        sugestoes.append(calculo)
    return sugestoes


def _sugerir_vazios(con, tabela, coluna: PerfilColuna) -> Sugestao:
    passo = {"op": "vazios_para_nulo", "coluna": coluna.nome}
    return Sugestao(
        f"vazios:{coluna.nome}", "vazios", descrever(passo),
        "Textos como N/A, - e #N/D escondem células vazias e atrapalham contagens e conversões.",
        passo, True, afetados=coluna.vazios_disfarcados,
        exemplos=_exemplos(con, tabela, coluna.nome, _expr_vazios(passo)),
    )


def _sugerir_aparar(con, tabela, coluna: PerfilColuna) -> Sugestao:
    passo = {"op": "aparar_espacos", "coluna": coluna.nome}
    return Sugestao(
        f"aparar:{coluna.nome}", "texto", descrever(passo),
        "Espaços sobrando fazem o mesmo valor contar como dois.",
        passo, True, afetados=coluna.com_espacos,
        exemplos=_exemplos(con, tabela, coluna.nome, _expr_aparar(passo)),
    )


def _sugerir_padronizar(con, tabela, coluna: PerfilColuna) -> Sugestao | None:
    mapa, afetados = _variantes(con, tabela, coluna)
    if not mapa:
        return None
    passo = {"op": "padronizar_valores", "coluna": coluna.nome, "mapa": mapa}
    exemplos = [[de, para] for de, para in list(mapa.items())[:4]]
    return Sugestao(
        f"padronizar:{coluna.nome}", "texto", descrever(passo),
        f"{len(mapa)} grafias diferentes do mesmo valor seriam contadas separadas.",
        passo, True, afetados=afetados, exemplos=exemplos,
    )


def _variantes(con, tabela, coluna: PerfilColuna) -> tuple[dict[str, str], int]:
    """Agrupa grafias do mesmo valor: "SP", "sp" e "São Paulo" viram "SP".

    Espaço e pontuação não separam valores: "Chat GPT" é "ChatGPT" e "Master's"
    é "Masters". Em colunas de gênero, "M" e "F" viram a forma por extenso que
    já existe na coluna.
    """
    c = ident(coluna.nome)
    linhas = con.execute(rf"""
        SELECT regexp_replace(trim({c}), '\s+', ' ', 'g') AS valor, count(*) AS n
        FROM {ident(tabela)} WHERE NOT {eh_nulo(coluna.nome, coluna.tipo_duckdb)}
        GROUP BY 1
    """).fetchall()
    total = sum(n for _, n in linhas)
    if not total:
        return {}, 0

    em_uf = sum(n for valor, n in linhas if nome_simples(valor) in CHAVES_UF)
    eh_uf = em_uf / total >= 0.8

    grupos = defaultdict(list)
    for valor, n in linhas:
        chave = nome_simples(valor)
        if eh_uf and chave in CHAVES_UF:
            chave = CHAVES_UF[chave]
        else:
            chave = chave.replace("_", "")
        grupos[chave].append((n, valor))
    _juntar_abreviacoes(grupos)

    mapa, afetados = {}, 0
    for chave, grafias in grupos.items():
        canonica = chave if eh_uf and chave in UFS else max(grafias, key=_preferencia)[1]
        for n, valor in sorted(grafias, reverse=True):
            if valor != canonica:
                mapa[valor] = canonica
                afetados += n
    return mapa, afetados


def _preferencia(grafia: tuple[int, str]):
    """A grafia mais usada vence. No empate, "Gemini" ganha de "gemini" e de "GEMINI"."""
    n, valor = grafia
    return n, valor[:1].isupper(), not (valor.isupper() and len(valor) > 3), valor


def _juntar_abreviacoes(grupos: dict[str, list]) -> None:
    """Só age em colunas de gênero, e só quando a forma por extenso é uma só.

    Fora desse contexto "M" pode ser tamanho de roupa, e "O" um tipo sanguíneo.
    """
    if not any(chave in grupos for chave in FORMAS_DE_GENERO):
        return
    for abreviacao, formas in ABREVIACOES_GENERO.items():
        presentes = [forma for forma in formas if forma in grupos]
        if abreviacao in grupos and len(presentes) == 1:
            grupos[presentes[0]] += grupos.pop(abreviacao)


def _sugerir_conversao(con, tabela, coluna: PerfilColuna) -> Sugestao | None:
    ja_tipada = coluna.tipo_duckdb != "VARCHAR" and coluna.formatos != ["%Y"]
    if ja_tipada or coluna.papel == "id":
        return None

    if coluna.tipo in TIPOS_CONVERTIVEIS:
        passo = {"op": "converter_numero", "coluna": coluna.nome, "tipo": coluna.tipo,
                 "milhar_ponto": coluna.milhar_ponto}
        expressao = _expr_numero(passo)
    elif coluna.tipo == "data":
        passo = {"op": "converter_data", "coluna": coluna.nome, "formatos": coluna.formatos}
        expressao = _expr_data(passo)
    elif coluna.tipo == "booleano":
        passo = {"op": "converter_booleano", "coluna": coluna.nome}
        expressao = _expr_booleano(passo)
    else:
        return None

    perdidos, invalidos = _invalidos(con, tabela, coluna, expressao)
    motivo = "Enquanto for texto, esta coluna não pode ser somada nem ordenada."
    if coluna.misto:
        motivo = "A coluna mistura mais de um formato. A conversão entende todos."
    exemplos = _exemplos(con, tabela, coluna.nome, expressao)
    exemplos += [[valor, None] for valor in invalidos]
    return Sugestao(
        f"converter:{coluna.nome}", "tipos", descrever(passo), motivo, passo, True,
        afetados=max(_preenchidos(con, tabela, coluna) - perdidos, 0), perdidos=perdidos, exemplos=exemplos,
    )


def _invalidos(con, tabela, coluna: PerfilColuna, expressao: str) -> tuple[int, list[str]]:
    filtro = f"NOT {eh_nulo(coluna.nome, coluna.tipo_duckdb)} AND ({expressao}) IS NULL"
    c = ident(coluna.nome)
    total = con.execute(f"SELECT count(*) FROM {ident(tabela)} WHERE {filtro}").fetchone()[0]
    amostra = con.execute(
        f"SELECT DISTINCT CAST({c} AS VARCHAR) FROM {ident(tabela)} WHERE {filtro} LIMIT 3"
    ).fetchall()
    return total, [linha[0] for linha in amostra]


def _preenchidos(con, tabela, coluna: PerfilColuna) -> int:
    filtro = f"NOT {eh_nulo(coluna.nome, coluna.tipo_duckdb)}"
    return con.execute(f"SELECT count(*) FROM {ident(tabela)} WHERE {filtro}").fetchone()[0]


def _exemplos(con, tabela, nome: str, expressao: str) -> list[list]:
    c = ident(nome)
    linhas = con.execute(f"""
        SELECT DISTINCT CAST({c} AS VARCHAR), CAST({expressao} AS VARCHAR)
        FROM {ident(tabela)}
        WHERE {c} IS NOT NULL AND CAST({c} AS VARCHAR) IS DISTINCT FROM CAST({expressao} AS VARCHAR)
        LIMIT 4
    """).fetchall()
    return [list(linha) for linha in linhas]


def _sugerir_linhas(perfil: Perfil) -> list[Sugestao]:
    sugestoes = []
    if perfil.linhas_vazias:
        passo = {"op": "remover_linhas_vazias"}
        sugestoes.append(Sugestao(
            "linhas_vazias", "linhas", descrever(passo),
            "Linhas sem nenhum dado, comuns no fim de planilhas exportadas.",
            passo, True, afetados=perfil.linhas_vazias,
        ))
    if perfil.linhas_duplicadas:
        tem_id = any(c.papel == "id" for c in perfil.colunas)
        motivo = "Linhas idênticas, inclusive no identificador: o mesmo registro entrou duas vezes."
        if not tem_id:
            motivo = ("Linhas idênticas. Sem uma coluna de identificador, podem ser registros "
                      "diferentes: confira antes de remover.")
        passo = {"op": "remover_duplicatas"}
        sugestoes.append(Sugestao(
            "duplicatas", "linhas", descrever(passo), motivo, passo, tem_id,
            afetados=perfil.linhas_duplicadas,
        ))
    return sugestoes


def _sugerir_extremos(con, tabela, coluna: PerfilColuna) -> Sugestao | None:
    """Vem desmarcada: só quem conhece os dados sabe se 999 é erro ou uma venda real."""
    if coluna.papel != "metrica" or coluna.tipo not in TIPOS_CONVERTIVEIS:
        return None
    expressao = ident(coluna.nome)
    if coluna.tipo_duckdb == "VARCHAR":
        expressao = _expr_numero({"coluna": coluna.nome, "tipo": coluna.tipo, "milhar_ponto": coluna.milhar_ponto})
    numero = f"CAST({expressao} AS DOUBLE)"

    faixa = faixa_normal(con, ident(tabela), numero)
    if faixa is None:
        return None
    baixo, alto, total = faixa
    fora, maior = con.execute(
        f"SELECT count(*), max({numero}) FROM {ident(tabela)} WHERE {numero} NOT BETWEEN ? AND ?", [baixo, alto]
    ).fetchone()
    if not fora or fora / total > 0.01:
        return None

    passo = {"op": "remover_extremos", "coluna": coluna.nome, "minimo": round(baixo, 2), "maximo": round(alto, 2)}
    return Sugestao(
        f"extremos:{coluna.nome}", "linhas", descrever(passo),
        f"Valores muito fora do padrão (o maior é {maior:g}) distorcem somas e escondem tendências. "
        "Vem desmarcada: remova só se for erro de digitação.",
        passo, False, afetados=fora,
    )


def _sugerir_colunas(perfil: Perfil, limites: Limites) -> list[Sugestao]:
    sugestoes = []
    removiveis = [c for c in perfil.colunas if c.sugerir_remover]
    # Se todas as colunas forem removíveis, o problema é o arquivo, não as colunas.
    if removiveis and len(removiveis) < len(perfil.colunas):
        passo = {"op": "remover_colunas", "colunas": [c.nome for c in removiveis]}
        partes = [f"{c.nome} ({'vazia' if c.vazio == 'vazia' else 'um valor só'})" for c in removiveis]
        sugestoes.append(Sugestao(
            "remover_colunas", "colunas", descrever(passo),
            "Sem informação: " + ", ".join(partes) + ".",
            passo, True, afetados=len(removiveis),
        ))
    validas = perfil.linhas - perfil.linhas_vazias
    for coluna in perfil.colunas:
        if coluna.vazio == "quase_vazia":
            passo = {"op": "remover_colunas", "colunas": [coluna.nome]}
            fracao = coluna.nulos / validas if validas else 0
            sugestoes.append(Sugestao(
                f"quase_vazia:{coluna.nome}", "colunas", descrever(passo),
                f"{fracao:.0%} vazia, mas o vazio pode ter significado. Vem desmarcada: "
                "remova só se a coluna não for útil.",
                passo, False, afetados=1,
            ))
    return sugestoes


def _sugerir_calculo(perfil: Perfil) -> Sugestao | None:
    numericas = [c for c in perfil.colunas if c.tipo in TIPOS_CONVERTIVEIS and c.papel == "metrica"]
    quantidade = next((c for c in numericas if nome_simples(c.nome).startswith(NOMES_QUANTIDADE)), None)
    preco = next((c for c in numericas if any(n in nome_simples(c.nome) for n in NOMES_PRECO)), None)
    tem_total = any(nome_simples(c.nome).startswith(NOMES_TOTAL) for c in perfil.colunas)
    if not quantidade or not preco or tem_total:
        return None

    passo = {"op": "coluna_calculada", "nome": "valor_total", "a": quantidade.nome,
             "operador": "*", "b": preco.nome}
    return Sugestao(
        "calculo:valor_total", "calculo", descrever(passo),
        "Preço e quantidade sozinhos não dizem quanto foi vendido. Esta coluna permite somar o total.",
        passo, True,
    )


def unidades_da_receita(receita: list[dict]) -> dict[str, str]:
    """Quais colunas são dinheiro ou porcentagem, depois da receita."""
    unidades = {}
    for passo in receita:
        if passo.get("op") == "converter_numero":
            if passo.get("tipo") == "moeda":
                unidades[passo["coluna"]] = "BRL"
            elif passo.get("tipo") == "percentual":
                unidades[passo["coluna"]] = "%"
        if passo.get("op") == "coluna_calculada":
            operandos = {unidades.get(passo["a"]), unidades.get(passo["b"])}
            if "BRL" in operandos and passo["operador"] in {"+", "-", "*"}:
                unidades[passo["nome"]] = "BRL"
        if passo.get("op") == "formula":
            texto = str(passo.get("formula") or "")
            if re.search(r"CONVERT", texto, re.I) and re.search(r"[\"']BRL[\"']", texto, re.I):
                unidades[passo["nome"]] = "BRL"
            else:
                unidades.pop(passo.get("nome"), None)
        if passo.get("op") == "renomear_coluna" and passo.get("coluna") in unidades:
            unidades[passo["nome"]] = unidades.pop(passo["coluna"])
    return unidades


def colunas_derivadas(receita: list[dict]) -> dict[str, set[str]]:
    """Coluna calculada -> colunas de onde ela veio. Correlação entre elas é óbvia."""
    from engine import formulas

    derivadas = {}
    for passo in receita:
        if passo.get("op") == "coluna_calculada":
            derivadas[passo["nome"]] = {passo["a"], passo["b"]}
        elif passo.get("op") == "formula":
            derivadas[passo["nome"]] = formulas.colunas_usadas(passo)
    return derivadas
