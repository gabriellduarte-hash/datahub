"""Ferramentas para montar SQL com segurança.

Nomes de coluna e valores vêm do arquivo do usuário, então nunca entram crus
no SQL: sempre passam por ident() ou literal().
"""

import re
import unicodedata

import duckdb

from engine.limites import VAZIOS_DISFARCADOS


def ident(nome: str) -> str:
    """Nome de coluna ou tabela entre aspas duplas."""
    return '"' + nome.replace('"', '""') + '"'


def literal(valor: str) -> str:
    """Texto entre aspas simples."""
    return "'" + valor.replace("'", "''") + "'"


LISTA_VAZIOS = ", ".join(literal(v) for v in sorted(VAZIOS_DISFARCADOS))


def colunas_de(con: duckdb.DuckDBPyConnection, tabela: str) -> list[tuple[str, str]]:
    """Lista de (nome, tipo) da tabela, na ordem do arquivo."""
    return [(linha[0], linha[1]) for linha in con.execute(f"DESCRIBE {ident(tabela)}").fetchall()]


def eh_nulo(nome: str, tipo: str) -> str:
    """Expressão SQL: esta célula está vazia, de verdade ou disfarçada?"""
    coluna = ident(nome)
    if tipo != "VARCHAR":
        return f"{coluna} IS NULL"
    return f"({coluna} IS NULL OR lower(trim({coluna})) IN ({LISTA_VAZIOS}))"


def nome_simples(nome: str) -> str:
    """"Valor Unitário (R$)" -> "valor_unitario_r", "ageAtAward" -> "age_at_award".

    Para comparar nomes de coluna: camelCase vira palavras separadas.
    """
    sem_acento = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    separado = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", sem_acento)
    return re.sub(r"[^a-z0-9]+", "_", separado.lower()).strip("_")
