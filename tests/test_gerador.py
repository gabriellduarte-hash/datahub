"""Confere o gabarito contra o arquivo, sem usar o engine.

Se o gabarito estiver errado, os testes do perfil passam ou falham pelo
motivo errado.
"""

import csv
from collections import Counter

import pytest

from engine.limites import LOCAL, VAZIOS_DISFARCADOS
from samples.gerar_vendas import gerar


def eh_nulo(valor: str) -> bool:
    return valor.strip().lower() in VAZIOS_DISFARCADOS


@pytest.fixture(scope="module")
def amostra(tmp_path_factory):
    caminho, gabarito = gerar(tmp_path_factory.mktemp("vendas"), linhas=2000)
    with caminho.open(newline="", encoding="utf-8") as f:
        cabecalho, *linhas = csv.reader(f, delimiter=";")
    cheias = [linha for linha in linhas if not all(eh_nulo(v) for v in linha)]
    return cabecalho, linhas, cheias, gabarito


def test_linhas(amostra):
    _, linhas, cheias, gabarito = amostra
    assert len(linhas) == gabarito["linhas_total"]
    assert len(linhas) - len(cheias) == gabarito["linhas_vazias"]
    copias = Counter(tuple(linha) for linha in cheias)
    assert sum(n - 1 for n in copias.values()) == gabarito["linhas_duplicadas"]


def test_nulos_por_coluna(amostra):
    cabecalho, _, cheias, gabarito = amostra
    for esperado in gabarito["colunas"]:
        valores = [linha[esperado["posicao"]] for linha in cheias]
        assert cabecalho[esperado["posicao"]] == esperado["nome"]
        assert sum(eh_nulo(v) for v in valores) == esperado["nulos"]
        assert sum(v != "" and eh_nulo(v) for v in valores) == esperado["vazios_disfarcados"]


def test_colunas_plantadas(amostra):
    cabecalho, _, cheias, _ = amostra

    def valores(nome):
        return [linha[cabecalho.index(nome)] for linha in cheias]

    assert all(eh_nulo(v) for v in valores("observacao"))
    assert all(eh_nulo(v) for v in valores(""))
    assert set(valores("pais")) == {"Brasil"}
    assert set(valores("usou_cupom")) == {"SIM", ""}
    fracao = sum(eh_nulo(v) for v in valores("data_cancelamento")) / len(cheias)
    assert LOCAL.limite_quase_vazia <= fracao < 1
