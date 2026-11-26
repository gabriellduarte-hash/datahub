"use client";

import { useState, type FormEvent } from "react";
import { GraficoEcharts } from "@/components/GraficoEcharts";
import * as api from "@/lib/api";
import type { Analise, Area, Grafico, GraficoLivre, PedidoGrafico, PerfilColuna } from "@/lib/tipos";

/* Cada tipo diz o que mostra e quais campos pede. Os campos só oferecem colunas
   que fazem sentido para ele: grupo é categoria, data é data, eixo é número. */
type Tipo = PedidoGrafico["tipo"];
const TIPOS: { tipo: Tipo; nome: string; ajuda: string }[] = [
  { tipo: "barras", nome: "Barras", ajuda: "Compara um valor entre grupos" },
  { tipo: "rosca", nome: "Rosca", ajuda: "Quanto cada grupo representa do total" },
  { tipo: "linha", nome: "Linha", ajuda: "Como um valor muda no tempo" },
  { tipo: "dispersao", nome: "Dispersão", ajuda: "Se dois números andam juntos" },
  { tipo: "histograma", nome: "Histograma", ajuda: "Como os valores de um número se espalham" },
];
const CALCULOS: [string, string][] = [
  ["soma", "soma"], ["media", "média"], ["mediana", "mediana"], ["minimo", "mínimo"], ["maximo", "máximo"], ["taxa", "% de sim"],
];
const GRAOS: [string, string][] = [["", "automático"], ["dia", "dia"], ["semana", "semana"], ["mes", "mês"], ["trimestre", "trimestre"], ["ano", "ano"]];
const NUMERICOS = new Set(["inteiro", "decimal", "moeda", "percentual"]);
const VAZIO: PedidoGrafico = { tipo: "barras", x: null, y: null, agregacao: "contagem", grao: null };

function colunasPara(colunas: PerfilColuna[]) {
  const nomes = (filtro: (c: PerfilColuna) => boolean) => colunas.filter((c) => c.papel !== "ignorar" && filtro(c)).map((c) => c.nome);
  return {
    grupos: nomes((c) => ["categoria", "indicador", "texto"].includes(c.papel)),
    datas: nomes((c) => c.tipo === "data" || c.papel === "data"),
    numeros: nomes((c) => NUMERICOS.has(c.tipo) && c.papel !== "id"),
    indicadores: new Set(nomes((c) => c.papel === "indicador")),
  };
}

/* Deixa o pedido coerente com o tipo: troca de tipo não pode levar uma coluna que ele não aceita. */
function ajustar(pedido: PedidoGrafico, opcoes: ReturnType<typeof colunasPara>): PedidoGrafico {
  const p = { ...pedido };
  if (p.tipo === "barras" || p.tipo === "rosca") {
    if (!p.x || !opcoes.grupos.includes(p.x)) p.x = opcoes.grupos[0] ?? null;
  } else if (p.tipo === "linha") {
    if (!p.x || !opcoes.datas.includes(p.x)) p.x = opcoes.datas[0] ?? null;
  } else if (p.tipo === "dispersao") {
    if (!p.x || !opcoes.numeros.includes(p.x)) p.x = opcoes.numeros[0] ?? null;
    if (!p.y || !opcoes.numeros.includes(p.y) || p.y === p.x) p.y = opcoes.numeros.find((n) => n !== p.x) ?? null;
  } else {
    p.x = null;
    if (!p.y || !opcoes.numeros.includes(p.y)) p.y = opcoes.numeros[0] ?? null;
  }
  if (p.tipo === "barras" || p.tipo === "rosca" || p.tipo === "linha") {
    const valores = [...opcoes.numeros, ...opcoes.indicadores];
    if (p.y && !valores.includes(p.y)) p.y = null;
    if (!p.y) p.agregacao = "contagem";
    else if (p.agregacao === "contagem" || !p.agregacao) p.agregacao = opcoes.indicadores.has(p.y) ? "taxa" : "soma";
    if (p.tipo === "rosca" && p.y && p.agregacao !== "soma") p.agregacao = "soma";
    if (p.agregacao === "taxa" && !opcoes.indicadores.has(p.y ?? "")) p.agregacao = "soma";
  } else {
    p.agregacao = null;
  }
  if (p.tipo !== "linha") p.grao = null;
  return p;
}

