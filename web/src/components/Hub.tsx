"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { BlocoArquivo } from "@/components/blocos/BlocoArquivo";
import { BlocoGraficos } from "@/components/blocos/BlocoGraficos";
import { BlocoInsights } from "@/components/blocos/BlocoInsights";
import { BlocoNormalizacao } from "@/components/blocos/BlocoNormalizacao";
import { BlocoPerfil } from "@/components/blocos/BlocoPerfil";
import * as api from "@/lib/api";
import { barraAscii, fmtTempo } from "@/lib/formato";
import type { Estado, NomeBloco, Papel } from "@/lib/tipos";

const BLOCOS: { id: NomeBloco; nome: string }[] = [
  { id: "arquivo", nome: "Arquivo" },
  { id: "perfil", nome: "Perfil" },
  { id: "normalizacao", nome: "Normalização" },
  { id: "graficos", nome: "Gráficos" },
  { id: "insights", nome: "Insights" },
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

export function Hub() {
  const [estado, setEstado] = useState<Estado | null>(null);
  const [bloco, setBloco] = useState<NomeBloco>("arquivo");
  const [selecao, setSelecao] = useState<Set<string>>(new Set());
  const [ocupado, setOcupado] = useState<{ texto: string; fracao?: number } | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [tema, setTema] = useState("sistema");

  const receber = useCallback((novo: Estado, manterSelecao = false) => {
    setEstado(novo);
    lembrar(CHAVE_SESSAO, novo.sessao);
    if (!manterSelecao) {
      const aplicados = new Set((novo.receita ?? []).map((passo) => JSON.stringify(passo)));
      setSelecao(new Set(novo.sugestoes
        .filter((s) => (novo.receita ? aplicados.has(JSON.stringify(s.passo)) : s.marcada))
        .map((s) => s.id)));
    }
  }, []);

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
    const salvo = lembrado(CHAVE_TEMA);
    if (salvo) {
      setTema(salvo);
      document.documentElement.dataset.theme = salvo;
    }
    const sessao = lembrado(CHAVE_SESSAO);
    if (!sessao) return;
    api.verSessao(sessao)
      .then((resposta) => {
        receber(resposta);
        setBloco(resposta.relatorio ? "normalizacao" : "perfil");
      })
      .catch(() => lembrar(CHAVE_SESSAO, null));
  }, [receber]);

  const inverter = () => {
    const escuro = tema === "dark" || (tema === "sistema" && matchMedia("(prefers-color-scheme: dark)").matches);
    const novo = escuro ? "light" : "dark";
    setTema(novo);
    document.documentElement.dataset.theme = novo;
    lembrar(CHAVE_TEMA, novo);
  };

  const enviar = (arquivo: File) => executar(`Enviando ${arquivo.name}`, async () => {
    const resposta = await api.enviarArquivo(arquivo, (fracao) => {
      setOcupado(fracao < 1
        ? { texto: `Enviando ${arquivo.name}`, fracao }
        : { texto: "Lendo o arquivo e montando o perfil" });
    });
    receber(resposta);
    setBloco("perfil");
  });

  const exemplo = () => executar("Gerando as vendas de exemplo", async () => {
    receber(await api.abrirExemplo());
    setBloco("perfil");
  });

  const trocarAba = (aba: string) => executar(`Lendo a aba ${aba}`, async () => {
    if (estado) receber(await api.trocarAba(estado.sessao, aba));
  });

  const aplicar = () => executar("Normalizando e gerando gráficos e insights", async () => {
    if (!estado) return;
    const receita = estado.sugestoes.filter((s) => selecao.has(s.id)).map((s) => s.passo);
    receber(await api.normalizar(estado.sessao, receita, estado.papeis), true);
    window.scrollTo({ top: 0 });
  });

  const trocarPapel = (coluna: string, papel: Papel) => {
    if (!estado) return;
    const papeis = { ...estado.papeis, [coluna]: papel };
    if (!estado.relatorio) {
      setEstado({ ...estado, papeis });
      return;
    }
    executar("Refazendo gráficos e insights", async () => {
      receber(await api.trocarPapeis(estado.sessao, papeis), true);
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

  const novoArquivo = () => {
    lembrar(CHAVE_SESSAO, null);
    setEstado(null);
    setBloco("arquivo");
    setErro(null);
  };

  const situacao = useMemo(() => Object.fromEntries((estado?.blocos ?? []).map((b) => [b.bloco, b])), [estado]);
  const liberado = (id: NomeBloco) => {
    if (id === "arquivo") return true;
    if (!estado) return false;
    if (id === "perfil" || id === "normalizacao") return Boolean(estado.perfil);
    return Boolean(estado.relatorio);
  };

  return (
    <>
      <header className="menu">
        <span className="logo">datahub</span>
        {estado ? <span className="menu-arquivo">{estado.arquivo.arquivo}</span> : null}
        <div className="menu-acoes">
          {estado ? <button type="button" className="botao pequeno" onClick={novoArquivo}>Novo arquivo</button> : null}
          <button type="button" className="botao pequeno" onClick={inverter}>Inverter</button>
        </div>
      </header>

      <div className="mesa">
        <nav aria-label="Blocos">
          <ol className="trilha">
            {BLOCOS.map((item, indice) => {
              const info = situacao[item.id];
              const sinal = info?.status === "ok" ? "✓" : info?.status === "falhou" ? "×" : "";
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    className="bloco-item"
                    disabled={!liberado(item.id)}
                    aria-current={bloco === item.id ? "step" : undefined}
                    onClick={() => setBloco(item.id)}
                  >
                    <span className="bloco-num">{indice + 1}</span>
                    <span>
                      <span className="bloco-nome">{item.nome}</span>
                      {info?.status === "ok" ? <span className="bloco-tempo">{fmtTempo(info.duracao_ms)}</span> : null}
                      {info?.status === "falhou" ? <span className="bloco-tempo">falhou</span> : null}
                    </span>
                    <span className="bloco-sinal" aria-hidden="true">{sinal}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          {estado && !estado.relatorio ? (
            <p className="trilha-nota">Gráficos e insights abrem depois que você aplicar a normalização.</p>
          ) : null}
        </nav>

        <main className="conteudo">
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
          {situacao[bloco]?.status === "falhou" ? (
            <div className="erro" role="alert">
              <strong>!</strong>
              <span>Este bloco falhou: {situacao[bloco].erro}</span>
            </div>
          ) : null}

          {bloco === "arquivo" ? (
            <BlocoArquivo estado={estado} ocupado={Boolean(ocupado)} aoEnviar={enviar} aoExemplo={exemplo} aoTrocarAba={trocarAba} />
          ) : null}
          {bloco === "perfil" && estado?.perfil ? (
            <BlocoPerfil key={estado.relatorio?.sql ?? "bruto"} estado={estado} ocupado={Boolean(ocupado)} aoTrocarPapel={trocarPapel} />
          ) : null}
          {bloco === "normalizacao" && estado ? (
            <BlocoNormalizacao estado={estado} ocupado={Boolean(ocupado)} selecao={selecao} aoAlternar={alternar} aoAplicar={aplicar} aoAvancar={() => setBloco("graficos")} />
          ) : null}
          {bloco === "graficos" && estado ? (
            <BlocoGraficos estado={estado} tema={tema} aoAvancar={() => setBloco("insights")} />
          ) : null}
          {bloco === "insights" && estado ? <BlocoInsights estado={estado} /> : null}
        </main>
      </div>
    </>
  );
}
