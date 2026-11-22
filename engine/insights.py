"""Bloco 5, insights: um catálogo de analisadores baseados em regras e estatística.

Cada analisador declara do que precisa, calcula a partir dos dados e só afirma
algo quando o efeito é grande o bastante. O texto sai de um modelo fixo, com os
números que o sustentam. Se um analisador falha ou demora, ele é pulado com o
motivo e os outros seguem.

Muitas linhas não atrapalham a estatística. O risco é ter poucos dados, ou fazer
muitas comparações e chamar de descoberta o que veio do acaso. Por isso as
regras olham tamanho de efeito e o ruído histórico da própria série.
"""

import math
import statistics
import time
from dataclasses import dataclass, field

import duckdb

from engine import formato, relogio
from engine.consultas import (GRAOS, Sim, categorias, datas, eh_alvo, expressao_sim, faixa_normal, grao_automatico,
                              indicadores, metricas, por_grupo, ranking, serie)
from engine.limites import LOCAL, Limites
from engine.normalizacao import Relatorio
from engine.perfil import Perfil, PerfilColuna
from engine.sql import colunas_de, ident

MIN_VARIACAO = 0.05            # abaixo de 5% uma variação não é notícia
MIN_CORRELACAO = 0.5
MIN_EFEITO_MEDIANA = 0.8       # diferença em desvios robustos
MIN_DIFERENCA_MEDIANA = 0.2
Z_PICO = 4.5
MIN_PERIODOS_PICO = 8
MIN_DESVIO_PICO = 0.2
MAX_PICOS = 3
MAX_CORRELACOES = 3
VEZES_ACIMA_DO_ACASO = 4
# Uma diferença entre grupos só vale se for 4 vezes maior que o erro esperado
# só pelo tamanho do grupo. Testando dezenas de grupos, 2 ou 3 erros-padrão
# aparecem por acaso.
Z_GRUPO = 4.0
MAX_CATEGORIAS_GRUPO = 10
MAX_METRICAS_GRUPO = 6
# Medianas de muitos grupos guardam os valores na memória: uma amostra basta.
LINHAS_AMOSTRA_GRUPOS = 200_000
ESPERADOS_REGRA = 14
MIN_V_TAXA = 0.1               # força da relação (V de Cramér) abaixo disso não é notícia
MAX_TAXAS = 4
MAX_TAXAS_POR_INDICADOR = 3
MAX_METRICAS_POR_INDICADOR = 3
# Sim contra não são dois grupos grandes comparados entre si: meio desvio
# (efeito médio) já é notícia, desde que passe do acaso (Z_GRUPO).
MIN_EFEITO_INDICADOR = 0.5
LINHAS_AMOSTRA_CORRELACAO = 200_000
SEMENTE = 42


@dataclass
class Insight:
    tipo: str
    titulo: str
    texto: str
    score: float                # 0 a 100, para ordenar
    evidencias: list[dict] = field(default_factory=list)   # {"rotulo": ..., "valor": ...}
    ressalvas: list[str] = field(default_factory=list)
    colunas: list[str] = field(default_factory=list)
    # Para o painel: o gráfico do dado que o insight cita, o grupo ou período a
    # destacar nele e o número que resume o achado.
    grafico: dict | None = None
    destaque: str | None = None
    numero: str | None = None


def _com(insight: Insight, grafico: dict | None, destaque, numero: str | None) -> Insight:
    """Acrescenta ao insight o que o painel mostra junto dele."""
    insight.grafico, insight.numero = grafico, numero
    insight.destaque = None if destaque is None else str(destaque)
    return insight


def _sinal(fracao: float) -> str:
    return f"{'+' if fracao >= 0 else '−'}{formato.pct(abs(fracao))}"


@dataclass
class Execucao:
    analisador: str
    status: str                 # ok, pulado ou falhou
    motivo: str | None = None
    duracao_ms: int = 0
    insights: int = 0


@dataclass
class ResultadoInsights:
    insights: list[Insight]
    execucoes: list[Execucao]


@dataclass
class Contexto:
    con: duckdb.DuckDBPyConnection
    tabela: str
    perfil: Perfil
    unidades: dict[str, str]
    limites: Limites
    relatorio: Relatorio | None
    derivadas: dict[str, set[str]]
    metricas: list[PerfilColuna]
    categorias: list[PerfilColuna]
    datas: list[PerfilColuna]
    linhas: int
    indicadores: list[PerfilColuna] = field(default_factory=list)

    def valor(self, numero: float, coluna: str) -> str:
        return formato.valor(numero, self.unidades.get(coluna))

    def fonte(self) -> tuple[str, bool]:
        """Tabela para análises pesadas: acima do limite, uma amostra declarada."""
        if self.linhas <= self.limites.linhas_amostra:
            return ident(self.tabela), False
        return (
            f"(SELECT * FROM {ident(self.tabela)} USING SAMPLE "
            f"reservoir({self.limites.linhas_amostra} ROWS) REPEATABLE ({SEMENTE}))"
        ), True


CATALOGO = []


def _vinculadas(ctx, a: str, b: str) -> bool:
    """Uma coluna saiu da outra (fórmula), ou as duas vieram da mesma tabela
    emprestada numa visão. A relação entre elas é cadastro, não descoberta."""
    return b in ctx.derivadas.get(a, set()) or a in ctx.derivadas.get(b, set())


def analisador(nome: str, precisa=None):
    """Registra um analisador. `precisa(ctx)` devolve o motivo para pular, se houver."""
    def registrar(funcao):
        CATALOGO.append((nome, funcao, precisa))
        return funcao
    return registrar


