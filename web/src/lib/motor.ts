/* O motor do navegador visto pela interface: um Web Worker (public/motor/motor.js)
   que roda o Python do datahub. Só é usado no build do site estático
   (NEXT_PUBLIC_MOTOR=navegador); no resto, a interface chama a API por rede. */

export const NO_NAVEGADOR = process.env.NEXT_PUBLIC_MOTOR === "navegador";
const BASE = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

export type RespostaMotor = { status: number; cabecalhos: [string, string][]; corpo: ArrayBuffer };
type Mensagem =
  | { tipo: "progresso"; etapa: string }
  | { tipo: "pronto" }
  | ({ tipo: "resposta"; id: number } & RespostaMotor);

let trabalhador: Worker | null = null;
let proximoId = 0;
const esperando = new Map<number, (resposta: RespostaMotor) => void>();
const ouvintes = new Set<(etapa: string | null) => void>();
let etapa: string | null = "Preparando o motor";

function avisar(nova: string | null) {
  etapa = nova;
  ouvintes.forEach((ouvinte) => ouvinte(nova));
}

function iniciar(): Worker {
  if (trabalhador) return trabalhador;
  trabalhador = new Worker(`${BASE}/motor/motor.js`, { type: "module" });
  trabalhador.onmessage = ({ data }: MessageEvent<Mensagem>) => {
    if (data.tipo === "progresso") avisar(data.etapa);
    if (data.tipo === "pronto") avisar(null);
    if (data.tipo === "resposta") {
      esperando.get(data.id)?.(data);
      esperando.delete(data.id);
    }
  };
  trabalhador.postMessage({ tipo: "iniciar" });
  return trabalhador;
}

/* Começa a baixar o motor antes do primeiro pedido: na primeira visita leva alguns segundos. */
export function aquecerMotor() {
  if (NO_NAVEGADOR) iniciar();
}

/* Um pedido HTTP feito ao motor, sem rede. */
export function pedirAoMotor(metodo: string, caminho: string, cabecalhos: [string, string][], corpo: ArrayBuffer): Promise<RespostaMotor> {
  const id = proximoId++;
  return new Promise((resolver) => {
    esperando.set(id, resolver);
    iniciar().postMessage({ tipo: "pedido", id, metodo, caminho, cabecalhos, corpo }, [corpo]);
  });
}

/* Avisa a cada etapa do carregamento; null quando o motor está pronto. Devolve a função que para de ouvir. */
export function acompanharMotor(ouvinte: (etapa: string | null) => void): () => void {
  ouvintes.add(ouvinte);
  ouvinte(etapa);
  return () => ouvintes.delete(ouvinte);
}
