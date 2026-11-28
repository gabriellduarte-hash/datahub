"use client";

/* Mexer nas colunas como numa planilha: duplo clique no nome renomeia, arrastar
   o cabeçalho muda a ordem, o ⋮ abre as outras opções e o painel "Nova coluna"
   cria uma coluna sem precisar escrever fórmula. Tudo vira passo da receita,
   então dá para desfazer. */

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { CustomHeaderProps } from "ag-grid-react";
import { fmtInteiro } from "@/lib/formato";
import type { Passo, PreviaFormula } from "@/lib/tipos";

export const NUMERICOS = /INT|DOUBLE|FLOAT|DECIMAL/;

type Coluna = { nome: string; tipo: string };
export type Lado = "apos" | "antes";

export type AcoesColuna = {
  ocupado: boolean;
  renomear: (coluna: string, nome: string) => void;
  apagar: (coluna: string) => void;
  inserirVazia: (coluna: string, lado: Lado) => void;
  calcular: (coluna: string) => void;
  mover: (coluna: string, sentido: "esquerda" | "direita") => void;
  vizinhas: (coluna: string) => { esquerda: string | null; direita: string | null };
  /* Soltar uma coluna arrastada pode virar um clique no cabeçalho: esse clique não ordena. */
  acabouDeArrastar: () => boolean;
  /* A coluna recém-criada abre com o nome já em edição, como no Sheets. */
  editarAoMontar: string | null;
  jaEditou: () => void;
};

export const ContextoColunas = createContext<AcoesColuna>({
  ocupado: true, renomear: () => {}, apagar: () => {}, inserirVazia: () => {}, calcular: () => {},
  mover: () => {}, vizinhas: () => ({ esquerda: null, direita: null }), acabouDeArrastar: () => false,
  editarAoMontar: null, jaEditou: () => {},
});

/* Um nome que ainda não existe: coluna_nova, coluna_nova_2, ... */
export function nomeLivre(base: string, colunas: Coluna[]) {
  const usados = new Set(colunas.map((c) => c.nome));
  if (!usados.has(base)) return base;
  let n = 2;
  while (usados.has(`${base}_${n}`)) n++;
  return `${base}_${n}`;
}

/* ----- cabeçalho ----- */

const ESPERA_DO_CLIQUE = 230; // o primeiro clique de um duplo clique não deve ordenar

