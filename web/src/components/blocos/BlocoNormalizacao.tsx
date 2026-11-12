"use client";

import { useState } from "react";
import { Janela, Triangulo } from "@/components/Janela";
import { TabelaDados } from "@/components/TabelaDados";
import { linkExportacao } from "@/lib/api";
import { fmtInteiro } from "@/lib/formato";
import type { Estado, Sugestao } from "@/lib/tipos";

const GRUPOS: { id: string; nome: string }[] = [
  { id: "vazios", nome: "Vazios disfarçados" },
  { id: "texto", nome: "Texto" },
  { id: "tipos", nome: "Tipos de dados" },
  { id: "linhas", nome: "Linhas" },
  { id: "colunas", nome: "Colunas" },
  { id: "calculo", nome: "Colunas calculadas" },
];

/* Deixa visíveis os espaços que estão sendo removidos. */
function comEspacos(texto: string | null) {
  return String(texto).replace(/^ +| +$/g, (espacos) => "␣".repeat(espacos.length));
}

function afetados(sugestao: Sugestao) {
  const quantidade = fmtInteiro(sugestao.afetados);
  if (sugestao.grupo === "colunas") return `${quantidade} ${sugestao.afetados === 1 ? "coluna" : "colunas"}`;
  if (sugestao.grupo === "linhas") return `${quantidade} ${sugestao.afetados === 1 ? "linha" : "linhas"}`;
  return `${quantidade} ${sugestao.afetados === 1 ? "valor" : "valores"}`;
}

function CartaoSugestao({ sugestao, marcada, aoAlternar }: { sugestao: Sugestao; marcada: boolean; aoAlternar: () => void }) {
  return (
    <label className={`sugestao${sugestao.marcada ? "" : " desmarcada"}`}>
      <input type="checkbox" id={`sugestao-${sugestao.id}`} checked={marcada} onChange={aoAlternar} />
      <span className="sugestao-texto">
        <span className="sugestao-titulo">{sugestao.titulo}</span>
        <span className="texto-suave">{sugestao.motivo}</span>
        <span className="marcas">
          {sugestao.afetados ? <span className="marca">{afetados(sugestao)}</span> : null}
          {sugestao.perdidos ? <span className="marca forte">{fmtInteiro(sugestao.perdidos)} viram vazio</span> : null}
        </span>
        {sugestao.exemplos.length ? (
          <span className="exemplos">
            {sugestao.exemplos.map(([antes, depois], indice) => (
              <span className="exemplo" key={indice}>
                <span className="antes" title={String(antes)}>{comEspacos(antes)}</span>
                <span aria-hidden="true">→</span>
                <span className={depois === null ? "nulo" : undefined} title={String(depois)}>{depois === null ? "vazio" : depois}</span>
              </span>
            ))}
          </span>
        ) : null}
      </span>
    </label>
  );
}

