"use client";

import { useState } from "react";
import { GraficoEcharts } from "@/components/GraficoEcharts";
import { Janela } from "@/components/Janela";
import { calcularGrafico } from "@/lib/api";
import type { Analise, Grafico } from "@/lib/tipos";

const AGREGACOES = [
  ["soma", "soma"], ["media", "média"], ["mediana", "mediana"],
  ["minimo", "mínimo"], ["maximo", "máximo"], ["contagem", "contagem"], ["taxa", "% de sim"],
];
const GRAOS = [["dia", "dia"], ["semana", "semana"], ["mes", "mês"], ["trimestre", "trimestre"], ["ano", "ano"]];

type PropsCartao = { original: Grafico; sessao: string; analise: Analise; tema: string };

export function CartaoGrafico({ original, sessao, analise, tema }: PropsCartao) {
  const [grafico, setGrafico] = useState(original);
  const [erro, setErro] = useState<string | null>(null);
  const colunas = analise.perfil_limpo?.colunas ?? [];
  const metricas = colunas
    .filter((c) => ["inteiro", "decimal", "moeda", "percentual"].includes(c.tipo) || c.papel === "indicador")
    .map((c) => c.nome);
  const eixos = grafico.tipo === "linha"
    ? colunas.filter((c) => c.tipo === "data").map((c) => c.nome)
    : colunas.filter((c) => c.papel === "categoria" || c.papel === "indicador" || c.papel === "texto").map((c) => c.nome);

  const mudar = async (campos: Partial<Grafico>) => {
    const pedido = { tipo: grafico.tipo, x: grafico.x, y: grafico.y, agregacao: grafico.agregacao, grao: grafico.grao, ...campos };
    try {
      setGrafico(await calcularGrafico(sessao, pedido));
      setErro(null);
    } catch (falha) {
      setErro((falha as Error).message);
    }
  };

  const editavel = grafico.tipo === "linha" || grafico.tipo === "barras";
  const id = `${original.id}-${original.titulo}`.replace(/[^\w-]/g, "_");

  return (
    <Janela titulo={grafico.titulo} nivel={3}>
      <GraficoEcharts grafico={grafico} tema={tema} />
      {grafico.aviso ? <p className="grafico-nota">! {grafico.aviso}</p> : null}
      {erro ? <p className="grafico-nota">! {erro}</p> : null}
      {editavel ? (
        <div className="grafico-editor">
          <label className="rotulo">
            {grafico.tipo === "linha" ? "Data" : "Grupo"}
            <select className="campo" id={`${id}-x`} value={grafico.x ?? ""} onChange={(e) => mudar({ x: e.target.value })}>
              {eixos.map((nome) => <option key={nome} value={nome}>{nome}</option>)}
            </select>
          </label>
          <label className="rotulo">
            Valor
            <select className="campo" id={`${id}-y`} value={grafico.y ?? ""} onChange={(e) => mudar({ y: e.target.value || null, agregacao: e.target.value ? grafico.agregacao : "contagem" })}>
              <option value="">linhas</option>
              {metricas.map((nome) => <option key={nome} value={nome}>{nome}</option>)}
            </select>
          </label>
          <label className="rotulo">
            Cálculo
            <select className="campo" id={`${id}-agregacao`} value={grafico.agregacao} disabled={!grafico.y} onChange={(e) => mudar({ agregacao: e.target.value })}>
              {AGREGACOES.map(([valor, nome]) => <option key={valor} value={valor}>{nome}</option>)}
            </select>
          </label>
          {grafico.tipo === "linha" ? (
            <label className="rotulo">
              Período
              <select className="campo" id={`${id}-grao`} value={grafico.grao ?? ""} onChange={(e) => mudar({ grao: e.target.value })}>
                {GRAOS.map(([valor, nome]) => <option key={valor} value={valor}>{nome}</option>)}
              </select>
            </label>
          ) : null}
        </div>
      ) : null}
    </Janela>
  );
}

/* A coluna de gráficos ao lado da planilha. */
export function PainelGraficos({ sessao, analise, tema }: { sessao: string; analise: Analise | null; tema: string }) {
  if (!analise) {
    return (
      <Janela titulo="Gráficos" nivel={3}>
        <p className="texto-suave">Os gráficos aparecem quando há uma base de análise.</p>
      </Janela>
    );
  }
  if (!analise.graficos.length) {
    return (
      <Janela titulo="Gráficos" nivel={3}>
        <p className="texto-suave">Nenhum gráfico faz sentido com os papéis atuais. Marque ao menos uma métrica, categoria ou data no perfil.</p>
      </Janela>
    );
  }
  const chave = `${analise.rotulo}-${analise.linhas}`;
  return (
    <>
      {analise.graficos.map((grafico) => (
        <CartaoGrafico key={`${chave}-${grafico.id}-${grafico.titulo}`} original={grafico} sessao={sessao} analise={analise} tema={tema} />
      ))}
    </>
  );
}
