"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BlocoArquivo } from "@/components/blocos/BlocoArquivo";
import { BlocoNormalizacao } from "@/components/blocos/BlocoNormalizacao";
import { BlocoPerfil } from "@/components/blocos/BlocoPerfil";
import { Tabua } from "@/components/sandbox/Tabua";
import * as api from "@/lib/api";
import { aquecerMotor } from "@/lib/motor";
import { barraAscii } from "@/lib/formato";
import type { Area, Papel, Tabela } from "@/lib/tipos";

type Etapa = "triagem" | "normalizacao" | "sandbox";
const ETAPAS: { id: Etapa; nome: string }[] = [
  { id: "triagem", nome: "Triagem" },
  { id: "normalizacao", nome: "Normalização" },
  { id: "sandbox", nome: "Sandbox" },
];
const CHAVE_SESSAO = "datahub-sessao";
const CHAVE_TEMA = "datahub-tema";

function lembrar(chave: string, valor: string | null) {
  try {
    if (valor) localStorage.setItem(chave, valor);
    else localStorage.removeItem(chave);
  } catch {}
}

function lembrado(chave: string): string | null {
  try {
    return localStorage.getItem(chave);
  } catch {
    return null;
  }
}

/* As sugestões marcadas de uma tabela: as que já estão na receita, ou as recomendadas. */
function selecaoInicial(tabela: Tabela | null): Set<string> {
  if (!tabela) return new Set();
  const aplicados = new Set((tabela.receita ?? []).map((passo) => JSON.stringify(passo)));
  return new Set(tabela.sugestoes
    .filter((s) => (tabela.receita ? aplicados.has(JSON.stringify(s.passo)) : s.marcada))
    .map((s) => s.id));
}