type PropsConstrutor = {
  analise: Analise;
  inicial: PedidoGrafico;
  editando: boolean;
  salvar: (pedido: PedidoGrafico) => Promise<void>;
  cancelar: () => void;
};

function Construtor({ analise, inicial, editando, salvar, cancelar }: PropsConstrutor) {
  const opcoes = colunasPara(analise.perfil_limpo?.colunas ?? []);
  const [pedido, setPedido] = useState(() => ajustar(inicial, opcoes));
  const [salvando, setSalvando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const mudar = (campos: Partial<PedidoGrafico>) => setPedido(ajustar({ ...pedido, ...campos }, opcoes));

  const enviar = async (evento: FormEvent) => {
    evento.preventDefault();
    setSalvando(true);
    setErro(null);
    try {
      await salvar(pedido);
    } catch (falha) {
      setErro((falha as Error).message);
    } finally {
      setSalvando(false);
    }
  };

  const valor = (rotulo: string) => (
    <label className="rotulo" htmlFor="livre-y">
      {rotulo}
      <select className="campo" id="livre-y" value={pedido.y ?? ""} onChange={(e) => mudar({ y: e.target.value || null })}>
        <option value="">linhas (contar)</option>
        {[...opcoes.numeros, ...opcoes.indicadores].map((nome) => <option key={nome} value={nome}>{nome}</option>)}
      </select>
    </label>
  );
  const calculo = (lista: [string, string][]) => (
    <label className="rotulo" htmlFor="livre-calculo">
      Cálculo
      <select className="campo" id="livre-calculo" value={pedido.agregacao ?? "contagem"} disabled={!pedido.y}
              onChange={(e) => mudar({ agregacao: e.target.value })}>
        {!pedido.y ? <option value="contagem">contagem</option> : null}
        {lista.filter(([v]) => v !== "taxa" || opcoes.indicadores.has(pedido.y ?? "")).map(([v, nome]) => <option key={v} value={v}>{nome}</option>)}
      </select>
    </label>
  );
  const coluna = (id: string, rotulo: string, lista: string[], atual: string | null, campo: "x" | "y") => (
    <label className="rotulo" htmlFor={id}>
      {rotulo}
      <select className="campo" id={id} value={atual ?? ""} onChange={(e) => mudar({ [campo]: e.target.value || null })}>
        {!lista.length ? <option value="">nenhuma coluna serve</option> : null}
        {lista.map((nome) => <option key={nome} value={nome}>{nome}</option>)}
      </select>
    </label>
  );

  const pronto = pedido.tipo === "histograma" ? Boolean(pedido.y) : pedido.tipo === "dispersao" ? Boolean(pedido.x && pedido.y) : Boolean(pedido.x);

  return (
    <form className="modulo construtor" onSubmit={enviar} aria-label="Montar um gráfico">
      <div className="modulo-topo">
        <span className="tag destaque">{editando ? "Editando gráfico" : "Novo gráfico"}</span>
        <span className="modulo-colunas">{TIPOS.find((t) => t.tipo === pedido.tipo)?.ajuda}</span>
      </div>
      <div className="segmentado tipos-grafico" role="radiogroup" aria-label="Tipo de gráfico">
        {TIPOS.map(({ tipo, nome }) => (
          <button key={tipo} type="button" role="radio" aria-checked={pedido.tipo === tipo}
                  className={pedido.tipo === tipo ? "on" : ""} onClick={() => mudar({ tipo })}>{nome}</button>
        ))}
      </div>
      <div className="grafico-editor">
        {pedido.tipo === "barras" ? <>{coluna("livre-x", "Grupo", opcoes.grupos, pedido.x, "x")}{valor("Valor")}{calculo(CALCULOS)}</> : null}
        {pedido.tipo === "rosca" ? <>{coluna("livre-x", "Grupo", opcoes.grupos, pedido.x, "x")}{valor("Valor")}{calculo([["soma", "soma"]])}</> : null}
        {pedido.tipo === "linha" ? (
          <>
            {coluna("livre-x", "Data", opcoes.datas, pedido.x, "x")}
            {valor("Valor")}
            {calculo(CALCULOS)}
            <label className="rotulo" htmlFor="livre-grao">
              Período
              <select className="campo" id="livre-grao" value={pedido.grao ?? ""} onChange={(e) => mudar({ grao: e.target.value || null })}>
                {GRAOS.map(([v, nome]) => <option key={v} value={v}>{nome}</option>)}
              </select>
            </label>
          </>
        ) : null}
        {pedido.tipo === "dispersao" ? <>{coluna("livre-x", "Eixo X", opcoes.numeros, pedido.x, "x")}{coluna("livre-y2", "Eixo Y", opcoes.numeros.filter((n) => n !== pedido.x), pedido.y, "y")}</> : null}
        {pedido.tipo === "histograma" ? coluna("livre-y3", "Coluna", opcoes.numeros, pedido.y, "y") : null}
      </div>
      {erro ? <p className="aviso-linha" role="alert">! {erro}</p> : null}
      <div className="construtor-acoes">
        <button type="submit" className="botao principal" disabled={salvando || !pronto}>{editando ? "Salvar gráfico" : "Adicionar ao painel"}</button>
        <button type="button" className="botao" onClick={cancelar}>Cancelar</button>
      </div>
    </form>
  );
}

function ModuloLivre({ livre, tema, editar, remover }: { livre: GraficoLivre; tema: string; editar: () => void; remover: () => void }) {
  const grafico = livre.grafico as Grafico | null;
  return (
    <article className="modulo livre">
      <div className="modulo-topo">
        <span className="tag">{TIPOS.find((t) => t.tipo === livre.pedido.tipo)?.nome ?? livre.pedido.tipo}</span>
        <span className="modulo-colunas">{[livre.pedido.x, livre.pedido.y].filter(Boolean).join(" × ")}</span>
        <span className="modulo-botoes">
          <button type="button" className="botao pequeno" onClick={editar}>Editar</button>
          <button type="button" className="botao pequeno" onClick={remover} aria-label="Tirar do painel">×</button>
        </span>
      </div>
      {grafico ? (
        <figure className="modulo-grafico">
          <figcaption>{grafico.titulo}</figcaption>
          <GraficoEcharts grafico={grafico} tema={tema} compacto />
          {grafico.aviso ? <p className="grafico-nota">! {grafico.aviso}</p> : null}
        </figure>
      ) : (
        <p className="aviso-linha">! {livre.erro ?? "Este gráfico não pôde ser calculado com a base atual."}</p>
      )}
    </article>
  );
}

type Props = { area: Area; analise: Analise; tema: string; ocupado: boolean; aoMudar: (area: Area) => void };

/* Gráficos que o usuário monta. Ficam guardados na sessão e se refazem quando a base muda. */
export function GraficosLivres({ area, analise, tema, ocupado, aoMudar }: Props) {
  const [aberto, setAberto] = useState<{ id: string | null; pedido: PedidoGrafico } | null>(null);
  const livres = analise.livres ?? [];

  const salvar = async (pedido: PedidoGrafico) => {
    const nova = aberto?.id
      ? await api.trocarGraficoLivre(area.sessao, aberto.id, pedido)
      : await api.criarGraficoLivre(area.sessao, pedido);
    aoMudar(nova);
    setAberto(null);
  };
  const remover = async (id: string) => aoMudar(await api.removerGraficoLivre(area.sessao, id));

  return (
    <>
      <div className="cantos">
        <span>Seus gráficos</span>
        <span>{livres.length ? `${livres.length} no painel` : "monte os seus, eles ficam na sessão"}</span>
      </div>
      <div className="modulos livres">
        {livres.map((livre) => (
          aberto?.id === livre.id ? (
            <Construtor key={livre.id} analise={analise} inicial={aberto.pedido} editando salvar={salvar} cancelar={() => setAberto(null)} />
          ) : (
            <ModuloLivre key={livre.id} livre={livre} tema={tema}
                         editar={() => setAberto({ id: livre.id, pedido: livre.pedido })} remover={() => remover(livre.id)} />
          )
        ))}
        {aberto && !aberto.id ? (
          <Construtor analise={analise} inicial={aberto.pedido} editando={false} salvar={salvar} cancelar={() => setAberto(null)} />
        ) : (
          <button type="button" className="modulo adicionar-grafico" disabled={ocupado} onClick={() => setAberto({ id: null, pedido: VAZIO })}>
            <span className="adicionar-mais" aria-hidden="true">+</span>
            <span>Adicionar gráfico</span>
            <span className="texto-suave">Barras, rosca, linha, dispersão ou histograma, com as colunas que você escolher</span>
          </button>
        )}
      </div>
    </>
  );
}
