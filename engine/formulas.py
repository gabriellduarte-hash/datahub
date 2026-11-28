"""Fórmulas de coluna, em português, traduzidas para SQL.

Uma fórmula vale para a coluna inteira: `= MonthlyCharges * 12` faz a mesma
conta em todas as linhas. Fórmula por célula, como `=A2*B7`, fica de fora de
propósito: ela depende da posição, e basta filtrar ou ordenar para apontar
para outra linha.

Escrita como no Excel em português:

- argumentos separados por `;`, e então a vírgula pode ser decimal (`2,5`).
  Sem nenhum `;`, o separador é a vírgula e o decimal é o ponto;
- texto entre aspas, coluna pelo nome (`tenure`) ou entre colchetes quando tem
  espaço (`[Average Cost for two]`);
- `SOMA(valor)` com um argumento é o total da coluna; com vários, a soma deles
  na linha. Vale o mesmo para MEDIA, MAXIMO, MINIMO e CONT.

O texto do usuário nunca entra cru no SQL: a fórmula vira uma árvore, e cada
pedaço é escrito com ident() e literal().
"""

import difflib
import re
from dataclasses import dataclass

from engine.sql import eh_interna, ident, literal, nome_simples


class FormulaInvalida(ValueError):
    """A fórmula não pode ser entendida. A mensagem é para o usuário."""


@dataclass
class Token:
    tipo: str      # numero, texto, coluna, nome, op, fim
    valor: str
    posicao: int


@dataclass
class Expressao:
    sql: str
    tipo: str      # inteiro, numero, texto, data, booleano, desconhecido
    agregada: bool = False


