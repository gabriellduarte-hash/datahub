"""Liga os blocos em ordem: arquivo, perfil, normalização, gráficos e insights.

Cada bloco recebe o que o anterior produziu e devolve um resultado próprio. A
falha de um bloco aparece no estado dele e não apaga o que os anteriores fizeram.
"""

import time
from dataclasses import asdict, dataclass
from pathlib import Path

import duckdb

from engine import graficos, insights, normalizacao
from engine.leitura import ArquivoInvalido, ler
from engine.limites import LOCAL, LimiteExcedido, Limites
from engine.normalizacao import Relatorio, ResultadoPasso
from engine.perfil import Perfil, ajustar_agregacoes, inferir_agregacao, perfilar_tabela

BLOCOS = ("arquivo", "perfil", "normalizacao", "graficos", "insights")
PAPEIS = {"metrica", "categoria", "data", "id", "indicador", "texto", "ignorar"}


@dataclass
class EstadoBloco:
    bloco: str
    status: str               # ok, falhou ou pendente
    duracao_ms: int = 0
    erro: str | None = None


def _rodar(nome: str, funcao, estados: dict):
    """Roda um bloco e guarda o estado dele. Erros de arquivo sobem para quem chamou."""
    inicio = time.perf_counter()
    try:
        resultado = funcao()
    except (ArquivoInvalido, LimiteExcedido):
        raise
    except Exception as erro:
        estados[nome] = EstadoBloco(nome, "falhou", _ms(inicio), str(erro).strip().splitlines()[0][:200])
        return None
    estados[nome] = EstadoBloco(nome, "ok", _ms(inicio))
    return resultado


def _ms(inicio: float) -> int:
    return round((time.perf_counter() - inicio) * 1000)


def _lista(estados: dict) -> list[dict]:
    return [asdict(estados.get(nome, EstadoBloco(nome, "pendente"))) for nome in BLOCOS]


def abrir(con: duckdb.DuckDBPyConnection, caminho: Path, limites: Limites = LOCAL,
          aba: str | None = None, nome: str | None = None) -> dict:
    """Blocos 1 a 3: lê o arquivo, faz o perfil e sugere a normalização."""
    estados = {}
    leitura = _rodar("arquivo", lambda: ler(con, caminho, limites, aba, nome=nome), estados)
    perfil = _rodar("perfil", lambda: perfilar_tabela(con, "bruta", limites), estados)
    sugestoes = []
    if perfil:
        sugestoes = _rodar("normalizacao", lambda: normalizacao.sugerir(con, "bruta", perfil, limites), estados) or []
    return {
        "blocos": _lista(estados),
        "arquivo": asdict(leitura),
        "perfil": asdict(perfil) if perfil else None,
        "sugestoes": [asdict(s) for s in sugestoes],
    }


def normalizar(con: duckdb.DuckDBPyConnection, receita: list[dict], papeis: dict[str, str] | None = None,
               limites: Limites = LOCAL, estados_anteriores: list[dict] | None = None) -> dict:
    """Bloco 3 aplicado, e em seguida os blocos 4 e 5 sobre a tabela limpa."""
    estados = _estados_de(estados_anteriores)
    relatorio = _rodar("normalizacao", lambda: normalizacao.aplicar(con, receita), estados)
    if relatorio is None:
        return {"blocos": _lista(estados), "relatorio": None, "perfil_limpo": None,
                "graficos": [], "insights": None, "receita": receita}
    return {"relatorio": asdict(relatorio), "receita": receita,
            **_analisar(con, receita, relatorio, papeis, limites, estados)}


def reanalisar(con: duckdb.DuckDBPyConnection, receita: list[dict], relatorio: dict,
               papeis: dict[str, str] | None = None, limites: Limites = LOCAL,
               estados_anteriores: list[dict] | None = None) -> dict:
    """Refaz os blocos 4 e 5 sem repetir a limpeza: o usuário trocou o papel de uma coluna."""
    return _analisar(con, receita, _relatorio_de(relatorio), papeis, limites, _estados_de(estados_anteriores))


def _analisar(con, receita, relatorio, papeis, limites, estados) -> dict:
    unidades = normalizacao.unidades_da_receita(receita)
    derivadas = normalizacao.colunas_derivadas(receita)
    perfil = perfilar_tabela(con, "limpa", limites)
    aplicar_papeis(perfil, papeis)

    lista_graficos = _rodar("graficos", lambda: graficos.sugerir(con, "limpa", perfil, unidades, limites, derivadas), estados)
    resultado_insights = _rodar(
        "insights",
        lambda: insights.gerar(con, "limpa", perfil, unidades, limites, relatorio, derivadas),
        estados,
    )
    return {
        "blocos": _lista(estados),
        "perfil_limpo": asdict(perfil),
        "unidades": unidades,
        "graficos": [asdict(g) for g in lista_graficos or []],
        "insights": asdict(resultado_insights) if resultado_insights else None,
    }


def aplicar_papeis(perfil: Perfil, papeis: dict[str, str] | None) -> None:
    """O papel escolhido pelo usuário vale mais que o detectado.

    Marcar ou desmarcar uma data muda o jeito de somar as métricas, então a
    agregação é refeita.
    """
    if not papeis:
        return
    for coluna in perfil.colunas:
        papel = papeis.get(coluna.nome)
        if papel in PAPEIS:
            coluna.papel = papel
    for coluna in perfil.colunas:
        coluna.agregacao = inferir_agregacao(coluna)
    ajustar_agregacoes(perfil.colunas)


def _estados_de(lista: list[dict] | None) -> dict:
    return {item["bloco"]: EstadoBloco(**item) for item in lista or [] if item["status"] != "pendente"}


def _relatorio_de(dados: dict) -> Relatorio:
    passos = [ResultadoPasso(**passo) for passo in dados["passos"]]
    return Relatorio(**{**dados, "passos": passos})
