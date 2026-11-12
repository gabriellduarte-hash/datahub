"use client";

import { useEffect, useRef } from "react";
import { BarChart, LineChart, ScatterChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { fmtEixo, fmtInteiro, fmtValor } from "@/lib/formato";
import type { Grafico } from "@/lib/tipos";

echarts.use([BarChart, LineChart, ScatterChart, GridComponent, TooltipComponent, CanvasRenderer]);

type Cores = { tinta: string; suave: string; papel: string; destaque: string };

function lerCores(): Cores {
  const estilo = getComputedStyle(document.documentElement);
  const cor = (nome: string) => estilo.getPropertyValue(nome).trim();
  return { tinta: cor("--tinta"), suave: cor("--tinta-suave"), papel: cor("--papel"), destaque: cor("--destaque") };
}

/* Em 1-bit, o preenchimento é um padrão pontilhado e não uma cor. */
const pontilhado = (cor: string) => ({ symbol: "rect", symbolSize: 1, dashArrayX: [1, 2], dashArrayY: [1, 2], color: cor });
const hachura = (cor: string) => ({ symbol: "rect", symbolSize: 1, dashArrayX: [1, 0], dashArrayY: [1, 3], rotation: Math.PI / 4, color: cor });

function opcoes(grafico: Grafico, c: Cores) {
  const eixoBase = {
    axisLine: { lineStyle: { color: c.tinta, width: 2 } },
    axisTick: { lineStyle: { color: c.tinta, width: 2 } },
    axisLabel: { color: c.tinta, fontFamily: "monospace", fontSize: 11 },
    splitLine: { show: false },
  };
  const eixoValor = {
    ...eixoBase,
    type: "value" as const,
    splitNumber: 4,
    axisLabel: { ...eixoBase.axisLabel, hideOverlap: true, formatter: (v: number) => fmtEixo(v, grafico.unidade) },
    splitLine: { show: true, lineStyle: { color: c.tinta, opacity: 0.25, type: "dotted" as const } },
  };
  const base = {
    animation: false,
    textStyle: { fontFamily: "monospace", color: c.tinta },
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    tooltip: {
      backgroundColor: c.papel,
      borderColor: c.tinta,
      borderWidth: 2,
      borderRadius: 0,
      textStyle: { color: c.tinta, fontFamily: "monospace", fontSize: 12 },
      extraCssText: `box-shadow: 3px 3px 0 ${c.tinta}; border-radius: 0;`,
    },
  };

  if (grafico.tipo === "dispersao") {
    return {
      ...base,
      grid: { ...base.grid, bottom: 26 },
      tooltip: { ...base.tooltip, trigger: "item" as const, formatter: (p: { value: number[] }) => `${grafico.x}: ${fmtValor(p.value[0], null)}<br>${grafico.y}: ${fmtValor(p.value[1], grafico.unidade)}` },
      xAxis: { ...eixoValor, axisLabel: { ...eixoValor.axisLabel, formatter: (v: number) => fmtEixo(v, null) }, name: grafico.x ?? "", nameLocation: "middle" as const, nameGap: 24, nameTextStyle: { color: c.tinta } },
      yAxis: eixoValor,
      series: [{ type: "scatter", symbol: "rect", symbolSize: 4, data: grafico.dados.map((d) => [d.x, d.y]), itemStyle: { color: c.tinta, opacity: 0.7 } }],
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
    return {
      ...base,
      tooltip: { ...base.tooltip, trigger: "axis" as const, axisPointer: { type: "line" as const, lineStyle: { color: c.tinta, type: "dashed" as const } }, formatter: dicas },
      xAxis: { ...eixoBase, type: "category" as const, data: rotulos, boundaryGap: false, axisLabel: { ...eixoBase.axisLabel, hideOverlap: true } },
      yAxis: eixoValor,
      series: [
        { type: "line", data: firmes, symbol: "rect", symbolSize: 6, lineStyle: { color: c.tinta, width: 2 }, itemStyle: { color: c.tinta }, areaStyle: { color: c.papel, decal: pontilhado(c.tinta) } },
        { type: "line", data: finais, symbol: "rect", symbolSize: 6, lineStyle: { color: c.tinta, width: 2, type: "dashed" as const }, itemStyle: { color: c.papel, borderColor: c.tinta, borderWidth: 2 } },
      ],
    };
  }

  const horizontal = grafico.tipo === "barras";
  const nomes = grafico.dados.map((d) => String(d.x));
  const barras = {
    type: "bar",
    barCategoryGap: horizontal ? "28%" : "6%",
    data: grafico.dados.map((d, i) => ({
      value: d.y,
      itemStyle: {
        color: c.papel,
        borderColor: c.tinta,
        borderWidth: 2,
        decal: d.outros ? hachura(c.tinta) : i === 0 && horizontal ? { ...pontilhado(c.tinta), dashArrayX: [1, 0], dashArrayY: [1, 0] } : pontilhado(c.tinta),
      },
    })),
  };
  const eixoCategorias = { ...eixoBase, type: "category" as const, data: nomes, axisLabel: { ...eixoBase.axisLabel, hideOverlap: true, width: 130, overflow: "truncate" as const } };
  return {
    ...base,
    tooltip: { ...base.tooltip, trigger: "axis" as const, axisPointer: { type: "shadow" as const, shadowStyle: { color: c.tinta, opacity: 0.08 } }, formatter: dicas },
    xAxis: horizontal ? eixoValor : eixoCategorias,
    yAxis: horizontal ? { ...eixoCategorias, inverse: true } : { ...eixoValor, axisLabel: { ...eixoValor.axisLabel, formatter: (v: number) => fmtEixo(v, null) } },
    series: [barras],
  };
}

export function GraficoEcharts({ grafico, tema }: { grafico: Grafico; tema: string }) {
  const area = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!area.current) return;
    const instancia = echarts.init(area.current);
    instancia.setOption(opcoes(grafico, lerCores()) as echarts.EChartsCoreOption);
    const observador = new ResizeObserver(() => instancia.resize());
    observador.observe(area.current);
    return () => { observador.disconnect(); instancia.dispose(); };
  }, [grafico, tema]);

  return <div ref={area} className="grafico-area" role="img" aria-label={grafico.titulo} />;
}