TIPOS_INTEIROS = {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT"}
CONSTANTES = {"VERDADEIRO": Expressao("true", "booleano"), "TRUE": Expressao("true", "booleano"),
              "FALSO": Expressao("false", "booleano"), "FALSE": Expressao("false", "booleano")}
COMPARACOES = {"=": "=", "<>": "<>", "!=": "<>", "<": "<", ">": ">", "<=": "<=", ">=": ">="}


def tipo_logico(tipo_duckdb: str) -> str:
    if tipo_duckdb in TIPOS_INTEIROS:
        return "inteiro"
    if tipo_duckdb in {"DOUBLE", "FLOAT"} or tipo_duckdb.startswith("DECIMAL"):
        return "numero"
    if tipo_duckdb == "BOOLEAN":
        return "booleano"
    if tipo_duckdb.startswith("DATE") or tipo_duckdb.startswith("TIMESTAMP"):
        return "data"
    if tipo_duckdb == "VARCHAR":
        return "texto"
    return "desconhecido"


# ----- texto para tokens -----

def _tokens(formula: str) -> list[Token]:
    texto = formula.strip()
    if texto.startswith("="):
        texto = texto[1:]
    if not texto.strip():
        raise FormulaInvalida("A fórmula está vazia.")
    sem_textos = re.sub(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'', "", texto)
    # Com ";" a vírgula é decimal, como no Excel em português. Sem ";" e sem
    # função, também: "= valor * 1,5". Com função e sem ";", a vírgula separa
    # argumentos, como no Excel em inglês, e o decimal é o ponto.
    virgula_decimal = ";" in sem_textos or "(" not in sem_textos
    numero = r"\d+(?:[.,]\d+)?" if virgula_decimal else r"\d+(?:\.\d+)?"
    separador = ";" if virgula_decimal else ","
    padrao = re.compile(
        r"\s+"
        r'|(?P<texto>"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\')'
        r"|(?P<coluna>\[[^\]]*\])"
        rf"|(?P<numero>{numero})"
        r"|(?P<op><=|>=|<>|!=|[-+*/^&=<>()" + re.escape(separador) + r"])"
        r"|(?P<nome>[^\W\d][\w.]*)"
    )
    tokens, posicao = [], 0
    while posicao < len(texto):
        achado = padrao.match(texto, posicao)
        if not achado:
            trecho = texto[posicao:posicao + 12]
            raise FormulaInvalida(f"Não entendi o trecho “{trecho}”, na posição {posicao + 1}.")
        if achado.lastgroup:
            valor = achado.group(achado.lastgroup)
            tipo = achado.lastgroup
            if tipo == "op" and valor == separador:
                tipo, valor = "separador", ";"
            tokens.append(Token(tipo, valor, posicao + 1))
        posicao = achado.end()
    tokens.append(Token("fim", "", len(texto) + 1))
    return tokens


# ----- tokens para SQL -----

class _Tradutor:
    def __init__(self, tokens: list[Token], colunas: dict[str, str]):
        self.tokens = tokens
        self.i = 0
        self.colunas = {nome: tipo for nome, tipo in colunas.items() if not eh_interna(nome)}
        self.usadas: set[str] = set()
        self.usa_cambio = False

    # navegação
    def atual(self) -> Token:
        return self.tokens[self.i]

    def comer(self, tipo: str, valor: str | None = None) -> Token:
        token = self.atual()
        if token.tipo != tipo or (valor is not None and token.valor != valor):
            esperado = valor or tipo
            achado = token.valor or "o fim da fórmula"
            raise FormulaInvalida(f"Esperava “{esperado}” na posição {token.posicao}, e veio “{achado}”.")
        self.i += 1
        return token

    def eh(self, tipo: str, *valores: str) -> bool:
        token = self.atual()
        return token.tipo == tipo and (not valores or token.valor in valores)

    # gramática
    def traduzir(self) -> Expressao:
        expressao = self.comparacao()
        if not self.eh("fim"):
            token = self.atual()
            if token.valor == ")":
                raise FormulaInvalida(f"Parêntese fechado a mais na posição {token.posicao}.")
            raise FormulaInvalida(f"Não esperava “{token.valor}” na posição {token.posicao}.")
        return expressao

    def comparacao(self) -> Expressao:
        esquerda = self.concatenacao()
        while self.eh("op", *COMPARACOES):
            operador = COMPARACOES[self.comer("op").valor]
            direita = self.concatenacao()
            esquerda = Expressao(f"({esquerda.sql} {operador} {direita.sql})", "booleano",
                                 esquerda.agregada or direita.agregada)
        return esquerda

    def concatenacao(self) -> Expressao:
        esquerda = self.soma()
        while self.eh("op", "&"):
            self.comer("op")
            direita = self.soma()
            esquerda = Expressao(f"concat(CAST({esquerda.sql} AS VARCHAR), CAST({direita.sql} AS VARCHAR))", "texto")
        return esquerda

    def soma(self) -> Expressao:
        esquerda = self.produto()
        while self.eh("op", "+", "-"):
            operador = self.comer("op").valor
            direita = self.produto()
            esquerda = self._aritmetica(esquerda, operador, direita)
        return esquerda

    def produto(self) -> Expressao:
        esquerda = self.potencia()
        while self.eh("op", "*", "/"):
            operador = self.comer("op").valor
            direita = self.potencia()
            esquerda = self._aritmetica(esquerda, operador, direita)
        return esquerda

    def potencia(self) -> Expressao:
        esquerda = self.unario()
        while self.eh("op", "^"):
            self.comer("op")
            direita = self.unario()
            esquerda = Expressao(f"power({_numero(esquerda)}, {_numero(direita)})", "numero")
        return esquerda

    def unario(self) -> Expressao:
        if self.eh("op", "-", "+"):
            sinal = self.comer("op").valor
            valor = self.unario()
            return Expressao(f"({sinal}{_numero(valor)})", valor.tipo if valor.tipo in {"inteiro", "numero"} else "numero")
        return self.primario()

    def primario(self) -> Expressao:
        token = self.atual()
        if token.tipo == "numero":
            self.i += 1
            texto = token.valor.replace(",", ".")
            return Expressao(texto, "numero" if "." in texto else "inteiro")
        if token.tipo == "texto":
            self.i += 1
            aspas = token.valor[0]
            return Expressao(literal(token.valor[1:-1].replace(aspas * 2, aspas)), "texto")
        if token.tipo == "coluna":
            self.i += 1
            return self._coluna(token.valor[1:-1].strip(), token)
        if token.tipo == "nome":
            self.i += 1
            if self.eh("op", "("):
                return self._funcao(token)
            if token.valor.upper() in CONSTANTES:
                return CONSTANTES[token.valor.upper()]
            return self._coluna(token.valor, token)
        if self.eh("op", "("):
            self.comer("op", "(")
            dentro = self.comparacao()
            if not self.eh("op", ")"):
                raise FormulaInvalida(f"Faltou fechar o parêntese aberto antes da posição {self.atual().posicao}.")
            self.comer("op", ")")
            return Expressao(f"({dentro.sql})", dentro.tipo, dentro.agregada)
        if token.tipo == "fim":
            raise FormulaInvalida("A fórmula terminou no meio de uma conta.")
        raise FormulaInvalida(f"Não esperava “{token.valor}” na posição {token.posicao}.")

    def _coluna(self, nome: str, token: Token) -> Expressao:
        encontrada = self._achar_coluna(nome)
        if encontrada is None and self.eh("nome"):
            # "nome completo" sem colchetes chega aqui como duas palavras.
            juntas = f"{nome} {self.atual().valor}"
            if self._achar_coluna(juntas):
                raise FormulaInvalida(f"Para nomes com espaço, use colchetes: [{juntas}].")
        if encontrada is None:
            parecidas = difflib.get_close_matches(nome, list(self.colunas), n=1, cutoff=0.6)
            dica = f" Você quis dizer {parecidas[0]}?" if parecidas else ""
            espaco = " Para nomes com espaço, use colchetes: [nome da coluna]." if " " in nome else ""
            raise FormulaInvalida(f"A coluna {nome} não existe.{dica}{espaco}")
        self.usadas.add(encontrada)
        return Expressao(ident(encontrada), tipo_logico(self.colunas[encontrada]))

    def _achar_coluna(self, nome: str) -> str | None:
        if nome in self.colunas:
            return nome
        for coluna in self.colunas:
            if coluna.lower() == nome.lower():
                return coluna
        for coluna in self.colunas:
            if nome_simples(coluna) == nome_simples(nome):
                return coluna
        return None

    def _funcao(self, token: Token) -> Expressao:
        nome = _sem_acento(token.valor.upper())
        self.comer("op", "(")
        argumentos = []
        if not self.eh("op", ")"):
            argumentos.append(self.comparacao())
            while self.eh("separador"):
                self.comer("separador")
                argumentos.append(self.comparacao())
        if not self.eh("op", ")"):
            raise FormulaInvalida(f"Faltou fechar o parêntese de {token.valor} (posição {token.posicao}).")
        self.comer("op", ")")
        funcao = FUNCOES.get(nome)
        if funcao is None:
            parecidas = difflib.get_close_matches(nome, list(FUNCOES), n=1, cutoff=0.6)
            dica = f" Você quis dizer {parecidas[0]}?" if parecidas else ""
            raise FormulaInvalida(f"Não conheço a função {token.valor}.{dica}")
        minimo, maximo, traduzir = funcao
        if len(argumentos) < minimo or (maximo is not None and len(argumentos) > maximo):
            if maximo == minimo:
                faixa = f"{minimo}"
            elif maximo is None:
                faixa = f"pelo menos {minimo}"
            else:
                faixa = f"de {minimo} a {maximo}"
            dica = ""
            if any(t.tipo == "separador" for t in self.tokens) and ";" not in [t.valor for t in self.tokens if t.tipo != "separador"]:
                dica = " Se havia um número decimal com vírgula, separe os argumentos com ; ou use ponto (1.5)."
            raise FormulaInvalida(f"{token.valor} recebe {faixa} {'argumento' if faixa == '1' else 'argumentos'}, "
                                  f"e a fórmula passou {len(argumentos)}.{dica}")
        if nome == "CONVERTER":
            self.usa_cambio = True
        return traduzir(*argumentos)

    @staticmethod
    def _aritmetica(a: Expressao, operador: str, b: Expressao) -> Expressao:
        agregada = a.agregada or b.agregada
        if a.tipo == "data" and operador in "+-" and b.tipo in {"inteiro", "numero"}:
            return Expressao(f"(CAST({a.sql} AS DATE) {operador} CAST({b.sql} AS INTEGER))", "data", agregada)
        if a.tipo == "data" and b.tipo == "data" and operador == "-":
            return Expressao(f"date_diff('day', CAST({b.sql} AS DATE), CAST({a.sql} AS DATE))", "inteiro", agregada)
        if operador == "/":
            return Expressao(f"(CAST({_numero(a)} AS DOUBLE) / nullif({_numero(b)}, 0))", "numero", agregada)
        tipo = "inteiro" if a.tipo == b.tipo == "inteiro" else "numero"
        return Expressao(f"({_numero(a)} {operador} {_numero(b)})", tipo, agregada)


def _sem_acento(texto: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()


def _numero(e: Expressao) -> str:
    """Texto entra em conta quando der para ler como número; o resto vira vazio."""
    if e.tipo == "texto":
        return f"TRY_CAST({e.sql} AS DOUBLE)"
    if e.tipo == "booleano":
        return f"CAST({e.sql} AS INTEGER)"
    return e.sql


def _texto(e: Expressao) -> str:
    return e.sql if e.tipo == "texto" else f"CAST({e.sql} AS VARCHAR)"


def _janela(funcao: str, e: Expressao, tipo: str = "numero") -> Expressao:
    """Um argumento só: o total da coluna inteira, repetido em cada linha."""
    return Expressao(f"{funcao}({_numero(e)}) OVER ()", tipo, True)


def _lista(funcao: str, argumentos, tipo: str = "numero") -> Expressao:
    """Vários argumentos: a conta entre eles, na mesma linha. Vazios são ignorados."""
    return Expressao(f"{funcao}([{', '.join(_numero(a) for a in argumentos)}])", tipo)


def _agregadora(janela: str, lista: str):
    def traduzir(*argumentos):
        if len(argumentos) == 1:
            return _janela(janela, argumentos[0])
        return _lista(lista, argumentos)
    return traduzir


def _criterio(coluna: Expressao, criterio: Expressao) -> str:
    """CONT.SE(coluna; ">10") compara; CONT.SE(coluna; "SP") procura igual."""
    if criterio.tipo == "texto" and criterio.sql.startswith("'"):
        bruto = criterio.sql[1:-1].replace("''", "'")
        achado = re.fullmatch(r"\s*(<=|>=|<>|!=|=|<|>)\s*(.*)", bruto)
        if achado:
            operador, resto = COMPARACOES[achado.group(1)], achado.group(2).strip()
            if re.fullmatch(r"-?\d+(?:[.,]\d+)?", resto):
                return f"{_numero(coluna)} {operador} {resto.replace(',', '.')}"
            return f"{_texto(coluna)} {operador} {literal(resto)}"
        return f"{_texto(coluna)} = {criterio.sql}"
    return f"{coluna.sql} = {criterio.sql}"


def _converter(valor: Expressao, moeda: Expressao, destino: Expressao, data: Expressao | None = None) -> Expressao:
    from engine import cambio

    if data is not None and data.tipo not in {"data", "inteiro"}:
        raise FormulaInvalida("O 4º argumento de CONVERTER precisa ser uma data (câmbio do dia) "
                              "ou um ano, como ANO(data) (média do ano).")
    modo = None if data is None else ("dia" if data.tipo == "data" else "ano")
    sql = cambio.expressao(_numero(valor), _texto(moeda), _texto(destino), data.sql if data else None, modo)
    return Expressao(sql, "numero", valor.agregada)


FUNCOES = {
    # lógica
    "SE": (2, 3, lambda c, a, b=None: Expressao(
        f"CASE WHEN {c.sql} THEN {a.sql} ELSE {b.sql if b else 'NULL'} END", a.tipo if a.tipo != "desconhecido" else (b.tipo if b else "desconhecido"))),
    "E": (1, None, lambda *a: Expressao("(" + " AND ".join(x.sql for x in a) + ")", "booleano")),
    "OU": (1, None, lambda *a: Expressao("(" + " OR ".join(x.sql for x in a) + ")", "booleano")),
    "NAO": (1, 1, lambda a: Expressao(f"(NOT {a.sql})", "booleano")),
    "SEVAZIO": (2, 2, lambda a, b: Expressao(f"coalesce({a.sql}, {b.sql})", a.tipo)),
    "EVAZIO": (1, 1, lambda a: Expressao(f"({a.sql} IS NULL)", "booleano")),
    # números
    "ARRED": (1, 2, lambda a, n=None: Expressao(f"round({_numero(a)}, {n.sql if n else 0})", "numero")),
    "ABS": (1, 1, lambda a: Expressao(f"abs({_numero(a)})", a.tipo if a.tipo == "inteiro" else "numero")),
    "RAIZ": (1, 1, lambda a: Expressao(f"sqrt({_numero(a)})", "numero")),
    "POTENCIA": (2, 2, lambda a, b: Expressao(f"power({_numero(a)}, {_numero(b)})", "numero")),
    "LN": (1, 1, lambda a: Expressao(f"ln(nullif({_numero(a)}, 0))", "numero")),
    "INT": (1, 1, lambda a: Expressao(f"CAST(floor({_numero(a)}) AS BIGINT)", "inteiro")),
    "MOD": (2, 2, lambda a, b: Expressao(f"({_numero(a)} % nullif({_numero(b)}, 0))", "numero")),
    "VALOR": (1, 1, lambda a: Expressao(f"TRY_CAST({_texto(a)} AS DOUBLE)", "numero")),
    # totais da coluna (um argumento) ou da linha (vários)
    "SOMA": (1, None, _agregadora("sum", "list_sum")),
    "MEDIA": (1, None, _agregadora("avg", "list_avg")),
    "MAXIMO": (1, None, _agregadora("max", "list_max")),
    "MINIMO": (1, None, _agregadora("min", "list_min")),
    "MEDIANA": (1, 1, lambda a: _janela("median", a)),
    "CONT": (1, 1, lambda a: Expressao(f"count({a.sql}) OVER ()", "inteiro", True)),
    "CONT.SE": (2, 2, lambda c, k: Expressao(
        f"sum(CASE WHEN {_criterio(c, k)} THEN 1 ELSE 0 END) OVER ()", "inteiro", True)),
    "SOMASE": (2, 3, lambda c, k, s=None: Expressao(
        f"sum(CASE WHEN {_criterio(c, k)} THEN {_numero(s or c)} END) OVER ()", "numero", True)),
    # texto
    "MAIUSCULA": (1, 1, lambda a: Expressao(f"upper({_texto(a)})", "texto")),
    "MINUSCULA": (1, 1, lambda a: Expressao(f"lower({_texto(a)})", "texto")),
    "ARRUMAR": (1, 1, lambda a: Expressao(f"trim(regexp_replace({_texto(a)}, '\\s+', ' ', 'g'))", "texto")),
    "NUM.CARACT": (1, 1, lambda a: Expressao(f"length({_texto(a)})", "inteiro")),
    "ESQUERDA": (1, 2, lambda a, n=None: Expressao(f"left({_texto(a)}, {n.sql if n else 1})", "texto")),
    "DIREITA": (1, 2, lambda a, n=None: Expressao(f"right({_texto(a)}, {n.sql if n else 1})", "texto")),
    "EXT.TEXTO": (3, 3, lambda a, i, n: Expressao(f"substr({_texto(a)}, {i.sql}, {n.sql})", "texto")),
    "SUBSTITUIR": (3, 3, lambda a, de, para: Expressao(f"replace({_texto(a)}, {_texto(de)}, {_texto(para)})", "texto")),
    "CONCATENAR": (1, None, lambda *a: Expressao(f"concat({', '.join(_texto(x) for x in a)})", "texto")),
    "CONTEM": (2, 2, lambda a, t: Expressao(f"contains(lower({_texto(a)}), lower({_texto(t)}))", "booleano")),
    "TEXTO": (1, 1, lambda a: Expressao(_texto(a), "texto")),
    # datas
    "ANO": (1, 1, lambda a: Expressao(f"year(CAST({a.sql} AS DATE))", "inteiro")),
    "MES": (1, 1, lambda a: Expressao(f"month(CAST({a.sql} AS DATE))", "inteiro")),
    "DIA": (1, 1, lambda a: Expressao(f"day(CAST({a.sql} AS DATE))", "inteiro")),
    "DIA.DA.SEMANA": (1, 1, lambda a: Expressao(f"isodow(CAST({a.sql} AS DATE))", "inteiro")),
    "HOJE": (0, 0, lambda: Expressao("current_date", "data")),
    "DATA": (3, 3, lambda a, m, d: Expressao(f"make_date({_numero(a)}, {_numero(m)}, {_numero(d)})", "data")),
    "DIAS": (2, 2, lambda fim, ini: Expressao(
        f"date_diff('day', CAST({ini.sql} AS DATE), CAST({fim.sql} AS DATE))", "inteiro")),
    # moedas
    "CONVERTER": (3, 4, _converter),
}
# Nomes em inglês, para quem vem do Excel em inglês ou do Google Sheets.
SINONIMOS = {
    "IF": "SE", "AND": "E", "OR": "OU", "NOT": "NAO", "IFNULL": "SEVAZIO", "ISBLANK": "EVAZIO",
    "ROUND": "ARRED", "SQRT": "RAIZ", "POWER": "POTENCIA", "VALUE": "VALOR", "SUM": "SOMA",
    "AVERAGE": "MEDIA", "AVG": "MEDIA", "MAX": "MAXIMO", "MIN": "MINIMO", "MEDIAN": "MEDIANA",
    "COUNT": "CONT", "COUNTIF": "CONT.SE", "SUMIF": "SOMASE", "UPPER": "MAIUSCULA", "LOWER": "MINUSCULA",
    "TRIM": "ARRUMAR", "LEN": "NUM.CARACT", "LEFT": "ESQUERDA", "RIGHT": "DIREITA", "MID": "EXT.TEXTO",
    "REPLACE": "SUBSTITUIR", "SUBSTITUTE": "SUBSTITUIR", "CONCAT": "CONCATENAR", "CONTAINS": "CONTEM",
    "TEXT": "TEXTO", "YEAR": "ANO", "MONTH": "MES", "DAY": "DIA", "WEEKDAY": "DIA.DA.SEMANA",
    "TODAY": "HOJE", "DATE": "DATA", "DAYS": "DIAS", "CONVERT": "CONVERTER", "NAO.VAZIO": "EVAZIO",
}
FUNCOES.update({sinonimo: FUNCOES[original] for sinonimo, original in SINONIMOS.items()})


# ----- uso -----

@dataclass
class Traducao:
    sql: str
    tipo: str
    colunas: set[str]
    usa_cambio: bool


def traduzir(formula: str, colunas: dict[str, str]) -> Traducao:
    """Fórmula para expressão SQL. `colunas` é {nome: tipo do DuckDB}."""
    tradutor = _Tradutor(_tokens(formula), colunas)
    expressao = tradutor.traduzir()
    return Traducao(expressao.sql, expressao.tipo, tradutor.usadas, tradutor.usa_cambio)


def _validar_nome(nome: str, existentes: list[str], substituir: bool) -> str:
    nome = str(nome or "").strip()
    if not nome:
        raise FormulaInvalida("Dê um nome para a coluna nova.")
    if eh_interna(nome):
        raise FormulaInvalida('Nomes que começam com "__" são do próprio hub.')
    repetida = next((c for c in existentes if c.lower() == nome.lower()), None)
    if repetida and not substituir:
        raise FormulaInvalida(f"Já existe uma coluna chamada {repetida}. Marque substituir para trocar os valores dela.")
    return repetida or nome


def compilar_passo(passo: dict, colunas: list[tuple[str, str]], origem: str) -> str:
    """O SELECT do passo de fórmula: acrescenta a coluna nova ou troca os valores de uma existente.

    `vazia` cria a coluna em branco, para preencher à mão como numa planilha.
    `apos` põe a coluna nova logo depois de outra, e `antes`, logo antes; sem
    nenhum dos dois, ela vai para o fim.
    """
    visiveis = [(nome, tipo) for nome, tipo in colunas if not eh_interna(nome)]
    if passo.get("vazia"):
        expressao = "CAST(NULL AS VARCHAR)"
    else:
        expressao = traduzir(str(passo.get("formula") or ""), dict(visiveis)).sql
    substituir = bool(passo.get("substituir"))
    nome = _validar_nome(passo.get("nome"), [n for n, _ in visiveis], substituir)
    if any(n == nome for n, _ in visiveis):
        return f"SELECT * REPLACE (({expressao}) AS {ident(nome)}) FROM {origem}"
    nomes = [n for n, _ in visiveis]
    apos, antes = passo.get("apos"), passo.get("antes")
    if apos in nomes or antes in nomes:
        nova, lista = f"({expressao}) AS {ident(nome)}", []
        for coluna, _ in colunas:
            if coluna == antes and apos not in nomes:
                lista.append(nova)
            lista.append(ident(coluna))
            if coluna == apos:
                lista.append(nova)
        return f"SELECT {', '.join(lista)} FROM {origem}"
    return f"SELECT *, ({expressao}) AS {ident(nome)} FROM {origem}"


def colunas_usadas(passo: dict) -> set[str]:
    """As colunas de onde a fórmula tirou valores. Sem validar: serve para evitar correlações óbvias."""
    usadas = set()
    for token in _tokens_sem_erro(str(passo.get("formula") or "")):
        if token.tipo == "coluna":
            usadas.add(token.valor[1:-1].strip())
        elif token.tipo == "nome":
            usadas.add(token.valor)
    return usadas


def _tokens_sem_erro(formula: str) -> list[Token]:
    try:
        return _tokens(formula)
    except FormulaInvalida:
        return []


def previa(con, tabela: str, passo: dict) -> dict:
    """Valida a fórmula e mostra os primeiros resultados, sem mudar a tabela."""
    import duckdb

    from engine.sql import LINHA, colunas_de

    todas = colunas_de(con, tabela, internas=True)
    visiveis = [(nome, tipo) for nome, tipo in todas if not eh_interna(nome)]
    if passo.get("vazia"):
        _validar_nome(passo.get("nome"), [n for n, _ in visiveis], False)
        return {"tipo": "texto", "colunas": [], "previa": [], "linhas": 0, "vazios": 0, "sql": "NULL"}
    traducao = traduzir(str(passo.get("formula") or ""), dict(visiveis))
    _validar_nome(passo.get("nome"), [n for n, _ in visiveis], bool(passo.get("substituir")))
    numero = ident(LINHA) if LINHA in dict(todas) else "NULL"
    ordem = f"ORDER BY {ident(LINHA)}" if LINHA in dict(todas) else ""
    try:
        linhas = con.execute(
            f"SELECT {numero}, ({traducao.sql}) FROM {ident(tabela)} {ordem} LIMIT 8"
        ).fetchall()
        total, vazios = con.execute(
            f"SELECT count(*), count(*) FILTER (v IS NULL) FROM (SELECT ({traducao.sql}) AS v FROM {ident(tabela)})"
        ).fetchone()
    except duckdb.Error as erro:
        raise FormulaInvalida(_explicar_erro_do_banco(erro)) from erro
    return {
        "sql": traducao.sql,
        "tipo": traducao.tipo,
        "colunas": sorted(traducao.colunas),
        "previa": [{"linha": linha, "valor": valor if isinstance(valor, (int, float, str, bool)) or valor is None
                    else str(valor)} for linha, valor in linhas],
        "linhas": total,
        "vazios": vazios,
    }


def _explicar_erro_do_banco(erro) -> str:
    texto = str(erro).strip().splitlines()[0]
    if "Cannot compare" in texto or "Could not convert" in texto or "No function matches" in texto:
        return ("Os tipos não combinam: a fórmula compara ou soma texto com número. "
                "Use VALOR(coluna) para ler um texto como número. Detalhe: " + texto[:160])
    return "O banco recusou a fórmula: " + texto[:200]
