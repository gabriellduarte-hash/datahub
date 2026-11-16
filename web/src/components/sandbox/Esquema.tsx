"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Background, BackgroundVariant, Controls, Handle, MarkerType, Position, ReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import * as api from "@/lib/api";
import { fmtInteiro } from "@/lib/formato";
import type { Area, Esquema as DadosEsquema, EsquemaTabela, PontaRelacao, Relacao } from "@/lib/tipos";

type DadosNo = { tabela: EsquemaTabela; base: boolean };

/* Cada tabela é uma janela 1-bit: nome na barra, colunas embaixo. */
function NoTabela({ data }: NodeProps<Node<DadosNo>>) {
  const { tabela, base } = data;
  return (
    <div className={`no-tabela${base ? " base" : ""}`}>
      <Handle type="target" position={Position.Left} />
      <div className="no-barra"><strong>{tabela.nome}</strong></div>
      <div className="no-info">{fmtInteiro(tabela.linhas)} linhas{tabela.normalizada ? "" : " · sem normalizar"}</div>
      <ul>
        {tabela.colunas.slice(0, 14).map((coluna) => (
          <li key={coluna.nome}>
            <span className="no-coluna">
              {coluna.chave ? <b title="chave única">⚿ </b> : null}
              {coluna.aponta ? <b title="aponta para outra tabela">→ </b> : null}
              {coluna.nome}
            </span>
            <span className="no-tipo">{coluna.tipo.toLowerCase()}</span>
          </li>
        ))}
        {tabela.colunas.length > 14 ? <li className="texto-suave">+ {tabela.colunas.length - 14} colunas</li> : null}
      </ul>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

const TIPOS_DE_NO = { tabela: NoTabela };

type Props = {
  area: Area;
  ocupado: boolean;
  aoMudar: (area: Area) => void;
  executar: (texto: string, tarefa: () => Promise<void>) => Promise<void>;
};

export function Esquema({ area, ocupado, aoMudar, executar }: Props) {
  const [esquema, setEsquema] = useState<DadosEsquema | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [manual, setManual] = useState<{ de: PontaRelacao; para: PontaRelacao }>({
    de: { tabela: "", coluna: "" }, para: { tabela: "", coluna: "" },
  });
  const [visao, setVisao] = useState<{ nome: string; tabela: string; relacoes: string[] }>({ nome: "", tabela: "", relacoes: [] });
  const [pilha, setPilha] = useState<{ nome: string; tabelas: string[] }>({ nome: "", tabelas: [] });

  const assinatura = [
    ...area.tabelas.map((t) => `${t.id}:${t.versao}:${t.nome}`),
    ...area.relacoes.map((r) => r.id),
    ...area.visoes.map((v) => v.id),
  ].join("|");

  useEffect(() => {
    let ativo = true;
    api.verEsquema(area.sessao)
      .then((dados) => { if (ativo) { setEsquema(dados); setErro(null); } })
      .catch((falha: Error) => { if (ativo) setErro(falha.message); });
    return () => { ativo = false; };
  }, [area.sessao, assinatura]);

  const nomeDe = (id: string) => area.tabelas.find((t) => t.id === id)?.nome ?? id;

  const nos = useMemo<Node<DadosNo>[]>(() => (esquema?.tabelas ?? []).map((tabela, i) => ({
    id: tabela.id,
    type: "tabela",
    position: { x: (i % 3) * 300, y: Math.floor(i / 3) * 330 },
    data: { tabela, base: area.base?.tipo === "tabela" && area.base.id === tabela.id },
  })), [esquema, area.base]);

  const arestas = useMemo<Edge[]>(() => (esquema?.relacoes ?? []).map((relacao) => ({
    id: relacao.id,
    source: relacao.de.tabela,
    target: relacao.para.tabela,
    type: "smoothstep",
    label: `${relacao.de.coluna} → ${relacao.para.coluna}`,
    className: "aresta-relacao",
    markerEnd: { type: MarkerType.ArrowClosed },
  })), [esquema]);

  const agir = (texto: string, tarefa: () => Promise<Area>) => executar(texto, async () => {
    aoMudar(await tarefa());
  });

  const colunasDe = (tabelaId: string) => esquema?.tabelas.find((t) => t.id === tabelaId)?.colunas ?? [];
  const relacoesDaBase = area.relacoes.filter((r) => visao.tabela && (r.de.tabela === visao.tabela || r.para.tabela === visao.tabela));
  const modoDe = (relacao: Relacao) => (relacao.de.tabela === visao.tabela ? "colunas" : "resumo");

  if (erro) return <p className="aviso-linha">! {erro}</p>;
  if (!esquema) return <p className="texto-suave">Montando o esquema…</p>;

  return (
    <div className="esquema">
      <div className="esquema-desenho">
        <ReactFlow
          nodes={nos}
          edges={arestas}
          nodeTypes={TIPOS_DE_NO}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          proOptions={{ hideAttribution: true }}
          nodesConnectable={false}
          minZoom={0.3}
        >
          <Background variant={BackgroundVariant.Dots} gap={14} size={1.2} />
          <Controls showInteractive={false} />
        </ReactFlow>
      </div>

      <aside className="esquema-painel">
        <section>
          <h4>Relações sugeridas</h4>
          {esquema.sugestoes.length ? esquema.sugestoes.map((s, i) => (
            <div key={i} className="sugestao-relacao">
              <span>{nomeDe(s.de.tabela)}.{s.de.coluna} → {nomeDe(s.para.tabela)}.{s.para.coluna}</span>
              <span className="texto-suave">{Math.round(s.cobertura * 100)}% dos valores encontrados</span>
              <button type="button" className="botao pequeno" disabled={ocupado}
                      onClick={() => agir("Criando a relação", () => api.criarRelacao(area.sessao, s.de, s.para))}>Criar</button>
            </div>
          )) : <p className="texto-suave">{area.tabelas.length < 2 ? "Carregue mais um arquivo para relacionar tabelas." : "Nenhuma relação nova pelos nomes das colunas."}</p>}
        </section>

        <section>
          <h4>Relação manual</h4>
          <p className="texto-suave">A seta aponta para a tabela onde a chave é única, como pedidos.cliente_id → clientes.cliente_id.</p>
          <div className="linha-campos">
            <select className="campo" aria-label="Tabela de origem" id="rel-de-tabela" value={manual.de.tabela}
                    onChange={(e) => setManual({ ...manual, de: { tabela: e.target.value, coluna: "" } })}>
              <option value="">tabela…</option>
              {area.tabelas.map((t) => <option key={t.id} value={t.id}>{t.nome}</option>)}
            </select>
            <select className="campo" aria-label="Coluna de origem" id="rel-de-coluna" value={manual.de.coluna}
                    onChange={(e) => setManual({ ...manual, de: { ...manual.de, coluna: e.target.value } })}>
              <option value="">coluna…</option>
              {colunasDe(manual.de.tabela).map((c) => <option key={c.nome} value={c.nome}>{c.nome}</option>)}
            </select>
          </div>
          <span aria-hidden="true">↓</span>
          <div className="linha-campos">
            <select className="campo" aria-label="Tabela de destino" id="rel-para-tabela" value={manual.para.tabela}
                    onChange={(e) => setManual({ ...manual, para: { tabela: e.target.value, coluna: "" } })}>
              <option value="">tabela…</option>
              {area.tabelas.map((t) => <option key={t.id} value={t.id}>{t.nome}</option>)}
            </select>
            <select className="campo" aria-label="Coluna de destino" id="rel-para-coluna" value={manual.para.coluna}
                    onChange={(e) => setManual({ ...manual, para: { ...manual.para, coluna: e.target.value } })}>
              <option value="">coluna…</option>
              {colunasDe(manual.para.tabela).map((c) => <option key={c.nome} value={c.nome}>{c.nome}</option>)}
            </select>
          </div>
          <button type="button" className="botao pequeno" disabled={ocupado || !manual.de.coluna || !manual.para.coluna}
                  onClick={() => agir("Criando a relação", () => api.criarRelacao(area.sessao, manual.de, manual.para))}>Criar relação</button>
          {area.relacoes.length ? (
            <ul className="lista-relacoes">
              {area.relacoes.map((r) => (
                <li key={r.id}>
                  <span>{nomeDe(r.de.tabela)}.{r.de.coluna} → {nomeDe(r.para.tabela)}.{r.para.coluna}</span>
                  <button type="button" className="botao pequeno" aria-label="Apagar relação" disabled={ocupado}
                          onClick={() => agir("Apagando a relação", () => api.apagarRelacao(area.sessao, r.id))}>×</button>
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <section>
          <h4>Visão unificada</h4>
          <p className="texto-suave">Parte de uma tabela e traz colunas das outras. Vira a base de gráficos e insights.</p>
          <select className="campo" aria-label="Tabela base" id="visao-base" value={visao.tabela}
                  onChange={(e) => setVisao({ ...visao, tabela: e.target.value, relacoes: [] })}>
            <option value="">tabela base…</option>
            {area.tabelas.filter((t) => t.relatorio).map((t) => <option key={t.id} value={t.id}>{t.nome}</option>)}
          </select>
          {relacoesDaBase.map((r) => (
            <label key={r.id} className="caixa juncao" htmlFor={`juncao-${r.id}`}>
              <input id={`juncao-${r.id}`} type="checkbox" checked={visao.relacoes.includes(r.id)}
                     onChange={(e) => setVisao({ ...visao, relacoes: e.target.checked ? [...visao.relacoes, r.id] : visao.relacoes.filter((x) => x !== r.id) })} />
              <span>
                {modoDe(r) === "colunas"
                  ? <>traz as colunas de <b>{nomeDe(r.para.tabela)}</b></>
                  : <>traz um resumo de <b>{nomeDe(r.de.tabela)}</b>: quantas linhas e somas por {r.para.coluna}. Trazer as linhas repetiria {nomeDe(visao.tabela)}.</>}
              </span>
            </label>
          ))}
          {visao.tabela && !relacoesDaBase.length ? <p className="texto-suave">Essa tabela ainda não tem relações.</p> : null}
          <input className="campo" aria-label="Nome da visão" id="visao-nome" placeholder="nome da visão" value={visao.nome}
                 onChange={(e) => setVisao({ ...visao, nome: e.target.value })} />
          <button type="button" className="botao pequeno principal" disabled={ocupado || !visao.tabela || !visao.relacoes.length}
                  onClick={() => agir("Criando a visão e analisando", () => api.criarVisao(
                    area.sessao, visao.nome || `${nomeDe(visao.tabela)}_completa`, visao.tabela,
                    visao.relacoes.map((id) => ({ relacao: id, modo: modoDe(area.relacoes.find((r) => r.id === id)!) })),
                  ))}>Criar visão e analisar</button>
          {area.visoes.length ? (
            <ul className="lista-relacoes">
              {area.visoes.map((v) => (
                <li key={v.id}>
                  <span>{v.nome} · {fmtInteiro(v.linhas)} linhas{v.erro ? ` · ! ${v.erro}` : ""}</span>
                  <span className="marcas">
                    <button type="button" className="botao pequeno" disabled={ocupado || (area.base?.tipo === "visao" && area.base.id === v.id)}
                            onClick={() => agir("Analisando a visão", () => api.escolherBase(area.sessao, { tipo: "visao", id: v.id }))}>Usar como base</button>
                    <button type="button" className="botao pequeno" aria-label="Apagar visão" disabled={ocupado}
                            onClick={() => agir("Apagando a visão", () => api.apagarVisao(area.sessao, v.id))}>×</button>
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <section>
          <h4>Empilhar</h4>
          <p className="texto-suave">Tabelas com as mesmas colunas, uma embaixo da outra, como vendas de cada mês.</p>
          {area.tabelas.map((t) => (
            <label key={t.id} className="caixa" htmlFor={`pilha-${t.id}`}>
              <input id={`pilha-${t.id}`} type="checkbox" checked={pilha.tabelas.includes(t.id)}
                     onChange={(e) => setPilha({ ...pilha, tabelas: e.target.checked ? [...pilha.tabelas, t.id] : pilha.tabelas.filter((x) => x !== t.id) })} />
              {t.nome}
            </label>
          ))}
          <input className="campo" aria-label="Nome da tabela empilhada" id="pilha-nome" placeholder="nome da tabela nova" value={pilha.nome}
                 onChange={(e) => setPilha({ ...pilha, nome: e.target.value })} />
          <button type="button" className="botao pequeno" disabled={ocupado || pilha.tabelas.length < 2}
                  onClick={() => agir("Empilhando as tabelas", () => api.empilhar(area.sessao, pilha.tabelas, pilha.nome || "empilhada"))}>Empilhar</button>
        </section>
      </aside>
    </div>
  );
}
