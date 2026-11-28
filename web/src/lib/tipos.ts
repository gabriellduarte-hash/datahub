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
  tipo: "linha" | "barras" | "rosca" | "histograma" | "dispersao";
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
  grafico: Grafico | null;
  destaque: string | null;
  numero: string | null;
};

export type ItemResumo = { icone: string; rotulo: string; valor: string };

export type Execucao = {
  analisador: string;
  status: "ok" | "pulado" | "falhou";
  motivo: string | null;
  duracao_ms: number;
  insights: number;
};

export type Tabela = {
  id: string;
  nome: string;
  titulo: string;
  origem: "arquivo" | "consulta" | "combinada";
  blocos: EstadoBloco[];
  arquivo: Leitura;
  perfil: Perfil | null;
  sugestoes: Sugestao[];
  papeis: Record<string, Papel>;
  receita: Passo[] | null;
  manipulacao: Passo[];
  relatorio: Relatorio | null;
  perfil_limpo: Perfil | null;
  desfazer_rapido: boolean;
  versao: number;
  caminho?: string;
  /* Tabela combinada: de quais arquivos veio, em que versão de cada um e se tirou as duplicatas. */
  definicao?: { tabelas: string[]; sem_duplicatas?: boolean; versoes?: Record<string, number> } | null;
};

export type Base = { tipo: "tabela" | "visao"; id: string };

export type Analise = {
  base: Base;
  rotulo: string;
  linhas: number;
  desatualizada: boolean;
  blocos: EstadoBloco[];
  perfil_limpo: Perfil;
  unidades: Record<string, string>;
  resumo: ItemResumo[];
  graficos: Grafico[];
  insights: { insights: Insight[]; execucoes: Execucao[] } | null;
  livres?: GraficoLivre[];
  grao?: Grao;
};

/* O que cada linha da base representa: decide somar ou tirar a média (engine/grao.py). */
export type TipoGrao = "evento" | "entidade" | "foto";
export type Grao = { tipo: TipoGrao; motivo: string; escolhido: boolean };

/* Um gráfico montado pelo usuário: o pedido fica na sessão e é recalculado a cada análise. */
export type PedidoGrafico = { tipo: Grafico["tipo"]; x: string | null; y: string | null; agregacao: string | null; grao: string | null };
export type GraficoLivre = { id: string; pedido: PedidoGrafico; grafico: Grafico | null; erro: string | null };

export type PontaRelacao = { tabela: string; coluna: string };
export type Relacao = { id: string; de: PontaRelacao; para: PontaRelacao; cobertura: number };
export type Juncao = { relacao: string; tabela: string; modo: "colunas" | "resumo" };
export type Visao = { id: string; nome: string; tabela: string; juncoes: Juncao[]; linhas: number; erro: string | null };
export type Consulta = { id: string; nome: string; sql: string };

export type Area = {
  sessao: string;
  tabelas: Tabela[];
  ativa: string | null;
  base: Base | null;
  analise: Analise | null;
  relacoes: Relacao[];
  visoes: Visao[];
  consultas: Consulta[];
  ultimo_passo?: ResultadoPasso;
};

export type Filtro = { coluna: string; operador: string; valor?: string };

export type PaginaLinhas = {
  colunas: { nome: string; tipo: string }[];
  numeros: (number | null)[];
  linhas: (string | number | boolean | null)[][];
  inicio: number;
  total: number;
  filtradas: number;
};

export type Contagem = { coluna: string; termo: string; linhas: number; encontradas: number };

export type PreviaFormula = {
  sql: string;
  tipo: string;
  colunas: string[];
  previa: { linha: number | null; valor: string | number | boolean | null }[];
  linhas: number;
  vazios: number;
};

export type ResultadoSql = {
  chave: string;
  colunas: { nome: string; tipo: string }[];
  linhas: (string | number | boolean | null)[][];
  total: number;
  inicio: number;
  duracao_ms: number;
  reaproveitada: boolean;
};

export type EsquemaColuna = { nome: string; tipo: string; chave: boolean; aponta: boolean };
export type EsquemaTabela = { id: string; nome: string; titulo: string; linhas: number; normalizada: boolean; colunas: EsquemaColuna[] };
export type Esquema = {
  tabelas: EsquemaTabela[];
  relacoes: Relacao[];
  visoes: Visao[];
  sugestoes: { de: PontaRelacao; para: PontaRelacao; cobertura: number }[];
};

/* O que /api/saude conta sobre o servidor: na máquina de quem roda ou no site público. */
export type Saude = {
  ok: boolean;
  modo: "local" | "publico" | "navegador";
  horas_de_vida: number;
  limites: { max_bytes_arquivo: number | null; max_linhas: number; max_colunas: number; max_celulas: number };
};

/* Comparar os arquivos da sessão (engine/comparacao.py). */
export type SituacaoColuna = "igual" | "nome_diferente" | "tipo_diferente" | "falta";
export type ColunaComparada = {
  chave: string;
  nomes: Record<string, string | null>;
  tipos: Record<string, string | null>;
  papeis: Record<string, string | null>;
  situacao: SituacaoColuna;
  parecida_com: string | null;
};
export type ParComparado = {
  a: string;
  b: string;
  colunas: string[];
  fora: string[];
  b_em_a: number;
  a_em_b: number;
  iguais: number;
  exemplos: { linha_a: number; linha_b: number; valores: (string | null)[] }[];
  chave: { coluna: string; ambos: number; so_a: number; so_b: number } | null;
};
export type PlanoCopia = {
  origem: string;
  destino: string;
  aplicaveis: string[];
  pulados: { titulo: string; motivo: string }[];
  trocas: { de: string; para: string }[];
  aplicado: boolean;
};
export type Comparacao = {
  tabelas: { id: string; nome: string; titulo: string; linhas: number; duplicatas_dentro: number; normalizada: boolean }[];
  colunas: ColunaComparada[];
  pares: ParComparado[];
  planos: PlanoCopia[];
  deixadas_de_fora: number;
};
export type ResultadoCopia = { aplicados: number; pulados: { titulo: string; motivo: string }[]; trocas: { de: string; para: string }[] };
