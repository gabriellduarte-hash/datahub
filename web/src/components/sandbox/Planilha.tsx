"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule, ModuleRegistry, themeQuartz,
  type CellValueChangedEvent, type ColDef, type GridApi, type IDatasource, type SelectionChangedEvent, type ValueFormatterParams,
} from "ag-grid-community";
import * as api from "@/lib/api";
import { fmtInteiro } from "@/lib/formato";
import type { Area, Contagem, Filtro, Passo, PreviaFormula, Tabela } from "@/lib/tipos";

ModuleRegistry.registerModules([AllCommunityModule]);

/* A AG Grid vestida de 1-bit: as cores vêm das mesmas variáveis do resto do hub,
   então o modo escuro funciona sem um tema à parte. */
const TEMA = themeQuartz.withParams({
  backgroundColor: "var(--papel)",
  foregroundColor: "var(--tinta)",
  textColor: "var(--tinta)",
  borderColor: "var(--linha)",
  chromeBackgroundColor: "var(--papel)",
  headerBackgroundColor: "var(--papel-2)",
  headerTextColor: "var(--tinta-suave)",
  headerFontWeight: 500,
  accentColor: "var(--destaque)",
  rowHoverColor: "var(--papel-2)",
  selectedRowBackgroundColor: "var(--destaque-fraco)",
  oddRowBackgroundColor: "var(--papel)",
  fontFamily: "var(--fonte-mono)",
  headerFontFamily: "var(--fonte-mono)",
  fontSize: 12.5,
  headerFontSize: 11.5,
  borderRadius: 4,
  wrapperBorderRadius: 8,
  checkboxBorderRadius: 3,
  spacing: 5,
  wrapperBorder: { width: 1, color: "var(--linha)", style: "solid" },
  rowBorder: { width: 1, color: "var(--linha)", style: "solid" },
  columnBorder: { width: 1, color: "var(--linha)", style: "solid" },
  headerColumnBorder: { width: 1, color: "var(--linha)", style: "solid" },
});

const OPERADORES: [string, string][] = [
  ["igual", "="], ["diferente", "≠"], ["maior", ">"], ["menor", "<"], ["maior_igual", "≥"], ["menor_igual", "≤"],
  ["contem", "contém"], ["nao_contem", "não contém"], ["vazio", "vazio"], ["preenchido", "preenchido"],
];
const NOMES_OPERADOR = Object.fromEntries(OPERADORES);
const NUMERICOS = /INT|DOUBLE|FLOAT|DECIMAL/;

type Linha = Record<string, string | number | boolean | null>;

type Props = {
  area: Area;
  tabela: Tabela;
  ocupado: boolean;
  aoMudar: (area: Area) => void;
  executar: (texto: string, tarefa: () => Promise<void>) => Promise<void>;
};

/* "Marcar todas" vale para a visão inteira, não só para as linhas já carregadas:
   a planilha busca as linhas aos poucos, então a escolha fica guardada aqui e as
   linhas que chegam depois já vêm marcadas. */
type Selecao = { todas: boolean; algumas: boolean; total: number; alternar: () => void };
const ContextoSelecao = createContext<Selecao>({ todas: false, algumas: false, total: 0, alternar: () => {} });

function CabecalhoSelecao() {
  const { todas, algumas, total, alternar } = useContext(ContextoSelecao);
  return (
    <input type="checkbox" className="marcar-todas" checked={todas} onChange={alternar}
           ref={(caixa) => { if (caixa) caixa.indeterminate = algumas && !todas; }}
           aria-label={todas ? "Desmarcar todas" : `Marcar todas as ${fmtInteiro(total)} linhas da visão`}
           title={todas ? "Desmarcar todas" : `Marcar todas as ${fmtInteiro(total)} linhas da visão`} />
  );
}

const FUNCOES_AGRUPAR: [string, string][] = [["soma", "soma"], ["media", "média"], ["minimo", "mínimo"], ["maximo", "máximo"]];
type Agrupamento = { colunas: string[]; contagem: string; agregacoes: { coluna: string; funcao: string }[] };
const AGRUPAMENTO_VAZIO: Agrupamento = { colunas: [], contagem: "quantidade", agregacoes: [] };

