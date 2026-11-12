"""Bloco 4, gráficos: recomenda gráficos por regras e calcula os dados de cada um.

Os dados saem sempre agregados ou amostrados no DuckDB. A interface nunca recebe
milhões de pontos.
"""

from dataclasses import dataclass, field

import duckdb

from engine import formato
from engine.consultas import (GRAOS, categorias, datas, eh_alvo, expressao_sim, grao_automatico, indicadores,
                              metricas, por_grupo, ranking, serie)
from engine.limites import LOCAL, Limites
from engine.perfil import Perfil, PerfilColuna
from engine.sql import colunas_de, ident

PONTOS_DISPERSAO = 1_500
FAIXAS_HISTOGRAMA = 20
# Para escolher quais gráficos mostrar basta uma amostra; o gráfico em si usa tudo.
LINHAS_AMOSTRA_ESCOLHA = 200_000
MAX_GRUPOS_LEGIVEIS = 20
# Abaixo disso o gráfico de sim/não mostraria barras quase iguais.
MIN_V_GRAFICO = 0.1
MIN_EFEITO_GRAFICO = 0.3
NOMES_AGREGACAO = {
    "soma": "Soma", "media": "Média", "mediana": "Mediana",
    "minimo": "Mínimo", "maximo": "Máximo", "contagem": "Contagem", "taxa": "Taxa",
}


@dataclass
class Grafico:
    id: str
    titulo: str
    tipo: str                   # linha, barras, histograma ou dispersao
    x: str | None
    y: str | None
    agregacao: str = "soma"
    grao: str | None = None
    unidade: str | None = None
    dados: list[dict] = field(default_factory=list)
    aviso: str | None = None


def sugerir(con: duckdb.DuckDBPyConnection, tabela: str, perfil: Perfil, unidades: dict[str, str],
            limites: Limites = LOCAL, derivadas: dict[str, set[str]] | None = None) -> list[Grafico]:
    """Os gráficos mostram os pares que mais dizem: a categoria que mais separa
    cada métrica, a taxa de sim/não que mais muda e as métricas mais ligadas."""
    medidas = metricas(perfil, unidades)
    grupos = _legiveis(categorias(perfil))
    tempos = datas(perfil)
    fonte = _fonte_para_escolha(con, tabela, perfil.linhas)
    pedidos = []

    if tempos:
        if medidas:
            for metrica in medidas[:2]:
                pedidos.append({"tipo": "linha", "x": tempos[0].nome, "y": metrica.nome,
                                "agregacao": metrica.agregacao})
        else:
            pedidos.append({"tipo": "linha", "x": tempos[0].nome, "y": None, "agregacao": "contagem"})

    usados = set()
    for metrica, quantos in zip(medidas[:2], (2, 1)):
        for grupo in _grupos_que_mais_separam(con, tabela, fonte, metrica, grupos)[:quantos]:
            if (grupo, metrica.nome) not in usados:
                usados.add((grupo, metrica.nome))
                pedidos.append({"tipo": "barras", "x": grupo, "y": metrica.nome, "agregacao": metrica.agregacao})

    sims = indicadores(perfil)
    de_sim_nao = []
    taxa = _melhor_taxa(con, tabela, fonte, sims, grupos)
    if taxa:
        de_sim_nao.append({"tipo": "barras", "x": taxa[1], "y": taxa[0], "agregacao": "taxa"})
    lado = _metrica_que_mais_muda(con, tabela, fonte, sims, medidas)
    if lado:
        de_sim_nao.append({"tipo": "barras", "x": lado[0], "y": lado[1].nome, "agregacao": lado[1].agregacao})
    # Se a planilha parece perguntar quem cancelou ou quem comprou, essa resposta vem primeiro.
    if sims and eh_alvo(sims[0]):
        depois_das_linhas = sum(1 for p in pedidos if p["tipo"] == "linha")
        pedidos[depois_das_linhas:depois_das_linhas] = de_sim_nao
    else:
        pedidos += de_sim_nao

    if medidas:
        pedidos.append({"tipo": "histograma", "x": None, "y": medidas[0].nome})
        par = _par_para_dispersao(con, fonte, medidas, derivadas or {})
        if par:
            pedidos.append({"tipo": "dispersao", "x": par[0], "y": par[1]})
    elif not taxa:
        for grupo in grupos[:3]:
            pedidos.append({"tipo": "barras", "x": grupo.nome, "y": None, "agregacao": "contagem"})

    return [calcular(con, tabela, unidades, limites, pedido, f"g{i}") for i, pedido in enumerate(pedidos, 1)]


