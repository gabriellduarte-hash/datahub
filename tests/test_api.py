"""A API de ponta a ponta: do envio do arquivo à visão que junta tabelas."""

import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from api import main, sessoes


@pytest.fixture(scope="module")
def cliente(tmp_path_factory):
    sessoes.PASTA = tmp_path_factory.mktemp("sessoes")
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def aberto(cliente):
    resposta = cliente.post("/api/sessoes/exemplo")
    assert resposta.status_code == 200
    return resposta.json()


@pytest.fixture(scope="module")
def normalizado(cliente, aberto):
    tabela = aberto["tabelas"][0]
    receita = [s["passo"] for s in tabela["sugestoes"] if s["marcada"]]
    resposta = cliente.post(f"/api/sessoes/{aberto['sessao']}/tabelas/{tabela['id']}/normalizar",
                            json={"receita": receita})
    assert resposta.status_code == 200
    return resposta.json()


def rota(estado, resto=""):
    return f"/api/sessoes/{estado['sessao']}{resto}"


def rota_tabela(estado, resto=""):
    return rota(estado, f"/tabelas/{estado['tabelas'][0]['id']}{resto}")


def test_abrir_arquivo_roda_os_tres_primeiros_blocos(aberto):
    tabela = aberto["tabelas"][0]
    status = {b["bloco"]: b["status"] for b in tabela["blocos"]}
    assert status == {"arquivo": "ok", "perfil": "ok", "normalizacao": "ok",
                      "graficos": "pendente", "insights": "pendente"}
    assert tabela["arquivo"]["linhas"] == 5055 and tabela["nome"] == "vendas_sujas"
    assert aberto["ativa"] == tabela["id"] and aberto["base"] is None


def test_normalizar_escolhe_a_base_e_analisa(normalizado):
    tabela = normalizado["tabelas"][0]
    assert tabela["relatorio"]["linhas_depois"] == 5000
    assert normalizado["base"] == {"tipo": "tabela", "id": tabela["id"]}
    analise = normalizado["analise"]
    assert {b["status"] for b in analise["blocos"]} == {"ok"}
    assert analise["graficos"] and analise["insights"]["insights"] and not analise["desatualizada"]


def test_paginas_com_numero_da_linha_filtro_e_ordem(cliente, normalizado):
    filtros = json.dumps([{"coluna": "uf", "operador": "igual", "valor": "SP"}])
    resposta = cliente.get(rota_tabela(normalizado, "/linhas"), params={
        "inicio": 10, "quantas": 10, "ordenar": "valor_total", "decrescente": True, "filtros": filtros}).json()
    assert len(resposta["linhas"]) == 10 and resposta["total"] == 5000
    assert 0 < resposta["filtradas"] < 5000 and len(resposta["numeros"]) == 10
    uf = [c["nome"] for c in resposta["colunas"]].index("uf")
    assert {linha[uf] for linha in resposta["linhas"]} == {"SP"}


def test_ordenar_por_coluna_inexistente_e_recusado(cliente, normalizado):
    resposta = cliente.get(rota_tabela(normalizado, "/linhas"), params={"ordenar": "1; DROP TABLE x"})
    assert resposta.status_code == 422


def test_contar_um_termo(cliente, normalizado):
    resposta = cliente.get(rota_tabela(normalizado, "/contar"), params={"coluna": "uf", "termo": "SP", "inteiro": True})
    assert resposta.status_code == 200 and 0 < resposta.json()["encontradas"] < 5000


def test_editar_desfazer_e_reanalisar_sozinho(cliente, normalizado):
    linhas = cliente.get(rota_tabela(normalizado, "/linhas"), params={"quantas": 1}).json()
    numero = linhas["numeros"][0]
    estado = cliente.post(rota_tabela(normalizado, "/passos"), json={"passo": {
        "op": "editar_celulas", "edicoes": [{"linha": numero, "coluna": "uf", "valor": "AC"}]}}).json()
    assert estado["ultimo_passo"]["status"] == "ok"
    assert estado["analise"]["desatualizada"] is False, "5 mil linhas: a análise se refaz sozinha"
    primeira = cliente.get(rota_tabela(normalizado, "/linhas"), params={"quantas": 1}).json()
    uf = [c["nome"] for c in primeira["colunas"]].index("uf")
    assert primeira["linhas"][0][uf] == "AC"
    estado = cliente.post(rota_tabela(normalizado, "/desfazer")).json()
    assert estado["tabelas"][0]["manipulacao"] == []
    depois = cliente.get(rota_tabela(normalizado, "/linhas"), params={"quantas": 1}).json()
    assert depois["linhas"][0] == linhas["linhas"][0]


def test_passo_invalido_explica_o_erro(cliente, normalizado):
    resposta = cliente.post(rota_tabela(normalizado, "/passos"), json={"passo": {
        "op": "renomear_coluna", "coluna": "valor_total", "nome": "UF"}})
    assert resposta.status_code == 422 and "Já existe uma coluna chamada uf" in resposta.json()["erro"]


