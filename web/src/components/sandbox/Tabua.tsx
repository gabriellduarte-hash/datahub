"use client";

import { useState } from "react";
import { PainelGraficos } from "@/components/blocos/BlocoGraficos";
import { PainelInsights } from "@/components/blocos/BlocoInsights";
import { Janela } from "@/components/Janela";
import { Esquema } from "@/components/sandbox/Esquema";
import { Planilha } from "@/components/sandbox/Planilha";
import { VistaSql } from "@/components/sandbox/VistaSql";
import * as api from "@/lib/api";
import { fmtInteiro } from "@/lib/formato";
import type { Area, Tabela } from "@/lib/tipos";

type Vista = "planilha" | "esquema" | "sql";
const VISTAS: [Vista, string][] = [["planilha", "Planilha"], ["esquema", "Esquema"], ["sql", "SQL"]];

type Props = {
  area: Area;
  tabela: Tabela | null;
  tema: string;
  ocupado: boolean;
  aoMudar: (area: Area) => void;
  executar: (texto: string, tarefa: () => Promise<void>) => Promise<void>;
  aoNormalizar: () => void;
};

/* A tábua: as três vistas no meio, gráficos ao lado e insights embaixo.
   A faixa tracejada diz de onde vêm gráficos e insights. */
export function Tabua({ area, tabela, tema, ocupado, aoMudar, executar, aoNormalizar }: Props) {
  const [vista, setVista] = useState<Vista>("planilha");
  const analise = area.analise;
  const opcoesBase = [
    ...area.tabelas.filter((t) => t.relatorio).map((t) => ({ valor: `tabela:${t.id}`, nome: t.nome })),
    ...area.visoes.map((v) => ({ valor: `visao:${v.id}`, nome: `${v.nome} (visão)` })),
  ];
  const baseAtual = area.base ? `${area.base.tipo}:${area.base.id}` : "";

  const trocarBase = (valor: string) => {
    const [tipo, id] = valor.split(":");
    executar("Analisando a nova base", async () => {
      aoMudar(await api.escolherBase(area.sessao, { tipo: tipo as "tabela" | "visao", id }));
    });
  };

  const reanalisar = () => executar("Refazendo gráficos e insights", async () => {
    aoMudar(await api.reanalisar(area.sessao));
  });

  return (
    <div className="tabua">
      <section className="tabua-centro janela" aria-label="Dados">
        <div className="barra">
          <h2>{vista === "planilha" ? tabela?.nome ?? "Planilha" : vista === "esquema" ? "Esquema" : "SQL"}</h2>
        </div>
        <div className="corpo">
          <div className="vistas-abas" role="tablist" aria-label="Jeito de ver os dados">
            {VISTAS.map(([valor, nome]) => (
              <button key={valor} type="button" role="tab" aria-selected={vista === valor}
                      className={vista === valor ? "on" : ""} onClick={() => setVista(valor)}>{nome}</button>
            ))}
          </div>
          {vista === "planilha" ? (
            tabela?.relatorio ? (
              <Planilha key={tabela.id} area={area} tabela={tabela} ocupado={ocupado} aoMudar={aoMudar} executar={executar} />
            ) : (
              <div className="vazio-planilha">
                <p>{tabela ? `${tabela.nome} ainda não passou pela normalização.` : "Nenhuma tabela escolhida."}</p>
                {tabela ? <button type="button" className="botao principal" onClick={aoNormalizar}>Normalizar {tabela.nome}</button> : null}
              </div>
            )
          ) : null}
          {vista === "esquema" ? <Esquema area={area} ocupado={ocupado} aoMudar={aoMudar} executar={executar} /> : null}
          {vista === "sql" ? <VistaSql area={area} ocupado={ocupado} aoMudar={aoMudar} executar={executar} /> : null}
        </div>
      </section>

      <aside className="tabua-lado" aria-label="Gráficos">
        <PainelGraficos sessao={area.sessao} analise={analise} tema={tema} />
      </aside>

      <div className="via">
        <label className="etiqueta base" htmlFor="base-analise">
          Base de análise ▸
          <select id="base-analise" value={baseAtual} disabled={ocupado || !opcoesBase.length} onChange={(e) => trocarBase(e.target.value)}>
            {!baseAtual ? <option value="">escolha…</option> : null}
            {opcoesBase.map((o) => <option key={o.valor} value={o.valor}>{o.nome}</option>)}
          </select>
        </label>
        <span className="estado-analise">
          {analise ? <span className="texto-suave">{fmtInteiro(analise.linhas)} linhas</span> : null}
          {analise?.desatualizada ? <span className="marca tracejada">desatualizado</span> : analise ? <span className="marca ok">em dia</span> : null}
          <button type="button" className="botao pequeno" disabled={ocupado || !area.base} onClick={reanalisar}>Reanalisar</button>
        </span>
      </div>

      <section className="tabua-baixo" aria-label="Insights">
        {analise ? <PainelInsights analise={analise} /> : (
          <Janela titulo="Insights"><p className="texto-suave">Normalize uma tabela para ter uma base de análise.</p></Janela>
        )}
      </section>
    </div>
  );
}
