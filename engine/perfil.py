"""Bloco 2, perfil: o que existe em cada coluna e o que está errado.

Regras:
- `linhas` conta todas as linhas, inclusive as vazias.
- Linha vazia: todas as colunas nulas. É contada à parte e fica fora das
  contas das colunas e das duplicatas.
- Nulo: célula vazia ou um dos VAZIOS_DISFARCADOS ("N/A", "-", "#N/D"...).
- Classificação de coluna (`vazio`), sobre as linhas não vazias:
    "vazia"        todos os valores nulos
    "constante"    nenhum nulo e um único valor distinto
    "quase_vazia"  fração de nulos >= limites.limite_quase_vazia
    None           o resto
  Uma coluna com um único valor E nulos (ex.: "SIM" ou vazio) não é
  constante: é um indicador, e o nulo é metade da informação.
- `sugerir_remover` só é True para "vazia" e "constante". Quem decide é o
  usuário; o engine só sugere.
- `papel` diz como a coluna será usada nos gráficos e insights: métrica,
  categoria, data, id, indicador, texto ou ignorar. O usuário pode trocar.
- `agregacao`: numa tabela sem coluna de data, cada linha costuma ser uma
  pessoa ou um cliente, e somar idade, horas de sono ou nota não diz nada.
  Nesse caso toda métrica usa média. Com data, as linhas são eventos (vendas,
  pedidos) e o nome da coluna decide.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from engine.leitura import ler
from engine.limites import LOCAL, Limites
from engine.sql import colunas_de, eh_nulo, ident, nome_simples
from engine.tipos import TipoDetectado, detectar, tipo_de_coluna_tipada

# Acima disso o número de valores distintos é aproximado: contar exato pesa.
CONTA_EXATA_ATE = 1_000_000
LINHAS_AMOSTRA_TIPOS = 50_000
MAX_DISTINTOS_CATEGORIA = 200

NOME_ID = re.compile(
    r"(^|_)(id|cod|codigo|code|cpf|cnpj|cep|zip|telefone|celular|phone|matricula|sku|ean|chave|protocolo|nsu)(_|$)"
)
NOME_TEMPO = re.compile(
    r"(^|_)(ano|mes|dia|semana|trimestre|semestre|hora|year|month|day|week|weekday|quarter|hour)(_|$)"
)
NOME_ANO = re.compile(r"(^|_)(ano|year)(_|$)")
NOME_QUANTIDADE = re.compile(r"qtd|qtde|quant|total|valor|vlr|soma|count|amount")
NOME_NAO_SOMAVEL = re.compile(
    r"unit|preco|price|taxa|rate|ratio|percent|rating|"
    r"(^|_)(pct|perc)(_|$)|"
    r"(^|_)(media|avg|mean|idade|age|nota(?!_fiscal)|score|temperatura|temperature|temp|saldo|balance|"
    r"indice|index|cotacao|latitude|longitude|lat|lon|lng)(_|$)"
)
# Escalas de nota (1 a 5, 0 a 10) são medidas, mesmo com poucos valores distintos.
NOME_ESCALA = re.compile(
    r"(^|_)(rating|ratings|score|nota|notas|avaliacao|satisfacao|satisfaction|stars|estrelas|likert)(_|$)"
)
# Texto longo e quase todo diferente é descrição, não identificador.
COMPRIMENTO_TEXTO_LIVRE = 40
# Coordenadas são números, mas comparar médias de latitude só diz onde fica cada lugar.
NOME_COORDENADA = re.compile(r"(^|_)(latitude|longitude|lat|lon|lng|long)(_|$)")
TIPOS_NUMERICOS = {"inteiro", "decimal", "moeda", "percentual"}


@dataclass
class PerfilColuna:
    posicao: int
    nome: str
    tipo_duckdb: str          # o tipo guardado no DuckDB: VARCHAR, BIGINT...
    nulos: int                # vazios de verdade + disfarçados
    vazios_disfarcados: int   # só os disfarçados
    distintos: int            # sem contar nulos
    vazio: str | None         # "vazia", "constante", "quase_vazia" ou None
    sugerir_remover: bool
    tipo: str = "texto"       # inteiro, decimal, moeda, percentual, data, booleano, texto
    papel: str = "texto"      # metrica, categoria, data, id, indicador, texto, ignorar
    agregacao: str = "soma"   # como somar esta coluna por padrão
    com_espacos: int = 0      # valores com espaços sobrando
    formatos: list[str] = field(default_factory=list)
    milhar_ponto: bool = True
    misto: bool = False       # mistura mais de um jeito de escrever
    sequencial: bool = False  # inteiros quase seguidos, como 1, 2, 3...
    comprimento_medio: float = 0.0   # em caracteres, na amostra
    exemplos: list[str] = field(default_factory=list)


@dataclass
class Perfil:
    linhas: int
    linhas_vazias: int
    linhas_duplicadas: int    # cópias extras: uma linha que aparece 3 vezes conta 2
    colunas: list[PerfilColuna]


def perfilar(caminho: Path, limites: Limites = LOCAL) -> Perfil:
    """Atalho para analisar um arquivo fora de uma sessão."""
    con = duckdb.connect()
    ler(con, caminho, limites)
    return perfilar_tabela(con, "bruta", limites)


def perfilar_tabela(con: duckdb.DuckDBPyConnection, tabela: str, limites: Limites = LOCAL) -> Perfil:
    colunas = colunas_de(con, tabela)
    nulos_sql = [eh_nulo(nome, tipo) for nome, tipo in colunas]
    total, vazias, por_coluna = _contar(con, tabela, colunas, nulos_sql)
    validas = total - vazias
    duplicadas = _duplicadas(con, tabela, colunas, nulos_sql)

    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE __amostra AS
        SELECT * FROM {ident(tabela)} USING SAMPLE reservoir({LINHAS_AMOSTRA_TIPOS} ROWS) REPEATABLE (42)
    """)

    perfis = []
    for posicao, (nome, tipo_duckdb) in enumerate(colunas):
        nulos, disfarcados, distintos, com_espacos = por_coluna[posicao]
        vazio = _classificar_vazio(validas, nulos, distintos, limites)

        if tipo_duckdb == "VARCHAR":
            detectado = detectar(con, "__amostra", nome, nulos_sql[posicao])
        else:
            detectado = TipoDetectado(tipo_de_coluna_tipada(tipo_duckdb))
        detectado = _ajustar_pelo_nome(con, nome, nulos_sql[posicao], detectado)

        coluna = PerfilColuna(
            posicao=posicao,
            nome=nome,
            tipo_duckdb=tipo_duckdb,
            nulos=nulos,
            vazios_disfarcados=disfarcados,
            distintos=distintos,
            vazio=vazio,
            sugerir_remover=vazio in {"vazia", "constante"},
            tipo=detectado.tipo,
            com_espacos=com_espacos,
            formatos=detectado.formatos,
            milhar_ponto=detectado.milhar_ponto,
            misto=detectado.misto,
            exemplos=_exemplos(con, nome, nulos_sql[posicao]),
        )
        coluna.sequencial = coluna.tipo == "inteiro" and _eh_sequencial(con, tabela, coluna)
        if coluna.tipo == "texto":
            coluna.comprimento_medio = _comprimento_medio(con, nome, nulos_sql[posicao])
        coluna.papel = inferir_papel(coluna, validas)
        coluna.agregacao = inferir_agregacao(coluna)
        perfis.append(coluna)

    ajustar_agregacoes(perfis)
    return Perfil(total, vazias, duplicadas, perfis)