def gerar(con: duckdb.DuckDBPyConnection, tabela: str, perfil: Perfil, unidades: dict[str, str],
          limites: Limites = LOCAL, relatorio: Relatorio | None = None,
          derivadas: dict[str, set[str]] | None = None, catalogo=None) -> ResultadoInsights:
    maximo = limites.max_colunas_insights
    ctx = Contexto(
        con, tabela, perfil, unidades, limites, relatorio, derivadas or {},
        metricas(perfil, unidades)[:maximo], categorias(perfil)[:maximo], datas(perfil)[:maximo],
        perfil.linhas, indicadores(perfil),
    )

    insights, execucoes = [], []
    for nome, funcao, precisa in (catalogo or CATALOGO):
        motivo = precisa(ctx) if precisa else None
        if motivo:
            execucoes.append(Execucao(nome, "pulado", motivo))
            continue
        execucao, achados = _executar(con, nome, funcao, ctx, limites.timeout_analisador_s)
        execucoes.append(execucao)
        insights += achados

    insights.sort(key=lambda i: -i.score)
    return ResultadoInsights(insights[:limites.max_insights_exibidos], execucoes)


def _executar(con, nome, funcao, ctx, tempo_limite) -> tuple[Execucao, list[Insight]]:
    inicio = time.perf_counter()
    try:
        with relogio.tempo_limite(con, tempo_limite):
            achados = list(funcao(ctx))
    except duckdb.InterruptException:
        return Execucao(nome, "pulado", f"Passou de {tempo_limite} s.", _ms(inicio)), []
    except Exception as erro:
        return Execucao(nome, "falhou", str(erro).strip().splitlines()[0][:200], _ms(inicio)), []
    return Execucao(nome, "ok", None, _ms(inicio), len(achados)), achados


def _ms(inicio: float) -> int:
    return round((time.perf_counter() - inicio) * 1000)


def _valores(quantidade: int) -> str:
    return f"{formato.inteiro(quantidade)} {'valor' if quantidade == 1 else 'valores'}"


# ----- pré-requisitos -----

def _precisa_data(ctx):
    if not ctx.datas:
        return "Precisa de uma coluna de data."
    if not ctx.metricas:
        return "Precisa de uma coluna de métrica."


def _precisa_metrica(ctx):
    if not ctx.metricas:
        return "Precisa de uma coluna de métrica."
    if ctx.linhas < ctx.limites.min_linhas_estatistica:
        return f"Precisa de pelo menos {ctx.limites.min_linhas_estatistica} linhas."


def _precisa_categoria(ctx):
    if not ctx.categorias:
        return "Precisa de uma coluna de categoria."
    return _precisa_metrica(ctx)


def _precisa_duas_metricas(ctx):
    if len(ctx.metricas) < 2:
        return "Precisa de duas colunas de métrica."
    return _precisa_metrica(ctx)


def _precisa_indicador(ctx):
    if not ctx.indicadores:
        return "Precisa de uma coluna de sim/não."
    if ctx.linhas < ctx.limites.min_linhas_estatistica:
        return f"Precisa de pelo menos {ctx.limites.min_linhas_estatistica} linhas."


def _precisa_indicador_e_metrica(ctx):
    if not ctx.metricas:
        return "Precisa de uma coluna de métrica."
    return _precisa_indicador(ctx)


def _precisa_relatorio(ctx):
    if ctx.relatorio is None:
        return "Roda depois da normalização."


# ----- tempo -----

def _serie_completa(ctx, metrica):
    grao = grao_automatico(ctx.con, ctx.tabela, ctx.datas[0].nome)
    pontos = serie(ctx.con, ctx.tabela, ctx.datas[0].nome, metrica.agregacao, metrica.nome, grao)
    return grao, pontos, [p for p in pontos if not p.incompleto]


def _variacoes(pontos) -> list[float]:
    return [
        (b.valor - a.valor) / abs(a.valor)
        for a, b in zip(pontos, pontos[1:]) if a.valor
    ]


def _ruido_do_acaso(ctx, metrica: PerfilColuna, linhas: int) -> float:
    """Quanto um período com tantas linhas oscila só por acaso, em fração do valor.

    Cai com a raiz do número de linhas. Usa mediana e MAD para um valor extremo
    não inflar a estimativa e esconder um efeito real.
    """
    mediana, mad = ctx.con.execute(
        f"SELECT median({ident(metrica.nome)}), mad({ident(metrica.nome)}) FROM {ident(ctx.tabela)}"
    ).fetchone()
    if not mediana:
        return 0.0
    variacao = 1.4826 * float(mad) / abs(float(mediana))
    return math.sqrt(1 + variacao ** 2) / math.sqrt(max(linhas, 1))


def _dispersao_robusta(valores: list[float]) -> float:
    """Desvio típico que um valor extremo não contamina (MAD escalado)."""
    mediana = statistics.median(valores)
    return 1.4826 * statistics.median(abs(v - mediana) for v in valores)