def test_previa_da_formula_nao_muda_nada(cliente, normalizado):
    resposta = cliente.post(rota_tabela(normalizado, "/formula"), json={
        "nome": "dobro", "formula": "=quantidade * 2"})
    assert resposta.status_code == 200 and len(resposta.json()["previa"]) == 8
    errada = cliente.post(rota_tabela(normalizado, "/formula"), json={"nome": "x", "formula": "=quantidad * 2"})
    assert errada.status_code == 422 and "quantidade" in errada.json()["erro"]
    estado = cliente.get(rota(normalizado)).json()
    assert estado["tabelas"][0]["manipulacao"] == []


def test_sql_so_consulta_e_pagina(cliente, normalizado):
    sql = "SELECT uf, count(*) AS n FROM vendas_sujas GROUP BY 1 ORDER BY 2 DESC"
    resposta = cliente.post(rota(normalizado, "/sql"), json={"sql": sql, "quantas": 3})
    assert resposta.status_code == 200 and len(resposta.json()["linhas"]) == 3
    proibida = cliente.post(rota(normalizado, "/sql"), json={"sql": "DROP TABLE vendas_sujas"})
    assert proibida.status_code == 422 and "DROP" in proibida.json()["erro"]
    disco = cliente.post(rota(normalizado, "/sql"), json={"sql": "SELECT * FROM read_csv('/etc/passwd')"})
    assert disco.status_code == 422


def test_consulta_salva_vira_arquivo_sql_e_tabela(cliente, normalizado):
    sql = "SELECT uf, sum(valor_total) AS total FROM vendas_sujas GROUP BY 1"
    estado = cliente.post(rota(normalizado, "/consultas"), json={"nome": "total por uf", "sql": sql}).json()
    salva = estado["consultas"][-1]
    arquivo = cliente.get(rota(normalizado, f"/consultas/{salva['id']}/arquivo"))
    assert arquivo.status_code == 200 and arquivo.text.strip().endswith(";")
    assert 'filename="total_por_uf.sql"' in arquivo.headers["content-disposition"]
    estado = cliente.post(rota(normalizado, "/sql/tabela"), json={"sql": sql, "nome": "total_por_uf"}).json()
    nova = estado["tabelas"][-1]
    assert nova["origem"] == "consulta" and nova["nome"] == "total_por_uf"
    assert estado["ativa"] == nova["id"]
    cliente.delete(rota(estado, f"/tabelas/{nova['id']}"))


def test_segundo_arquivo_relacao_e_visao_como_base(cliente, normalizado):
    regioes = "uf;regiao;populacao\n" + "".join(
        f"{uf};{regiao};{1000 + i}\n" for i, (uf, regiao) in enumerate(
            [("SP", "Sudeste"), ("RJ", "Sudeste"), ("MG", "Sudeste"), ("BA", "Nordeste"), ("PE", "Nordeste"),
             ("RS", "Sul"), ("PR", "Sul"), ("SC", "Sul"), ("CE", "Nordeste"), ("GO", "Centro-Oeste"),
             ("DF", "Centro-Oeste"), ("AM", "Norte"), ("PA", "Norte"), ("ES", "Sudeste"), ("AC", "Norte")]))
    estado = cliente.post(rota(normalizado, "/tabelas"),
                          files={"arquivo": ("ufs.csv", regioes.encode(), "text/csv")}).json()
    vendas, ufs = estado["tabelas"][0], estado["tabelas"][-1]
    assert ufs["nome"] == "ufs" and estado["ativa"] == ufs["id"]
    receita = [s["passo"] for s in ufs["sugestoes"] if s["marcada"]]
    cliente.post(rota(estado, f"/tabelas/{ufs['id']}/normalizar"), json={"receita": receita})

    esquema = cliente.get(rota(estado, "/esquema")).json()
    sugestao = next(s for s in esquema["sugestoes"] if s["de"]["tabela"] == vendas["id"])
    assert sugestao["para"] == {"tabela": ufs["id"], "coluna": "uf"} and sugestao["cobertura"] > 0.9

    estado = cliente.post(rota(estado, "/relacoes"), json={"de": sugestao["de"], "para": sugestao["para"]}).json()
    relacao = estado["relacoes"][-1]
    recusada = cliente.post(rota(estado, "/visoes"), json={
        "nome": "ufs_com_vendas", "tabela": ufs["id"], "juncoes": [{"relacao": relacao["id"]}]})
    assert recusada.status_code == 422 and "repetiria" in recusada.json()["erro"]

    estado = cliente.post(rota(estado, "/visoes"), json={
        "nome": "vendas_com_regiao", "tabela": vendas["id"], "juncoes": [{"relacao": relacao["id"]}]}).json()
    visao = estado["visoes"][-1]
    assert estado["base"] == {"tipo": "visao", "id": visao["id"]} and visao["linhas"] == 5000
    assert estado["analise"]["rotulo"] == "vendas_com_regiao"
    grafico = cliente.post(rota(estado, "/grafico"), json={
        "tipo": "barras", "x": "ufs_regiao", "y": "valor_total", "agregacao": "soma"})
    assert grafico.status_code == 200 and {p["x"] for p in grafico.json()["dados"]} >= {"Sudeste", "Sul"}

    estado = cliente.delete(rota(estado, f"/visoes/{visao['id']}")).json()
    assert estado["base"] is None
    estado = cliente.post(rota(estado, "/base"), json={"tipo": "tabela", "id": vendas["id"]}).json()
    assert estado["analise"]["base"] == {"tipo": "tabela", "id": vendas["id"]}


