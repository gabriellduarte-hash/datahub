"""Bloco 1, arquivo: transforma o arquivo do usuário na tabela `bruta`.

CSV e Excel entram com tudo como texto. Assim um valor estranho no fim do
arquivo não derruba a leitura, e quem decide os tipos é o bloco de perfil, que
entende formato brasileiro. Parquet já vem tipado e mantém os tipos.
"""

import codecs
import csv
from dataclasses import dataclass, field
from datetime import date, datetime, time
from itertools import islice
from pathlib import Path

import duckdb
from openpyxl import load_workbook

from engine.limites import LOCAL, LimiteExcedido, Limites
from engine.sql import LINHA, colunas_de, ident, literal

EXTENSOES_CSV = {".csv", ".tsv", ".txt"}
LINHAS_BUSCA_CABECALHO = 20


class ArquivoInvalido(Exception):
    """O arquivo não pôde ser lido. A mensagem é para o usuário."""


@dataclass
class Leitura:
    arquivo: str
    formato: str              # csv, xlsx ou parquet
    bytes: int
    linhas: int
    colunas: int
    separador: str | None = None
    codificacao: str | None = None
    aba: str | None = None
    abas: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)


def ler(con: duckdb.DuckDBPyConnection, caminho: Path, limites: Limites = LOCAL,
        aba: str | None = None, tabela: str = "bruta", nome: str | None = None) -> Leitura:
    """Carrega o arquivo em `tabela`. Verifica os limites antes de carregar os dados."""
    caminho = Path(caminho)
    extensao = caminho.suffix.lower()
    tamanho = caminho.stat().st_size

    if limites.max_bytes_arquivo is not None and tamanho > limites.max_bytes_arquivo:
        raise LimiteExcedido("bytes_arquivo", tamanho, limites.max_bytes_arquivo)
    if tamanho == 0:
        raise ArquivoInvalido("O arquivo está vazio.")

    nome = nome or caminho.name
    if extensao in EXTENSOES_CSV:
        return _ler_csv(con, caminho, limites, tabela, nome, tamanho)
    if extensao == ".xlsx":
        return _ler_excel(con, caminho, limites, aba, tabela, nome, tamanho)
    if extensao == ".parquet":
        return _ler_parquet(con, caminho, limites, tabela, nome, tamanho)
    if extensao == ".xls":
        raise ArquivoInvalido("O formato .xls antigo não é aceito. Salve como .xlsx ou .csv.")
    raise ArquivoInvalido(f"Formato {extensao or 'desconhecido'} não aceito. Use CSV, XLSX ou Parquet.")


def _ler_csv(con, caminho, limites, tabela, nome, tamanho) -> Leitura:
    codificacao = _detectar_codificacao(caminho)
    # As aspas são declaradas porque o DuckDB detecta o formato pelo começo do
    # arquivo: se as primeiras 20 mil linhas não usam aspas, ele conclui que o
    # arquivo não tem, e um "Korea, South" na linha 75 mil quebra a leitura.
    opcoes = f"all_varchar=true, null_padding=true, quote='\"', escape='\"', encoding={literal(codificacao)}"
    avisos = []
    if codificacao == "latin-1":
        avisos.append("Arquivo em Latin-1, comum em exportações do Excel. Os acentos foram convertidos.")

    # Três tentativas, da mais rápida para a mais tolerante:
    # 1. normal, em paralelo;
    # 2. em série: a leitura em paralelo não aceita quebra de linha dentro de
    #    aspas (uma descrição de vídeo com parágrafos, por exemplo);
    # 3. tolerante, com o separador tirado do cabeçalho: quando o arquivo foge
    #    do padrão CSV e o DuckDB não consegue nem adivinhar o formato.
    tentativas = ["", ", parallel=false", None]
    for numero, extra in enumerate(tentativas, 1):
        tolerante = extra is None
        if tolerante:
            separador = _separador_do_cabecalho(caminho, codificacao)
            extra = f", delim={literal(separador)}, header=true, strict_mode=false, parallel=false"
        try:
            _verificar_limites(con, f"read_csv(?, {opcoes}{extra}, ignore_errors=true)", str(caminho), limites)
            if tolerante:
                tem_cabecalho = True
                avisos.append("O arquivo foge do padrão CSV em alguns trechos (aspas sem par, por exemplo). "
                              "Li no modo tolerante.")
            else:
                separador, tem_cabecalho = _farejar(con, caminho, opcoes)
            con.execute(
                f"CREATE OR REPLACE TABLE {ident(tabela)} AS "
                f"SELECT * FROM read_csv(?, {opcoes}{extra}, store_rejects=true)",
                [str(caminho)],
            )
            rejeitadas = con.execute("SELECT count(*) FROM reject_errors").fetchone()[0]
            break
        except duckdb.Error as erro:
            recuperavel = "parallel" in str(erro) or "sniffing" in str(erro)
            if recuperavel and numero < len(tentativas):
                continue
            raise ArquivoInvalido(_mensagem(erro)) from erro

    if rejeitadas:
        avisos.append(f"{rejeitadas} linhas malformadas ficaram de fora da leitura.")
    if not tem_cabecalho:
        avisos.append("Não encontrei cabeçalho. As colunas receberam nomes automáticos.")

    linhas, colunas = _dimensoes(con, tabela)
    return Leitura(nome, "csv", tamanho, linhas, colunas, separador=separador,
                   codificacao=codificacao, avisos=avisos)