def _legiveis(grupos: list[PerfilColuna]) -> list[PerfilColuna]:
    """Barras com até 20 grupos. Se nenhuma categoria couber, as maiores entram mesmo assim."""
    return [g for g in grupos if g.distintos <= MAX_GRUPOS_LEGIVEIS] or grupos[:3]


def _fonte_para_escolha(con, tabela: str, linhas: int) -> str:
    if linhas <= LINHAS_AMOSTRA_ESCOLHA:
        return ident(tabela)
    return f"(SELECT * FROM {ident(tabela)} USING SAMPLE reservoir({LINHAS_AMOSTRA_ESCOLHA} ROWS) REPEATABLE (42))"


def _grupos_que_mais_separam(con, tabela, fonte, metrica: PerfilColuna, grupos: list[PerfilColuna]) -> list[str]:
    """Categorias ordenadas pela parte da variação da métrica que elas explicam (eta²).

    Uma categoria com muitos grupos explica um pouco só por acaso; esse pedaço,
    (grupos - 1) / linhas, é descontado.
    """
    if not grupos:
        return []
    m = f"TRY_CAST({ident(metrica.nome)} AS DOUBLE)"
    media, variancia, total = con.execute(f"SELECT avg({m}), var_pop({m}), count({m}) FROM {fonte}").fetchone()
    if not total or not variancia:
        return [g.nome for g in grupos]
    tipos = dict(colunas_de(con, tabela))
    tabela_grupos = por_grupo(con, fonte, [(g.nome, tipos[g.nome]) for g in grupos],
                              {"__m": m}, {"n": "count(__m)", "media": "avg(__m)"})
    notas = {}
    for nome, linhas in tabela_grupos.items():
        validas = [(d["n"], d["media"]) for _, d in linhas if d["n"]]
        entre = sum(n * (media_g - media) ** 2 for n, media_g in validas)
        notas[nome] = entre / (total * variancia) - (len(validas) - 1) / total
    return sorted((g.nome for g in grupos), key=lambda nome: -notas.get(nome, 0))


def _melhor_taxa(con, tabela, fonte, sims: list[PerfilColuna], grupos: list[PerfilColuna]) -> tuple[str, str] | None:
    """(sim/não, categoria) em que a taxa de sim mais muda (V de Cramér)."""
    if not sims or not grupos:
        return None
    for indicador in sims:
        try:
            sim = expressao_sim(con, tabela, indicador.nome)
        except ValueError:
            continue
        tipos = dict(colunas_de(con, tabela))
        tabela_grupos = por_grupo(con, fonte, [(g.nome, tipos[g.nome]) for g in grupos],
                                  {"__e": sim.expressao}, {"n": "count(__e)", "s": "sum(__e)"})
        melhor = None
        for nome, linhas in tabela_grupos.items():
            contagens = [(d["n"], float(d["s"] or 0)) for _, d in linhas if d["n"]]
            total = sum(n for n, _ in contagens)
            p = sum(s for _, s in contagens) / total if total else 0
            if not 0 < p < 1:
                continue
            v = (sum(n * (s / n - p) ** 2 for n, s in contagens) / (total * p * (1 - p))) ** 0.5
            if v >= MIN_V_GRAFICO and (melhor is None or v > melhor[0]):
                melhor = (v, nome)
        if melhor:
            return indicador.nome, melhor[1]
    return None


def _metrica_que_mais_muda(con, tabela, fonte, sims: list[PerfilColuna], medidas: list[PerfilColuna]):
    """(sim/não, métrica) em que os dois lados mais diferem, em desvios-padrão."""
    if not sims or not medidas:
        return None
    try:
        sim = expressao_sim(con, tabela, sims[0].nome)
    except ValueError:
        return None
    candidatas = [m for m in medidas[:15] if m.nome != sims[0].nome]
    if not candidatas:
        return None
    contas = ", ".join(
        f"avg(TRY_CAST({ident(m.nome)} AS DOUBLE)) FILTER (__e = 1), "
        f"avg(TRY_CAST({ident(m.nome)} AS DOUBLE)) FILTER (__e = 0), "
        f"stddev_pop(TRY_CAST({ident(m.nome)} AS DOUBLE))"
        for m in candidatas
    )
    linha = con.execute(f"SELECT {contas} FROM (SELECT {sim.expressao} AS __e, * FROM {fonte})").fetchone()
    melhor = None
    for j, metrica in enumerate(candidatas):
        sim_media, nao_media, desvio = linha[3 * j:3 * j + 3]
        if sim_media is None or nao_media is None or not desvio:
            continue
        efeito = abs(sim_media - nao_media) / desvio
        if efeito >= MIN_EFEITO_GRAFICO and (melhor is None or efeito > melhor[0]):
            melhor = (efeito, metrica)
    return (sims[0].nome, melhor[1]) if melhor else None


