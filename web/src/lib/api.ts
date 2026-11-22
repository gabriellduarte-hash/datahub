import { NO_NAVEGADOR, pedirAoMotor, type RespostaMotor } from "./motor";
import type {
  Area, Base, Contagem, Esquema, Filtro, Grafico, PaginaLinhas, Papel, Passo, PreviaFormula, PontaRelacao, ResultadoSql, Saude,
} from "./tipos";

export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const SEM_API = "Não consegui falar com a API. Ela está rodando na porta 8000?";

/* Erro com o status HTTP, para separar "a sessão não existe" (404) de uma falha passageira. */
export class ErroApi extends Error {
  constructor(mensagem: string, readonly status: number) {
    super(mensagem);
  }
}

function lerJson(resposta: RespostaMotor) {
  try {
    return JSON.parse(new TextDecoder().decode(resposta.corpo));
  } catch {
    return null;
  }
}

/* No site estático, o mesmo pedido vai para o motor do navegador em vez da rede. */
async function pedirNoNavegador<T>(caminho: string, opcoes?: RequestInit): Promise<T> {
  const cabecalhos = Object.entries((opcoes?.headers ?? {}) as Record<string, string>);
  const corpo = typeof opcoes?.body === "string" ? new TextEncoder().encode(opcoes.body).buffer : new ArrayBuffer(0);
  const resposta = await pedirAoMotor(opcoes?.method ?? "GET", caminho, cabecalhos, corpo);
  const dados = lerJson(resposta);
  if (resposta.status >= 400) throw new ErroApi(dados?.erro ?? "O motor devolveu um erro inesperado.", resposta.status);
  return dados as T;
}

async function pedir<T>(caminho: string, opcoes?: RequestInit): Promise<T> {
  if (NO_NAVEGADOR) return pedirNoNavegador<T>(caminho, opcoes);
  let resposta: Response;
  try {
    resposta = await fetch(`${API}${caminho}`, opcoes);
  } catch {
    throw new Error(SEM_API);
  }
  const dados = await resposta.json().catch(() => null);
  if (!resposta.ok) throw new ErroApi(dados?.erro ?? "A API devolveu um erro inesperado.", resposta.status);
  return dados as T;
}

function enviarJson<T>(caminho: string, corpo: unknown, metodo = "POST"): Promise<T> {
  return pedir<T>(caminho, {
    method: metodo,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(corpo),
  });
}

/* No navegador, o FormData vira o mesmo corpo multipart que a rede levaria. */
async function enviarFormularioNoNavegador(caminho: string, formulario: FormData, aoProgredir: (fracao: number) => void): Promise<Area> {
  const montado = new Request("http://motor.local", { method: "POST", body: formulario });
  const corpo = await montado.arrayBuffer();
  aoProgredir(1);
  const resposta = await pedirAoMotor("POST", caminho, [["content-type", montado.headers.get("content-type") ?? ""]], corpo);
  const dados = lerJson(resposta);
  if (resposta.status >= 400) throw new ErroApi(dados?.erro ?? "O envio falhou.", resposta.status);
  return dados as Area;
}

/* XMLHttpRequest porque o fetch não informa o progresso do envio. */
function enviarFormulario(caminho: string, arquivo: File, aoProgredir: (fracao: number) => void): Promise<Area> {
  const formulario = new FormData();
  formulario.append("arquivo", arquivo);
  if (NO_NAVEGADOR) return enviarFormularioNoNavegador(caminho, formulario, aoProgredir);
  return new Promise((resolver, rejeitar) => {
    const pedido = new XMLHttpRequest();
    pedido.upload.onprogress = (evento) => {
      if (evento.lengthComputable) aoProgredir(evento.loaded / evento.total);
    };
    pedido.onerror = () => rejeitar(new Error(SEM_API));
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

/* Baixar um arquivo da API. Com servidor, um link comum resolve (veja Baixar.tsx);
   no navegador, o arquivo vem do motor e vira um download local. */
export function enderecoDownload(caminho: string) {
  return `${API}${caminho}`;
}

export async function baixarDoMotor(caminho: string): Promise<void> {
  const resposta = await pedirAoMotor("GET", caminho, [], new ArrayBuffer(0));
  if (resposta.status >= 400) throw new ErroApi(lerJson(resposta)?.erro ?? "O download falhou.", resposta.status);
  const cabecalho = (nome: string) => resposta.cabecalhos.find(([chave]) => chave === nome)?.[1] ?? "";
  const disposicao = cabecalho("content-disposition");
  const nome = decodeURIComponent(/filename\*=UTF-8''([^;]+)/i.exec(disposicao)?.[1] ?? /filename="?([^";]+)"?/i.exec(disposicao)?.[1] ?? "datahub");
  const endereco = URL.createObjectURL(new Blob([resposta.corpo], { type: cabecalho("content-type") }));
  const link = Object.assign(document.createElement("a"), { href: endereco, download: nome });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(endereco), 10_000);
}

const s = (sessao: string) => `/api/sessoes/${sessao}`;
const t = (sessao: string, tabela: string) => `${s(sessao)}/tabelas/${tabela}`;

// Sessão e arquivos
export const enviarArquivo = (arquivo: File, aoProgredir: (fracao: number) => void) =>
  enviarFormulario("/api/sessoes", arquivo, aoProgredir);
export const adicionarArquivo = (sessao: string, arquivo: File, aoProgredir: (fracao: number) => void) =>
  enviarFormulario(`${s(sessao)}/tabelas`, arquivo, aoProgredir);
export const saude = () => pedir<Saude>("/api/saude");
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
export const caminhoConsulta = (sessao: string, id: string) => `${s(sessao)}/consultas/${id}/arquivo`;

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

export const caminhoExportacao = (sessao: string, tabela: string, formato: string) =>
  `${t(sessao, tabela)}/exportar/${formato}`;
