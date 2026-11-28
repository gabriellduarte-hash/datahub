"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { AgGridReact } from "ag-grid-react";
import {
  AllCommunityModule, ModuleRegistry, themeQuartz,
  type CellValueChangedEvent, type ColDef, type GridApi, type IDatasource, type SelectionChangedEvent, type ValueFormatterParams,
} from "ag-grid-community";
import * as api from "@/lib/api";
import { fmtInteiro } from "@/lib/formato";
import type { Area, Contagem, Filtro, Passo, Tabela } from "@/lib/tipos";
import { CabecalhoColuna, ContextoColunas, NovaColuna, NUMERICOS, nomeLivre, type AcoesColuna, type Lado } from "./Colunas";

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
  const [erroContagem, setErroContagem] = useState<string | null>(null);
  const [novoFiltro, setNovoFiltro] = useState<Filtro>({ coluna: "", operador: "igual", valor: "" });
  const [novaColuna, setNovaColuna] = useState<{ apos: string | null } | null>(null);
  const [editarAoMontar, setEditarAoMontar] = useState<string | null>(null);
  const revelar = useRef<string | null>(null); // coluna recém-criada: a grade rola até ela
  const ultimoArraste = useRef(0);
  const arrastada = useRef<string | null>(null);
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

  /* A grade só desenha as colunas à vista: a coluna nova pode nascer fora da tela. */
  useEffect(() => {
    const nome = revelar.current;
    if (!nome || !colunas.some((c) => c.nome === nome)) return;
    revelar.current = null;
    requestAnimationFrame(() => grade.current?.ensureColumnVisible(nome, "auto"));
  }, [colunas]);

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
    { field: "__linha", headerName: "#", width: 84, pinned: "left", lockPosition: "left", suppressMovable: true, editable: false, sortable: false, cellClass: "celula-numero", headerTooltip: "Número da linha no arquivo" },
    ...colunas.map((coluna): ColDef => ({
      field: coluna.nome,
      headerName: coluna.nome,
      headerTooltip: `${coluna.nome} · ${coluna.tipo}`,
      editable: true,
      sortable: true,
      headerComponent: CabecalhoColuna,
      cellDataType: false,
      minWidth: 120,
      flex: 1,
      valueFormatter: formatar,
      cellClassRules: { "celula-vazia": (p) => p.value === null || p.value === undefined },
      type: NUMERICOS.test(coluna.tipo) ? "rightAligned" : undefined,
    })),
  ], [colunas]);

  const passo = useCallback((descricao: string, novo: Passo, depois?: () => void, aoFalhar?: () => void) => executar(descricao, async () => {
    try {
      aoMudar(await api.acrescentarPasso(sessao, tabela.id, novo));
      depois?.();
    } catch (falha) {
      grade.current?.refreshInfiniteCache();
      aoFalhar?.();
      throw falha;
    }
  }), [executar, aoMudar, sessao, tabela.id]);

  /* A grade volta à ordem da tabela: depois de um arrasto recusado, ou feito com outro passo em andamento. */
  const restaurarOrdem = useCallback(() => {
    const api = grade.current;
    if (!api) return;
    const dados = new Set(colunas.map((c) => c.nome));
    const fixas = api.getColumnState().filter((e) => !dados.has(e.colId)).map((e) => ({ colId: e.colId }));
    api.applyColumnState({ state: [...fixas, ...colunas.map((c) => ({ colId: c.nome }))], applyOrder: true });
  }, [colunas]);

  /* Arrastar um cabeçalho muda a coluna de lugar só na tela; ao soltar, a posição nova vira passo da receita,
     dita como "depois de" (ou "antes de") outra coluna. */
  const aoSoltarColuna = () => {
    ultimoArraste.current = Date.now();
    const api = grade.current;
    if (!api) return;
    const nomes = colunas.map((c) => c.nome);
    const dados = new Set(nomes);
    const nova = api.getAllDisplayedColumns().map((c) => c.getColId()).filter((id) => dados.has(id));
    const juntar = (lista: string[]) => lista.join("\u0000");
    if (juntar(nova) === juntar(nomes)) return; // só mudou a largura
    if (ocupado) {
      restaurarOrdem();
      return;
    }
    const sem = (lista: string[], coluna: string) => juntar(lista.filter((x) => x !== coluna));
    const candidatas = nova.filter((coluna) => sem(nova, coluna) === sem(nomes, coluna));
    const coluna = arrastada.current && candidatas.includes(arrastada.current) ? arrastada.current : candidatas[0];
    if (!coluna) {
      restaurarOrdem();
      return;
    }
    const i = nova.indexOf(coluna);
    const lugar = i > 0 ? { apos: nova[i - 1] } : { antes: nova[1] };
    passo(`Movendo ${coluna}`, { op: "mover_coluna", coluna, ...lugar }, undefined, restaurarOrdem);
  };

  const editar = (evento: CellValueChangedEvent) => {
    if (evento.oldValue === evento.newValue) return;
    const valor = evento.newValue === null || evento.newValue === undefined ? "" : String(evento.newValue);
    passo("Gravando a edição", {
      op: "editar_celulas",
      edicoes: [{ linha: evento.data.__linha, coluna: evento.colDef.field, valor }],
    });
  };

  const verPrevia = useCallback((nome: string, formula: string, substituir: boolean) =>
    api.previaFormula(sessao, tabela.id, nome, formula, substituir), [sessao, tabela.id]);

  /* O que o cabeçalho de cada coluna pode fazer. */
  const acoesColuna = useMemo<AcoesColuna>(() => ({
    ocupado,
    renomear: (coluna, nome) => passo(`Renomeando ${coluna} para ${nome}`, { op: "renomear_coluna", coluna, nome }),
    apagar: (coluna) => passo(`Apagando a coluna ${coluna}`, { op: "remover_colunas", colunas: [coluna] }),
    inserirVazia: (coluna: string, lado: Lado) => {
      const nome = nomeLivre("coluna_nova", colunas);
      passo("Inserindo uma coluna", { op: "formula", nome, vazia: true, [lado]: coluna }, () => {
        revelar.current = nome;
        setEditarAoMontar(nome);
      });
    },
    calcular: (coluna) => setNovaColuna({ apos: coluna }),
    mover: (coluna, sentido) => {
      const i = colunas.findIndex((c) => c.nome === coluna);
      const alvo = colunas[sentido === "esquerda" ? i - 1 : i + 1]?.nome;
      if (i < 0 || !alvo) return;
      passo(`Movendo ${coluna}`, { op: "mover_coluna", coluna, ...(sentido === "esquerda" ? { antes: alvo } : { apos: alvo }) },
            () => { revelar.current = coluna; });
    },
    vizinhas: (coluna) => {
      const i = colunas.findIndex((c) => c.nome === coluna);
      return { esquerda: i > 0 ? colunas[i - 1].nome : null, direita: i >= 0 && i < colunas.length - 1 ? colunas[i + 1].nome : null };
    },
    acabouDeArrastar: () => Date.now() - ultimoArraste.current < 400,
    editarAoMontar,
    jaEditou: () => setEditarAoMontar(null),
  }), [ocupado, passo, colunas, editarAoMontar]);

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
      setErroContagem(null);
    } catch (falha) {
      setContagem(null);
      setErroContagem((falha as Error).message);
    }
  };

  const desfazer = () => executar("Desfazendo o último passo", async () => {
    aoMudar(await api.desfazer(sessao, tabela.id));
  });

  const passosDaSandbox = tabela.relatorio?.passos.slice(-tabela.manipulacao.length || undefined) ?? [];
  const opcoesColunas = colunas.map((c) => <option key={c.nome} value={c.nome}>{c.nome}</option>);

  return (
    <div className="planilha">
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
        <button type="button" className={`botao pequeno${novaColuna ? " on" : ""}`} disabled={ocupado} aria-expanded={Boolean(novaColuna)}
                onClick={() => { setNovaColuna(novaColuna ? null : { apos: null }); setAgrupando(null); }}>
          + Coluna
        </button>
        <button type="button" className={`botao pequeno${agrupando ? " on" : ""}`} disabled={ocupado} aria-expanded={Boolean(agrupando)}
                onClick={() => { setAgrupando(agrupando ? null : { ...AGRUPAMENTO_VAZIO }); setNovaColuna(null); }}>
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
        <span className="dica-colunas texto-suave">Duplo clique no nome renomeia · arraste o cabeçalho para mudar de lugar · ⋮ mais opções</span>
      </div>

      {novaColuna ? (
        <NovaColuna key={novaColuna.apos ?? ""} colunas={colunas} apos={novaColuna.apos} ocupado={ocupado} verPrevia={verPrevia}
                    criar={(descricao, novo, nome) => passo(descricao, novo, () => { revelar.current = nome; setNovaColuna(null); })}
                    fechar={() => setNovaColuna(null)} />
      ) : null}

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
        <ContextoColunas.Provider value={acoesColuna}>
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
          onColumnMoved={(evento) => { if (evento.column) arrastada.current = evento.column.getColId(); }}
          onDragStopped={aoSoltarColuna}
          suppressDragLeaveHidesColumns
          onCellValueChanged={editar}
          selectionColumnDef={{ headerComponent: CabecalhoSelecao, pinned: "left", width: 52, sortable: false, resizable: false }}
          onSelectionChanged={aoMudarSelecao}
          onModelUpdated={(evento) => {
            if (todasAgora.current) evento.api.forEachNode((no) => { if (no.data && !no.isSelected()) no.setSelected(true, false, "api"); });
          }}
          localeText={{ noRowsToShow: "Nenhuma linha com esses filtros.", loadingOoo: "carregando…" }}
        />
        </ContextoSelecao.Provider>
        </ContextoColunas.Provider>
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
          {erroContagem ? <span className="resultado-contagem" role="alert">! {erroContagem}</span> : null}
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