@analisador("variacao_periodo", _precisa_data)
def variacao_periodo(ctx):
    for metrica in ctx.metricas[:3]:
        grao, pontos, completos = _serie_completa(ctx, metrica)
        if len(completos) < 2:
            continue
        anterior, atual = completos[-2:]
        minimo = ctx.limites.min_linhas_estatistica
        if not anterior.valor or anterior.linhas < minimo or atual.linhas < minimo:
            continue

        variacao = (atual.valor - anterior.valor) / abs(anterior.valor)
        if abs(variacao) < MIN_VARIACAO:
            continue
        acaso = _ruido_do_acaso(ctx, metrica, min(anterior.linhas, atual.linhas))
        if abs(variacao) < VEZES_ACIMA_DO_ACASO * math.sqrt(2) * acaso:
            continue

        historico = _variacoes(completos[:-1])
        ressalvas = []
        if len(historico) >= 4:
            ruido = _dispersao_robusta(historico)
            if abs(variacao - statistics.median(historico)) < 3.5 * ruido:
                continue
        else:
            ressalvas.append(f"Só há {len(completos)} períodos completos: não dá para saber se essa variação é normal.")

        if pontos[-1].incompleto:
            ressalvas.append(
                f"{formato.periodo(pontos[-1].inicio, grao).capitalize()} ficou fora da comparação "
                "porque ainda está incompleto."
            )

        verbo = "subiu" if variacao > 0 else "caiu"
        nome_atual = formato.periodo(atual.inicio, grao)
        nome_anterior = formato.periodo(anterior.inicio, grao)
        texto = (
            f"{metrica.nome} {verbo} {formato.pct(abs(variacao))} em {nome_atual}: "
            f"{ctx.valor(atual.valor, metrica.nome)} contra "
            f"{ctx.valor(anterior.valor, metrica.nome)} em {nome_anterior}."
        )
        evidencias = [
            {"rotulo": nome_atual, "valor": ctx.valor(atual.valor, metrica.nome)},
            {"rotulo": nome_anterior, "valor": ctx.valor(anterior.valor, metrica.nome)},
            {"rotulo": "Diferença", "valor": ctx.valor(atual.valor - anterior.valor, metrica.nome)},
        ]
        colunas = [ctx.datas[0].nome, metrica.nome]

        if metrica.agregacao == "soma" and anterior.linhas and atual.linhas:
            # Uma soma sobe só porque entraram mais linhas: vale dizer isso.
            var_linhas = (atual.linhas - anterior.linhas) / anterior.linhas
            if _volume_explica(1 + variacao, 1 + var_linhas):
                por_linha = (atual.valor / atual.linhas) / (anterior.valor / anterior.linhas) - 1
                texto += (
                    f" O número de linhas também {'subiu' if var_linhas > 0 else 'caiu'} "
                    f"{formato.pct(abs(var_linhas))}; por linha, a variação foi de "
                    f"{'+' if por_linha >= 0 else '-'}{formato.pct(abs(por_linha))}."
                )
                evidencias.append({"rotulo": "Variação no número de linhas", "valor": formato.pct(var_linhas)})
                ressalvas.append("Parte da variação vem de haver mais ou menos registros no período.")

        if metrica.agregacao == "soma":
            motor = _maior_contribuicao(ctx, metrica, grao, anterior, atual)
            if motor:
                categoria, nome, delta, fatia = motor
                if fatia >= 1:
                    texto += f" {nome} ({categoria}) respondeu por toda a diferença; o resto andou no sentido contrário."
                else:
                    texto += f" {formato.pct(fatia, 0)} dessa diferença veio de {nome} ({categoria})."
                evidencias.append({"rotulo": f"Diferença em {nome}", "valor": ctx.valor(delta, metrica.nome)})
                colunas.append(categoria)

        yield _com(Insight("variacao_periodo", f"{metrica.nome} {verbo} em {nome_atual}", texto,
                      min(100, 55 + abs(variacao) * 60), evidencias, ressalvas, colunas), {"tipo": "linha", "x": ctx.datas[0].nome, "y": metrica.nome, "agregacao": metrica.agregacao, "grao": grao}, nome_atual, _sinal(variacao))


def _volume_explica(razao_valor: float, razao_linhas: float) -> bool:
    """O número de linhas explica ao menos metade da mudança de uma soma?

    Compara em escala de vezes (log): um total 12 mil vezes maior com 500 vezes
    mais linhas tem dois terços da mudança vindos do volume.
    """
    if razao_valor <= 0 or razao_linhas <= 0:
        return False
    log_valor, log_linhas = math.log(razao_valor), math.log(razao_linhas)
    return log_valor * log_linhas > 0 and abs(log_linhas) >= 0.5 * abs(log_valor)


def _maior_contribuicao(ctx, metrica, grao, anterior, atual):
    """A categoria que mais explica a diferença entre dois períodos."""
    total = atual.valor - anterior.valor
    d = ident(ctx.datas[0].nome)
    m = ident(metrica.nome)
    melhor = None
    for categoria in ctx.categorias[:3]:
        c = ident(categoria.nome)
        linhas = ctx.con.execute(f"""
            SELECT CAST({c} AS VARCHAR),
                   coalesce(sum({m}) FILTER (date_trunc('{GRAOS[grao]}', {d})::DATE = ?), 0)
                   - coalesce(sum({m}) FILTER (date_trunc('{GRAOS[grao]}', {d})::DATE = ?), 0)
            FROM {ident(ctx.tabela)} WHERE {c} IS NOT NULL
            GROUP BY 1
        """, [atual.inicio, anterior.inicio]).fetchall()
        for nome, delta in linhas:
            fatia = float(delta) / total
            if fatia >= 0.3 and (melhor is None or fatia > melhor[3]):
                melhor = (categoria.nome, nome, float(delta), fatia)
    return melhor


@analisador("tendencia", _precisa_data)
def tendencia(ctx):
    for metrica in ctx.metricas[:3]:
        grao, _, completos = _serie_completa(ctx, metrica)
        n = len(completos)
        if n < 6:
            continue
        ys = [p.valor for p in completos]
        media = statistics.fmean(ys)
        if not media:
            continue
        inclinacao, r2 = _regressao(ys)
        relativa = inclinacao / abs(media)
        # Com poucos pontos, uma reta boa aparece por acaso com facilidade.
        if r2 < (0.5 if n >= 10 else 0.8) or abs(relativa) < 0.01:
            continue

        direcao = "cresce" if relativa > 0 else "cai"
        yield _com(Insight(
            "tendencia", f"{metrica.nome} {direcao} de forma consistente",
            f"{metrica.nome} {direcao} em média {ctx.valor(abs(inclinacao), metrica.nome)} por {formato.NOME_GRAO[grao]} "
            f"({formato.pct(abs(relativa))} da média), ao longo de {n} períodos.",
            min(90, 45 + r2 * 40),
            [
                {"rotulo": f"Inclinação por {formato.NOME_GRAO[grao]}", "valor": ctx.valor(inclinacao, metrica.nome)},
                {"rotulo": "Ajuste da reta (R²)", "valor": formato.numero(r2)},
                {"rotulo": "Períodos completos", "valor": str(n)},
            ],
            ["Uma tendência passada não garante o futuro."],
            [ctx.datas[0].nome, metrica.nome],
        ), {"tipo": "linha", "x": ctx.datas[0].nome, "y": metrica.nome, "agregacao": metrica.agregacao, "grao": grao}, None, f"{_sinal(relativa)} por {formato.NOME_GRAO[grao]}")


