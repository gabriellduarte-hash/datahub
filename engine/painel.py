"""O painel executivo embaixo da tabela: indicadores da base e cada insight com o seu gráfico.

Cada insight diz qual gráfico mostra o dado que ele cita (barras por contrato,
a série no tempo, a dispersão das duas colunas) e o que destacar nele. Aqui
esses gráficos são calculados uma vez, no servidor, junto da análise.
"""

import json
from dataclasses import asdict

import duckdb

from engine import formato, graficos
from engine.consultas import agregado, datas, expressao_sim, indicadores, metricas
from engine.limites import LOCAL, Limites
from engine.perfil import Perfil
from engine.sql import ident

NOMES_AGREGACAO = {"soma": "soma", "media": "média"}


def anexar_graficos(con: duckdb.DuckDBPyConnection, tabela: str, unidades: dict[str, str], lista: list,
                    limites: Limites = LOCAL) -> None:
    """Troca o pedido de gráfico de cada insight pelo gráfico calculado.

    Dois insights que citam o mesmo dado usam o mesmo cálculo. Um gráfico que
    falha não derruba o insight: ele só aparece sem gráfico.
    """
    feitos: dict[str, dict | None] = {}
    for indice, insight in enumerate(lista):
        pedido = insight.grafico
        if not pedido:
            continue
        chave = json.dumps(pedido, sort_keys=True)
        if chave not in feitos:
            try:
                feitos[chave] = asdict(graficos.calcular(con, tabela, unidades, limites, pedido, f"i{indice}"))
            except (ValueError, KeyError, duckdb.Error):
                feitos[chave] = None
        insight.grafico = feitos[chave]


def resumo(con: duckdb.DuckDBPyConnection, tabela: str, perfil: Perfil, unidades: dict[str, str]) -> list[dict]:
    """Os números do topo do painel: tamanho, preenchimento, a métrica principal, a taxa de sim e o período."""
    validas = perfil.linhas - perfil.linhas_vazias
    itens = [
        {"icone": "linhas", "rotulo": "linhas", "valor": formato.inteiro(perfil.linhas)},
        {"icone": "colunas", "rotulo": "colunas", "valor": formato.inteiro(len(perfil.colunas))},
    ]
    celulas = validas * len(perfil.colunas)
    if celulas:
        nulos = sum(coluna.nulos for coluna in perfil.colunas)
        itens.append({"icone": "preenchido", "rotulo": "células preenchidas", "valor": formato.pct(1 - nulos / celulas)})

    medidas = metricas(perfil, unidades)
    if medidas:
        principal = medidas[0]
        agregacao = principal.agregacao if principal.agregacao in NOMES_AGREGACAO else "soma"
        valor = con.execute(f"SELECT {agregado(con, tabela, agregacao, principal.nome)} FROM {ident(tabela)}").fetchone()[0]
        if valor is not None:
            itens.append({"icone": "metrica", "rotulo": f"{NOMES_AGREGACAO[agregacao]} de {principal.nome}",
                          "valor": formato.compacto(float(valor), unidades.get(principal.nome))})

    for indicador in indicadores(perfil)[:1]:
        try:
            sim = expressao_sim(con, tabela, indicador.nome)
        except ValueError:
            continue
        taxa = con.execute(f"SELECT avg({sim.expressao}) FROM {ident(tabela)}").fetchone()[0]
        if taxa is not None:
            itens.append({"icone": "taxa", "rotulo": sim.sim, "valor": formato.pct(float(taxa))})

    tempos = datas(perfil)
    if tempos:
        d = ident(tempos[0].nome)
        inicio, fim = con.execute(f"SELECT min({d})::DATE, max({d})::DATE FROM {ident(tabela)}").fetchone()
        if inicio and fim:
            itens.append({"icone": "periodo", "rotulo": f"período de {tempos[0].nome}",
                          "valor": f"{formato.periodo(inicio, 'mes')} – {formato.periodo(fim, 'mes')}"})
    return itens
