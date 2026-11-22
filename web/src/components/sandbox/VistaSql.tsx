"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { autocompletion, completionKeymap } from "@codemirror/autocomplete";
import { defaultKeymap, history, historyKeymap } from "@codemirror/commands";
import { sql, StandardSQL } from "@codemirror/lang-sql";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { EditorState } from "@codemirror/state";
import { EditorView, keymap, lineNumbers, placeholder } from "@codemirror/view";
import { tags } from "@lezer/highlight";
import * as api from "@/lib/api";
import { fmtInteiro, fmtTempo } from "@/lib/formato";
import type { Area, Consulta, ResultadoSql } from "@/lib/tipos";

const POR_PAGINA = 100;

/* Destaque em 1-bit: palavra-chave em negrito, texto em itálico, comentário apagado. */
const DESTAQUE = HighlightStyle.define([
  { tag: tags.keyword, color: "var(--destaque-texto)" },
  { tag: [tags.string, tags.special(tags.string)], fontStyle: "italic" },
  { tag: tags.comment, color: "var(--tinta-suave)" },
  { tag: tags.number, textDecoration: "underline dotted" },
]);

const VISUAL = EditorView.theme({
  "&": { backgroundColor: "var(--papel)", color: "var(--tinta)", border: "1px solid var(--linha)", borderRadius: "8px", fontSize: "13px" },
  ".cm-scroller": { borderRadius: "8px" },
  ".cm-content": { fontFamily: "var(--fonte-mono)", caretColor: "var(--destaque-texto)", minHeight: "120px", padding: "10px 0" },
  ".cm-gutters": { backgroundColor: "var(--papel)", color: "var(--tinta-fraca)", borderRight: "1px solid var(--linha)", borderRadius: "8px 0 0 8px" },
  "&.cm-focused": { outline: "none", borderColor: "var(--linha-forte)" },
  ".cm-activeLine": { backgroundColor: "transparent" },
  ".cm-cursor": { borderLeftColor: "var(--destaque-texto)" },
  ".cm-selectionBackground, &.cm-focused .cm-selectionBackground": { backgroundColor: "var(--destaque-fraco)" },
  ".cm-tooltip": { border: "1px solid var(--linha-forte)", backgroundColor: "var(--papel-2)", borderRadius: "6px" },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--destaque)", color: "var(--sobre-destaque)" },
});

type Props = {
  area: Area;
  ocupado: boolean;
  aoMudar: (area: Area) => void;
  executar: (texto: string, tarefa: () => Promise<void>) => Promise<void>;
};