def _ler_parquet(con, caminho, limites, tabela, nome, tamanho) -> Leitura:
    try:
        _verificar_limites(con, "read_parquet(?)", str(caminho), limites)
        con.execute(f"CREATE OR REPLACE TABLE {ident(tabela)} AS SELECT * FROM read_parquet(?)",
                    [str(caminho)])
    except duckdb.Error as erro:
        raise ArquivoInvalido(_mensagem(erro)) from erro

    linhas, colunas = _dimensoes(con, tabela)
    return Leitura(nome, "parquet", tamanho, linhas, colunas)


def _ler_excel(con, caminho, limites, aba, tabela, nome, tamanho) -> Leitura:
    temporario = caminho.with_suffix(".aba.csv")
    try:
        abas, aba_usada, puladas = _excel_para_csv(caminho, temporario, aba, limites)
        fonte = "read_csv(?, all_varchar=true, delim=',', quote='\"', escape='\"', header=true)"
        _verificar_limites(con, fonte, str(temporario), limites)
        con.execute(f"CREATE OR REPLACE TABLE {ident(tabela)} AS SELECT * FROM {fonte}",
                    [str(temporario)])
    except duckdb.Error as erro:
        raise ArquivoInvalido(_mensagem(erro)) from erro
    finally:
        temporario.unlink(missing_ok=True)

    avisos = []
    if len(abas) > 1:
        avisos.append(f"O arquivo tem {len(abas)} abas. Li a aba \"{aba_usada}\".")
    if puladas:
        avisos.append(f"Pulei {puladas} linhas acima do cabeçalho, como títulos e linhas em branco.")
    linhas, colunas = _dimensoes(con, tabela)
    return Leitura(nome, "xlsx", tamanho, linhas, colunas, aba=aba_usada, abas=abas, avisos=avisos)


def _excel_para_csv(origem: Path, destino: Path, aba: str | None, limites: Limites) -> tuple[list[str], str, int]:
    """Copia uma aba para CSV. Datas viram ISO e números, texto com ponto decimal."""
    try:
        livro = load_workbook(origem, read_only=True, data_only=True)
    except Exception as erro:
        raise ArquivoInvalido("Não consegui abrir o Excel. O arquivo pode estar corrompido.") from erro

    try:
        abas = list(livro.sheetnames)
        aba_usada = aba or abas[0]
        if aba_usada not in abas:
            raise ArquivoInvalido(f"A aba \"{aba_usada}\" não existe neste arquivo.")

        linhas = livro[aba_usada].iter_rows(values_only=True)
        inicio = [[_celula(valor) for valor in linha] for linha in islice(linhas, LINHAS_BUSCA_CABECALHO)]
        posicao = _posicao_do_cabecalho(inicio)
        if posicao is None:
            raise ArquivoInvalido(f"A aba \"{aba_usada}\" está vazia.")
        if len(inicio[posicao]) > limites.max_colunas:
            raise LimiteExcedido("colunas", len(inicio[posicao]), limites.max_colunas)

        with destino.open("w", newline="", encoding="utf-8") as saida:
            escritor = csv.writer(saida)
            escritor.writerows(inicio[posicao:])
            escritas = len(inicio) - posicao - 1
            for linha in linhas:
                escritas += 1
                if escritas > limites.max_linhas:
                    raise LimiteExcedido("linhas", escritas, limites.max_linhas)
                escritor.writerow([_celula(valor) for valor in linha])
        return abas, aba_usada, posicao
    finally:
        livro.close()