def test_trocar_o_papel_refaz_a_analise(cliente, normalizado):
    estado = cliente.post(rota(normalizado, "/papeis"), json={"papeis": {"valor_total": "ignorar"}}).json()
    assert all(g["y"] != "valor_total" for g in estado["analise"]["graficos"])
    cliente.post(rota(normalizado, "/papeis"), json={"papeis": {}})


def test_taxa_de_coluna_que_nao_e_sim_nao_explica_o_erro(cliente, normalizado):
    resposta = cliente.post(rota(normalizado, "/grafico"), json={
        "tipo": "barras", "x": "categoria", "y": "valor_total", "agregacao": "taxa"})
    assert resposta.status_code == 422 and "sim/não" in resposta.json()["erro"]


def test_exportar_csv_sem_colunas_internas(cliente, normalizado):
    resposta = cliente.get(rota_tabela(normalizado, "/exportar/csv"))
    assert resposta.status_code == 200 and resposta.content.startswith(b"\xef\xbb\xbf")
    linhas = list(csv.DictReader(io.StringIO(resposta.content.decode("utf-8-sig")), delimiter=";"))
    assert len(linhas) == 5000 and "__linha" not in linhas[0]
    assert "," in linhas[0]["valor_unitario"]


def test_exportar_parquet_e_receita(cliente, normalizado):
    assert cliente.get(rota_tabela(normalizado, "/exportar/parquet")).content[:4] == b"PAR1"
    receita = cliente.get(rota_tabela(normalizado, "/exportar/receita")).json()
    assert receita == normalizado["tabelas"][0]["receita"]


def test_envio_de_csv_real(cliente):
    conteudo = "nome;cidade;valor\nJoão;São Paulo;R$ 10,00\nAna;Recife;R$ 20,00\n".encode("cp1252")
    resposta = cliente.post("/api/sessoes", files={"arquivo": ("meus dados.csv", conteudo, "text/csv")})
    assert resposta.status_code == 200
    tabela = resposta.json()["tabelas"][0]
    assert tabela["arquivo"]["arquivo"] == "meus dados.csv" and tabela["nome"] == "meus_dados"
    assert tabela["arquivo"]["codificacao"] == "latin-1"


def test_arquivo_invalido_nao_deixa_sessao_para_tras(cliente):
    antes = len(list(sessoes.PASTA.iterdir()))
    resposta = cliente.post("/api/sessoes", files={"arquivo": ("foto.png", b"x", "image/png")})
    assert resposta.status_code == 422 and "Formato" in resposta.json()["erro"]
    assert len(list(sessoes.PASTA.iterdir())) == antes


def test_limite_de_tamanho_vale_durante_o_envio(cliente, monkeypatch):
    from dataclasses import replace

    monkeypatch.setattr(main, "LIMITES", replace(main.LIMITES, max_bytes_arquivo=100))
    resposta = cliente.post("/api/sessoes", files={"arquivo": ("grande.csv", b"a;b\n" * 500, "text/csv")})
    assert resposta.status_code == 413 and resposta.json()["limite"] == "bytes_arquivo"


def test_id_de_sessao_nao_vira_caminho(cliente):
    assert cliente.get("/api/sessoes/..%2F..%2Fetc").status_code == 404
    assert cliente.get("/api/sessoes/sessao_que_nao_existe_000").status_code == 404


def test_tabela_inexistente_e_404(cliente, normalizado):
    assert cliente.get(rota(normalizado, "/tabelas/t999/linhas")).status_code == 404


def test_sem_acesso_a_arquivos_durante_a_analise(aberto):
    with sessoes.abrir(aberto["sessao"]) as con, pytest.raises(Exception, match="disabled"):
        con.execute("SELECT * FROM read_csv('/etc/passwd')").fetchall()


def test_converter_carrega_o_cambio_na_hora(cliente, normalizado):
    estado = cliente.post(rota_tabela(normalizado, "/passos"), json={"passo": {
        "op": "formula", "nome": "total_dolar", "formula": '=CONVERTER(valor_total; "BRL"; "USD")'}}).json()
    assert estado["ultimo_passo"]["status"] == "ok"
    linhas = cliente.get(rota_tabela(normalizado, "/linhas"), params={"quantas": 1}).json()
    nomes = [c["nome"] for c in linhas["colunas"]]
    valor = linhas["linhas"][0][nomes.index("valor_total")]
    dolar = linhas["linhas"][0][nomes.index("total_dolar")]
    assert valor and 0 < dolar < valor
    cliente.post(rota_tabela(normalizado, "/desfazer"))
