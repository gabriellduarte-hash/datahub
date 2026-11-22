// O motor do datahub dentro do Pyodide (o Python do navegador), rodando no Node:
//   1. os testes do motor, com a DuckDB que o Pyodide traz;
//   2. o caminho da interface no site estático: a API inteira pela ponte.
//
//   cd navegador && npm ci && node testar.mjs
import { fileURLToPath } from "node:url";
import { loadPyodide } from "pyodide";

const RAIZ = fileURLToPath(new URL("..", import.meta.url));
const py = await loadPyodide();
// Os mesmos pacotes que o site carrega (web/public/motor/motor.js), mais o pytest.
await py.loadPackage(["micropip", "duckdb", "pydantic", "anyio", "fastapi", "starlette", "pytest"], { messageCallback: () => {} });
py.FS.mkdirTree("/datahub");
py.FS.mount(py.FS.filesystems.NODEFS, { root: RAIZ }, "/datahub");

const codigo = await py.runPythonAsync(`
import sys
sys.dont_write_bytecode = True
import micropip
await micropip.install(["python-multipart==0.0.33", "openpyxl==3.1.5"])
sys.path[:0] = ["/datahub", "/datahub/navegador"]
import duckdb, pytest
print("Pyodide com DuckDB", duckdb.__version__)
# test_api e test_publico usam o TestClient, que abre uma thread; a API é testada pela ponte, abaixo.
pytest.main(["-q", "-p", "no:cacheprovider", "-o", "addopts=", "/datahub/tests",
             "--ignore=/datahub/tests/test_api.py", "--ignore=/datahub/tests/test_publico.py"])
`);
if (codigo !== 0) process.exit(1);

const ponte = py.pyimport("ponte");
async function pedir(metodo, caminho, json) {
  const corpo = json === undefined ? new Uint8Array() : new TextEncoder().encode(JSON.stringify(json));
  const proxy = await ponte.pedir(metodo, caminho, json === undefined ? [] : [["content-type", "application/json"]], corpo);
  const resposta = proxy.toJs({ dict_converter: Object.fromEntries });
  proxy.destroy();
  const texto = new TextDecoder().decode(resposta.corpo);
  if (resposta.status >= 400) throw new Error(`${metodo} ${caminho} → ${resposta.status}: ${texto.slice(0, 300)}`);
  console.log(`ok: ${metodo} ${caminho.replace(/sessoes\/[^/]+/, "sessoes/…")}`);
  return texto.startsWith("{") ? JSON.parse(texto) : texto;
}

let area = await pedir("POST", "/api/sessoes/exemplo");
const sessao = area.sessao, tabela = area.tabelas[0];
area = await pedir("POST", `/api/sessoes/${sessao}/tabelas/${tabela.id}/normalizar`,
  { receita: tabela.sugestoes.filter((s) => s.marcada).map((s) => s.passo), papeis: {} });
if (!area.analise.insights.insights.length || !area.analise.insights.insights[0].grafico) throw new Error("análise sem insights com gráfico");
await pedir("GET", `/api/sessoes/${sessao}/tabelas/${tabela.id}/linhas?inicio=0&quantas=20`);
await pedir("POST", `/api/sessoes/${sessao}/tabelas/${tabela.id}/passos`, { passo: { op: "remover_linhas", linhas: [1, 2] } });
await pedir("POST", `/api/sessoes/${sessao}/tabelas/${tabela.id}/formula`, { nome: "dobro", formula: "quantidade * 2" });
await pedir("POST", `/api/sessoes/${sessao}/sql`, { sql: "SELECT categoria, count(*) FROM vendas_sujas GROUP BY 1" });
const csv = await pedir("GET", `/api/sessoes/${sessao}/tabelas/${tabela.id}/exportar/csv`);
if (csv.length < 1000) throw new Error("CSV exportado vazio");
console.log("A API inteira respondeu pela ponte do navegador.");
