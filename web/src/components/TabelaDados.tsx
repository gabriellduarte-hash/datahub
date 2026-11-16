"use client";

import { useEffect, useState } from "react";
import { verLinhas } from "@/lib/api";
import { fmtInteiro } from "@/lib/formato";
import type { PaginaLinhas } from "@/lib/tipos";

const POR_PAGINA = 25;

type Props = { sessao: string; tabela: string; bruta?: boolean; versao?: string | number };

/* Pede uma página por vez ao servidor. A tabela inteira nunca vem para o navegador. */
export function TabelaDados({ sessao, tabela, bruta = false, versao }: Props) {
  const [pagina, setPagina] = useState(1);
  const [ordem, setOrdem] = useState<{ coluna: string | null; decrescente: boolean }>({ coluna: null, decrescente: false });
  const [dados, setDados] = useState<PaginaLinhas | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  useEffect(() => {
    let ativo = true;
    verLinhas(sessao, tabela, (pagina - 1) * POR_PAGINA, POR_PAGINA, ordem, [], bruta)
      .then((resposta) => { if (ativo) { setDados(resposta); setErro(null); } })
      .catch((falha: Error) => { if (ativo) setErro(falha.message); });
    return () => { ativo = false; };
  }, [sessao, tabela, bruta, pagina, ordem, versao]);

  if (erro) return <p className="corpo">{erro}</p>;
  if (!dados) return <p className="corpo texto-suave">Carregando linhas…</p>;

  const paginas = Math.max(Math.ceil(dados.total / POR_PAGINA), 1);
  const ordenar = (coluna: string) => {
    setPagina(1);
    setOrdem((atual) => ({ coluna, decrescente: atual.coluna === coluna ? !atual.decrescente : false }));
  };

  return (
    <>
      <div className="rolagem">
        <table className="tabela">
          <thead>
            <tr>
              <th scope="col" className="numero-linha">#</th>
              {dados.colunas.map((coluna) => (
                <th key={coluna.nome} scope="col">
                  <button type="button" className="ordenavel" onClick={() => ordenar(coluna.nome)}>
                    {coluna.nome}
                    {ordem.coluna === coluna.nome ? (ordem.decrescente ? " ▼" : " ▲") : ""}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dados.linhas.map((linha, indice) => (
              <tr key={dados.numeros[indice] ?? indice}>
                <td className="numero-linha">{dados.numeros[indice] ?? ""}</td>
                {linha.map((valor, posicao) => (
                  <td key={posicao} className={`curta${valor === null ? " nulo" : ""}`} title={valor === null ? undefined : String(valor)}>
                    {valor === null ? "vazio" : String(valor)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="paginacao">
        <span>{fmtInteiro(dados.total)} linhas</span>
        <div>
          <button type="button" className="botao pequeno" disabled={pagina <= 1} onClick={() => setPagina(pagina - 1)}>◀</button>
          <span>{fmtInteiro(pagina)} de {fmtInteiro(paginas)}</span>
          <button type="button" className="botao pequeno" disabled={pagina >= paginas} onClick={() => setPagina(pagina + 1)}>▶</button>
        </div>
      </div>
    </>
  );
}