def ajustar_agregacoes(colunas: list[PerfilColuna]) -> None:
    """Sem coluna de data, as linhas são pessoas ou clientes: métrica usa média."""
    if any(c.papel == "data" for c in colunas):
        return
    for coluna in colunas:
        if coluna.papel == "metrica":
            coluna.agregacao = "media"


def _comprimento_medio(con, nome: str, nulo: str) -> float:
    media = con.execute(
        f"SELECT avg(length(CAST({ident(nome)} AS VARCHAR))) FROM __amostra WHERE NOT {nulo}"
    ).fetchone()[0]
    return round(float(media or 0), 1)


def _eh_sequencial(con, tabela: str, coluna: PerfilColuna) -> bool:
    """Um identificador ocupa quase toda a faixa entre o menor e o maior valor."""
    c = f"TRY_CAST({ident(coluna.nome)} AS HUGEINT)"
    minimo, maximo = con.execute(f"SELECT min({c}), max({c}) FROM {ident(tabela)}").fetchone()
    return minimo is not None and maximo - minimo + 1 <= 2 * coluna.distintos


def _ajustar_pelo_nome(con, nome: str, nulo: str, detectado: TipoDetectado) -> TipoDetectado:
    """O nome da coluna resolve o que os valores sozinhos não dizem."""
    if detectado.tipo == "inteiro" and NOME_ANO.search(nome_simples(nome)):
        minimo, maximo = con.execute(
            f"SELECT min(TRY_CAST({ident(nome)} AS INT)), max(TRY_CAST({ident(nome)} AS INT)) "
            f"FROM __amostra WHERE NOT {nulo}"
        ).fetchone()
        if minimo is not None and 1800 <= minimo and maximo <= 2200:
            return TipoDetectado("data", formatos=["%Y"])
    if detectado.tipo in {"inteiro", "decimal"} and "R$" in nome:
        detectado.tipo = "moeda"
    return detectado