def _regressao(ys: list[float]) -> tuple[float, float]:
    """Inclinação e R² da reta que melhor passa pelos pontos."""
    n = len(ys)
    media_x = (n - 1) / 2
    media_y = statistics.fmean(ys)
    sxx = sum((x - media_x) ** 2 for x in range(n))
    sxy = sum((x - media_x) * (y - media_y) for x, y in enumerate(ys))
    syy = sum((y - media_y) ** 2 for y in ys)
    if not syy:
        return 0.0, 0.0
    inclinacao = sxy / sxx
    return inclinacao, sxy ** 2 / (sxx * syy)


@analisador("pico_temporal", _precisa_data)
def pico_temporal(ctx):
    """Compara cada período com os vizinhos. Assim a tendência não vira pico."""
    achados = []
    vistos = set()
    for metrica in ctx.metricas[:2]:
        for z, ponto, vizinho, desvio, grao, linhas_vizinhas in _picos(ctx, metrica):
            # Métricas ligadas saltam juntas: basta contar uma vez cada período.
            if ponto.inicio not in vistos:
                vistos.add(ponto.inicio)
                achados.append((abs(z), metrica, ponto, vizinho, desvio, grao, linhas_vizinhas))

    achados.sort(key=lambda item: -item[0])
    for z, metrica, ponto, vizinho, desvio, grao, linhas_vizinhas in achados[:MAX_PICOS]:
        nome = formato.periodo(ponto.inicio, grao)
        acima = desvio > 0
        texto = (
            f"{metrica.nome} teve {'um salto' if acima else 'uma queda'} em {nome}: "
            f"{ctx.valor(ponto.valor, metrica.nome)}, {formato.pct(abs(desvio))} "
            f"{'acima' if acima else 'abaixo'} dos períodos vizinhos."
        )
        score = min(80, 40 + z * 4)
        evidencias = [
            {"rotulo": nome, "valor": ctx.valor(ponto.valor, metrica.nome)},
            {"rotulo": "Períodos vizinhos", "valor": ctx.valor(vizinho, metrica.nome)},
        ]
        ressalvas = []
        desvio_linhas = ponto.linhas / linhas_vizinhas - 1 if linhas_vizinhas else 0
        if metrica.agregacao == "soma" and _volume_explica(1 + desvio, 1 + desvio_linhas):
            texto += (
                f" O número de linhas também ficou {formato.pct(abs(desvio_linhas))} "
                f"{'acima' if desvio_linhas > 0 else 'abaixo'} dos vizinhos: o salto vem do volume de registros."
            )
            evidencias.append({"rotulo": "Linhas no período", "valor": formato.inteiro(ponto.linhas)})
            score -= 15
        maior = _maior_linha(ctx, metrica, grao, ponto)
        if metrica.agregacao == "soma" and abs(ponto.valor - maior - vizinho) <= MIN_DESVIO_PICO * abs(vizinho):
            texto += f" Uma única linha explica isso: {ctx.valor(maior, metrica.nome)}."
            ressalvas.append("Confira se esse valor é real. Sem ele, o período fica normal.")
            evidencias.append({"rotulo": "Maior linha do período", "valor": ctx.valor(maior, metrica.nome)})
            score = 38
        yield _com(Insight("pico_temporal", f"{'Salto' if acima else 'Queda'} em {nome}", texto, score,
                      evidencias, ressalvas, [ctx.datas[0].nome, metrica.nome]), {"tipo": "linha", "x": ctx.datas[0].nome, "y": metrica.nome, "agregacao": metrica.agregacao, "grao": grao}, nome, _sinal(desvio))


def _picos(ctx, metrica):
    minimo = ctx.limites.min_linhas_estatistica
    grao, _, completos = _serie_completa(ctx, metrica)
    if len(completos) < MIN_PERIODOS_PICO:
        return
    ys = [p.valor for p in completos]
    ns = [p.linhas for p in completos]
    vizinhos = [statistics.median(ys[max(i - 3, 0):i] + ys[i + 1:i + 4]) for i in range(len(ys))]
    linhas_vizinhas = [statistics.median(ns[max(i - 3, 0):i] + ns[i + 1:i + 4]) for i in range(len(ns))]
    desvios = [(y - v) / abs(v) if v else 0.0 for y, v in zip(ys, vizinhos)]
    ruido = _dispersao_robusta(desvios)
    if not ruido:
        return
    centro = statistics.median(desvios)

    # O último período já é assunto da variação.
    for ponto, vizinho, desvio, n_vizinho in list(zip(completos, vizinhos, desvios, linhas_vizinhas))[:-1]:
        z = (desvio - centro) / ruido
        if abs(z) < Z_PICO or abs(desvio) < MIN_DESVIO_PICO or ponto.linhas < minimo:
            continue
        if abs(desvio) < VEZES_ACIMA_DO_ACASO * _ruido_do_acaso(ctx, metrica, ponto.linhas):
            continue
        yield z, ponto, vizinho, desvio, grao, n_vizinho


def _maior_linha(ctx, metrica, grao, ponto) -> float:
    d, m = ident(ctx.datas[0].nome), ident(metrica.nome)
    valor = ctx.con.execute(
        f"SELECT max({m}) FROM {ident(ctx.tabela)} WHERE date_trunc('{GRAOS[grao]}', {d})::DATE = ?",
        [ponto.inicio],
    ).fetchone()[0]
    return float(valor or 0)


# ----- comparação entre grupos -----

def _precisa_categoria_so(ctx):
    if not ctx.categorias:
        return "Precisa de uma coluna de categoria."
    if ctx.linhas < ctx.limites.min_linhas_estatistica:
        return f"Precisa de pelo menos {ctx.limites.min_linhas_estatistica} linhas."