def _par_para_dispersao(con, fonte, medidas, derivadas: dict[str, set[str]]) -> tuple[str, str] | None:
    """O par de métricas mais ligado (correlação de postos), fora coluna contra a conta feita dela."""
    candidatas = medidas[:8]
    pares = [
        (a.nome, b.nome) for i, a in enumerate(candidatas) for b in candidatas[i + 1:]
        if a.nome not in derivadas.get(b.nome, set()) and b.nome not in derivadas.get(a.nome, set())
    ]
    if not pares:
        return None
    nomes = [m.nome for m in candidatas]
    postos = ", ".join(
        f"rank() OVER (ORDER BY TRY_CAST({ident(n)} AS DOUBLE)) AS r{i}" for i, n in enumerate(nomes)
    )
    contas = ", ".join(f"corr(r{nomes.index(a)}, r{nomes.index(b)})" for a, b in pares)
    valores = con.execute(f"WITH r AS (SELECT {postos} FROM {fonte}) SELECT {contas} FROM r").fetchone()
    forcas = [abs(v) if v is not None else 0 for v in valores]
    return pares[max(range(len(pares)), key=forcas.__getitem__)]


def calcular(con: duckdb.DuckDBPyConnection, tabela: str, unidades: dict[str, str], limites: Limites,
             pedido: dict, id: str = "g") -> Grafico:
    """Calcula um gráfico. É o que roda quando o usuário edita um gráfico na tela."""
    tipo = pedido["tipo"]
    if tipo == "linha":
        return _linha(con, tabela, unidades, pedido, id)
    if tipo == "barras":
        return _barras(con, tabela, unidades, limites, pedido, id)
    if tipo == "histograma":
        return _histograma(con, tabela, unidades, pedido, id)
    if tipo == "dispersao":
        return _dispersao(con, tabela, unidades, pedido, id)
    raise ValueError(f"Tipo de gráfico inválido: {tipo}")


def _rotulo(agregacao: str, y: str | None) -> str:
    if agregacao == "contagem" or y is None:
        return "Contagem de linhas"
    return f"{NOMES_AGREGACAO[agregacao]} de {y}"


def _unidade(unidades: dict[str, str], agregacao: str, y: str | None) -> str | None:
    """A taxa sai em %, seja qual for a coluna."""
    return "%" if agregacao == "taxa" else unidades.get(y)


def _linha(con, tabela, unidades, pedido, id) -> Grafico:
    x, y = pedido["x"], pedido.get("y")
    agregacao = pedido.get("agregacao") or "soma"
    grao = pedido.get("grao") or grao_automatico(con, tabela, x)
    if grao not in GRAOS:
        raise ValueError(f"Período inválido: {grao}")

    pontos = serie(con, tabela, x, agregacao, y, grao)
    dados = [
        {"x": p.inicio.isoformat(), "rotulo": formato.periodo(p.inicio, grao), "y": p.valor,
         "n": p.linhas, "incompleto": p.incompleto}
        for p in pontos
    ]
    aviso = None
    incompletos = [p for p in pontos if p.incompleto]
    if incompletos:
        nomes = ", ".join(formato.periodo(p.inicio, grao) for p in incompletos)
        aviso = f"Período incompleto no gráfico: {nomes}."
    return Grafico(id, f"{_rotulo(agregacao, y)} por {formato.NOME_GRAO[grao]}", "linha", x, y, agregacao, grao,
                   _unidade(unidades, agregacao, y), dados, aviso)


