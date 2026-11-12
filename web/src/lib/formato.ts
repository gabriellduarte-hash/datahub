const inteiros = new Intl.NumberFormat("pt-BR");
const decimais = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });
const reais = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export const fmtInteiro = (n: number) => inteiros.format(n);

export function fmtValor(valor: number, unidade: string | null): string {
  if (unidade === "BRL") return reais.format(valor);
  if (unidade === "%") return `${decimais.format(valor)}%`;
  return decimais.format(valor);
}

/* Eixo curto: R$ 1,2 mi em vez de R$ 1.234.567,00. */
export function fmtEixo(valor: number, unidade: string | null): string {
  if (unidade === "%") return `${decimais.format(valor)}%`;
  const absoluto = Math.abs(valor);
  const prefixo = unidade === "BRL" ? "R$ " : "";
  if (absoluto >= 1e9) return `${prefixo}${decimais.format(valor / 1e9)} bi`;
  if (absoluto >= 1e6) return `${prefixo}${decimais.format(valor / 1e6)} mi`;
  if (absoluto >= 1e3) return `${prefixo}${decimais.format(valor / 1e3)} mil`;
  return `${prefixo}${decimais.format(valor)}`;
}

export function fmtBytes(bytes: number): string {
  if (bytes >= 1024 ** 3) return `${decimais.format(bytes / 1024 ** 3)} GB`;
  if (bytes >= 1024 ** 2) return `${decimais.format(bytes / 1024 ** 2)} MB`;
  return `${decimais.format(bytes / 1024)} KB`;
}

export function fmtTempo(ms: number): string {
  return ms >= 1000 ? `${decimais.format(ms / 1000)} s` : `${ms} ms`;
}

export function barraAscii(fracao: number, largura = 18): string {
  const cheios = Math.round(Math.min(Math.max(fracao, 0), 1) * largura);
  return "▓".repeat(cheios) + "░".repeat(largura - cheios);
}