@analisador("concentracao", _precisa_categoria_so)
def concentracao(ctx):
    """Onde está a maior parte do total. Sem métrica somável, a parte das linhas
    (clientes por estado, por exemplo)."""
    somaveis = [m for m in ctx.metricas if m.agregacao == "soma"][:1]
    if not somaveis and not ctx.metricas:
        yield from _concentracao_de_linhas(ctx)
    for metrica in somaveis:
        for categoria in ctx.categorias[:3]:
            if _vinculadas(ctx, categoria.nome, metrica.nome):
                continue
            linhas = ranking(ctx.con, ctx.tabela, categoria.nome, "soma", metrica.nome)
            grupos = len(linhas)
            total = sum(valor for _, valor, _ in linhas)
            if grupos < 3 or total <= 0 or linhas[-1][1] < 0:
                continue
            lider, valor_lider, linhas_lider = linhas[0]
            fatia = valor_lider / total
            justa = 1 / grupos
            if fatia < max(2 * justa, 0.25) or linhas_lider < ctx.limites.min_linhas_estatistica:
                continue

            acumulado, quantos = 0, 0
            for _, valor, _ in linhas:
                acumulado += valor
                quantos += 1
                if acumulado / total >= 0.8:
                    break

            nome = f"{lider} ({categoria.nome})"
            fatia_linhas = linhas_lider / sum(n for _, _, n in linhas)
            texto = (
                f"{nome} concentra {formato.pct(fatia)} de {metrica.nome} "
                f"({ctx.valor(valor_lider, metrica.nome)}), em {grupos} grupos."
            )
            if quantos < grupos * 0.6:
                texto += f" {quantos} de {grupos} grupos somam 80% do total."
            # Se o líder também tem a maior parte das linhas, a concentração vem do volume.
            pelo_volume = fatia_linhas >= 0.8 * fatia
            if pelo_volume:
                texto += f" Também é onde estão {formato.pct(fatia_linhas)} das linhas: a fatia vem do volume de registros."
            yield _com(Insight(
                "concentracao", f"{nome} concentra {metrica.nome}", texto,
                min(85, 40 + (fatia - justa) * 90) - (10 if pelo_volume else 0),
                [
                    {"rotulo": lider, "valor": formato.pct(fatia)},
                    {"rotulo": "Fatia das linhas", "valor": formato.pct(fatia_linhas)},
                    {"rotulo": "Se fosse igual para todos", "valor": formato.pct(justa)},
                    {"rotulo": "Total", "valor": ctx.valor(total, metrica.nome)},
                ],
                [],
                [categoria.nome, metrica.nome],
            ), {"tipo": "barras", "x": categoria.nome, "y": metrica.nome, "agregacao": "soma"}, lider, formato.pct(fatia))


def _concentracao_de_linhas(ctx):
    for categoria in ctx.categorias[:3]:
        linhas = ranking(ctx.con, ctx.tabela, categoria.nome, "contagem", None)
        grupos = len(linhas)
        total = sum(n for _, n, _ in linhas)
        if grupos < 3 or not total:
            continue
        lider, quantas, _ = linhas[0]
        fatia = quantas / total
        justa = 1 / grupos
        if fatia < max(2 * justa, 0.25) or quantas < ctx.limites.min_linhas_estatistica:
            continue
        acumulado, quantos = 0, 0
        for _, n, _ in linhas:
            acumulado += n
            quantos += 1
            if acumulado / total >= 0.8:
                break
        nome = f"{lider} ({categoria.nome})"
        texto = f"{nome} tem {formato.pct(fatia)} das linhas, em {grupos} grupos."
        if quantos < grupos * 0.6:
            texto += f" {quantos} de {grupos} grupos somam 80% das linhas."
        yield _com(Insight(
            "concentracao", f"{nome} concentra as linhas", texto,
            min(75, 35 + (fatia - justa) * 80),
            [
                {"rotulo": lider, "valor": formato.pct(fatia)},
                {"rotulo": "Se fosse igual para todos", "valor": formato.pct(justa)},
                {"rotulo": "Linhas", "valor": formato.inteiro(total)},
            ],
            [],
            [categoria.nome],
        ), {"tipo": "barras", "x": categoria.nome, "y": None, "agregacao": "contagem"}, lider, formato.pct(fatia))


@analisador("diferenca_entre_grupos", _precisa_categoria)
def diferenca_entre_grupos(ctx):
    """Compara medianas: uma média seria puxada por um único valor extremo.

    Todas as categorias de uma vez, numa varredura por métrica.
    """
    minimo = ctx.limites.min_linhas_estatistica
    limite = min(ctx.limites.linhas_amostra, LINHAS_AMOSTRA_GRUPOS)
    amostrada = ctx.linhas > limite
    fonte = ident(ctx.tabela)
    if amostrada:
        fonte = f"(SELECT * FROM {fonte} USING SAMPLE reservoir({limite} ROWS) REPEATABLE ({SEMENTE}))"
    tipos = dict(colunas_de(ctx.con, ctx.tabela))
    grupos = [(c.nome, tipos[c.nome]) for c in ctx.categorias[:MAX_CATEGORIAS_GRUPO]]
    medidas = ctx.metricas[:MAX_METRICAS_GRUPO]
    campos = {f"__m{j}": ident(m.nome) for j, m in enumerate(medidas)}
    contas = {}
    for j in range(len(medidas)):
        contas[f"mediana{j}"] = f"median(__m{j})"
        contas[f"n{j}"] = f"count(__m{j})"
        contas[f"constante{j}"] = f"min(__m{j}) = max(__m{j})"
    tabela = por_grupo(ctx.con, fonte, grupos, campos, contas)
    gerais = ctx.con.execute(
        f"SELECT {', '.join(f'median({c}), mad({c})' for c in campos.values())} FROM {fonte}"
    ).fetchone()

    for j, metrica in enumerate(medidas):
        geral, escala = gerais[2 * j], gerais[2 * j + 1]
        if geral is None or not escala or not geral:
            continue
        geral, escala = float(geral), float(escala) * 1.4826

        melhor = None
        for categoria, linhas in tabela.items():
            if _vinculadas(ctx, categoria, metrica.nome):
                continue
            validas = [(nome, float(d[f"mediana{j}"]), d[f"n{j}"]) for nome, d in linhas if d[f"n{j}"] >= minimo]
            # Um grupo em que todos têm o mesmo valor ("Not rated" sempre com nota 0)
            # é regra de cadastro: a categoria inteira fica de fora.
            if len(validas) < 2 or any(d[f"constante{j}"] for _, d in linhas if d[f"n{j}"] >= minimo):
                continue
            for nome, mediana, n in validas:
                efeito = abs(mediana - geral) / escala
                diferenca = abs(mediana - geral) / abs(geral)
                # Erro-padrão da mediana de n valores: 1,25 desvio / raiz de n.
                z = abs(mediana - geral) / (1.2533 * escala / math.sqrt(n))
                if efeito >= MIN_EFEITO_MEDIANA and diferenca >= MIN_DIFERENCA_MEDIANA and z >= Z_GRUPO:
                    if melhor is None or efeito > melhor[0]:
                        melhor = (efeito, categoria, nome, mediana, n, diferenca)
        if melhor:
            efeito, categoria, nome, mediana, n, diferenca = melhor
            direcao = "acima" if mediana > geral else "abaixo"
            grupo = f"{nome} ({categoria})"
            ressalvas = []
            if amostrada:
                ressalvas.append(f"Calculado sobre uma amostra de {formato.inteiro(limite)} linhas.")
            yield _com(Insight(
                "diferenca_entre_grupos", f"{grupo} foge do típico em {metrica.nome}",
                f"O valor típico de {metrica.nome} em {grupo} é {ctx.valor(mediana, metrica.nome)}, "
                f"{formato.pct(diferenca)} {direcao} do típico geral "
                f"({ctx.valor(geral, metrica.nome)}).",
                min(70, 35 + efeito * 10),
                [
                    {"rotulo": f"Mediana em {nome}", "valor": ctx.valor(mediana, metrica.nome)},
                    {"rotulo": "Mediana geral", "valor": ctx.valor(geral, metrica.nome)},
                    {"rotulo": "Linhas no grupo", "valor": formato.inteiro(n)},
                ],
                ressalvas,
                [categoria, metrica.nome],
            ), {"tipo": "barras", "x": categoria, "y": metrica.nome, "agregacao": "mediana"}, nome, ctx.valor(mediana, metrica.nome))