def _barras(con, tabela, unidades, limites, pedido, id) -> Grafico:
    x, y = pedido["x"], pedido.get("y")
    agregacao = pedido.get("agregacao") or "soma"
    linhas = ranking(con, tabela, x, agregacao, y)
    maximo = limites.max_categorias_grafico
    topo = linhas[:maximo]
    dados = [{"x": nome, "y": valor, "n": n} for nome, valor, n in topo]

    aviso = None
    resto = linhas[maximo:]
    if resto:
        if agregacao in {"soma", "contagem"}:
            dados.append({"x": f"Outros ({len(resto)})", "y": sum(v for _, v, _ in resto),
                          "n": sum(n for _, _, n in resto), "outros": True})
        else:
            aviso = f"Mostrando as {maximo} maiores de {len(linhas)} categorias."
    return Grafico(id, f"{_rotulo(agregacao, y)} por {x}", "barras", x, y, agregacao, None,
                   _unidade(unidades, agregacao, y), dados, aviso)


def _histograma(con, tabela, unidades, pedido, id) -> Grafico:
    """Faixas entre os percentis 1 e 99, para um valor extremo não achatar o gráfico."""
    y = pedido["y"]
    c = ident(y)
    fonte = f"(SELECT TRY_CAST({c} AS DOUBLE) AS v FROM {ident(tabela)} WHERE TRY_CAST({c} AS DOUBLE) IS NOT NULL)"
    baixo, alto, total = con.execute(
        f"SELECT quantile_cont(v, 0.01), quantile_cont(v, 0.99), count(*) FROM {fonte}"
    ).fetchone()
    grafico = Grafico(id, f"Distribuição de {y}", "histograma", y, None, "contagem", None, unidades.get(y))
    if not total or baixo is None or alto <= baixo:
        grafico.aviso = "Valores concentrados demais para formar um histograma."
        return grafico

    largura = (alto - baixo) / FAIXAS_HISTOGRAMA
    linhas = con.execute(f"""
        SELECT least(floor((v - ?) / ?), {FAIXAS_HISTOGRAMA - 1})::INT AS faixa, count(*)
        FROM {fonte} WHERE v BETWEEN ? AND ?
        GROUP BY 1 ORDER BY 1
    """, [baixo, largura, baixo, alto]).fetchall()
    contagens = dict(linhas)
    grafico.dados = [
        {
            "x": formato.valor(round(baixo + i * largura, 2), unidades.get(y)),
            "fim": formato.valor(round(baixo + (i + 1) * largura, 2), unidades.get(y)),
            "y": contagens.get(i, 0),
        }
        for i in range(FAIXAS_HISTOGRAMA)
    ]
    fora = total - sum(contagens.values())
    if fora:
        grafico.aviso = f"{formato.inteiro(fora)} valores extremos ficaram fora do gráfico (percentis 1 a 99)."
    return grafico


def _dispersao(con, tabela, unidades, pedido, id) -> Grafico:
    """Corta as pontas: um único 999 achataria todos os outros pontos num canto."""
    x, y = pedido["x"], pedido["y"]
    a, b = f"TRY_CAST({ident(x)} AS DOUBLE)", f"TRY_CAST({ident(y)} AS DOUBLE)"
    base = f"SELECT {a} AS a, {b} AS b FROM {ident(tabela)} WHERE {a} IS NOT NULL AND {b} IS NOT NULL"
    a_min, a_max, b_min, b_max, total = con.execute(f"""
        SELECT quantile_cont(a, 0.005), quantile_cont(a, 0.995),
               quantile_cont(b, 0.005), quantile_cont(b, 0.995), count(*)
        FROM ({base})
    """).fetchone()
    grafico = Grafico(id, f"{x} × {y}", "dispersao", x, y, "contagem", None, unidades.get(y))
    if not total:
        grafico.aviso = "Não há linhas com as duas colunas preenchidas."
        return grafico

    dentro = f"a BETWEEN {a_min} AND {a_max} AND b BETWEEN {b_min} AND {b_max}"
    visiveis = con.execute(f"SELECT count(*) FROM ({base}) WHERE {dentro}").fetchone()[0]
    linhas = con.execute(f"""
        SELECT a, b FROM (SELECT a, b FROM ({base}) WHERE {dentro})
        USING SAMPLE reservoir({PONTOS_DISPERSAO} ROWS) REPEATABLE (42)
    """).fetchall()
    grafico.dados = [{"x": linha[0], "y": linha[1]} for linha in linhas]

    avisos = []
    if visiveis > PONTOS_DISPERSAO:
        avisos.append(f"Amostra de {formato.inteiro(PONTOS_DISPERSAO)} pontos de {formato.inteiro(total)}.")
    if total > visiveis:
        avisos.append(f"{formato.inteiro(total - visiveis)} pontos extremos ficaram fora.")
    grafico.aviso = " ".join(avisos) or None
    return grafico
