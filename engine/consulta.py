"""Vista SQL: o usuário escreve a consulta, o hub roda com três cuidados.

1. Só consulta. O próprio DuckDB diz o tipo de cada comando antes de rodar:
   SELECT e WITH passam; DROP, COPY, ATTACH, SET e dois comandos juntos não.
2. Sem disco. A conexão da sessão abre sem acesso a arquivos, então nem uma
   consulta consegue ler `/etc/passwd` com read_csv.
3. Com tempo. Uma consulta que passa do limite é interrompida.

O resultado fica guardado numa tabela da sessão: virar a página não roda a
consulta de novo, e "salvar como tabela" aproveita o que já foi calculado.
"""

import hashlib
import threading
import time

import duckdb

from engine.sql import colunas_de, ident

RESULTADO = "__resultado_sql"


class ConsultaRecusada(ValueError):
    """A consulta não pode rodar. A mensagem é para o usuário."""


def validar(sql: str) -> str:
    """Devolve a consulta pronta para rodar, ou recusa explicando por quê."""
    texto = (sql or "").strip()
    while texto.endswith(";"):
        texto = texto[:-1].rstrip()
    if not texto:
        raise ConsultaRecusada("Escreva uma consulta.")
    try:
        comandos = duckdb.extract_statements(texto)
    except duckdb.Error as erro:
        raise ConsultaRecusada("Não entendi o SQL: " + _primeira_linha(erro)) from erro
    if len(comandos) != 1:
        raise ConsultaRecusada(f"Rode um comando por vez. Vieram {len(comandos)}.")
    tipo = str(comandos[0].type).split(".")[-1]
    if tipo != "SELECT":
        raise ConsultaRecusada(f"Aqui só rodam consultas (SELECT ou WITH). Este comando é {tipo}.")
    return texto


def chave(sql: str, versao: str) -> str:
    """Muda quando a consulta muda ou quando alguma tabela da sessão muda."""
    return hashlib.sha256(f"{versao}\n{sql}".encode()).hexdigest()[:24]


def executar(con, sql: str, versao: str, guardada: str | None, inicio: int = 0, quantas: int = 100,
             tempo_limite: int = 15, maximo: int = 500) -> dict:
    """Roda (ou reaproveita) a consulta e devolve uma página do resultado."""
    texto = validar(sql)
    marca = chave(texto, versao)
    duracao = 0
    existe = con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [RESULTADO]
    ).fetchone()[0]
    if marca != guardada or not existe:
        inicio_relogio = time.perf_counter()
        relogio = threading.Timer(tempo_limite, con.interrupt)
        relogio.start()
        try:
            con.execute(f"CREATE OR REPLACE TABLE {RESULTADO} AS SELECT * FROM ({texto})")
        except duckdb.InterruptException as erro:
            raise ConsultaRecusada(f"A consulta passou de {tempo_limite} s e foi interrompida.") from erro
        except duckdb.Error as erro:
            raise ConsultaRecusada(_explicar(con, erro)) from erro
        finally:
            relogio.cancel()
        duracao = round((time.perf_counter() - inicio_relogio) * 1000)

    quantas = max(1, min(int(quantas), maximo))
    colunas = colunas_de(con, RESULTADO, internas=True)
    total = con.execute(f"SELECT count(*) FROM {RESULTADO}").fetchone()[0]
    linhas = con.execute(f"SELECT * FROM {RESULTADO} LIMIT ? OFFSET ?", [quantas, max(0, int(inicio))]).fetchall()
    return {
        "chave": marca,
        "colunas": [{"nome": nome, "tipo": tipo} for nome, tipo in colunas],
        "linhas": [list(linha) for linha in linhas],
        "total": total,
        "inicio": max(0, int(inicio)),
        "duracao_ms": duracao,
        "reaproveitada": duracao == 0,
    }


def _primeira_linha(erro) -> str:
    return str(erro).strip().splitlines()[0][:220]


def _explicar(con, erro) -> str:
    texto = _primeira_linha(erro)
    if "does not exist" in texto and ("Table" in texto or "table" in texto):
        visiveis = [linha[0] for linha in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE NOT starts_with(table_name, '__') ORDER BY 1"
        ).fetchall()]
        return f"Essa tabela não existe. As tabelas desta sessão são: {', '.join(visiveis) or 'nenhuma'}."
    if "external access" in texto.lower() or "disabled" in texto.lower():
        return "Consultas não leem arquivos do computador. Use as tabelas da sessão."
    return "O banco recusou a consulta: " + texto


def salvar_como_sql(sql: str) -> str:
    """O texto que vai para o arquivo .sql baixado."""
    return validar(sql) + ";\n"


def colunas_do_resultado(con) -> list[tuple[str, str]]:
    return colunas_de(con, RESULTADO, internas=True)


def selecao_salvavel(sql: str) -> str:
    return f"SELECT * FROM ({validar(sql)})"


def nome_resultado() -> str:
    return ident(RESULTADO)