function formatar({ value }: ValueFormatterParams) {
  if (value === null || value === undefined) return "vazio";
  if (value === true) return "sim";
  if (value === false) return "não";
  if (typeof value === "number") return new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 6 }).format(value);
  return String(value);
}

export function Planilha({ area, tabela, ocupado, aoMudar, executar }: Props) {
  const grade = useRef<GridApi | null>(null);
  const [colunas, setColunas] = useState<{ nome: string; tipo: string }[]>([]);
  const [totais, setTotais] = useState({ total: 0, filtradas: 0 });
  const [filtros, setFiltros] = useState<Filtro[]>([]);
  const [marcadas, setMarcadas] = useState<number[]>([]);
  const [contagem, setContagem] = useState<Contagem | null>(null);
  const [previa, setPrevia] = useState<PreviaFormula | null>(null);
  const [erroFormula, setErroFormula] = useState<string | null>(null);
  const [formula, setFormula] = useState({ nome: "", texto: "", substituir: false });
  const [novoFiltro, setNovoFiltro] = useState<Filtro>({ coluna: "", operador: "igual", valor: "" });
  const [colunaAcao, setColunaAcao] = useState("");
  const [nomeNovo, setNomeNovo] = useState("");
  const [busca, setBusca] = useState({ coluna: "", termo: "", inteiro: false });
  const [todas, setTodas] = useState(false);
  const todasAgora = useRef(false);
  const [agrupando, setAgrupando] = useState<Agrupamento | null>(null);
  const [novaConta, setNovaConta] = useState({ funcao: "soma", coluna: "" });
  const sessao = area.sessao;

  const marcarTodas = useCallback((marcar: boolean) => {
    todasAgora.current = marcar;
    setTodas(marcar);
    if (marcar) grade.current?.forEachNode((no) => { if (no.data) no.setSelected(true, false, "api"); });
    else {
      grade.current?.deselectAll();
      setMarcadas([]);
    }
  }, []);

  /* As colunas vêm da primeira página. Mudam quando uma fórmula cria ou um passo apaga ou renomeia uma coluna. */
  useEffect(() => {
    let ativo = true;
    api.verLinhas(sessao, tabela.id, 0, 1, { coluna: null, decrescente: false }, [])
      .then((pagina) => { if (ativo) setColunas(pagina.colunas); })
      .catch(() => {});
    return () => { ativo = false; };
  }, [sessao, tabela.id, tabela.versao]);

  const fonte = useMemo<IDatasource>(() => ({
    getRows: (pedido) => {
      const ordem = pedido.sortModel[0]
        ? { coluna: pedido.sortModel[0].colId, decrescente: pedido.sortModel[0].sort === "desc" }
        : { coluna: null, decrescente: false };
      api.verLinhas(sessao, tabela.id, pedido.startRow, pedido.endRow - pedido.startRow, ordem, filtros)
        .then((pagina) => {
          const linhas: Linha[] = pagina.linhas.map((valores, i) => {
            const linha: Linha = { __linha: pagina.numeros[i] };
            pagina.colunas.forEach((coluna, j) => { linha[coluna.nome] = valores[j]; });
            return linha;
          });
          setTotais({ total: pagina.total, filtradas: pagina.filtradas });
          pedido.successCallback(linhas, pagina.filtradas);
        })
        .catch(() => pedido.failCallback());
    },
  }), [sessao, tabela.id, filtros]);

  /* Depois de um passo, a planilha busca de novo o que está à vista, sem perder a rolagem. */
  useEffect(() => {
    grade.current?.refreshInfiniteCache();
    marcarTodas(false);
  }, [tabela.versao, marcarTodas]);

  /* Com outro filtro, "todas" passaria a ser outras linhas: a marcação recomeça. */
  useEffect(() => { marcarTodas(false); }, [filtros, marcarTodas]);

  const aoMudarSelecao = (evento: SelectionChangedEvent) => {
    const selecionadas = evento.api.getSelectedRows().map((linha) => linha.__linha as number);
    if (todasAgora.current && evento.source !== "api") {
      // desmarcou uma linha depois de marcar todas: vale o que está marcado na tela
      todasAgora.current = false;
      setTodas(false);
    }
    if (!todasAgora.current) setMarcadas(selecionadas);
  };

  const apagarMarcadas = () => {
    if (todas) {
      passo(`Apagando as ${fmtInteiro(totais.filtradas)} linhas do filtro`, { op: "filtrar_linhas", modo: "remover", filtros }, () => setFiltros([]));
    } else {
      passo("Apagando as linhas marcadas", { op: "remover_linhas", linhas: marcadas });
    }
  };

  const agrupar = () => {
    if (!agrupando) return;
    const nomes = agrupando.colunas.join(", ");
    passo(`Agrupando por ${nomes}`, { op: "agrupar", ...agrupando }, () => setAgrupando(null));
  };

  const definicoes = useMemo<ColDef[]>(() => [
    { field: "__linha", headerName: "#", width: 84, pinned: "left", editable: false, sortable: false, cellClass: "celula-numero", headerTooltip: "Número da linha no arquivo" },
    ...colunas.map((coluna): ColDef => ({
      field: coluna.nome,
      headerName: coluna.nome,
      headerTooltip: `${coluna.nome} · ${coluna.tipo}`,
      editable: true,
      sortable: true,
      cellDataType: false,
      minWidth: 120,
      flex: 1,
      valueFormatter: formatar,
      cellClassRules: { "celula-vazia": (p) => p.value === null || p.value === undefined },
      type: NUMERICOS.test(coluna.tipo) ? "rightAligned" : undefined,
    })),
  ], [colunas]);

  const passo = useCallback((descricao: string, novo: Passo, depois?: () => void) => executar(descricao, async () => {
    try {
      aoMudar(await api.acrescentarPasso(sessao, tabela.id, novo));
      depois?.();
    } catch (falha) {
      grade.current?.refreshInfiniteCache();
      throw falha;
    }
  }), [executar, aoMudar, sessao, tabela.id]);

  const editar = (evento: CellValueChangedEvent) => {
    if (evento.oldValue === evento.newValue) return;
    const valor = evento.newValue === null || evento.newValue === undefined ? "" : String(evento.newValue);
    passo("Gravando a edição", {
      op: "editar_celulas",
      edicoes: [{ linha: evento.data.__linha, coluna: evento.colDef.field, valor }],
    });
  };

  const verPrevia = async (evento?: FormEvent) => {
    evento?.preventDefault();
    try {
      setPrevia(await api.previaFormula(sessao, tabela.id, formula.nome, formula.texto, formula.substituir));
      setErroFormula(null);
    } catch (falha) {
      setPrevia(null);
      setErroFormula((falha as Error).message);
    }
  };

  const criarColuna = () => passo(`Calculando ${formula.nome}`, {
    op: "formula", nome: formula.nome, formula: formula.texto, substituir: formula.substituir,
  }, () => { setPrevia(null); setFormula({ nome: "", texto: "", substituir: false }); });

  const adicionarFiltro = (evento: FormEvent) => {
    evento.preventDefault();
    if (!novoFiltro.coluna) return;
    setFiltros([...filtros, { ...novoFiltro }]);
    setNovoFiltro({ ...novoFiltro, valor: "" });
  };

  const aplicarFiltros = () => passo("Aplicando o filtro à tabela", { op: "filtrar_linhas", filtros }, () => setFiltros([]));

  const contar = async (evento: FormEvent) => {
    evento.preventDefault();
    if (!busca.coluna || !busca.termo) return;
    try {
      setContagem(await api.contarTermo(sessao, tabela.id, busca.coluna, busca.termo, busca.inteiro, filtros));
    } catch (falha) {
      setContagem(null);
      setErroFormula((falha as Error).message);
    }
  };

  const desfazer = () => executar("Desfazendo o último passo", async () => {
    aoMudar(await api.desfazer(sessao, tabela.id));
  });

  const passosDaSandbox = tabela.relatorio?.passos.slice(-tabela.manipulacao.length || undefined) ?? [];
  const opcoesColunas = colunas.map((c) => <option key={c.nome} value={c.nome}>{c.nome}</option>);

  return (
    <div className="planilha">
      <form className="barra-formula" onSubmit={verPrevia}>
        <span className="fx" aria-hidden="true">ƒx</span>
        <label className="sr" htmlFor="formula-nome">Nome da coluna</label>
        <input id="formula-nome" className="campo" placeholder="nova coluna" value={formula.nome}
               onChange={(e) => setFormula({ ...formula, nome: e.target.value })} />
        <span aria-hidden="true">=</span>
        <label className="sr" htmlFor="formula-texto">Fórmula</label>
        <input id="formula-texto" className="campo formula-texto" placeholder='SE(tenure < 12; "novo"; "antigo")'
               value={formula.texto} onChange={(e) => setFormula({ ...formula, texto: e.target.value })} />
        <label className="caixa" htmlFor="formula-substituir">
          <input id="formula-substituir" type="checkbox" checked={formula.substituir}
                 onChange={(e) => setFormula({ ...formula, substituir: e.target.checked })} /> substituir
        </label>
        <button type="submit" className="botao pequeno" disabled={!formula.nome || !formula.texto}>Prévia</button>
        <button type="button" className="botao pequeno principal" disabled={ocupado || !formula.nome || !formula.texto} onClick={criarColuna}>Criar coluna</button>
      </form>
      {erroFormula ? <p className="aviso-linha" role="alert">! {erroFormula}</p> : null}
      {previa ? (
        <div className="previa">
          <span className="texto-suave">{previa.tipo} · {fmtInteiro(previa.vazios)} vazios em {fmtInteiro(previa.linhas)} linhas</span>
          <span className="previa-valores">
            {previa.previa.map((p, i) => <span key={i}><b>#{p.linha}</b> {p.valor === null ? "vazio" : String(p.valor)}</span>)}
          </span>
        </div>
      ) : null}

      <div className="filtros-vista">
        {filtros.map((filtro, i) => (
          <span key={i} className="filtro-chip">
            {filtro.coluna} {NOMES_OPERADOR[filtro.operador]} {["vazio", "preenchido"].includes(filtro.operador) ? "" : filtro.valor}
            <button type="button" aria-label="Tirar filtro" onClick={() => setFiltros(filtros.filter((_, j) => j !== i))}>×</button>
          </span>
        ))}
        <form className="filtro-novo" onSubmit={adicionarFiltro}>
          <select className="campo" aria-label="Coluna do filtro" id="filtro-coluna" value={novoFiltro.coluna}
                  onChange={(e) => setNovoFiltro({ ...novoFiltro, coluna: e.target.value })}>
            <option value="">coluna…</option>
            {opcoesColunas}
          </select>
          <select className="campo" aria-label="Operador" id="filtro-operador" value={novoFiltro.operador}
                  onChange={(e) => setNovoFiltro({ ...novoFiltro, operador: e.target.value })}>
            {OPERADORES.map(([valor, nome]) => <option key={valor} value={valor}>{nome}</option>)}
          </select>
          {["vazio", "preenchido"].includes(novoFiltro.operador) ? null : (
            <input className="campo" aria-label="Valor do filtro" id="filtro-valor" value={novoFiltro.valor}
                   onChange={(e) => setNovoFiltro({ ...novoFiltro, valor: e.target.value })} placeholder="valor" />
          )}
          <button type="submit" className="botao pequeno" disabled={!novoFiltro.coluna}>+ filtro</button>
        </form>
        {filtros.length ? (
          <button type="button" className="botao pequeno" disabled={ocupado} onClick={aplicarFiltros}
                  title="Mantém só as linhas filtradas: vira um passo da receita e muda a análise">Aplicar à base</button>
        ) : null}
      </div>

      <div className="acoes-planilha">
        <select className="campo" aria-label="Coluna" id="acao-coluna" value={colunaAcao} onChange={(e) => setColunaAcao(e.target.value)}>
          <option value="">coluna…</option>
          {opcoesColunas}
        </select>
        <input className="campo" aria-label="Nome novo" id="acao-nome" placeholder="nome novo" value={nomeNovo} onChange={(e) => setNomeNovo(e.target.value)} />
        <button type="button" className="botao pequeno" disabled={ocupado || !colunaAcao || !nomeNovo}
                onClick={() => passo("Renomeando a coluna", { op: "renomear_coluna", coluna: colunaAcao, nome: nomeNovo }, () => { setNomeNovo(""); setColunaAcao(""); })}>
          Renomear
        </button>
        <button type="button" className="botao pequeno" disabled={ocupado || !colunaAcao}
                onClick={() => passo("Apagando a coluna", { op: "remover_colunas", colunas: [colunaAcao] }, () => setColunaAcao(""))}>
          Apagar coluna
        </button>
        <button type="button" className={`botao pequeno${agrupando ? " on" : ""}`} disabled={ocupado} aria-expanded={Boolean(agrupando)}
                onClick={() => setAgrupando(agrupando ? null : { ...AGRUPAMENTO_VAZIO })}>
          Agrupar…
        </button>
        <span className="separador" aria-hidden="true" />
        <button type="button" className="botao pequeno" disabled={ocupado || (todas ? !filtros.length : !marcadas.length)} onClick={apagarMarcadas}
                title={todas && !filtros.length ? "Sem filtro, isso apagaria a tabela inteira. Filtre antes, ou desmarque algumas linhas." : undefined}>
          {todas
            ? `Apagar as ${fmtInteiro(totais.filtradas)} linhas${filtros.length ? " do filtro" : ""}`
            : marcadas.length ? `Apagar ${fmtInteiro(marcadas.length)} ${marcadas.length === 1 ? "linha" : "linhas"}` : "Apagar linhas marcadas"}
        </button>
        <button type="button" className="botao pequeno" disabled={ocupado || !tabela.manipulacao.length} onClick={desfazer}>
          ↶ Desfazer
        </button>
      </div>

      {agrupando ? (
        <div className="painel-agrupar" role="group" aria-label="Agrupar linhas">
          <div className="agrupar-linha">
            <span className="rotulo-curto">Agrupar por</span>
            <div className="opcoes-agrupar">
              {colunas.map((coluna) => (
                <label key={coluna.nome} className={`chip-escolha${agrupando.colunas.includes(coluna.nome) ? " on" : ""}`}>
                  <input type="checkbox" checked={agrupando.colunas.includes(coluna.nome)}
                         onChange={(e) => setAgrupando({
                           ...agrupando,
                           colunas: e.target.checked ? [...agrupando.colunas, coluna.nome] : agrupando.colunas.filter((c) => c !== coluna.nome),
                         })} />
                  {coluna.nome}
                </label>
              ))}
            </div>
          </div>
          <div className="agrupar-linha">
            <label className="rotulo-curto" htmlFor="agrupar-contagem">Contagem em</label>
            <input id="agrupar-contagem" className="campo" value={agrupando.contagem}
                   onChange={(e) => setAgrupando({ ...agrupando, contagem: e.target.value })} />
            <span className="rotulo-curto">Também calcular</span>
            <select className="campo" aria-label="Cálculo" id="agrupar-funcao" value={novaConta.funcao} onChange={(e) => setNovaConta({ ...novaConta, funcao: e.target.value })}>
              {FUNCOES_AGRUPAR.map(([valor, nome]) => <option key={valor} value={valor}>{nome}</option>)}
            </select>
            <span>de</span>
            <select className="campo" aria-label="Coluna do cálculo" id="agrupar-coluna" value={novaConta.coluna} onChange={(e) => setNovaConta({ ...novaConta, coluna: e.target.value })}>
              <option value="">coluna numérica…</option>
              {colunas.filter((c) => NUMERICOS.test(c.tipo) && !agrupando.colunas.includes(c.nome)).map((c) => <option key={c.nome} value={c.nome}>{c.nome}</option>)}
            </select>
            <button type="button" className="botao pequeno" disabled={!novaConta.coluna}
                    onClick={() => { setAgrupando({ ...agrupando, agregacoes: [...agrupando.agregacoes, { ...novaConta }] }); setNovaConta({ ...novaConta, coluna: "" }); }}>
              + cálculo
            </button>
            {agrupando.agregacoes.map((conta, i) => (
              <span key={`${conta.funcao}-${conta.coluna}-${i}`} className="filtro-chip">
                {FUNCOES_AGRUPAR.find(([v]) => v === conta.funcao)?.[1]} de {conta.coluna}
                <button type="button" aria-label="Tirar cálculo" onClick={() => setAgrupando({ ...agrupando, agregacoes: agrupando.agregacoes.filter((_, j) => j !== i) })}>×</button>
              </span>
            ))}
          </div>
          <div className="agrupar-linha">
            <button type="button" className="botao pequeno principal" disabled={ocupado || !agrupando.colunas.length || !agrupando.contagem.trim()} onClick={agrupar}>
              Agrupar
            </button>
            <button type="button" className="botao pequeno" onClick={() => setAgrupando(null)}>Cancelar</button>
            <span className="texto-suave">
              {agrupando.colunas.length
                ? `Uma linha para cada combinação de ${agrupando.colunas.join(" + ")}, com a coluna ${agrupando.contagem || "quantidade"} dizendo quantas linhas foram juntadas. Vira um passo da receita: dá para desfazer.`
                : "Marque as colunas que definem um grupo."}
            </span>
          </div>
        </div>
      ) : null}

      <div className="grade-ag">
        <ContextoSelecao.Provider value={{ todas, algumas: marcadas.length > 0, total: totais.filtradas, alternar: () => marcarTodas(!todas) }}>
        <AgGridReact
          theme={TEMA}
          rowModelType="infinite"
          datasource={fonte}
          columnDefs={definicoes}
          cacheBlockSize={100}
          maxBlocksInCache={20}
          suppressFieldDotNotation
          getRowId={(p) => String(p.data.__linha)}
          rowSelection={{ mode: "multiRow", checkboxes: true, headerCheckbox: false, enableClickSelection: false }}
          singleClickEdit={false}
          stopEditingWhenCellsLoseFocus
          onGridReady={(evento) => { grade.current = evento.api; }}
          onCellValueChanged={editar}
          selectionColumnDef={{ headerComponent: CabecalhoSelecao, pinned: "left", width: 52, sortable: false, resizable: false }}
          onSelectionChanged={aoMudarSelecao}
          onModelUpdated={(evento) => {
            if (todasAgora.current) evento.api.forEachNode((no) => { if (no.data && !no.isSelected()) no.setSelected(true, false, "api"); });
          }}
          localeText={{ noRowsToShow: "Nenhuma linha com esses filtros.", loadingOoo: "carregando…" }}
        />
        </ContextoSelecao.Provider>
      </div>

      <div className="leitura">
        <span><b>Filtro de visão</b> {fmtInteiro(totais.filtradas)} de {fmtInteiro(totais.total)} linhas</span>
        <form className="contar" onSubmit={contar}>
          <b>Contar</b>
          <input className="campo" aria-label="Termo" id="contar-termo" placeholder="termo" value={busca.termo} onChange={(e) => setBusca({ ...busca, termo: e.target.value })} />
          <span>em</span>
          <select className="campo" aria-label="Coluna da contagem" id="contar-coluna" value={busca.coluna} onChange={(e) => setBusca({ ...busca, coluna: e.target.value })}>
            <option value="">coluna…</option>
            {opcoesColunas}
          </select>
          <label className="caixa" htmlFor="contar-exato">
            <input id="contar-exato" type="checkbox" checked={busca.inteiro} onChange={(e) => setBusca({ ...busca, inteiro: e.target.checked })} /> exato
          </label>
          <button type="submit" className="botao pequeno" disabled={!busca.coluna || !busca.termo}>→</button>
          {contagem ? <span className="resultado-contagem">{fmtInteiro(contagem.encontradas)} de {fmtInteiro(contagem.linhas)}</span> : null}
        </form>
        <span><b>Receita</b> {(tabela.receita?.length ?? 0) + tabela.manipulacao.length} passos · {tabela.manipulacao.length} da sandbox</span>
      </div>

      {passosDaSandbox.length && tabela.manipulacao.length ? (
        <details className="passos-sandbox">
          <summary>Passos da sandbox ({tabela.manipulacao.length})</summary>
          <ol>
            {passosDaSandbox.map((p) => (
              <li key={p.indice}>
                <span>{p.titulo}</span>
                <span className="texto-suave">
                  {p.linhas_depois !== p.linhas_antes ? `${fmtInteiro(p.linhas_antes)} → ${fmtInteiro(p.linhas_depois)} linhas` : ""}
                  {p.perdidos ? ` · ${fmtInteiro(p.perdidos)} viraram vazio` : ""}
                </span>
              </li>
            ))}
          </ol>
        </details>
      ) : null}
    </div>
  );
}