# ----- colunas de sim/não -----

def _sims(ctx) -> dict[str, Sim]:
    sims = {}
    for coluna in ctx.indicadores:
        try:
            sims[coluna.nome] = expressao_sim(ctx.con, ctx.tabela, coluna.nome)
        except ValueError:
            continue
    return sims


def _avaliar_taxa(contagens: list[tuple[str, int, int]], minimo: int):
    """Teste de duas proporções, grupo contra o resto, e a força da relação.

    `contagens` é [(grupo, linhas com valor, quantos "sim")]. Devolve
    (V de Cramér, taxa geral, grupos ordenados) ou None.
    """
    validos = [(nome, n, s) for nome, n, s in contagens if n >= minimo]
    total = sum(n for _, n, _ in validos)
    positivos = sum(s for _, _, s in validos)
    if len(validos) < 2 or not total:
        return None
    p = positivos / total
    if p in (0, 1):
        return None
    raro = min(p, 1 - p)

    significativo = False
    for _, n, s in validos:
        q = s / n
        # 0% ou 100% exatos onde o acaso daria uma dúzia de casos do outro lado
        # é regra de cadastro ("No phone service" nunca tem telefone), não
        # descoberta. Com 14 esperados, a chance de ver zero é de um em um milhão.
        esperados_do_outro_lado = n * p if q == 0 else n * (1 - p)
        if q in (0, 1) and esperados_do_outro_lado >= ESPERADOS_REGRA:
            return None
        resto = total - n
        if resto < minimo:
            continue
        q_resto = (positivos - s) / resto
        erro = math.sqrt(p * (1 - p) * (1 / n + 1 / resto))
        if abs(q - q_resto) / erro >= Z_GRUPO and abs(q - p) >= max(0.02, 0.2 * raro):
            significativo = True
    if not significativo:
        return None

    v = math.sqrt(sum(n * (s / n - p) ** 2 for _, n, s in validos) / (total * p * (1 - p)))
    if v < MIN_V_TAXA:
        return None
    ordenados = sorted(((nome, n, s / n) for nome, n, s in validos), key=lambda item: -item[2])
    return v, p, ordenados


@analisador("taxa_por_grupo", _precisa_indicador)
def taxa_por_grupo(ctx):
    """Quanto a taxa de "sim" muda de um grupo para outro: churn por tipo de contrato."""
    sims = _sims(ctx)
    if not sims:
        return
    fonte, amostrada = ctx.fonte()
    tipos = dict(colunas_de(ctx.con, ctx.tabela))
    por_nome = {c.nome: c for c in ctx.indicadores + ctx.categorias}
    grupos = [(c.nome, tipos[c.nome]) for c in ctx.categorias] + [(nome, tipos[nome]) for nome in sims]
    campos = {f"__e{i}": sim.expressao for i, sim in enumerate(sims.values())}
    contas = {}
    for i in range(len(sims)):
        contas[f"n{i}"] = f"count(__e{i})"
        contas[f"s{i}"] = f"sum(__e{i})"
    tabela = por_grupo(ctx.con, fonte, grupos, campos, contas)

    achados = []
    particoes = set()
    for i, (nome, sim) in enumerate(sims.items()):
        for grupo, linhas in tabela.items():
            if grupo == nome or _vinculadas(ctx, grupo, nome):
                continue
            # Dois sim/não: conta uma vez, com a taxa da coluna que parece ser a pergunta
            # (ou da que vem depois no arquivo).
            if grupo in sims and (eh_alvo(por_nome[grupo]), por_nome[grupo].posicao) > (
                    eh_alvo(por_nome[nome]), por_nome[nome].posicao):
                continue
            contagens = [(valor, d[f"n{i}"], int(d[f"s{i}"] or 0)) for valor, d in linhas if d[f"n{i}"]]
            # "Rating color" e "Rating text" dividem as linhas do mesmo jeito: basta uma.
            particao = (nome, tuple(sorted((n, s) for _, n, s in contagens)))
            if particao in particoes:
                continue
            particoes.add(particao)
            avaliacao = _avaliar_taxa(contagens, ctx.limites.min_linhas_estatistica)
            if avaliacao:
                achados.append((eh_alvo(por_nome[nome]), *avaliacao, nome, sim, grupo))

    achados.sort(key=lambda item: (not item[0], -item[1]))
    por_indicador = {}
    for alvo, v, p, ordenados, nome, sim, grupo in achados:
        if sum(por_indicador.values()) >= MAX_TAXAS or por_indicador.get(nome, 0) >= MAX_TAXAS_POR_INDICADOR:
            continue
        por_indicador[nome] = por_indicador.get(nome, 0) + 1

        if grupo in sims:
            rotulo = lambda valor: f"quando {grupo} = {valor}"
        else:
            rotulo = lambda valor: f"em {valor}"
        if len(ordenados) <= 4:
            partes = [f"{formato.pct(q)} {rotulo(valor)}" for valor, _, q in ordenados]
            lista = ", ".join(partes[:-1]) + " e " + partes[-1]
        else:
            (maior, _, q_maior), (menor, _, q_menor) = ordenados[0], ordenados[-1]
            lista = f"vai de {formato.pct(q_menor)} {rotulo(menor)} a {formato.pct(q_maior)} {rotulo(maior)}"
        texto = f"A taxa de {sim.sim} muda com {grupo}: {lista}. No geral, {formato.pct(p)}."

        evidencias = [{"rotulo": f"{valor} ({formato.inteiro(n)} linhas)", "valor": formato.pct(q)}
                      for valor, n, q in ordenados[:6]]
        evidencias.append({"rotulo": "Geral", "valor": formato.pct(p)})
        ressalvas = ["Uma diferença entre grupos não prova que o grupo causa o resultado."]
        if amostrada:
            ressalvas.append(f"Calculado sobre uma amostra de {formato.inteiro(ctx.limites.linhas_amostra)} linhas.")
        yield _com(Insight(
            "taxa_por_grupo", f"{nome} muda conforme {grupo}", texto,
            min(90, 50 + 100 * v) if alvo else min(75, 40 + 80 * v),
            evidencias, ressalvas, [nome, grupo],
        ), {"tipo": "barras", "x": grupo, "y": nome, "agregacao": "taxa"}, ordenados[0][0], formato.pct(ordenados[0][2]))


