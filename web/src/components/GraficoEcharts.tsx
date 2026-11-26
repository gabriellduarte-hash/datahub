"use client";

import { useEffect, useRef } from "react";
import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { fmtEixo, fmtInteiro, fmtValor } from "@/lib/formato";
import type { Grafico } from "@/lib/tipos";

echarts.use([BarChart, LineChart, PieChart, ScatterChart, GridComponent, TooltipComponent, CanvasRenderer]);

type Cores = { tinta: string; suave: string; fraca: string; papel: string; destaque: string; linha: string; trilho: string; mono: string };

function lerCores(): Cores {
  const estilo = getComputedStyle(document.documentElement);
  const cor = (nome: string) => estilo.getPropertyValue(nome).trim();
  return {
    tinta: cor("--tinta"), suave: cor("--tinta-suave"), fraca: cor("--tinta-fraca"), papel: cor("--papel"),
    destaque: cor("--destaque-grafico"), linha: cor("--linha"), trilho: cor("--trilho"),
    mono: getComputedStyle(document.body).fontFamily || "monospace",
  };
}

/* "#ececea" com 40% de opacidade vira "rgba(236, 236, 234, 0.4)": o canvas do ECharts não lê color-mix. */
function comAlfa(cor: string, alfa: number): string {
  const hex = cor.replace("#", "");
  if (!/^[0-9a-f]{6}$/i.test(hex)) return cor;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alfa})`;
}

/* Monocromia com um destaque: o grupo ou período que o insight cita fica verde,
   o resto fica em branco sobre um trilho cinza, como uma régua. */
function opcoes(grafico: Grafico, c: Cores, destaque: string | null, compacto: boolean) {
  const texto = { color: c.suave, fontFamily: c.mono, fontSize: compacto ? 10 : 10.5 };
  const eixoBase = {
    axisLine: { lineStyle: { color: c.linha } },
    axisTick: { show: false },
    axisLabel: { ...texto },
    splitLine: { show: false },
  };
  const eixoValor = {
    ...eixoBase,
    type: "value" as const,
    splitNumber: compacto ? 3 : 4,
    axisLine: { show: false },
    axisLabel: { ...texto, hideOverlap: true, formatter: (v: number) => fmtEixo(v, grafico.unidade) },
    splitLine: { show: true, lineStyle: { color: c.linha, type: "dashed" as const } },
  };
  const base = {
    animation: false,
    textStyle: { fontFamily: c.mono, color: c.tinta },
    grid: { left: 4, right: compacto ? 44 : 56, top: 10, bottom: 4, containLabel: true },
    tooltip: {
      backgroundColor: c.papel,
      borderColor: c.linha,
      borderWidth: 1,
      padding: [6, 9],
      textStyle: { color: c.tinta, fontFamily: c.mono, fontSize: 11.5 },
      extraCssText: "border-radius: 6px; box-shadow: none;",
    },
  };

  if (grafico.tipo === "dispersao") {
    return {
      ...base,
      grid: { ...base.grid, right: 12, bottom: 22 },
      tooltip: { ...base.tooltip, trigger: "item" as const, formatter: (p: { value: number[] }) => `${grafico.x}: ${fmtValor(p.value[0], null)}<br>${grafico.y}: ${fmtValor(p.value[1], grafico.unidade)}` },
      xAxis: { ...eixoValor, axisLabel: { ...eixoValor.axisLabel, formatter: (v: number) => fmtEixo(v, null) }, name: grafico.x ?? "", nameLocation: "middle" as const, nameGap: 20, nameTextStyle: { ...texto } },
      yAxis: eixoValor,
      series: [{ type: "scatter", symbol: "circle", symbolSize: compacto ? 3 : 4, data: grafico.dados.map((d) => [d.x, d.y]), itemStyle: { color: c.tinta, opacity: 0.55 } }],
    };
  }

  const dicas = (params: unknown) => {
    const lista = (Array.isArray(params) ? params : [params]) as { dataIndex: number }[];
    const ponto = grafico.dados[lista[0].dataIndex];
    const nome = ponto.fim ? `${ponto.x} a ${ponto.fim}` : ponto.rotulo ?? ponto.x;
    const valor = grafico.tipo === "histograma" ? `${fmtInteiro(ponto.y)} linhas` : fmtValor(ponto.y, grafico.unidade);
    const nota = ponto.incompleto ? "<br>período incompleto" : "";
    return `${nome}<br><b>${valor}</b>${nota}`;
  };

  if (grafico.tipo === "linha") {
    const rotulos = grafico.dados.map((d) => d.rotulo ?? String(d.x));
    const valores = grafico.dados.map((d) => d.y);
    const ultimoIncompleto = grafico.dados.at(-1)?.incompleto;
    const fim = ultimoIncompleto ? grafico.dados.length - 1 : grafico.dados.length;
    const firmes = valores.map((v, i) => (i < fim ? v : null));
    const finais = valores.map((v, i) => (i >= fim - 1 && ultimoIncompleto ? v : null));
    const marcado = destaque ? rotulos.indexOf(destaque) : -1;
    return {
      ...base,
      grid: { ...base.grid, right: 12 },
      tooltip: { ...base.tooltip, trigger: "axis" as const, axisPointer: { type: "line" as const, lineStyle: { color: c.linha, type: "dashed" as const } }, formatter: dicas },
      xAxis: { ...eixoBase, type: "category" as const, data: rotulos, boundaryGap: false, axisLabel: { ...texto, hideOverlap: true } },
      yAxis: eixoValor,
      series: [
        {
          type: "line", data: firmes, symbol: "circle", showSymbol: false, lineStyle: { color: c.tinta, width: 1.5 },
          itemStyle: { color: c.tinta },
          areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: c.trilho }, { offset: 1, color: "rgba(0,0,0,0)" }]) },
          markPoint: marcado >= 0 && marcado < fim ? {
            symbol: "circle", symbolSize: 9, label: { show: false },
            itemStyle: { color: c.destaque, borderColor: c.papel, borderWidth: 2 },
            data: [{ coord: [marcado, valores[marcado]] }],
          } : undefined,
        },
        { type: "line", data: finais, symbol: "circle", symbolSize: 5, lineStyle: { color: c.suave, width: 1.5, type: "dashed" as const }, itemStyle: { color: c.papel, borderColor: c.suave, borderWidth: 1.5 } },
      ],
    };
  }

  if (grafico.tipo === "histograma") {
    return {
      ...base,
      grid: { ...base.grid, right: 8 },
      tooltip: { ...base.tooltip, trigger: "axis" as const, axisPointer: { type: "shadow" as const, shadowStyle: { color: c.trilho } }, formatter: dicas },
      xAxis: { ...eixoBase, type: "category" as const, data: grafico.dados.map((d) => String(d.x)), axisLabel: { ...texto, hideOverlap: true } },
      yAxis: { ...eixoValor, axisLabel: { ...eixoValor.axisLabel, formatter: (v: number) => fmtEixo(v, null) } },
      series: [{ type: "bar", barCategoryGap: "18%", data: grafico.dados.map((d) => d.y), itemStyle: { color: c.suave, borderRadius: [2, 2, 0, 0] } }],
    };
  }

  /* Rosca: partes de um todo. A fatia citada (ou a maior) em verde, as outras em tons da tinta. */
  if (grafico.tipo === "rosca") {
    const total = grafico.dados.reduce((soma, d) => soma + (d.y ?? 0), 0) || 1;
    const maior = [...grafico.dados].sort((a, b) => (b.y ?? 0) - (a.y ?? 0))[0];
    const realce = destaque && grafico.dados.some((d) => String(d.x) === destaque) ? destaque : String(maior?.x);
    const tom = (indice: number) => comAlfa(c.tinta, Math.max(0.22, 0.88 - indice * 0.14));
    let comuns = 0;
    return {
      ...base,
      tooltip: {
        ...base.tooltip, trigger: "item" as const,
        formatter: (p: { name: string; value: number }) => `${p.name}<br><b>${fmtValor(p.value, grafico.unidade)}</b> · ${fmtValor((p.value / total) * 100, "%")}`,
      },
      series: [{
        type: "pie",
        radius: compacto ? ["42%", "66%"] : ["46%", "72%"],
        center: ["50%", "52%"],
        avoidLabelOverlap: true,
        itemStyle: { borderColor: c.papel, borderWidth: 2 },
        label: {
          color: c.suave, fontFamily: c.mono, fontSize: compacto ? 10 : 10.5,
          formatter: (p: { name: string; percent: number }) => `${p.name.length > 18 ? `${p.name.slice(0, 17)}…` : p.name}  ${p.percent.toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`,
        },
        labelLine: { lineStyle: { color: c.linha }, length: 8, length2: 8 },
        data: grafico.dados.map((d) => ({
          name: String(d.x),
          value: d.y ?? 0,
          itemStyle: { color: d.outros ? c.fraca : String(d.x) === realce ? c.destaque : tom(comuns++) },
        })),
      }],
    };
  }

  /* Barras deitadas, uma régua por grupo, com o valor na ponta. */
  const nomes = grafico.dados.map((d) => String(d.x));
  const realce = destaque && nomes.includes(destaque) ? destaque : nomes[0];
  return {
    ...base,
    tooltip: { ...base.tooltip, trigger: "axis" as const, axisPointer: { type: "shadow" as const, shadowStyle: { color: c.trilho } }, formatter: dicas },
    xAxis: { ...eixoValor, show: !compacto },
    yAxis: {
      ...eixoBase, type: "category" as const, data: nomes, inverse: true, axisLine: { show: false },
      axisLabel: { ...texto, color: c.tinta, hideOverlap: true, width: compacto ? 110 : 140, overflow: "truncate" as const },
    },
    series: [{
      type: "bar",
      barWidth: compacto ? 8 : 11,
      showBackground: true,
      backgroundStyle: { color: c.trilho, borderRadius: 2 },
      label: {
        show: grafico.dados.length <= 12, position: "right" as const, color: c.tinta, fontFamily: c.mono, fontSize: 10.5,
        formatter: (p: { value: number }) => fmtValor(p.value, grafico.unidade),
      },
      data: grafico.dados.map((d) => ({
        value: d.y,
        itemStyle: { color: d.outros ? c.fraca : String(d.x) === realce ? c.destaque : c.tinta, borderRadius: 2 },
      })),
    }],
  };
}

type Props = { grafico: Grafico; tema: string; destaque?: string | null; compacto?: boolean };

export function GraficoEcharts({ grafico, tema, destaque = null, compacto = false }: Props) {
  const area = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!area.current) return;
    const instancia = echarts.init(area.current);
    instancia.setOption(opcoes(grafico, lerCores(), destaque, compacto) as echarts.EChartsCoreOption);
    const observador = new ResizeObserver(() => instancia.resize());
    observador.observe(area.current);
    return () => { observador.disconnect(); instancia.dispose(); };
  }, [grafico, tema, destaque, compacto]);

  /* Barras deitadas crescem com o número de grupos: duas barras não pedem a mesma altura que doze. */
  const deitadas = grafico.tipo === "barras";
  const altura = deitadas ? Math.max(compacto ? 70 : 200, grafico.dados.length * (compacto ? 28 : 48) + (compacto ? 14 : 40)) : undefined;
  return <div ref={area} className={`grafico-area${compacto ? " compacto" : ""}`} style={altura ? { height: altura } : undefined}
              role="img" aria-label={grafico.titulo} />;
}
