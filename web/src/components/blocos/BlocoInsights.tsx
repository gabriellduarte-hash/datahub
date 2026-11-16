import { Janela } from "@/components/Janela";
import { fmtTempo } from "@/lib/formato";
import type { Analise, Insight } from "@/lib/tipos";

const NOMES: Record<string, string> = {
  variacao_periodo: "Variação no período",
  tendencia: "Tendência",
  pico_temporal: "Picos no tempo",
  concentracao: "Concentração",
  diferenca_entre_grupos: "Diferença entre grupos",
  valores_extremos: "Valores extremos",
  correlacao: "Correlação",
  taxa_por_grupo: "Taxa de sim por grupo",
  metrica_por_indicador: "Sim contra não",
  qualidade: "Qualidade dos dados",
};

export function Nota({ insight }: { insight: Insight }) {
  return (
    <Janela titulo={insight.titulo} nivel={3}>
      <p className="insight-texto">{insight.texto}</p>
      {insight.evidencias.length ? (
        <dl className="evidencias">
          {insight.evidencias.map((item) => (
            <div key={item.rotulo}><dt>{item.rotulo}</dt><dd>{item.valor}</dd></div>
          ))}
        </dl>
      ) : null}
      {insight.ressalvas.length ? (
        <ul className="ressalvas">{insight.ressalvas.map((texto) => <li key={texto}>{texto}</li>)}</ul>
      ) : null}
      <span className="marca tracejada">{NOMES[insight.tipo] ?? insight.tipo}</span>
    </Janela>
  );
}

export function PainelInsights({ analise }: { analise: Analise | null }) {
  const resultado = analise?.insights;
  if (!analise) {
    return <Janela titulo="Insights"><p className="texto-suave">Os insights aparecem quando há uma base de análise.</p></Janela>;
  }
  if (!resultado) {
    return <Janela titulo="Insights"><p>O bloco de insights não rodou. Reanalise para tentar de novo.</p></Janela>;
  }

  return (
    <>
      {resultado.insights.length ? (
        <div className="insights">
          {resultado.insights.map((insight, indice) => <Nota key={indice} insight={insight} />)}
        </div>
      ) : (
        <Janela titulo="Nenhum insight">
          <p>Nada nestes dados passou dos critérios. Isso é um resultado válido: o hub não afirma o que os números não sustentam.</p>
        </Janela>
      )}
      <Janela titulo="O que foi analisado">
        <p className="texto-suave">Cada analisador só afirma algo quando o efeito é maior do que o acaso explicaria.</p>
        <ul className="execucoes">
          {resultado.execucoes.map((execucao) => (
            <li key={execucao.analisador} title={execucao.motivo ?? undefined}>
              <span>{NOMES[execucao.analisador] ?? execucao.analisador}</span>
              <span className="marcas">
                {execucao.status === "ok"
                  ? <span className={`marca${execucao.insights ? " ok" : ""}`}>{execucao.insights} · {fmtTempo(execucao.duracao_ms)}</span>
                  : <span className="marca tracejada">{execucao.status}</span>}
              </span>
            </li>
          ))}
        </ul>
      </Janela>
    </>
  );
}
