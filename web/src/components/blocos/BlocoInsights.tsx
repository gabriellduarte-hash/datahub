import { fmtTempo } from "@/lib/formato";
import type { Execucao } from "@/lib/tipos";

export const NOMES: Record<string, string> = {
  variacao_periodo: "Variação no período",
  tendencia: "Tendência",
  pico_temporal: "Pico no tempo",
  concentracao: "Concentração",
  diferenca_entre_grupos: "Diferença entre grupos",
  valores_extremos: "Valores extremos",
  correlacao: "Correlação",
  taxa_por_grupo: "Taxa de sim por grupo",
  metrica_por_indicador: "Sim contra não",
  qualidade: "Qualidade dos dados",
};

/* O que rodou, em uma linha por analisador: quantos achados e quanto tempo. */
export function Execucoes({ execucoes }: { execucoes: Execucao[] }) {
  const achados = execucoes.reduce((total, e) => total + (e.insights ?? 0), 0);
  return (
    <details className="analisado">
      <summary>
        <span>O que foi analisado</span>
        <span className="texto-suave">{execucoes.length} analisadores · {achados} achados · só o que passa do acaso</span>
      </summary>
      <ul className="execucoes">
        {execucoes.map((execucao) => (
          <li key={execucao.analisador} title={execucao.motivo ?? undefined}>
            <span>{NOMES[execucao.analisador] ?? execucao.analisador}</span>
            {execucao.status === "ok"
              ? <span className={`marca${execucao.insights ? " ok" : ""}`}>{execucao.insights} · {fmtTempo(execucao.duracao_ms)}</span>
              : <span className="marca tracejada">{execucao.status === "pulado" ? "pulado" : "falhou"}</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}