def _contar(con, tabela, colunas, nulos_sql):
    """Uma varredura só, com todas as contas de todas as colunas."""
    marcas = ", ".join(f"{expressao} AS __n{i}" for i, expressao in enumerate(nulos_sql))
    vazia = " AND ".join(f"__n{i}" for i in range(len(colunas)))

    total = con.execute(f"SELECT count(*) FROM {ident(tabela)}").fetchone()[0]
    distinto = "count(DISTINCT {})" if total <= CONTA_EXATA_ATE else "approx_count_distinct({})"

    contas = []
    for i, (nome, tipo) in enumerate(colunas):
        c = ident(nome)
        contas += [
            f"count(*) FILTER (NOT __vazia AND __n{i})",
            f"count(*) FILTER (NOT __vazia AND __n{i} AND {c} IS NOT NULL)",
            distinto.format(c) + f" FILTER (NOT __n{i})",
            f"count(*) FILTER (NOT __n{i} AND {c} <> trim({c}))" if tipo == "VARCHAR" else "0",
        ]

    linha = con.execute(f"""
        WITH marcada AS (SELECT *, {marcas} FROM {ident(tabela)}),
             com_vazia AS (SELECT *, ({vazia}) AS __vazia FROM marcada)
        SELECT count(*) FILTER (__vazia), {", ".join(contas)} FROM com_vazia
    """).fetchone()

    vazias = linha[0]
    valores = linha[1:]
    por_coluna = [tuple(valores[i:i + 4]) for i in range(0, len(valores), 4)]
    return total, vazias, por_coluna


def _duplicadas(con, tabela, colunas, nulos_sql) -> int:
    """Compara só as colunas do arquivo: o número da linha é sempre diferente."""
    nao_vazia = "NOT (" + " AND ".join(nulos_sql) + ")"
    lista = ", ".join(ident(nome) for nome, _ in colunas)
    return con.execute(f"""
        WITH cheias AS (SELECT {lista} FROM {ident(tabela)} WHERE {nao_vazia})
        SELECT (SELECT count(*) FROM cheias) - (SELECT count(*) FROM (SELECT DISTINCT * FROM cheias))
    """).fetchone()[0]


def _classificar_vazio(validas: int, nulos: int, distintos: int, limites: Limites) -> str | None:
    if validas == 0 or nulos == validas:
        return "vazia"
    if validas > 1 and nulos == 0 and distintos == 1:
        return "constante"
    if nulos / validas >= limites.limite_quase_vazia:
        return "quase_vazia"
    return None


def _exemplos(con, nome: str, nulo: str) -> list[str]:
    c = ident(nome)
    linhas = con.execute(
        f"SELECT DISTINCT CAST({c} AS VARCHAR) FROM __amostra WHERE NOT {nulo} LIMIT 3"
    ).fetchall()
    return [linha[0] for linha in linhas]


def inferir_papel(coluna: PerfilColuna, validas: int) -> str:
    if coluna.vazio in {"vazia", "constante"}:
        return "ignorar"
    if coluna.tipo == "data":
        return "data"

    nome = nome_simples(coluna.nome)
    preenchidos = validas - coluna.nulos
    quase_tudo_unico = preenchidos >= 20 and coluna.distintos >= 0.95 * preenchidos

    if NOME_ID.search(nome):
        return "id"
    if coluna.tipo in TIPOS_NUMERICOS and NOME_COORDENADA.search(nome):
        return "ignorar"
    if coluna.tipo in TIPOS_NUMERICOS:
        if coluna.tipo == "inteiro":
            if quase_tudo_unico and coluna.sequencial:
                return "id"
            if NOME_TEMPO.search(nome) and coluna.distintos <= 100:
                return "categoria"
            if coluna.distintos <= 2:
                return "indicador"
            # Poucos inteiros pequenos costumam ser código: classe 1, 2 ou 3.
            # Uma nota de 1 a 5 também tem poucos valores, mas é medida.
            if coluna.distintos <= 5 and not NOME_QUANTIDADE.search(nome) and not NOME_ESCALA.search(nome):
                return "categoria"
        return "metrica"
    if coluna.tipo == "booleano" or coluna.distintos == 1:
        return "indicador"
    if quase_tudo_unico:
        return "texto" if coluna.comprimento_medio >= COMPRIMENTO_TEXTO_LIVRE else "id"
    if coluna.distintos <= MAX_DISTINTOS_CATEGORIA:
        return "categoria"
    return "texto"


def inferir_agregacao(coluna: PerfilColuna) -> str:
    """Somar preço unitário não diz nada: nesses casos o padrão é a média."""
    if coluna.tipo == "percentual" or NOME_NAO_SOMAVEL.search(nome_simples(coluna.nome)):
        return "media"
    return "soma"
