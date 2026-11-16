import type {
  Area, Base, Contagem, Esquema, Filtro, Grafico, PaginaLinhas, Papel, Passo, PreviaFormula, PontaRelacao, ResultadoSql,
} from "./tipos";

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

function enviarJson<T>(caminho: string, corpo: unknown, metodo = "POST"): Promise<T> {
  return pedir<T>(caminho, {
    method: metodo,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corpo),
  });
}

/* XMLHttpRequest porque o fetch não informa o progresso do envio. */
function enviarFormulario(caminho: string, arquivo: File, aoProgredir: (fracao: number) => void): Promise<Area> {
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
      if (pedido.status >= 200 && pedido.status < 300) resolver(dados as Area);
      else rejeitar(new Error(dados?.erro ?? "O envio falhou."));
    };
    pedido.open("POST", `${API}${caminho}`);
    pedido.send(formulario);
  });
}

const s = (sessao: string) => `/api/sessoes/${sessao}`;
const t = (sessao: string, tabela: string) => `${s(sessao)}/tabelas/${tabela}`;

// Sessão e arquivos
export const enviarArquivo = (arquivo: File, aoProgredir: (fracao: number) => void) =>
  enviarFormulario("/api/sessoes", arquivo, aoProgredir);
export const adicionarArquivo = (sessao: string, arquivo: File, aoProgredir: (fracao: number) => void) =>
  enviarFormulario(`${s(sessao)}/tabelas`, arquivo, aoProgredir);
export const abrirExemplo = () => pedir<Area>("/api/sessoes/exemplo", { method: "POST" });
export const verSessao = (sessao: string) => pedir<Area>(s(sessao));
export const escolherAtiva = (sessao: string, tabela: string) => enviarJson<Area>(`${s(sessao)}/ativa`, { tabela });
export const renomearTabela = (sessao: string, tabela: string, nome: string) =>
  enviarJson<Area>(`${t(sessao, tabela)}/nome`, { nome });
export const removerTabela = (sessao: string, tabela: string) => pedir<Area>(t(sessao, tabela), { method: "DELETE" });
export const trocarAba = (sessao: string, tabela: string, aba: string) => enviarJson<Area>(`${t(sessao, tabela)}/aba`, { aba });
export const carregarAbas = (sessao: string, tabela: string) => pedir<Area>(`${t(sessao, tabela)}/abas`, { method: "POST" });

// Normalização e sandbox
export const normalizar = (sessao: string, tabela: string, receita: Passo[], papeis: Record<string, Papel>) =>
  enviarJson<Area>(`${t(sessao, tabela)}/normalizar`, { receita, papeis });
export const acrescentarPasso = (sessao: string, tabela: string, passo: Passo) =>
  enviarJson<Area>(`${t(sessao, tabela)}/passos`, { passo });
export const desfazer = (sessao: string, tabela: string) => pedir<Area>(`${t(sessao, tabela)}/desfazer`, { method: "POST" });
export const previaFormula = (sessao: string, tabela: string, nome: string, formula: string, substituir: boolean) =>
  enviarJson<PreviaFormula>(`${t(sessao, tabela)}/formula`, { nome, formula, substituir });

export function verLinhas(sessao: string, tabela: string, inicio: number, quantas: number,
                          ordem: { coluna: string | null; decrescente: boolean }, filtros: Filtro[], bruta = false) {
  const busca = new URLSearchParams({ inicio: String(inicio), quantas: String(quantas), decrescente: String(ordem.decrescente) });
  if (ordem.coluna) busca.set("ordenar", ordem.coluna);
  if (filtros.length) busca.set("filtros", JSON.stringify(filtros));
  if (bruta) busca.set("bruta", "true");
  return pedir<PaginaLinhas>(`${t(sessao, tabela)}/linhas?${busca}`);
}

export function contarTermo(sessao: string, tabela: string, coluna: string, termo: string, inteiro: boolean, filtros: Filtro[]) {
  const busca = new URLSearchParams({ coluna, termo, inteiro: String(inteiro) });
  if (filtros.length) busca.set("filtros", JSON.stringify(filtros));
  return pedir<Contagem>(`${t(sessao, tabela)}/contar?${busca}`);
}

// Base de análise
export const escolherBase = (sessao: string, base: Base) => enviarJson<Area>(`${s(sessao)}/base`, base);
export const reanalisar = (sessao: string) => pedir<Area>(`${s(sessao)}/reanalisar`, { method: "POST" });
export const trocarPapeis = (sessao: string, papeis: Record<string, Papel>) => enviarJson<Area>(`${s(sessao)}/papeis`, { papeis });
export const calcularGrafico = (sessao: string, pedido: Partial<Grafico>) => enviarJson<Grafico>(`${s(sessao)}/grafico`, pedido);

// SQL
export const rodarSql = (sessao: string, sql: string, inicio: number, quantas: number) =>
  enviarJson<ResultadoSql>(`${s(sessao)}/sql`, { sql, inicio, quantas });
export const salvarSqlComoTabela = (sessao: string, sql: string, nome: string) =>
  enviarJson<Area>(`${s(sessao)}/sql/tabela`, { sql, nome });
export const salvarConsulta = (sessao: string, nome: string, sql: string, id?: string) =>
  enviarJson<Area>(`${s(sessao)}/consultas`, { nome, sql, id });
export const apagarConsulta = (sessao: string, id: string) => pedir<Area>(`${s(sessao)}/consultas/${id}`, { method: "DELETE" });
export const linkConsulta = (sessao: string, id: string) => `${API}${s(sessao)}/consultas/${id}/arquivo`;

// Várias tabelas
export const verEsquema = (sessao: string) => pedir<Esquema>(`${s(sessao)}/esquema`);
export const criarRelacao = (sessao: string, de: PontaRelacao, para: PontaRelacao) =>
  enviarJson<Area>(`${s(sessao)}/relacoes`, { de, para });
export const apagarRelacao = (sessao: string, id: string) => pedir<Area>(`${s(sessao)}/relacoes/${id}`, { method: "DELETE" });
export const criarVisao = (sessao: string, nome: string, tabela: string, juncoes: { relacao: string; modo: string }[]) =>
  enviarJson<Area>(`${s(sessao)}/visoes`, { nome, tabela, juncoes });
export const apagarVisao = (sessao: string, id: string) => pedir<Area>(`${s(sessao)}/visoes/${id}`, { method: "DELETE" });
export const empilhar = (sessao: string, tabelas: string[], nome: string) =>
  enviarJson<Area>(`${s(sessao)}/empilhar`, { tabelas, nome });

export const linkExportacao = (sessao: string, tabela: string, formato: string) =>
  `${API}${t(sessao, tabela)}/exportar/${formato}`;