function Resultado({ estado, aoAvancar }: { estado: Estado; aoAvancar: () => void }) {
  const relatorio = estado.relatorio!;
  const [copiado, setCopiado] = useState(false);
  const falhas = relatorio.passos.filter((p) => p.status === "falhou");

  const copiar = async () => {
    await navigator.clipboard.writeText(relatorio.sql);
    setCopiado(true);
    setTimeout(() => setCopiado(false), 1800);
  };

  return (
    <>
      <Janela titulo="Resultado">
        <dl className="numeros">
          <div className="numero"><dt>Linhas</dt><dd>{fmtInteiro(relatorio.linhas_antes)} → {fmtInteiro(relatorio.linhas_depois)}</dd></div>
          <div className="numero"><dt>Colunas</dt><dd>{relatorio.colunas_antes} → {relatorio.colunas_depois}</dd></div>
          <div className="numero"><dt>Passos aplicados</dt><dd>{relatorio.passos.length - falhas.length}</dd></div>
        </dl>
        {falhas.length ? (
          <ul className="avisos">{falhas.map((p) => <li key={p.indice}>{p.titulo}: {p.erro}</li>)}</ul>
        ) : null}
        <div className="acoes">
          <button type="button" className="botao principal" onClick={aoAvancar}>Ver gráficos →</button>
          <a className="botao" href={linkExportacao(estado.sessao, "csv")}>Baixar CSV</a>
          <a className="botao" href={linkExportacao(estado.sessao, "parquet")}>Baixar Parquet</a>
          <a className="botao" href={linkExportacao(estado.sessao, "receita")}>Baixar receita</a>
        </div>
      </Janela>

      <Janela titulo="Passo a passo" semFolga>
        <div className="rolagem">
          <table className="tabela">
            <thead><tr><th scope="col">#</th><th scope="col">Passo</th><th scope="col">Linhas</th><th scope="col">Alterados</th><th scope="col">Viraram vazio</th><th scope="col">Status</th></tr></thead>
            <tbody>
              {relatorio.passos.map((passo) => (
                <tr key={passo.indice}>
                  <td>{passo.indice}</td>
                  <td>{passo.titulo}</td>
                  <td>{fmtInteiro(passo.linhas_antes)}{passo.linhas_depois !== passo.linhas_antes ? ` → ${fmtInteiro(passo.linhas_depois)}` : ""}</td>
                  <td>{fmtInteiro(passo.alterados)}</td>
                  <td>{fmtInteiro(passo.perdidos)}</td>
                  <td><span className={`marca${passo.status === "ok" ? " ok" : " forte"}`}>{passo.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Janela>

      <Janela titulo="SQL gerado">
        <div className="terminal-cabeca">
          <span className="texto-suave">A receita inteira como uma consulta. Roda em qualquer DuckDB com a tabela bruta.</span>
          <button type="button" className="botao pequeno" onClick={copiar}>{copiado ? "Copiado" : "Copiar"}</button>
        </div>
        <pre className="terminal" tabIndex={0}>{relatorio.sql}</pre>
      </Janela>

      <Janela titulo="Tabela limpa" semFolga>
        <TabelaDados sessao={estado.sessao} tabela="limpa" versao={relatorio.sql} />
      </Janela>
    </>
  );
}

type Props = {
  estado: Estado;
  ocupado: boolean;
  selecao: Set<string>;
  aoAlternar: (id: string) => void;
  aoAplicar: () => void;
  aoAvancar: () => void;
};

export function BlocoNormalizacao({ estado, ocupado, selecao, aoAlternar, aoAplicar, aoAvancar }: Props) {
  const [confirmando, setConfirmando] = useState(false);
  const escolhidas = estado.sugestoes.filter((s) => selecao.has(s.id));
  const colunas = escolhidas.flatMap((s) => (s.passo.op === "remover_colunas" ? (s.passo.colunas as string[]) : []));

  const aplicar = () => {
    if (colunas.length) setConfirmando(true);
    else aoAplicar();
  };

  return (
    <>
      {estado.relatorio ? <Resultado estado={estado} aoAvancar={aoAvancar} /> : null}

      <Janela titulo={estado.relatorio ? "Refazer a normalização" : "Passos sugeridos"}>
        {estado.sugestoes.length === 0 ? (
          <p>Nada para normalizar: o arquivo já chegou limpo.</p>
        ) : (
          <p className="texto-suave">
            Tudo que vem marcado é seguro. Passos com borda tracejada dependem do seu conhecimento dos dados.
            A tabela original continua intacta.
          </p>
        )}
        {GRUPOS.map((grupo) => {
          const lista = estado.sugestoes.filter((s) => s.grupo === grupo.id);
          if (!lista.length) return null;
          return (
            <div className="grupo" key={grupo.id}>
              <h3>{grupo.nome} <span className="marca">{lista.length}</span></h3>
              {lista.map((sugestao) => (
                <CartaoSugestao key={sugestao.id} sugestao={sugestao} marcada={selecao.has(sugestao.id)} aoAlternar={() => aoAlternar(sugestao.id)} />
              ))}
            </div>
          );
        })}
        <div className="rodape-acao">
          <span>{escolhidas.length} de {estado.sugestoes.length} passos marcados</span>
          <button type="button" className="botao principal" disabled={ocupado} onClick={aplicar}>
            Aplicar e analisar
          </button>
        </div>
      </Janela>

      {confirmando ? (
        <div className="fundo-dialogo" role="alertdialog" aria-modal="true" aria-labelledby="titulo-dialogo">
          <div className="janela dialogo">
            <div className="aviso">
              <Triangulo />
              <div className="aviso-texto">
                <p id="titulo-dialogo"><strong>Remover {colunas.length} {colunas.length === 1 ? "coluna" : "colunas"} da tabela limpa?</strong></p>
                <p className="texto-suave">{colunas.join(", ")}</p>
                <p className="texto-suave">O arquivo original continua com elas.</p>
                <div className="dialogo-acoes">
                  <button type="button" className="botao" onClick={() => setConfirmando(false)}>Revisar</button>
                  <button type="button" className="botao principal" onClick={() => { setConfirmando(false); aoAplicar(); }}>Remover e aplicar</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
