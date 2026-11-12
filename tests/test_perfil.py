"""O que o perfil precisa acertar. Fica vermelho até engine/perfil.py existir."""

from dataclasses import replace

import pytest

from engine.limites import LOCAL, LimiteExcedido
from engine.perfil import perfilar
from samples.gerar_vendas import gerar


@pytest.fixture(scope="module")
def amostra(tmp_path_factory):
    return gerar(tmp_path_factory.mktemp("vendas"), linhas=2000)


@pytest.fixture(scope="module")
def perfil(amostra):
    caminho, _ = amostra
    return perfilar(caminho)


def test_contagem_de_linhas(perfil, amostra):
    _, gabarito = amostra
    assert perfil.linhas == gabarito["linhas_total"]
    assert perfil.linhas_vazias == gabarito["linhas_vazias"]
    assert perfil.linhas_duplicadas == gabarito["linhas_duplicadas"]


# As colunas são comparadas pela posição: o DuckDB dá um nome próprio para a
# coluna que veio sem cabeçalho.

def test_nulos_por_coluna(perfil, amostra):
    _, gabarito = amostra
    assert len(perfil.colunas) == len(gabarito["colunas"])
    for esperado in gabarito["colunas"]:
        coluna = perfil.colunas[esperado["posicao"]]
        assert coluna.nulos == esperado["nulos"], coluna.nome
        assert coluna.vazios_disfarcados == esperado["vazios_disfarcados"], coluna.nome


def test_classificacao_de_vazio(perfil, amostra):
    _, gabarito = amostra
    for esperado in gabarito["colunas"]:
        coluna = perfil.colunas[esperado["posicao"]]
        assert coluna.vazio == esperado["vazio"], coluna.nome
        assert coluna.sugerir_remover == esperado["sugerir_remover"], coluna.nome


def test_indicador_nao_e_constante(perfil):
    # usou_cupom tem só "SIM" ou vazio. Removê-la apagaria quem usou cupom.
    cupom = next(c for c in perfil.colunas if c.nome == "usou_cupom")
    assert cupom.vazio is None
    assert not cupom.sugerir_remover


@pytest.mark.parametrize("campo, maximo", [
    ("max_bytes_arquivo", 1_000),
    ("max_colunas", 5),
    ("max_linhas", 100),
    ("max_celulas", 10_000),
])
def test_limites_de_carga(amostra, campo, maximo):
    caminho, _ = amostra
    with pytest.raises(LimiteExcedido) as erro:
        perfilar(caminho, replace(LOCAL, **{campo: maximo}))
    assert erro.value.limite == campo.removeprefix("max_")
