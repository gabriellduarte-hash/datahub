"use client";

import { CartaoGrafico } from "@/components/blocos/BlocoGraficos";
import { Execucoes, NOMES } from "@/components/blocos/BlocoInsights";
import { GraficoEcharts } from "@/components/GraficoEcharts";
import { GraficosLivres } from "@/components/sandbox/GraficosLivres";
import { fmtInteiro } from "@/lib/formato";
import type { Analise, Area, Grafico, Insight, ItemResumo } from "@/lib/tipos";

/* Ícones do topo do painel: traços simples sobre o quadrado verde. */
const ICONES: Record<string, string> = {
  linhas: "M4 6h12M4 10h12M4 14h12",
  colunas: "M6 4v12M10 4v12M14 4v12",
  preenchido: "M4 4h12v12H4zM7 10l2 2 4-4",
  metrica: "M4 14l4-4 3 3 5-6",
  taxa: "M5 15L15 5M6.5 7.5a1 1 0 1 0 0-.01M13.5 14.5a1 1 0 1 0 0-.01",
  periodo: "M4 6h12v10H4zM4 9h12M8 4v3M12 4v3",
};
const ALERTA = new Set(["valores_extremos", "qualidade"]);

const chave = (g: Pick<Grafico, "tipo" | "x" | "y" | "agregacao">) => `${g.tipo}|${g.x}|${g.y}|${g.agregacao}`;

function Kpi({ item }: { item: ItemResumo }) {
  return (
    <div className="kpi">
      <span className="kpi-icone" aria-hidden="true">
        <svg viewBox="0 0 20 20"><path d={ICONES[item.icone] ?? ICONES.linhas} /></svg>
      </span>
      <span className="kpi-texto">
        <strong>{item.valor}</strong>
        <span>{item.rotulo}</span>
      </span>
    </div>
  );
}

function ModuloInsight({ insight, principal, tema }: { insight: Insight; principal: boolean; tema: string }) {
  return (
    <article className={`modulo${principal ? " principal" : ""}`}>
      <div className="modulo-texto">
        <div className="modulo-topo">
          <span className={`tag${ALERTA.has(insight.tipo) ? " alerta" : ""}`}>{NOMES[insight.tipo] ?? insight.tipo}</span>
          {insight.colunas.length ? <span className="modulo-colunas">{insight.colunas.join(" × ")}</span> : null}
        </div>
        {insight.numero ? <p className="modulo-numero">{insight.numero}</p> : null}
        <h3>{insight.titulo}</h3>
        <p className="modulo-desc">{insight.texto}</p>
        {insight.evidencias.length ? (
          <dl className="evidencias">
            {insight.evidencias.slice(0, principal ? 6 : 3).map((item) => (
              <div key={item.rotulo}><dt>{item.rotulo}</dt><dd>{item.valor}</dd></div>
            ))}
          </dl>
        ) : null}
        {insight.ressalvas.length ? (
          <ul className="ressalvas">{insight.ressalvas.map((texto) => <li key={texto}>{texto}</li>)}</ul>
        ) : null}
      </div>
      {insight.grafico?.dados.length ? (
        <figure className="modulo-grafico">
          <figcaption>{insight.grafico.titulo}</figcaption>
          <GraficoEcharts grafico={insight.grafico} tema={tema} destaque={insight.destaque} compacto={!principal} />
        </figure>
      ) : null}
    </article>
  );
}

type Props = { area: Area; analise: Analise | null; tema: string; ocupado: boolean; aoMudar: (area: Area) => void };

/* Embaixo da tabela: os números da base e, em módulos, cada insight junto do dado que ele cita. */
export function PainelExecutivo({ area, analise, tema, ocupado, aoMudar }: Props) {
  const sessao = area.sessao;
  if (!analise) {
    return (
      <section className="painel-executivo vazio">
        <p>Normalize uma tabela para ter uma base de análise. O painel aparece aqui, embaixo dela.</p>
      </section>
    );
  }
  const insights = analise.insights?.insights ?? [];
  const citados = new Set(insights.filter((i) => i.grafico).map((i) => chave(i.grafico!)));
  const explorar = analise.graficos.filter((g) => !citados.has(chave(g)));

  return (
    <section className="painel-executivo" aria-label="Painel executivo">
      <div className="cantos">
        <span>Painel executivo</span>
        <span>{analise.rotulo} · {fmtInteiro(analise.linhas)} linhas</span>
      </div>
      {analise.resumo?.length ? <div className="kpis">{analise.resumo.map((item) => <Kpi key={item.rotulo} item={item} />)}</div> : null}

      {insights.length ? (
        <div className="modulos">
          {insights.map((insight, indice) => (
            <ModuloInsight key={`${insight.tipo}-${insight.titulo}`} insight={insight} principal={indice === 0} tema={tema} />
          ))}
        </div>
      ) : (
        <div className="modulo vazio">
          <p>Nada nestes dados passou dos critérios. Isso é um resultado válido: o hub não afirma o que os números não sustentam.</p>
        </div>
      )}

      <GraficosLivres area={area} analise={analise} tema={tema} ocupado={ocupado} aoMudar={aoMudar} />

      {explorar.length ? (
        <>
          <div className="cantos">
            <span>Explorar</span>
            <span>gráficos que você pode editar</span>
          </div>
          <div className="modulos explorar">
            {explorar.map((grafico) => (
              <CartaoGrafico key={`${analise.rotulo}-${chave(grafico)}`} original={grafico} sessao={sessao} analise={analise} tema={tema} />
            ))}
          </div>
        </>
      ) : null}

      {analise.insights ? <Execucoes execucoes={analise.insights.execucoes} /> : null}
    </section>
  );
}