export function CabecalhoColuna(props: CustomHeaderProps) {
  const acoes = useContext(ContextoColunas);
  const { column, enableSorting, progressSort, setSort, displayName } = props;
  const nome = column.getColId();
  const [editando, setEditando] = useState(false);
  const [ordem, setOrdem] = useState(column.getSort());
  const [menu, setMenu] = useState<DOMRect | null>(null);
  const campo = useRef<HTMLInputElement>(null);
  const botaoMenu = useRef<HTMLButtonElement>(null);
  const relogio = useRef<number | undefined>(undefined);
  const fechado = useRef(false);
  const { editarAoMontar, jaEditou } = acoes;

  useEffect(() => {
    const atualizar = () => setOrdem(column.getSort());
    column.addEventListener("sortChanged", atualizar);
    return () => column.removeEventListener("sortChanged", atualizar);
  }, [column]);

  useEffect(() => () => window.clearTimeout(relogio.current), []);

  const comecar = useCallback(() => {
    window.clearTimeout(relogio.current);
    fechado.current = false;
    setEditando(true);
  }, []);

  useEffect(() => {
    if (editarAoMontar !== nome) return;
    jaEditou();
    comecar();
  }, [editarAoMontar, nome, jaEditou, comecar]);

  const confirmar = useCallback(() => {
    if (fechado.current) return;
    fechado.current = true;
    const novo = (campo.current?.value ?? "").trim();
    setEditando(false);
    if (novo && novo !== nome) acoes.renomear(nome, novo);
  }, [acoes, nome]);

  const cancelar = useCallback(() => {
    fechado.current = true;
    setEditando(false);
  }, []);

  /* A grade escuta teclado e mouse no cabeçalho (navegar, arrastar a coluna).
     Enquanto o nome está em edição, esses eventos são só do campo. */
  useEffect(() => {
    const caixa = campo.current;
    if (!editando || !caixa) return;
    caixa.focus();
    caixa.select();
    const parar = (evento: Event) => evento.stopPropagation();
    const tecla = (evento: KeyboardEvent) => {
      evento.stopPropagation();
      if (evento.key === "Enter") { evento.preventDefault(); confirmar(); }
      if (evento.key === "Escape") { evento.preventDefault(); cancelar(); }
    };
    const eventos = ["mousedown", "pointerdown", "touchstart", "click", "dblclick"];
    caixa.addEventListener("keydown", tecla);
    eventos.forEach((tipo) => caixa.addEventListener(tipo, parar));
    return () => {
      caixa.removeEventListener("keydown", tecla);
      eventos.forEach((tipo) => caixa.removeEventListener(tipo, parar));
    };
  }, [editando, confirmar, cancelar]);

  const clicar = (evento: React.MouseEvent) => {
    if (!enableSorting || acoes.acabouDeArrastar()) return;
    const varias = evento.shiftKey;
    window.clearTimeout(relogio.current);
    relogio.current = window.setTimeout(() => progressSort(varias), ESPERA_DO_CLIQUE);
  };

  const rotuloOrdem = ordem === "asc" ? "em ordem crescente" : ordem === "desc" ? "em ordem decrescente" : "";

  return (
    <div className={`cabecalho-coluna${editando ? " editando" : ""}`}>
      {editando ? (
        <input ref={campo} className="cabecalho-campo" defaultValue={nome} onBlur={confirmar}
               aria-label={`Novo nome da coluna ${nome}. Enter grava, Esc cancela`} spellCheck={false} />
      ) : (
        <button type="button" className="cabecalho-nome" onClick={clicar} onDoubleClick={comecar}
                title={`${nome}\nClique para ordenar · duplo clique para renomear · arraste para mudar de lugar`}
                aria-label={`${nome}${rotuloOrdem ? `, ${rotuloOrdem}` : ""}. Clique para ordenar, duplo clique para renomear`}>
          <span className="cabecalho-texto">{displayName}</span>
          {ordem ? <span className="cabecalho-ordem" aria-hidden="true">{ordem === "asc" ? "↑" : "↓"}</span> : null}
        </button>
      )}
      <button ref={botaoMenu} type="button" className="cabecalho-menu" aria-haspopup="menu" aria-expanded={Boolean(menu)}
              aria-label={`Opções da coluna ${nome}`} title="Opções da coluna"
              onClick={() => setMenu(menu ? null : botaoMenu.current?.getBoundingClientRect() ?? null)}>
        ⋮
      </button>
      {menu ? createPortal(
        <MenuColuna nome={nome} posicao={menu} ordem={ordem} podeOrdenar={Boolean(enableSorting)}
                    fechar={() => { setMenu(null); botaoMenu.current?.focus(); }}
                    renomear={comecar} ordenar={(sentido) => setSort(sentido)} />,
        document.body,
      ) : null}
    </div>
  );
}

type PropsMenu = {
  nome: string;
  posicao: DOMRect;
  ordem: "asc" | "desc" | null | undefined;
  podeOrdenar: boolean;
  fechar: () => void;
  renomear: () => void;
  ordenar: (sentido: "asc" | "desc" | null) => void;
};