@analisador("metrica_por_indicador", _precisa_indicador_e_metrica)
def metrica_por_indicador(ctx):
    """Quem disse sim é diferente de quem disse não? Compara as medianas dos dois lados."""
    sims = _sims(ctx)
    if not sims:
        return
    minimo = ctx.limites.min_linhas_estatistica
    fonte, amostrada = ctx.fonte()
    medidas = [m for m in ctx.metricas[:ctx.limites.max_metricas_correlacao] if m.nome not in sims]
    if not medidas:
        return
    cols = [ident(m.nome) for m in medidas]
    gerais = ctx.con.execute(
        f"SELECT {', '.join(f'median({c}), mad({c})' for c in cols)} FROM {fonte}"
    ).fetchone()

    achados = []
    for nome, sim in sims.items():
        alvo = eh_alvo(next(c for c in ctx.indicadores if c.nome == nome))
        linhas = ctx.con.execute(f"""
            SELECT __e, {", ".join(f"median({c}), count({c}), min({c}) = max({c})" for c in cols)}
            FROM (SELECT {sim.expressao} AS __e, * FROM {fonte}) WHERE __e IS NOT NULL GROUP BY 1
        """).fetchall()
        lados = {linha[0]: linha[1:] for linha in linhas}
        if set(lados) != {0, 1}:
            continue
        for j, metrica in enumerate(medidas):
            if _vinculadas(ctx, nome, metrica.nome):
                continue
            geral, mad = gerais[2 * j], gerais[2 * j + 1]
            m1, n1, constante1 = lados[1][3 * j:3 * j + 3]
            m0, n0, constante0 = lados[0][3 * j:3 * j + 3]
            if not geral or not mad or m1 is None or m0 is None or n1 < minimo or n0 < minimo:
                continue
            # Um lado inteiro com o mesmo valor (comentário desativado, 0 comentários)
            # é regra de cadastro, não descoberta.
            if constante1 or constante0:
                continue
            escala = 1.4826 * float(mad)
            m1, m0 = float(m1), float(m0)
            efeito = abs(m1 - m0) / escala
            diferenca = abs(m1 - m0) / abs(float(geral))
            z = abs(m1 - m0) / (1.2533 * escala * math.sqrt(1 / n1 + 1 / n0))
            if efeito >= MIN_EFEITO_INDICADOR and diferenca >= MIN_DIFERENCA_MEDIANA and z >= Z_GRUPO:
                achados.append((alvo, efeito, nome, sim, metrica, m1, n1, m0, n0))

    achados.sort(key=lambda item: (not item[0], -item[1]))
    for alvo, efeito, nome, sim, metrica, m1, n1, m0, n0 in achados[:MAX_METRICAS_POR_INDICADOR]:
        direcao = "maior" if m1 > m0 else "menor"
        ressalvas = ["Uma diferença entre grupos não prova que uma coisa causa a outra."]
        if amostrada:
            ressalvas.append(f"Calculado sobre uma amostra de {formato.inteiro(ctx.limites.linhas_amostra)} linhas.")
        yield _com(Insight(
            "metrica_por_indicador", f"{metrica.nome} é {direcao} quando {sim.sim}",
            f"O valor típico de {metrica.nome} é {ctx.valor(m1, metrica.nome)} quando {sim.sim}, "
            f"contra {ctx.valor(m0, metrica.nome)} quando {sim.nao}.",
            min(88, 50 + efeito * 12) if alvo else min(70, 35 + efeito * 10),
            [
                {"rotulo": f"Mediana quando {sim.sim} ({formato.inteiro(n1)} linhas)", "valor": ctx.valor(m1, metrica.nome)},
                {"rotulo": f"Mediana quando {sim.nao} ({formato.inteiro(n0)} linhas)", "valor": ctx.valor(m0, metrica.nome)},
            ],
            ressalvas,
            [nome, metrica.nome],
        ), {"tipo": "barras", "x": nome, "y": metrica.nome, "agregacao": "mediana"}, sim.sim.split(" = ", 1)[1] if " = " in sim.sim else None, ctx.valor(m1, metrica.nome))