export function VistaSql({ area, ocupado, aoMudar, executar }: Props) {
  const caixa = useRef<HTMLDivElement>(null);
  const editor = useRef<EditorView | null>(null);
  const rodarRef = useRef<() => void>(() => {});
  const primeira = area.tabelas[0]?.nome ?? "tabela";
  const [texto, setTexto] = useState(`SELECT *\nFROM ${primeira}\nLIMIT 100`);
  const [resultado, setResultado] = useState<ResultadoSql | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [rodando, setRodando] = useState(false);
  const [aberta, setAberta] = useState<Consulta | null>(null);
  const [nome, setNome] = useState("");

  /* O autocompletar conhece as tabelas e visões da sessão, com as colunas de cada uma. */
  const esquema = useMemo(() => {
    const mapa: Record<string, string[]> = {};
    for (const tabela of area.tabelas) {
      const perfil = tabela.perfil_limpo ?? tabela.perfil;
      mapa[tabela.nome] = perfil?.colunas.map((c) => c.nome) ?? [];
    }
    for (const visao of area.visoes) mapa[visao.nome] = [];
    return mapa;
  }, [area.tabelas, area.visoes]);

  const rodar = useCallback(async (inicio = 0) => {
    const consulta = editor.current?.state.doc.toString() ?? texto;
    setRodando(true);
    try {
      setResultado(await api.rodarSql(area.sessao, consulta, inicio, POR_PAGINA));
      setErro(null);
    } catch (falha) {
      setErro((falha as Error).message);
    } finally {
      setRodando(false);
    }
  }, [area.sessao, texto]);

  rodarRef.current = () => { rodar(0); };

  useEffect(() => {
    if (!caixa.current) return;
    const vista = new EditorView({
      parent: caixa.current,
      state: EditorState.create({
        doc: texto,
        extensions: [
          lineNumbers(),
          history(),
          autocompletion(),
          sql({ dialect: StandardSQL, schema: esquema, upperCaseKeywords: true }),
          syntaxHighlighting(DESTAQUE),
          placeholder("SELECT … FROM …"),
          keymap.of([
            { key: "Mod-Enter", run: () => { rodarRef.current(); return true; } },
            ...defaultKeymap, ...historyKeymap, ...completionKeymap,
          ]),
          VISUAL,
          EditorView.lineWrapping,
          EditorView.updateListener.of((u) => { if (u.docChanged) setTexto(u.state.doc.toString()); }),
          EditorView.contentAttributes.of({ "aria-label": "Consulta SQL" }),
        ],
      }),
    });
    editor.current = vista;
    return () => { vista.destroy(); editor.current = null; };
    // O editor nasce uma vez por esquema; o texto vive dentro dele.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [esquema]);

  const abrir = (consulta: Consulta) => {
    setAberta(consulta);
    setNome(consulta.nome);
    editor.current?.dispatch({ changes: { from: 0, to: editor.current.state.doc.length, insert: consulta.sql } });
  };

  const salvar = () => executar("Salvando a consulta", async () => {
    aoMudar(await api.salvarConsulta(area.sessao, nome || "consulta", texto, aberta?.id));
  });

  const comoTabela = () => executar("Criando a tabela a partir da consulta", async () => {
    aoMudar(await api.salvarSqlComoTabela(area.sessao, texto, nome || "consulta"));
  });

  const paginas = resultado ? Math.max(Math.ceil(resultado.total / POR_PAGINA), 1) : 1;
  const pagina = resultado ? Math.floor(resultado.inicio / POR_PAGINA) + 1 : 1;

  return (
    <div className="vista-sql">
      <div className="sql-topo">
        <div ref={caixa} className="sql-editor" />
        <aside className="sql-salvas">
          <h4>Consultas salvas</h4>
          {area.consultas.length ? (
            <ul>
              {area.consultas.map((c) => (
                <li key={c.id}>
                  <button type="button" className={`link${aberta?.id === c.id ? " ativa" : ""}`} onClick={() => abrir(c)}>{c.nome}.sql</button>
                  <a className="botao pequeno" href={api.linkConsulta(area.sessao, c.id)} aria-label={`Baixar ${c.nome}.sql`}>↓</a>
                  <button type="button" className="botao pequeno" aria-label={`Apagar ${c.nome}`} disabled={ocupado}
                          onClick={() => executar("Apagando a consulta", async () => { aoMudar(await api.apagarConsulta(area.sessao, c.id)); if (aberta?.id === c.id) setAberta(null); })}>×</button>
                </li>
              ))}
            </ul>
          ) : <p className="texto-suave">Nenhuma ainda.</p>}
          <p className="texto-suave">Tabelas: {area.tabelas.map((t) => t.nome).concat(area.visoes.map((v) => v.nome)).join(", ")}</p>
        </aside>
      </div>
      <div className="sql-acoes">
        <button type="button" className="botao principal" disabled={rodando} onClick={() => rodar(0)}>Rodar <kbd>Ctrl+Enter</kbd></button>
        <input className="campo" aria-label="Nome da consulta" id="sql-nome" placeholder="nome" value={nome} onChange={(e) => setNome(e.target.value)} />
        <button type="button" className="botao pequeno" disabled={ocupado || !texto.trim()} onClick={salvar}>{aberta ? "Atualizar .sql" : "Salvar .sql"}</button>
        <button type="button" className="botao pequeno" disabled={ocupado || !texto.trim()} onClick={comoTabela}>Salvar resultado como tabela</button>
        <span className="texto-suave">Só consultas rodam aqui: SELECT ou WITH.</span>
      </div>
      {erro ? <p className="aviso-linha" role="alert">! {erro}</p> : null}
      {resultado ? (
        <div className="sql-resultado">
          <div className="rolagem">
            <table className="tabela">
              <thead><tr>{resultado.colunas.map((c) => <th key={c.nome} scope="col" title={c.tipo}>{c.nome}</th>)}</tr></thead>
              <tbody>
                {resultado.linhas.map((linha, i) => (
                  <tr key={i}>{linha.map((valor, j) => <td key={j} className={`curta${valor === null ? " nulo" : ""}`}>{valor === null ? "vazio" : String(valor)}</td>)}</tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="paginacao">
            <span>{fmtInteiro(resultado.total)} linhas · {resultado.reaproveitada ? "resultado guardado" : fmtTempo(resultado.duracao_ms)}</span>
            <div>
              <button type="button" className="botao pequeno" disabled={pagina <= 1 || rodando} onClick={() => rodar((pagina - 2) * POR_PAGINA)}>◀</button>
              <span>{fmtInteiro(pagina)} de {fmtInteiro(paginas)}</span>
              <button type="button" className="botao pequeno" disabled={pagina >= paginas || rodando} onClick={() => rodar(pagina * POR_PAGINA)}>▶</button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
