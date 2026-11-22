"use client";

import { useState } from "react";
import { Esquema } from "@/components/sandbox/Esquema";
import { PainelExecutivo } from "@/components/sandbox/Painel";
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

/* A sandbox: os dados em cima, o painel executivo embaixo. A pílula no meio
   diz de onde vêm os números do painel e se eles estão em dia. */
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

  const reanalisar = () => executar("Refazendo o painel", async () => {
    aoMudar(await api.reanalisar(area.sessao));
  });

  return (
    <div className="sandbox">
      <section className="painel-dados" aria-label="Dados">
        <div className="painel-dados-topo">
          <span className="tag destaque">
            {vista === "planilha" ? tabela?.nome ?? "planilha" : vista === "esquema" ? `${area.tabelas.length} ${area.tabelas.length === 1 ? "tabela" : "tabelas"}` : "consulta"}
          </span>
          <div className="segmentado" role="tablist" aria-label="Jeito de ver os dados">
            {VISTAS.map(([valor, nome]) => (
              <button key={valor} type="button" role="tab" aria-selected={vista === valor}
                      className={vista === valor ? "on" : ""} onClick={() => setVista(valor)}>{nome}</button>
            ))}
          </div>
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
      </section>

      <div className="conector" aria-label="Base de análise">
        <div className="conector-pilula">
          <span className={`ponto${analise?.desatualizada ? " parado" : ""}`} aria-hidden="true" />
          <label htmlFor="base-analise">Base de análise</label>
          <select id="base-analise" value={baseAtual} disabled={ocupado || !opcoesBase.length} onChange={(e) => trocarBase(e.target.value)}>
            {!baseAtual ? <option value="">escolha…</option> : null}
            {opcoesBase.map((o) => <option key={o.valor} value={o.valor}>{o.nome}</option>)}
          </select>
          {analise ? <span className="texto-suave">{fmtInteiro(analise.linhas)} linhas</span> : null}
          {analise ? <span className={`marca${analise.desatualizada ? " tracejada" : " ok"}`}>{analise.desatualizada ? "desatualizado" : "em dia"}</span> : null}
          <button type="button" className="botao pequeno" disabled={ocupado || !area.base} onClick={reanalisar}>Reanalisar</button>
        </div>
      </div>

      <PainelExecutivo sessao={area.sessao} analise={analise} tema={tema} />
    </div>
  );
}