function MenuColuna({ nome, posicao, ordem, podeOrdenar, fechar, renomear, ordenar }: PropsMenu) {
  const acoes = useContext(ContextoColunas);
  const caixa = useRef<HTMLDivElement>(null);
  const largura = 236;

  useEffect(() => {
    caixa.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    const fora = (evento: MouseEvent) => { if (!caixa.current?.contains(evento.target as Node)) fechar(); };
    const tecla = (evento: KeyboardEvent) => {
      if (evento.key === "Escape") fechar();
      if (evento.key === "ArrowDown" || evento.key === "ArrowUp") {
        evento.preventDefault();
        const itens = [...(caixa.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
        const atual = itens.indexOf(document.activeElement as HTMLButtonElement);
        const proximo = evento.key === "ArrowDown" ? (atual + 1) % itens.length : (atual - 1 + itens.length) % itens.length;
        itens[proximo]?.focus();
      }
    };
    const rolou = (evento: Event) => { if (!caixa.current?.contains(evento.target as Node)) fechar(); };
    document.addEventListener("mousedown", fora);
    document.addEventListener("keydown", tecla);
    window.addEventListener("scroll", rolou, true);
    window.addEventListener("resize", fechar);
    return () => {
      document.removeEventListener("mousedown", fora);
      document.removeEventListener("keydown", tecla);
      window.removeEventListener("scroll", rolou, true);
      window.removeEventListener("resize", fechar);
    };
  }, [fechar]);

  const fazer = (acao: () => void) => () => { fechar(); acao(); };
  const { esquerda, direita } = acoes.vizinhas(nome);
  const xDoMenu = Math.max(8, Math.min(posicao.right - largura, window.innerWidth - largura - 8));

  return (
    <div ref={caixa} className="menu-coluna" role="menu" aria-label={`Coluna ${nome}`}
         style={{ top: posicao.bottom + 4, left: xDoMenu, width: largura }}>
      <p className="menu-coluna-titulo">{nome}</p>
      <button type="button" role="menuitem" disabled={acoes.ocupado} onClick={fazer(renomear)}>
        Renomear <kbd>duplo clique</kbd>
      </button>
      {podeOrdenar ? (
        <>
          <span className="menu-coluna-linha" role="separator" />
          <button type="button" role="menuitem" onClick={fazer(() => ordenar("asc"))}>Ordenar A → Z, 0 → 9</button>
          <button type="button" role="menuitem" onClick={fazer(() => ordenar("desc"))}>Ordenar Z → A, 9 → 0</button>
          {ordem ? <button type="button" role="menuitem" onClick={fazer(() => ordenar(null))}>Tirar a ordenação</button> : null}
        </>
      ) : null}
      <span className="menu-coluna-linha" role="separator" />
      <button type="button" role="menuitem" disabled={acoes.ocupado || !esquerda} onClick={fazer(() => acoes.mover(nome, "esquerda"))}>
        Mover para a esquerda
      </button>
      <button type="button" role="menuitem" disabled={acoes.ocupado || !direita} onClick={fazer(() => acoes.mover(nome, "direita"))}>
        Mover para a direita
      </button>
      <span className="menu-coluna-linha" role="separator" />
      <button type="button" role="menuitem" disabled={acoes.ocupado} onClick={fazer(() => acoes.inserirVazia(nome, "antes"))}>
        Inserir coluna à esquerda
      </button>
      <button type="button" role="menuitem" disabled={acoes.ocupado} onClick={fazer(() => acoes.inserirVazia(nome, "apos"))}>
        Inserir coluna à direita
      </button>
      <button type="button" role="menuitem" disabled={acoes.ocupado} onClick={fazer(() => acoes.calcular(nome))}>
        Coluna calculada à direita…
      </button>
      <span className="menu-coluna-linha" role="separator" />
      <button type="button" role="menuitem" className="perigo" disabled={acoes.ocupado} onClick={fazer(() => acoes.apagar(nome))}>
        Apagar coluna
      </button>
    </div>
  );
}

/* ----- nova coluna sem escrever fórmula ----- */

type Modo = "vazia" | "conta" | "juntar" | "condicao" | "formula";
const MODOS: [Modo, string, string][] = [
  ["vazia", "Em branco", "Para digitar os valores à mão"],
  ["conta", "Conta", "Somar, subtrair, multiplicar ou dividir colunas"],
  ["juntar", "Juntar textos", "Colar duas colunas numa só"],
  ["condicao", "Se… então", "Um valor quando a condição vale, outro quando não"],
  ["formula", "Fórmula", "Escrever a fórmula, como no Excel"],
];
const CONTAS: [string, string, string][] = [["+", "+", "mais"], ["-", "−", "menos"], ["*", "×", "vezes"], ["/", "÷", "por"]];
const CONDICOES: [string, string][] = [
  ["=", "é igual a"], ["<>", "é diferente de"], [">", "é maior que"], ["<", "é menor que"],
  [">=", "é maior ou igual a"], ["<=", "é menor ou igual a"], ["contem", "contém"], ["vazio", "está vazia"],
];
const NUMERO = /^-?\d+([.,]\d+)?$/;
const UM_NUMERO = "__um_numero__";

const coluna = (nome: string) => `[${nome}]`;
const texto = (valor: string) => `"${valor.replace(/"/g, '""')}"`;
const valorOuTexto = (valor: string, numerico: boolean) => (numerico && NUMERO.test(valor.trim()) ? valor.trim() : texto(valor));
const semSimbolos = (valor: string) => valor.replace(/[^\p{L}\p{N}]+/gu, "_").replace(/^_|_$/g, "");

type PropsNova = {
  colunas: Coluna[];
  apos: string | null;
  ocupado: boolean;
  verPrevia: (nome: string, formula: string, substituir: boolean) => Promise<PreviaFormula>;
  criar: (descricao: string, passo: Passo, nome: string) => void;
  fechar: () => void;
};

export function NovaColuna({ colunas, apos: aposInicial, ocupado, verPrevia, criar, fechar }: PropsNova) {
  const [modo, setModo] = useState<Modo>(aposInicial ? "conta" : "vazia");
  const [nome, setNome] = useState("");
  const [apos, setApos] = useState(aposInicial ?? "");
  const [substituir, setSubstituir] = useState(false);
  const primeiraNumerica = colunas.find((c) => NUMERICOS.test(c.tipo))?.nome ?? "";
  const [conta, setConta] = useState({ a: aposInicial ?? primeiraNumerica, op: "*", b: "", numero: "" });
  const [juntar, setJuntar] = useState({ a: "", separador: " ", b: "" });
  const [condicao, setCondicao] = useState({ coluna: aposInicial ?? "", operador: "=", valor: "", entao: "", senao: "" });
  const [livre, setLivre] = useState("");
  const [previa, setPrevia] = useState<PreviaFormula | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const campoNome = useRef<HTMLInputElement>(null);

  useEffect(() => { campoNome.current?.focus(); }, []);

  const tipoDe = (nomeColuna: string) => colunas.find((c) => c.nome === nomeColuna)?.tipo ?? "";
  const ehNumerica = (nomeColuna: string) => NUMERICOS.test(tipoDe(nomeColuna));

  /* Cada modo só monta uma fórmula; ela aparece embaixo, para quem quiser aprender a escrever. */
  let formula = "";
  let sugestao = "";
  if (modo === "conta" && conta.a && (conta.b === UM_NUMERO ? NUMERO.test(conta.numero.trim()) : conta.b)) {
    const direita = conta.b === UM_NUMERO ? conta.numero.trim() : coluna(conta.b);
    formula = `${coluna(conta.a)} ${conta.op} ${direita}`;
    const palavra = CONTAS.find(([op]) => op === conta.op)?.[2] ?? "";
    sugestao = `${conta.a}_${palavra}_${conta.b === UM_NUMERO ? semSimbolos(conta.numero) : conta.b}`;
  }
  if (modo === "juntar" && juntar.a && juntar.b) {
    formula = juntar.separador
      ? `CONCATENAR(${coluna(juntar.a)}; ${texto(juntar.separador)}; ${coluna(juntar.b)})`
      : `CONCATENAR(${coluna(juntar.a)}; ${coluna(juntar.b)})`;
    sugestao = `${juntar.a}_${juntar.b}`;
  }
  if (modo === "condicao" && condicao.coluna && condicao.entao && (condicao.operador === "vazio" || condicao.valor.trim())) {
    const c = coluna(condicao.coluna);
    const teste = condicao.operador === "vazio" ? `EVAZIO(${c})`
      : condicao.operador === "contem" ? `CONTEM(${c}; ${texto(condicao.valor)})`
      : `${c} ${condicao.operador} ${valorOuTexto(condicao.valor, ehNumerica(condicao.coluna))}`;
    // os dois resultados têm o mesmo tipo: números só se os dois forem números
    const numeros = NUMERO.test(condicao.entao.trim()) && (!condicao.senao || NUMERO.test(condicao.senao.trim()));
    const entao = valorOuTexto(condicao.entao, numeros);
    formula = condicao.senao ? `SE(${teste}; ${entao}; ${valorOuTexto(condicao.senao, numeros)})` : `SE(${teste}; ${entao})`;
    sugestao = `${condicao.coluna}_grupo`;
  }
  if (modo === "formula") formula = livre.trim();
  if (modo === "vazia") sugestao = "coluna_nova";

  const nomeFinal = nome.trim() || (sugestao ? nomeLivre(sugestao, colunas) : "");
  const existe = colunas.some((c) => c.nome === nomeFinal);
  const pronto = Boolean(nomeFinal) && (modo === "vazia" ? !existe : Boolean(formula) && (!existe || substituir));

  useEffect(() => {
    if (!formula || !nomeFinal || (existe && !substituir)) {
      setPrevia(null);
      setErro(null);
      return;
    }
    let ativo = true;
    const espera = window.setTimeout(() => {
      verPrevia(nomeFinal, formula, existe && substituir)
        .then((resposta) => { if (ativo) { setPrevia(resposta); setErro(null); } })
        .catch((falha) => { if (ativo) { setPrevia(null); setErro((falha as Error).message); } });
    }, 350);
    return () => { ativo = false; window.clearTimeout(espera); };
  }, [formula, nomeFinal, existe, substituir, verPrevia]);

  const enviar = (evento: React.FormEvent) => {
    evento.preventDefault();
    if (!pronto || ocupado) return;
    const passo: Passo = { op: "formula", nome: nomeFinal, formula, vazia: modo === "vazia", substituir: existe && substituir };
    if (apos && !existe) passo.apos = apos;
    criar(existe ? `Recalculando ${nomeFinal}` : `Criando a coluna ${nomeFinal}`, passo, nomeFinal);
  };

  const opcoes = (filtro?: (c: Coluna) => boolean) =>
    colunas.filter(filtro ?? (() => true)).map((c) => <option key={c.nome} value={c.nome}>{c.nome}</option>);

  return (
    <form className="painel-nova-coluna" onSubmit={enviar} aria-label="Nova coluna"
          onKeyDown={(evento) => { if (evento.key === "Escape") fechar(); }}>
      <div className="agrupar-linha">
        <label className="rotulo-curto" htmlFor="nova-nome">Nome</label>
        <input ref={campoNome} id="nova-nome" className="campo" value={nome} placeholder={nomeFinal || "nome da coluna"}
               onChange={(e) => setNome(e.target.value)} spellCheck={false} />
        <label className="rotulo-curto" htmlFor="nova-onde">Onde</label>
        <select id="nova-onde" className="campo" value={apos} onChange={(e) => setApos(e.target.value)} disabled={existe}>
          <option value="">no fim da tabela</option>
          {colunas.map((c) => <option key={c.nome} value={c.nome}>depois de {c.nome}</option>)}
        </select>
      </div>

      <div className="modos-coluna" role="radiogroup" aria-label="Como preencher a coluna">
        {MODOS.map(([valor, rotulo, dica]) => (
          <label key={valor} className={`chip-escolha${modo === valor ? " on" : ""}`} title={dica}>
            <input type="radio" name="modo-coluna" value={valor} checked={modo === valor} onChange={() => setModo(valor)} />
            {rotulo}
          </label>
        ))}
      </div>

      {modo === "vazia" ? (
        <p className="texto-suave dica-modo">A coluna começa vazia. Dê duplo clique numa célula para digitar; cada edição fica na receita.</p>
      ) : null}

      {modo === "conta" ? (
        <div className="agrupar-linha">
          <select className="campo" aria-label="Primeira coluna da conta" value={conta.a} onChange={(e) => setConta({ ...conta, a: e.target.value })}>
            <option value="">coluna…</option>
            {opcoes((c) => NUMERICOS.test(c.tipo))}
          </select>
          <div className="operadores" role="radiogroup" aria-label="Conta">
            {CONTAS.map(([op, simbolo, palavra]) => (
              <label key={op} className={`chip-escolha quadrado${conta.op === op ? " on" : ""}`} title={palavra}>
                <input type="radio" name="conta-op" checked={conta.op === op} onChange={() => setConta({ ...conta, op })} />
                {simbolo}
              </label>
            ))}
          </div>
          <select className="campo" aria-label="Segunda coluna da conta, ou um número" value={conta.b}
                  onChange={(e) => setConta({ ...conta, b: e.target.value })}>
            <option value="">coluna…</option>
            <option value={UM_NUMERO}>um número…</option>
            {opcoes((c) => NUMERICOS.test(c.tipo))}
          </select>
          {conta.b === UM_NUMERO ? (
            <input className="campo numero-curto" aria-label="Número" inputMode="decimal" placeholder="12" value={conta.numero}
                   onChange={(e) => setConta({ ...conta, numero: e.target.value })} />
          ) : null}
        </div>
      ) : null}

      {modo === "juntar" ? (
        <div className="agrupar-linha">
          <select className="campo" aria-label="Primeira coluna" value={juntar.a} onChange={(e) => setJuntar({ ...juntar, a: e.target.value })}>
            <option value="">coluna…</option>
            {opcoes()}
          </select>
          <label className="rotulo-curto" htmlFor="juntar-separador">com</label>
          <input id="juntar-separador" className="campo numero-curto" value={juntar.separador} placeholder="nada"
                 onChange={(e) => setJuntar({ ...juntar, separador: e.target.value })} title="O que vai entre os dois textos: espaço, hífen, vírgula…" />
          <select className="campo" aria-label="Segunda coluna" value={juntar.b} onChange={(e) => setJuntar({ ...juntar, b: e.target.value })}>
            <option value="">coluna…</option>
            {opcoes()}
          </select>
        </div>
      ) : null}

      {modo === "condicao" ? (
        <div className="agrupar-linha">
          <span className="rotulo-curto">Se</span>
          <select className="campo" aria-label="Coluna da condição" value={condicao.coluna} onChange={(e) => setCondicao({ ...condicao, coluna: e.target.value })}>
            <option value="">coluna…</option>
            {opcoes()}
          </select>
          <select className="campo" aria-label="Condição" value={condicao.operador} onChange={(e) => setCondicao({ ...condicao, operador: e.target.value })}>
            {CONDICOES.map(([valor, rotulo]) => <option key={valor} value={valor}>{rotulo}</option>)}
          </select>
          {condicao.operador === "vazio" ? null : (
            <input className="campo" aria-label="Valor da condição" placeholder="valor" value={condicao.valor}
                   onChange={(e) => setCondicao({ ...condicao, valor: e.target.value })} />
          )}
          <span className="rotulo-curto">então</span>
          <input className="campo" aria-label="Valor quando a condição vale" placeholder="ex.: alto" value={condicao.entao}
                 onChange={(e) => setCondicao({ ...condicao, entao: e.target.value })} />
          <span className="rotulo-curto">senão</span>
          <input className="campo" aria-label="Valor quando a condição não vale" placeholder="vazio" value={condicao.senao}
                 onChange={(e) => setCondicao({ ...condicao, senao: e.target.value })} />
        </div>
      ) : null}

      {modo === "formula" ? (
        <div className="agrupar-linha">
          <span className="fx" aria-hidden="true">ƒx =</span>
          <input className="campo formula-texto" aria-label="Fórmula" placeholder='SE(tenure < 12; "novo"; "antigo")' value={livre}
                 onChange={(e) => setLivre(e.target.value)} spellCheck={false} />
        </div>
      ) : null}

      {existe && modo !== "vazia" ? (
        <label className="caixa aviso-existe">
          <input type="checkbox" checked={substituir} onChange={(e) => setSubstituir(e.target.checked)} />
          Já existe uma coluna {nomeFinal}: trocar os valores dela por esta conta
        </label>
      ) : null}
      {existe && modo === "vazia" ? <p className="aviso-linha" role="alert">! Já existe uma coluna {nomeFinal}. Escolha outro nome.</p> : null}

      {modo !== "vazia" && modo !== "formula" && formula ? (
        <p className="formula-gerada"><span className="rotulo-curto">fórmula</span> <code>= {formula}</code></p>
      ) : null}
      {erro ? <p className="aviso-linha" role="alert">! {erro}</p> : null}
      {previa ? (
        <div className="previa" aria-live="polite">
          <span className="texto-suave">{previa.tipo} · {fmtInteiro(previa.vazios)} vazios em {fmtInteiro(previa.linhas)} linhas</span>
          <span className="previa-valores">
            {previa.previa.map((p, i) => <span key={i}><b>#{p.linha}</b> {p.valor === null ? "vazio" : String(p.valor)}</span>)}
          </span>
        </div>
      ) : null}

      <div className="agrupar-linha">
        <button type="submit" className="botao pequeno principal" disabled={ocupado || !pronto}>
          {existe && modo !== "vazia" ? "Trocar os valores" : "Criar coluna"}
        </button>
        <button type="button" className="botao pequeno" onClick={fechar}>Cancelar</button>
        <span className="texto-suave">Vira um passo da receita: dá para desfazer.</span>
      </div>
    </form>
  );
}