# ----- valores fora do padrão e relações -----

@analisador("valores_extremos", _precisa_metrica)
def valores_extremos(ctx):
    fonte, amostrada = ctx.fonte()
    for metrica in ctx.metricas[:5]:
        if metrica.nome in ctx.derivadas:
            continue
        m = f"CAST({ident(metrica.nome)} AS DOUBLE)"
        faixa = faixa_normal(ctx.con, fonte, m)
        if faixa is None:
            continue
        baixo, alto, total = faixa
        fora, maior = ctx.con.execute(
            f"SELECT count(*), max(abs({m})) FROM {fonte} WHERE {m} < ? OR {m} > ?", [baixo, alto]
        ).fetchone()
        # Acima de 1% não é exceção: é uma distribuição de cauda longa.
        if not fora or fora / total > 0.01:
            continue

        ressalvas = ["Confira se são erros de digitação antes de removê-los."]
        if amostrada:
            ressalvas.append(f"Calculado sobre uma amostra de {formato.inteiro(ctx.limites.linhas_amostra)} linhas.")
        yield _com(Insight(
            "valores_extremos", f"Valores fora do padrão em {metrica.nome}",
            f"{_valores(fora)} de {metrica.nome} {'foge' if fora == 1 else 'fogem'} muito do padrão: "
            f"{'é' if fora == 1 else 'o maior é'} {ctx.valor(maior, metrica.nome)}, "
            f"e a faixa normal vai até {ctx.valor(round(alto, 2), metrica.nome)}.",
            min(75, 45 + 5 * min(fora, 4)),
            [
                {"rotulo": "Valores fora", "valor": formato.inteiro(fora)},
                {"rotulo": "Maior valor", "valor": ctx.valor(maior, metrica.nome)},
                {"rotulo": "Limite da faixa normal", "valor": ctx.valor(round(alto, 2), metrica.nome)},
            ],
            ressalvas,
            [metrica.nome],
        ), {"tipo": "histograma", "x": None, "y": metrica.nome}, None, formato.inteiro(fora))


@analisador("correlacao", _precisa_duas_metricas)
def correlacao(ctx):
    """Correlação de Spearman: compara a ordem dos valores, não os valores em si."""
    medidas = ctx.metricas[:ctx.limites.max_metricas_correlacao]
    ranks = ", ".join(
        f"(rank() OVER (ORDER BY {ident(m.nome)}) + "
        f"(count(*) OVER (PARTITION BY {ident(m.nome)}) - 1) / 2.0) AS r{i}"
        for i, m in enumerate(medidas)
    )
    limite = min(ctx.limites.linhas_amostra, LINHAS_AMOSTRA_CORRELACAO)
    amostrada = ctx.linhas > limite
    fonte = ident(ctx.tabela)
    if amostrada:
        fonte = f"(SELECT * FROM {fonte} USING SAMPLE reservoir({limite} ROWS) REPEATABLE ({SEMENTE}))"

    pares = [(i, j) for i in range(len(medidas)) for j in range(i + 1, len(medidas))]
    contas = ", ".join(f"corr(r{i}, r{j})" for i, j in pares)
    valores = ctx.con.execute(f"WITH r AS (SELECT {ranks} FROM {fonte}) SELECT count(*), {contas} FROM r").fetchone()
    total, coeficientes = valores[0], valores[1:]
    if total < ctx.limites.min_linhas_estatistica:
        return

    achados = []
    for (i, j), rho in zip(pares, coeficientes):
        a, b = medidas[i].nome, medidas[j].nome
        if rho is not None and abs(rho) >= MIN_CORRELACAO and not _vinculadas(ctx, a, b):
            achados.append((abs(rho), rho, a, b))

    for _, rho, a, b in sorted(achados, reverse=True, key=lambda item: item[0])[:MAX_CORRELACOES]:
        sentido = "sobe" if rho > 0 else "cai"
        ressalvas = ["Correlação não prova que uma coisa causa a outra."]
        if amostrada:
            ressalvas.append(f"Calculado sobre uma amostra de {formato.inteiro(limite)} linhas.")
        yield _com(Insight(
            "correlacao", f"{a} e {b} andam juntas",
            f"Quando {a} sobe, {b} tende a {'subir' if sentido == 'sobe' else 'cair'} "
            f"(correlação de {formato.numero(rho)}).",
            min(70, 30 + abs(rho) * 40),
            [
                {"rotulo": "Correlação de Spearman", "valor": formato.numero(rho)},
                {"rotulo": "Linhas usadas", "valor": formato.inteiro(total)},
            ],
            ressalvas,
            [a, b],
        ), {"tipo": "dispersao", "x": a, "y": b}, None, formato.numero(rho))


# ----- qualidade -----

@analisador("qualidade", _precisa_relatorio)
def qualidade(ctx):
    perdas = [p for p in ctx.relatorio.passos if p.status == "ok" and p.perdidos]
    for passo in perdas:
        yield Insight(
            "qualidade", "Valores que não converteram",
            f"{_valores(passo.perdidos)} {'virou' if passo.perdidos == 1 else 'viraram'} nulo em "
            f"\"{passo.titulo}\". {'Ele ficou' if passo.perdidos == 1 else 'Eles ficaram'} de fora "
            "dos cálculos dessa coluna.",
            30,
            [{"rotulo": "Valores perdidos", "valor": formato.inteiro(passo.perdidos)}],
            ["Veja os valores originais na tabela bruta antes de descartar."],
            [],
        )

    validas = ctx.perfil.linhas - ctx.perfil.linhas_vazias
    for coluna in ctx.metricas:
        fracao = coluna.nulos / validas if validas else 0
        if fracao >= 0.2:
            yield Insight(
                "qualidade", f"{coluna.nome} tem muitos vazios",
                f"{formato.pct(fracao)} das linhas não têm {coluna.nome}. Somas e médias "
                "desta coluna representam só uma parte dos dados.",
                35,
                [{"rotulo": "Linhas sem valor", "valor": formato.inteiro(coluna.nulos)}],
                [],
                [coluna.nome],
            )
