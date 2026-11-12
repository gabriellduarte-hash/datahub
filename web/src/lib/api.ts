import type { Estado, Grafico, PaginaLinhas, Papel, Passo } from "./tipos";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function pedir<T>(caminho: string, opcoes?: RequestInit): Promise<T> {
  let resposta: Response;
  try {
    resposta = await fetch(`${API}${caminho}`, opcoes);
  } catch {
    throw new Error("Não consegui falar com a API. Ela está rodando na porta 8000?");
  }
  const dados = await resposta.json().catch(() => null);
  if (!resposta.ok) throw new Error(dados?.erro ?? "A API devolveu um erro inesperado.");
  return dados as T;
}

function enviarJson<T>(caminho: string, corpo: unknown): Promise<T> {
  return pedir<T>(caminho, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corpo),
  });
}

/* XMLHttpRequest porque o fetch não informa o progresso do envio. */
export function enviarArquivo(arquivo: File, aoProgredir: (fracao: number) => void): Promise<Estado> {
  return new Promise((resolver, rejeitar) => {
    const pedido = new XMLHttpRequest();
    const formulario = new FormData();
    formulario.append("arquivo", arquivo);
    pedido.upload.onprogress = (evento) => {
      if (evento.lengthComputable) aoProgredir(evento.loaded / evento.total);
    };
    pedido.onerror = () => rejeitar(new Error("Não consegui falar com a API. Ela está rodando na porta 8000?"));
    pedido.onload = () => {
      let dados = null;
      try {
        dados = JSON.parse(pedido.responseText);
      } catch {}
      if (pedido.status >= 200 && pedido.status < 300) resolver(dados as Estado);
      else rejeitar(new Error(dados?.erro ?? "O envio falhou."));
    };
    pedido.open("POST", `${API}/api/sessoes`);
    pedido.send(formulario);
  });
}

export const abrirExemplo = () => pedir<Estado>("/api/sessoes/exemplo", { method: "POST" });
export const verSessao = (sessao: string) => pedir<Estado>(`/api/sessoes/${sessao}`);
export const trocarAba = (sessao: string, aba: string) => enviarJson<Estado>(`/api/sessoes/${sessao}/aba`, { aba });
export const normalizar = (sessao: string, receita: Passo[], papeis: Record<string, Papel>) =>
  enviarJson<Estado>(`/api/sessoes/${sessao}/normalizar`, { receita, papeis });
export const trocarPapeis = (sessao: string, papeis: Record<string, Papel>) =>
  enviarJson<Estado>(`/api/sessoes/${sessao}/papeis`, { papeis });
export const calcularGrafico = (sessao: string, pedido: Partial<Grafico>) =>
  enviarJson<Grafico>(`/api/sessoes/${sessao}/grafico`, pedido);

export function verLinhas(sessao: string, tabela: string, pagina: number, ordenar: string | null, decrescente: boolean) {
  const busca = new URLSearchParams({ tabela, pagina: String(pagina), por_pagina: "25", decrescente: String(decrescente) });
  if (ordenar) busca.set("ordenar", ordenar);
  return pedir<PaginaLinhas>(`/api/sessoes/${sessao}/linhas?${busca}`);
}

export const linkExportacao = (sessao: string, formato: string) => `${API}/api/sessoes/${sessao}/exportar/${formato}`;