export function Hub() {
  const [area, setArea] = useState<Area | null>(null);
  const [etapa, setEtapa] = useState<Etapa>("triagem");
  const [selecao, setSelecao] = useState<Set<string>>(new Set());
  const [ocupado, setOcupado] = useState<{ texto: string; fracao?: number } | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [tema, setTema] = useState("dark");

  const ativa = useMemo(() => area?.tabelas.find((t) => t.id === area.ativa) ?? null, [area]);

  const receber = useCallback((nova: Area) => {
    setArea(nova);
    lembrar(CHAVE_SESSAO, nova.sessao);
  }, []);

  /* A seleção de sugestões recomeça quando muda a tabela ativa ou a receita aplicada. */
  const chaveSelecao = ativa ? `${ativa.id}:${JSON.stringify(ativa.receita)}` : "";
  useEffect(() => {
    setSelecao(selecaoInicial(ativa));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chaveSelecao]);

  const executar = useCallback(async (texto: string, tarefa: () => Promise<void>) => {
    setOcupado({ texto });
    setErro(null);
    try {
      await tarefa();
    } catch (falha) {
      setErro((falha as Error).message);
    } finally {
      setOcupado(null);
    }
  }, []);

  /* Lê o que só existe no navegador: a sessão anterior e o tema escolhido. */
  useEffect(() => {
    aquecerMotor();
    const salvo = lembrado(CHAVE_TEMA);
    if (salvo) {
      setTema(salvo);
      document.documentElement.dataset.theme = salvo;
    }
    const sessao = lembrado(CHAVE_SESSAO);
    if (!sessao) return;
    api.verSessao(sessao)
      .then((resposta) => {
        if (!Array.isArray(resposta.tabelas)) throw new Error("sessão de uma versão antiga");
        receber(resposta);
        const tabela = resposta.tabelas.find((t) => t.id === resposta.ativa);
        setEtapa(tabela?.relatorio ? "sandbox" : "triagem");
      })
      .catch((falha) => {
        /* Só esquece a sessão se ela não existe mais; se a API só não respondeu, guarda para a próxima vez. */
        if (falha instanceof api.ErroApi && falha.status === 404) lembrar(CHAVE_SESSAO, null);
        else setErro((falha as Error).message);
      });
  }, [receber]);

  /* O hub nasce escuro; o tema claro fica guardado no navegador de quem escolheu. */
  const inverter = () => {
    const novo = tema === "light" ? "dark" : "light";
    setTema(novo);
    document.documentElement.dataset.theme = novo;
    lembrar(CHAVE_TEMA, novo);
  };

  const progresso = (nome: string) => (fracao: number) => {
    setOcupado(fracao < 1 ? { texto: `Enviando ${nome}`, fracao } : { texto: "Lendo o arquivo e montando o perfil" });
  };

  const enviar = (arquivo: File) => executar(`Enviando ${arquivo.name}`, async () => {
    const nova = area
      ? await api.adicionarArquivo(area.sessao, arquivo, progresso(arquivo.name))
      : await api.enviarArquivo(arquivo, progresso(arquivo.name));
    receber(nova);
    setEtapa("triagem");
  });

  const exemplo = () => executar("Gerando as vendas de exemplo", async () => {
    receber(await api.abrirExemplo());
    setEtapa("triagem");
  });

  const trocarAba = (aba: string) => executar(`Lendo a aba ${aba}`, async () => {
    if (area && ativa) receber(await api.trocarAba(area.sessao, ativa.id, aba));
  });

  const carregarAbas = () => executar("Lendo todas as abas", async () => {
    if (area && ativa) receber(await api.carregarAbas(area.sessao, ativa.id));
  });

  const escolherTabela = (id: string) => executar("Trocando de tabela", async () => {
    if (area) receber(await api.escolherAtiva(area.sessao, id));
  });

  const removerTabela = (tabela: Tabela) => executar(`Tirando ${tabela.nome} da sessão`, async () => {
    if (area) receber(await api.removerTabela(area.sessao, tabela.id));
  });

  const aplicar = () => executar("Normalizando e analisando", async () => {
    if (!area || !ativa) return;
    const receita = ativa.sugestoes.filter((s) => selecao.has(s.id)).map((s) => s.passo);
    receber(await api.normalizar(area.sessao, ativa.id, receita, ativa.papeis));
    window.scrollTo({ top: 0 });
  });

  const trocarPapel = (coluna: string, papel: Papel) => {
    if (!area || !ativa) return;
    const papeis = { ...ativa.papeis, [coluna]: papel };
    const baseEhAtiva = area.base?.tipo === "tabela" && area.base.id === ativa.id;
    if (!ativa.relatorio || !baseEhAtiva) {
      setArea({ ...area, tabelas: area.tabelas.map((t) => (t.id === ativa.id ? { ...t, papeis } : t)) });
      return;
    }
    executar("Refazendo gráficos e insights", async () => {
      receber(await api.trocarPapeis(area.sessao, papeis));
    });
  };

  const alternar = (id: string) => {
    setSelecao((atual) => {
      const nova = new Set(atual);
      if (nova.has(id)) nova.delete(id);
      else nova.add(id);
      return nova;
    });
  };

  const novaSessao = () => {
    lembrar(CHAVE_SESSAO, null);
    setArea(null);
    setEtapa("triagem");
    setErro(null);
  };

  const liberada = (id: Etapa) => {
    if (id === "triagem") return true;
    if (!area) return false;
    if (id === "normalizacao") return Boolean(ativa?.perfil);
    return area.tabelas.some((t) => t.relatorio);
  };

  return (
    <>
      <header className="menu">
        <span className="logo"><span className="logo-marca" aria-hidden="true" />Tabula rasa</span>
        <nav className="etapas" aria-label="Etapas">
          {ETAPAS.map((item, indice) => (
            <button key={item.id} type="button" className="etapa" disabled={!liberada(item.id)}
                    aria-current={etapa === item.id ? "step" : undefined} onClick={() => setEtapa(item.id)}>
              <span className="etapa-num">{String(indice + 1).padStart(2, "0")}</span>{item.nome}
            </button>
          ))}
        </nav>
        <div className="menu-acoes">
          {area ? <button type="button" className="botao pequeno" onClick={novaSessao}>Nova sessão</button> : null}
          <button type="button" className="botao pequeno" onClick={inverter}>{tema === "light" ? "Tema escuro" : "Tema claro"}</button>
        </div>
      </header>

      {area ? (
        <div className="tabelas-barra" role="tablist" aria-label="Tabelas da sessão">
          {area.tabelas.map((tabela) => (
            <span key={tabela.id} className={`tabela-aba${tabela.id === area.ativa ? " ativa" : ""}`}>
              <button type="button" role="tab" aria-selected={tabela.id === area.ativa} disabled={Boolean(ocupado)}
                      onClick={() => escolherTabela(tabela.id)} title={tabela.titulo}>
                {tabela.nome} {tabela.relatorio ? <span aria-label="normalizada">✓</span> : null}
              </button>
              {area.tabelas.length > 1 ? (
                <button type="button" className="fechar" aria-label={`Tirar ${tabela.nome} da sessão`} disabled={Boolean(ocupado)}
                        onClick={() => removerTabela(tabela)}>×</button>
              ) : null}
            </span>
          ))}
          <label className="tabela-aba nova" htmlFor="mais-arquivo">
            + arquivo
            <input id="mais-arquivo" type="file" accept=".csv,.tsv,.txt,.xlsx,.parquet" disabled={Boolean(ocupado)}
                   onChange={(e) => { const arquivo = e.target.files?.[0]; e.target.value = ""; if (arquivo) enviar(arquivo); }} />
          </label>
        </div>
      ) : null}

      <main className={`conteudo${etapa === "sandbox" && area ? " largo" : ""}`}>
        <div className="cantos">
          <span>{String(ETAPAS.findIndex((e) => e.id === etapa) + 1).padStart(2, "0")} · {ETAPAS.find((e) => e.id === etapa)?.nome}</span>
          <span>{ativa ? `${ativa.nome} · ${ativa.arquivo.linhas.toLocaleString("pt-BR")} linhas` : "nenhum arquivo ainda"}</span>
        </div>
        {ocupado ? (
          <div className="processando" role="status">
            <span className="barra-ascii pulsar">{barraAscii(ocupado.fracao ?? 0.5)}</span>
            <span>{ocupado.texto}…</span>
          </div>
        ) : null}
        {erro ? (
          <div className="erro" role="alert">
            <strong>!</strong>
            <span>{erro}</span>
            <button type="button" className="botao pequeno" onClick={() => setErro(null)}>Fechar</button>
          </div>
        ) : null}

        {etapa === "triagem" ? (
          <>
            <BlocoArquivo sessao={area?.sessao ?? null} tabela={ativa} ocupado={Boolean(ocupado)} aoEnviar={enviar}
                          aoExemplo={exemplo} aoTrocarAba={trocarAba} aoCarregarAbas={carregarAbas} />
            {ativa?.perfil ? (
              <>
                <BlocoPerfil key={`${ativa.id}-${ativa.versao}`} tabela={ativa} ocupado={Boolean(ocupado)} aoTrocarPapel={trocarPapel} />
                <div className="acoes">
                  <button type="button" className="botao principal" onClick={() => setEtapa("normalizacao")}>Ir para a normalização →</button>
                </div>
              </>
            ) : null}
          </>
        ) : null}
        {etapa === "normalizacao" && area && ativa ? (
          <BlocoNormalizacao sessao={area.sessao} tabela={ativa} ocupado={Boolean(ocupado)} selecao={selecao}
                             aoAlternar={alternar} aoAplicar={aplicar} aoAvancar={() => setEtapa("sandbox")} />
        ) : null}
        {etapa === "sandbox" && area ? (
          <Tabua area={area} tabela={ativa} tema={tema} ocupado={Boolean(ocupado)} aoMudar={receber}
                 executar={executar} aoNormalizar={() => setEtapa("normalizacao")} />
        ) : null}
      </main>
    </>
  );
}
