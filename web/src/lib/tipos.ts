export type NomeBloco = "arquivo" | "perfil" | "normalizacao" | "graficos" | "insights";

export type EstadoBloco = {
  bloco: NomeBloco;
  status: "ok" | "falhou" | "pendente";
  duracao_ms: number;
  erro: string | null;
};

export type Leitura = {
  arquivo: string;
  formato: string;
  bytes: number;
  linhas: number;
  colunas: number;
  separador: string | null;
  codificacao: string | null;
  aba: string | null;
  abas: string[];
  avisos: string[];
};

export type Papel = "metrica" | "categoria" | "data" | "id" | "indicador" | "texto" | "ignorar";

export type PerfilColuna = {
  posicao: number;
  nome: string;
  tipo_duckdb: string;
  nulos: number;
  vazios_disfarcados: number;
  distintos: number;
  vazio: string | null;
  sugerir_remover: boolean;
  tipo: string;
  papel: Papel;
  agregacao: string;
  com_espacos: number;
  misto: boolean;
  exemplos: string[];
};

export type Perfil = {
  linhas: number;
  linhas_vazias: number;
  linhas_duplicadas: number;
  colunas: PerfilColuna[];
};

export type Passo = Record<string, unknown> & { op: string };

export type Sugestao = {
  id: string;
  grupo: string;
  titulo: string;
  motivo: string;
  passo: Passo;
  marcada: boolean;
  afetados: number;
  perdidos: number;
  exemplos: (string | null)[][];
};

export type ResultadoPasso = {
  indice: number;
  titulo: string;
  op: string;
  status: "ok" | "falhou";
  linhas_antes: number;
  linhas_depois: number;
  alterados: number;
  perdidos: number;
  erro: string | null;
};

export type Relatorio = {
  linhas_antes: number;
  linhas_depois: number;
  colunas_antes: number;
  colunas_depois: number;
  passos: ResultadoPasso[];
  sql: string;
};

export type PontoGrafico = {
  x: string | number;
  y: number;
  rotulo?: string;
  fim?: string;
  n?: number;
  incompleto?: boolean;
  outros?: boolean;
};

export type Grafico = {
  id: string;
  titulo: string;
  tipo: "linha" | "barras" | "histograma" | "dispersao";
  x: string | null;
  y: string | null;
  agregacao: string;
  grao: string | null;
  unidade: string | null;
  dados: PontoGrafico[];
  aviso: string | null;
};

export type Insight = {
  tipo: string;
  titulo: string;
  texto: string;
  score: number;
  evidencias: { rotulo: string; valor: string }[];
  ressalvas: string[];
  colunas: string[];
};

export type Execucao = {
  analisador: string;
  status: "ok" | "pulado" | "falhou";
  motivo: string | null;
  duracao_ms: number;
  insights: number;
};

export type Estado = {
  sessao: string;
  blocos: EstadoBloco[];
  arquivo: Leitura;
  perfil: Perfil | null;
  sugestoes: Sugestao[];
  papeis: Record<string, Papel>;
  receita?: Passo[];
  relatorio?: Relatorio | null;
  perfil_limpo?: Perfil | null;
  graficos?: Grafico[];
  insights?: { insights: Insight[]; execucoes: Execucao[] } | null;
};

export type PaginaLinhas = {
  colunas: { nome: string; tipo: string }[];
  linhas: (string | number | boolean | null)[][];
  total: number;
  pagina: number;
  por_pagina: number;
};