def _posicao_do_cabecalho(linhas: list[list[str]]) -> int | None:
    """A primeira linha quase tão larga quanto os dados. Um título ocupa uma célula só."""
    preenchidas = [sum(1 for celula in linha if celula) for linha in linhas]
    if not any(preenchidas):
        return None
    largura = max(preenchidas)
    return next(i for i, n in enumerate(preenchidas) if n >= max(1, largura / 2))


def _celula(valor) -> str:
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, datetime):
        return valor.date().isoformat() if valor.time() == time(0) else valor.isoformat(" ")
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor)


def _verificar_limites(con, fonte: str, caminho: str, limites: Limites) -> None:
    """Das verificações mais baratas para as mais caras: contar exige ler tudo."""
    colunas = len(con.execute(f"DESCRIBE SELECT * FROM {fonte}", [caminho]).fetchall())
    if colunas > limites.max_colunas:
        raise LimiteExcedido("colunas", colunas, limites.max_colunas)

    linhas = con.execute(f"SELECT count(*) FROM {fonte}", [caminho]).fetchone()[0]
    if linhas > limites.max_linhas:
        raise LimiteExcedido("linhas", linhas, limites.max_linhas)

    celulas = linhas * colunas
    if celulas > limites.max_celulas:
        raise LimiteExcedido("celulas", celulas, limites.max_celulas)


def _separador_do_cabecalho(caminho: Path, codificacao: str) -> str:
    """O candidato que mais aparece na primeira linha."""
    with caminho.open(encoding="latin-1" if codificacao == "latin-1" else "utf-8", errors="replace") as arquivo:
        cabecalho = arquivo.readline(65536)
    return max([",", ";", "\t", "|"], key=cabecalho.count)


def _farejar(con, caminho: Path, opcoes: str) -> tuple[str, bool]:
    """Separador e presença de cabeçalho, como o DuckDB detectou."""
    sem_all_varchar = opcoes.replace("all_varchar=true, ", "")
    linha = con.execute(
        f"SELECT Delimiter, HasHeader FROM sniff_csv(?, {sem_all_varchar})", [str(caminho)]
    ).fetchone()
    return linha[0], bool(linha[1])


def _detectar_codificacao(caminho: Path) -> str:
    """UTF-8 se o arquivo inteiro for válido; senão Latin-1, o padrão do Excel no Brasil."""
    with caminho.open("rb") as arquivo:
        inicio = arquivo.read(4)
        if inicio.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return "utf-16"
        arquivo.seek(0)
        decodificador = codecs.getincrementaldecoder("utf-8")()
        try:
            while bloco := arquivo.read(1024 * 1024):
                decodificador.decode(bloco)
            decodificador.decode(b"", final=True)
        except UnicodeDecodeError:
            return "latin-1"
    return "utf-8"


def _dimensoes(con, tabela: str) -> tuple[int, int]:
    """Numera as linhas e devolve (linhas, colunas). Toda leitura termina aqui."""
    _numerar(con, tabela)
    linhas = con.execute(f"SELECT count(*) FROM {ident(tabela)}").fetchone()[0]
    return linhas, len(colunas_de(con, tabela))


def _numerar(con, tabela: str) -> None:
    """A linha 1 do arquivo vira __linha = 1. O rowid segue a ordem em que as linhas entraram."""
    t = ident(tabela)
    con.execute(f"CREATE OR REPLACE TABLE {t} AS SELECT rowid + 1 AS {ident(LINHA)}, * FROM {t}")


def _mensagem(erro: duckdb.Error) -> str:
    primeira = str(erro).strip().splitlines()[0]
    return f"Não consegui ler o arquivo. Detalhe técnico: {primeira[:200]}"
