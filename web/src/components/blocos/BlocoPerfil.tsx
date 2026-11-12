"use client";

import { useState } from "react";
import { Janela } from "@/components/Janela";
import { fmtInteiro } from "@/lib/formato";
import type { Estado, Papel, Perfil, PerfilColuna } from "@/lib/tipos";

const PAPEIS: { valor: Papel; nome: string }[] = [
  { valor: "metrica", nome: "métrica" },
  { valor: "categoria", nome: "categoria" },
  { valor: "data", nome: "data" },
  { valor: "id", nome: "identificador" },
  { valor: "indicador", nome: "indicador" },
  { valor: "texto", nome: "texto" },
  { valor: "ignorar", nome: "ignorar" },
];

function situacoes(coluna: PerfilColuna): { texto: string; forte?: boolean }[] {
  const lista: { texto: string; forte?: boolean }[] = [];
  if (coluna.vazio === "vazia") lista.push({ texto: "vazia", forte: true });
  if (coluna.vazio === "constante") lista.push({ texto: "um valor só", forte: true });
  if (coluna.vazio === "quase_vazia") lista.push({ texto: "quase vazia" });
  if (coluna.vazios_disfarcados) lista.push({ texto: `${fmtInteiro(coluna.vazios_disfarcados)} disfarçados` });
  if (coluna.com_espacos) lista.push({ texto: `${fmtInteiro(coluna.com_espacos)} com espaços` });
  if (coluna.misto) lista.push({ texto: "formatos misturados" });
  return lista;
}

function BarraVazios({ fracao }: { fracao: number }) {
  return (
    <div className="vazios">
      <span className="vazios-faixa">
        <span className={`vazios-valor${fracao >= 1 ? " cheio" : ""}`} style={{ width: `${fracao * 100}%` }} />
      </span>
      <span>{Math.round(fracao * 100)}%</span>
    </div>
  );
}

type Props = {
  estado: Estado;
  ocupado: boolean;
  aoTrocarPapel: (coluna: string, papel: Papel) => void;
};

export function BlocoPerfil({ estado, ocupado, aoTrocarPapel }: Props) {
  const temLimpo = Boolean(estado.perfil_limpo);
  const [vendo, setVendo] = useState<"antes" | "depois">(temLimpo ? "depois" : "antes");
  const perfil = (vendo === "depois" && estado.perfil_limpo ? estado.perfil_limpo : estado.perfil) as Perfil;
  const validas = perfil.linhas - perfil.linhas_vazias;
  const comProblema = perfil.colunas.filter((c) => situacoes(c).length).length;

  return (
    <>
      <Janela titulo="Perfil dos dados">
        <dl className="numeros">
          <div className="numero"><dt>Linhas</dt><dd>{fmtInteiro(perfil.linhas)}</dd></div>
          <div className="numero"><dt>Colunas</dt><dd>{fmtInteiro(perfil.colunas.length)}</dd></div>
          <div className="numero"><dt>Linhas vazias</dt><dd>{fmtInteiro(perfil.linhas_vazias)}</dd></div>
          <div className="numero"><dt>Duplicadas</dt><dd>{fmtInteiro(perfil.linhas_duplicadas)}</dd></div>
          <div className="numero"><dt>Colunas a revisar</dt><dd>{fmtInteiro(comProblema)}</dd></div>
        </dl>
        {temLimpo ? (
          <div className="acoes">
            <button type="button" className={`botao pequeno${vendo === "antes" ? " principal" : ""}`} onClick={() => setVendo("antes")}>Antes da normalização</button>
            <button type="button" className={`botao pequeno${vendo === "depois" ? " principal" : ""}`} onClick={() => setVendo("depois")}>Depois da normalização</button>
          </div>
        ) : null}
        <p className="texto-suave">
          O papel diz como a coluna entra nos gráficos e insights. Se a detecção errou, troque aqui.
        </p>
      </Janela>

      <Janela titulo="Colunas" semFolga>
        <div className="rolagem">
          <table className="tabela">
            <thead>
              <tr>
                <th scope="col">Coluna</th>
                <th scope="col">Tipo</th>
                <th scope="col">Papel</th>
                <th scope="col">Vazios</th>
                <th scope="col">Distintos</th>
                <th scope="col">Situação</th>
                <th scope="col">Exemplos</th>
              </tr>
            </thead>
            <tbody>
              {perfil.colunas.map((coluna) => (
                <tr key={coluna.nome}>
                  <td><strong>{coluna.nome}</strong></td>
                  <td>{coluna.tipo}</td>
                  <td>
                    <select
                      id={`papel-${coluna.posicao}`}
                      aria-label={`Papel de ${coluna.nome}`}
                      className="campo"
                      disabled={ocupado}
                      value={estado.papeis[coluna.nome] ?? coluna.papel}
                      onChange={(evento) => aoTrocarPapel(coluna.nome, evento.target.value as Papel)}
                    >
                      {PAPEIS.map((papel) => <option key={papel.valor} value={papel.valor}>{papel.nome}</option>)}
                    </select>
                  </td>
                  <td><BarraVazios fracao={validas ? coluna.nulos / validas : 0} /></td>
                  <td>{fmtInteiro(coluna.distintos)}</td>
                  <td>
                    <div className="marcas">
                      {situacoes(coluna).map((item) => (
                        <span key={item.texto} className={`marca${item.forte ? " forte" : ""}`}>{item.texto}</span>
                      ))}
                    </div>
                  </td>
                  <td className="curta" title={coluna.exemplos.join(" · ")}>{coluna.exemplos.join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Janela>
    </>
  );
}
