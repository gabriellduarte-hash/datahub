/* O motor do datahub dentro do navegador, num Web Worker.
 *
 * Carrega o Pyodide (o Python compilado para WebAssembly), a DuckDB e o código
 * do datahub, e atende os pedidos da interface chamando a mesma API FastAPI do
 * servidor, sem rede (navegador/ponte.py). Os arquivos do usuário ficam na
 * memória desta aba e nunca saem do computador.
 *
 * Mensagens: { tipo: "iniciar" } e { tipo: "pedido", id, metodo, caminho, cabecalhos, corpo }.
 * Respostas: { tipo: "progresso", etapa }, { tipo: "pronto" } e { tipo: "resposta", id, status, cabecalhos, corpo }.
 */
// Worker do tipo módulo: o Pyodide 314 só publica o núcleo como módulo ES.
import { loadPyodide } from "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/pyodide.mjs";

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";

let motor = null;              // promessa da ponte Python, criada uma vez
let fila = Promise.resolve();  // um pedido por vez: o motor tem uma thread só

const avisar = (etapa) => postMessage({ tipo: "progresso", etapa });

async function iniciar() {
  avisar("Carregando o Python do navegador");
  const py = await loadPyodide({ indexURL: PYODIDE });
  avisar("Carregando a DuckDB e as bibliotecas");
  await py.loadPackage(["micropip", "duckdb", "pydantic", "anyio", "fastapi", "starlette"], { messageCallback: () => {} });
  avisar("Carregando o motor do datahub");
  const aqui = new URL("./", self.location.href);
  const manifesto = await (await fetch(new URL("manifesto.json", aqui), { cache: "no-cache" })).json();
  const codigo = await (await fetch(new URL(manifesto.codigo, aqui))).arrayBuffer();
  py.unpackArchive(codigo, "zip", { extractDir: "/datahub" });
  py.globals.set("rodas", py.toPy(manifesto.rodas.map((roda) => new URL(roda, aqui).href)));
  await py.runPythonAsync(`
import sys, micropip
await micropip.install(list(rodas), deps=False)
sys.path[:0] = ["/datahub", "/datahub/navegador"]
import ponte
`);
  postMessage({ tipo: "pronto" });
  return py.pyimport("ponte");
}

function garantir() {
  motor ??= iniciar().catch((erro) => {
    motor = null; // na próxima tentativa, recomeça
    throw erro;
  });
  return motor;
}

async function atender({ id, metodo, caminho, cabecalhos, corpo }) {
  try {
    const ponte = await garantir();
    const proxy = await ponte.pedir(metodo, caminho, cabecalhos, new Uint8Array(corpo ?? new ArrayBuffer(0)));
    const resposta = proxy.toJs({ dict_converter: Object.fromEntries });
    proxy.destroy();
    const bytes = resposta.corpo.slice().buffer;
    postMessage({ tipo: "resposta", id, status: resposta.status, cabecalhos: resposta.cabecalhos, corpo: bytes }, [bytes]);
  } catch (erro) {
    const texto = JSON.stringify({ erro: `O motor do navegador falhou: ${String(erro?.message ?? erro).slice(0, 300)}` });
    const bytes = new TextEncoder().encode(texto).buffer;
    postMessage({ tipo: "resposta", id, status: 500, cabecalhos: [["content-type", "application/json"]], corpo: bytes }, [bytes]);
  }
}

self.onmessage = ({ data }) => {
  if (data.tipo === "iniciar") garantir().catch((erro) => avisar(`Falhou: ${erro?.message ?? erro}`));
  if (data.tipo === "pedido") fila = fila.then(() => atender(data));
};
