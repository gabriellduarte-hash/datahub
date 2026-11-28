"use client";

/* Comparar os arquivos da sessão antes de combinar: colunas que batem,
   duplicatas entre eles e a mesma limpeza aplicada em todos. */

import { useEffect, useMemo, useState } from "react";
import { Janela } from "@/components/Janela";
import * as api from "@/lib/api";
import { fmtInteiro } from "@/lib/formato";
import type { Area, ColunaComparada, Comparacao, ParComparado, PlanoCopia, Tabela } from "@/lib/tipos";

type Props = {
  area: Area;
  ocupado: boolean;
  executar: (texto: string, tarefa: () => Promise<void>) => Promise<void>;
  aoMudar: (area: Area) => void;
};

const NOMES_TIPO: Record<string, string> = { numero: "número", percentual: "percentual", data: "data", booleano: "sim/não", texto: "texto" };
const ORDEM: Record<string, number> = { tipo_diferente: 0, falta: 1, nome_diferente: 2, igual: 3 };

function plural(n: number, um: string, varios: string) {
  return `${fmtInteiro(n)} ${n === 1 ? um : varios}`;
}

export function BlocoComparacao({ area, ocupado, executar, aoMudar }: Props) {
  const [dados, setDados] = useState<Comparacao | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [carregando, setCarregando] = useState(true);
  const [tudo, setTudo] = useState(false);
  const [aviso, setAviso] = useState<string | null>(null);
  const versao = area.tabelas.map((t) => `${t.id}:${t.versao}`).join(",");

  useEffect(() => {
    let ativo = true;
    setCarregando(true);
    api.comparar(area.sessao)
      .then((resposta) => { if (ativo) { setDados(resposta); setErro(null); } })
      .catch((falha) => { if (ativo) setErro((falha as Error).message); })
      .finally(() => { if (ativo) setCarregando(false); });
    return () => { ativo = false; };
  }, [area.sessao, versao]);

  const nomeDe = useMemo(() => Object.fromEntries((dados?.tabelas ?? []).map((t) => [t.id, t.nome])), [dados]);

  if (!dados) {
    return (
      <Janela titulo="Comparar arquivos">
        {erro ? <p className="aviso-linha" role="alert">! {erro}</p> : <p className="texto-suave">Comparando os arquivos…</p>}
      </Janela>
    );
  }

  const contagem = (situacao: string) => dados.colunas.filter((c) => c.situacao === situacao).length;
  const diferentes = dados.colunas.filter((c) => c.situacao !== "igual");
  const linhas = [...(tudo ? dados.colunas : diferentes)].sort((x, y) => ORDEM[x.situacao] - ORDEM[y.situacao]);
  const duplicatasEntre = dados.pares.reduce((soma, par) => soma + par.b_em_a, 0);
  const semNormalizar = dados.tabelas.filter((t) => !t.normalizada);

  const copiar = (plano: PlanoCopia) => executar(`Copiando a limpeza de ${nomeDe[plano.origem]} para ${nomeDe[plano.destino]}`, async () => {
    const resposta = await api.copiarReceita(area.sessao, plano.destino, plano.origem);
    aoMudar(resposta);
    const { aplicados, pulados } = resposta.copia;
    setAviso(`${nomeDe[plano.destino]} recebeu ${plural(aplicados, "passo", "passos")} de ${nomeDe[plano.origem]}`
      + (pulados.length ? `; ${plural(pulados.length, "ficou", "ficaram")} de fora.` : "."));
  });

  return (
    <Janela titulo="Comparar arquivos" extra={carregando ? "atualizando…" : `${dados.tabelas.length} arquivos`}>
      <dl className="numeros">
        <div className="numero"><dt>Colunas iguais</dt><dd>{fmtInteiro(contagem("igual"))}</dd></div>
        <div className="numero"><dt>Nome escrito diferente</dt><dd>{fmtInteiro(contagem("nome_diferente"))}</dd></div>
        <div className="numero"><dt>Tipo diferente</dt><dd>{fmtInteiro(contagem("tipo_diferente"))}</dd></div>
        <div className="numero"><dt>Em parte dos arquivos</dt><dd>{fmtInteiro(contagem("falta"))}</dd></div>
        <div className="numero"><dt>Duplicatas entre eles</dt><dd>{fmtInteiro(duplicatasEntre)}</dd></div>
      </dl>
      {dados.deixadas_de_fora ? (
        <p className="texto-suave">A comparação usa os 6 primeiros arquivos; {plural(dados.deixadas_de_fora, "ficou", "ficaram")} de fora.</p>
      ) : null}
      {aviso ? <p className="aviso-linha" role="status">{aviso}</p> : null}

      <section className="comparacao-secao" aria-labelledby="cmp-colunas">
        <div className="comparacao-titulo">
          <h3 id="cmp-colunas">Colunas</h3>
          <label className="caixa" htmlFor="cmp-todas">
            <input id="cmp-todas" type="checkbox" checked={tudo} onChange={(e) => setTudo(e.target.checked)} /> mostrar as iguais também
          </label>
        </div>
        {linhas.length ? (
          <div className="rolagem">
            <table className="tabela tabela-comparacao">
              <thead>
                <tr>
                  <th scope="col">Coluna</th>
                  {dados.tabelas.map((t) => <th key={t.id} scope="col">{t.nome}</th>)}
                  <th scope="col">Situação</th>
                </tr>
              </thead>
              <tbody>
                {linhas.map((coluna) => (
                  <LinhaColuna key={coluna.chave} coluna={coluna} tabelas={dados.tabelas.map((t) => t.id)} nomeDe={nomeDe} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="texto-suave">As {fmtInteiro(dados.colunas.length)} colunas têm o mesmo nome e o mesmo tipo em todos os arquivos.</p>
        )}
      </section>

      <section className="comparacao-secao" aria-labelledby="cmp-duplicatas">
        <div className="comparacao-titulo"><h3 id="cmp-duplicatas">Duplicatas</h3></div>
        {semNormalizar.length ? (
          <p className="texto-suave">
            {semNormalizar.map((t) => t.nome).join(", ")} ainda sem normalizar: datas escritas de jeitos diferentes não batem.
            Números e textos já são comparados sem ligar para formato, maiúsculas e espaços nas pontas.
          </p>
        ) : null}
        {dados.tabelas.filter((t) => t.duplicatas_dentro).map((t) => (
          <p key={t.id} className="comparacao-dentro">
            <strong>{t.nome}</strong> tem {plural(t.duplicatas_dentro, "duplicata", "duplicatas")} dentro dele mesmo.
          </p>
        ))}
        {dados.pares.map((par) => <Par key={`${par.a}-${par.b}`} par={par} nomeDe={nomeDe} />)}
      </section>

      <section className="comparacao-secao" aria-labelledby="cmp-limpeza">
        <div className="comparacao-titulo"><h3 id="cmp-limpeza">Mesma limpeza em todos</h3></div>
        {dados.planos.length ? (
          <ul className="planos">
            {dados.planos.map((plano) => (
              <Plano key={`${plano.origem}-${plano.destino}`} plano={plano} nomeDe={nomeDe} ocupado={ocupado || carregando}
                     temReceita={Boolean(area.tabelas.find((t) => t.id === plano.destino)?.receita?.length)}
                     aoCopiar={() => copiar(plano)} />
            ))}
          </ul>
        ) : (
          <p className="texto-suave">
            Normalize um dos arquivos (e, se quiser, mexa nele na sandbox) e volte aqui: dá para copiar a mesma limpeza para os outros,
            com os nomes de coluna ajustados.
          </p>
        )}
      </section>

      <Combinar area={area} dados={dados} ocupado={ocupado || carregando} executar={executar} aoMudar={aoMudar} aoAvisar={setAviso} />
    </Janela>
  );
}

function LinhaColuna({ coluna, tabelas, nomeDe }: { coluna: ColunaComparada; tabelas: string[]; nomeDe: Record<string, string> }) {
  const tiposPresentes = new Set(tabelas.map((id) => coluna.tipos[id]).filter(Boolean));
  const ausentes = tabelas.filter((id) => !coluna.nomes[id]);
  let situacao: string;
  if (coluna.situacao === "tipo_diferente") situacao = "tipo diferente";
  else if (coluna.situacao === "falta") situacao = ausentes.length === 1 ? `falta em ${nomeDe[ausentes[0]]}` : `só em ${tabelas.filter((id) => coluna.nomes[id]).map((id) => nomeDe[id]).join(", ")}`;
  else if (coluna.situacao === "nome_diferente") situacao = "nome escrito diferente";
  else situacao = "igual";
  return (
    <tr className={`situacao-${coluna.situacao}`}>
      <td><strong>{coluna.chave}</strong></td>
      {tabelas.map((id) => (
        <td key={id}>
          {coluna.nomes[id] ? (
            <span className="celula-coluna">
              <span>{coluna.nomes[id]}</span>
              <span className={`tipo-coluna${tiposPresentes.size > 1 ? " destaque" : ""}`}>{NOMES_TIPO[coluna.tipos[id] ?? ""] ?? coluna.tipos[id]}</span>
            </span>
          ) : <span className="nulo">não tem</span>}
        </td>
      ))}
      <td>
        <div className="marcas">
          <span className={`marca${coluna.situacao === "igual" ? "" : " forte"}`}>{situacao}</span>
          {coluna.parecida_com ? <span className="marca">parece com {coluna.parecida_com}</span> : null}
        </div>
      </td>
    </tr>
  );
}

function Par({ par, nomeDe }: { par: ParComparado; nomeDe: Record<string, string> }) {
  const a = nomeDe[par.a];
  const b = nomeDe[par.b];
  if (!par.colunas.length) {
    return <div className="par"><p><strong>{a} × {b}</strong> não têm nenhuma coluna em comum para comparar.</p></div>;
  }
  return (
    <div className="par">
      <p className="par-titulo">
        <strong>{a} × {b}</strong>
        <span className="par-numero">{par.iguais ? `${plural(par.b_em_a, "duplicata", "duplicatas")}: ${par.b_em_a === 1 ? `linha de ${b} que já está` : `linhas de ${b} que já estão`} em ${a}` : "nenhuma duplicata"}</span>
      </p>
      <p className="texto-suave">
        Comparando {par.colunas.join(", ")}.
        {par.fora.length ? ` Ficou fora da conta, por ter tipo diferente: ${par.fora.join(", ")}.` : ""}
      </p>
      {par.chave && par.colunas.length > 1 ? (
        <p className="texto-suave">
          Pelo identificador <strong>{par.chave.coluna}</strong>: {fmtInteiro(par.chave.ambos)} nos dois,
          {" "}{fmtInteiro(par.chave.so_a)} só em {a}, {fmtInteiro(par.chave.so_b)} só em {b}.
          {par.chave.ambos > par.iguais ? ` ${plural(par.chave.ambos - par.iguais, "registro aparece", "registros aparecem")} nos dois com algum campo diferente.` : ""}
        </p>
      ) : null}
      {par.exemplos.length ? (
        <details className="par-exemplos">
          <summary>Ver {par.exemplos.length === 1 ? "o exemplo" : `${par.exemplos.length} exemplos`}</summary>
          <div className="rolagem">
            <table className="tabela">
              <thead>
                <tr>
                  <th scope="col">Linha em {a}</th>
                  <th scope="col">Linha em {b}</th>
                  {par.colunas.map((c) => <th key={c} scope="col">{c}</th>)}
                </tr>
              </thead>
              <tbody>
                {par.exemplos.map((exemplo) => (
                  <tr key={exemplo.linha_a}>
                    <td className="numero-linha">#{exemplo.linha_a}</td>
                    <td className="numero-linha">#{exemplo.linha_b}</td>
                    {exemplo.valores.map((valor, i) => <td key={i} className={valor === null ? "nulo" : "curta"}>{valor ?? "vazio"}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </div>
  );
}

type PropsPlano = { plano: PlanoCopia; nomeDe: Record<string, string>; ocupado: boolean; temReceita: boolean; aoCopiar: () => void };

function Plano({ plano, nomeDe, ocupado, temReceita, aoCopiar }: PropsPlano) {
  const origem = nomeDe[plano.origem];
  const destino = nomeDe[plano.destino];
  const servem = plano.aplicaveis.length;
  return (
    <li className={`plano${plano.aplicado ? " aplicado" : ""}`}>
      <div className="plano-linha">
        {plano.aplicado ? (
          <span><strong>{destino}</strong> já tem a limpeza de <strong>{origem}</strong></span>
        ) : (
          <span>
            Limpeza de <strong>{origem}</strong> em <strong>{destino}</strong>:{" "}
            {plural(servem, "passo serve", "passos servem")}
            {plano.pulados.length ? `, ${plural(plano.pulados.length, "fica", "ficam")} de fora` : ""}
          </span>
        )}
        <button type="button" className={`botao pequeno${plano.aplicado ? "" : " principal"}`} disabled={ocupado || !servem || plano.aplicado}
                onClick={aoCopiar} title={plano.aplicado ? "Aplicar de novo não mudaria nada. Mude a limpeza da origem para aplicar outra vez." : undefined}>
          {plano.aplicado ? "✓ Aplicada" : `Aplicar em ${destino}`}
        </button>
      </div>
      <details>
        <summary>{plano.aplicado ? "O que foi aplicado" : "O que muda"}</summary>
        <ul className="plano-passos">
          {plano.aplicaveis.map((titulo, i) => <li key={`a${i}`}><span aria-hidden="true">✓</span> {titulo}</li>)}
          {plano.pulados.map((p, i) => <li key={`p${i}`} className="pulado"><span aria-hidden="true">–</span> {p.titulo} <em>({p.motivo})</em></li>)}
        </ul>
        {plano.trocas.length ? (
          <p className="texto-suave">Em {destino}, usa {plano.trocas.map((t) => `${t.para} no lugar de ${t.de}`).join(", ")}.</p>
        ) : null}
        {plano.aplicado ? (
          <p className="texto-suave">Se a limpeza de {origem} mudar, o botão volta a ficar ativo.</p>
        ) : (
          <p className="texto-suave">
            Conversões de número e data usam o formato detectado em {destino}.{" "}
            {temReceita ? `A normalização de ${destino} é trocada por esta. ` : ""}
            Os passos da sandbox entram depois dos que {destino} já tem; dá para desfazer um a um na sandbox.
          </p>
        )}
      </details>
    </li>
  );
}

type PropsCombinar = Props & { dados: Comparacao; aoAvisar: (texto: string) => void };

/* Uma tabela já combinada com os mesmos arquivos, na mesma versão e com a mesma opção:
   combinar de novo daria a mesma coisa. */
function jaCombinada(tabelas: Tabela[], escolhidas: string[], semDuplicatas: boolean) {
  const versao = Object.fromEntries(tabelas.map((t) => [t.id, t.versao]));
  return tabelas.find((t) => {
    const d = t.origem === "combinada" ? t.definicao : null;
    return Boolean(d) && d!.sem_duplicatas === semDuplicatas && d!.tabelas.join() === escolhidas.join()
      && escolhidas.every((id) => d!.versoes?.[id] === versao[id]);
  }) ?? null;
}

function Combinar({ area, dados, ocupado, executar, aoMudar, aoAvisar }: PropsCombinar) {
  const [fora, setFora] = useState<string[]>([]);
  const [nome, setNome] = useState("combinada");
  const temDuplicatas = dados.pares.some((p) => p.iguais) || dados.tabelas.some((t) => t.duplicatas_dentro);
  const [semDuplicatas, setSemDuplicatas] = useState(temDuplicatas);
  const validas = dados.tabelas.map((t) => t.id).filter((id) => !fora.includes(id));
  const feita = jaCombinada(area.tabelas, validas, semDuplicatas);

  const combinar = () => executar("Combinando os arquivos", async () => {
    const resposta = await api.combinar(area.sessao, validas, nome.trim() || "combinada", semDuplicatas);
    aoMudar(resposta);
    const nova = resposta.tabelas[resposta.tabelas.length - 1];
    aoAvisar(`Tabela ${nova.nome} criada com ${plural(nova.arquivo.linhas, "linha", "linhas")}. ${nova.arquivo.avisos.join(" ")}`);
  });

  return (
    <section className="comparacao-secao" aria-labelledby="cmp-combinar">
      <div className="comparacao-titulo"><h3 id="cmp-combinar">Combinar arquivos</h3></div>
      <div className="agrupar-linha">
        {dados.tabelas.map((t) => (
          <label key={t.id} className={`chip-escolha${validas.includes(t.id) ? " on" : ""}`}>
            <input type="checkbox" checked={validas.includes(t.id)}
                   onChange={(e) => setFora(e.target.checked ? fora.filter((id) => id !== t.id) : [...fora, t.id])} />
            {t.nome}
          </label>
        ))}
      </div>
      <div className="agrupar-linha">
        <label className="rotulo-curto" htmlFor="combinar-nome">Nome</label>
        <input id="combinar-nome" className="campo" value={nome} onChange={(e) => setNome(e.target.value)} />
        <label className="caixa" htmlFor="combinar-sem-duplicatas">
          <input id="combinar-sem-duplicatas" type="checkbox" checked={semDuplicatas} onChange={(e) => setSemDuplicatas(e.target.checked)} />
          Remover duplicatas (fica a primeira aparição)
        </label>
        <button type="button" className={`botao pequeno${feita ? "" : " principal"}`} disabled={ocupado || validas.length < 2 || Boolean(feita)}
                onClick={combinar}>
          {feita ? "✓ Combinados" : `Combinar ${validas.length} arquivos`}
        </button>
      </div>
      <p className="texto-suave">
        {feita
          ? `Já estão combinados em ${feita.nome}, do jeito que está aqui. Mude um dos arquivos, a escolha ou a opção de duplicatas para combinar de novo.`
          : "Uma tabela nova, um arquivo embaixo do outro, com a coluna origem dizendo de onde veio cada linha. Colunas com o mesmo nome, como Valor Total e valor_total, viram uma só. Os arquivos continuam como estão."}
      </p>
    </section>
  );
}
