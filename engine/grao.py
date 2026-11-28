"""O que cada linha da base representa, e por isso: somar ou tirar a média.

Três jeitos de uma tabela ser:

- evento: cada linha é um acontecimento (uma venda, um item de nota, uma
  sessão). Somar faz sentido: a receita do mês é a soma das vendas do mês.
- entidade: cada linha é uma pessoa ou coisa (um cliente, uma música, um
  laureado), mesmo que tenha uma data, como o ano de lançamento. Somar a
  idade ou o bpm de todo mundo não diz nada: o padrão é a média.
- foto: a mesma coisa aparece em várias datas (o vídeo em cada dia de
  tendência, o estoque de cada semana). Somar contaria a mesma coisa várias
  vezes: o padrão também é a média.

A regra antiga era "tem data, então soma". Medido em 15 arquivos reais e 7
tabelas difíceis (.build/experimento), ela errou 7; este detector errou 1 (um
estoque semanal, que a regra antiga também errava). O motivo vai junto para a
tela, e o usuário pode trocar.
"""

import re
from dataclasses import dataclass

from engine.perfil import Perfil
from engine.sql import ident

TIPOS = {"evento", "entidade", "foto"}
NOMES = {"evento": "um acontecimento", "entidade": "uma pessoa ou coisa", "foto": "um retrato repetido"}
EVENTO = re.compile(r"pedido|order|venda|sale|transac|compra|purchase|invoice|nota|fatura|recibo|quantidade|qty|quantity",
                    re.I)


@dataclass
class Grao:
    tipo: str
    motivo: str
    escolhido: bool = False  # o usuário trocou o que foi detectado


def detectar(con, tabela: str, perfil: Perfil) -> Grao:
    colunas = perfil.colunas
    linhas = max(perfil.linhas, 1)
    t = ident(tabela)
    datas = [c.nome for c in colunas if c.papel == "data"]
    if not datas:
        return Grao("entidade", "sem coluna de data: cada linha costuma ser uma pessoa ou coisa")

    distintos = {}
    for c in colunas:
        if c.papel in {"id", "texto"}:
            distintos[c.nome] = con.execute(f"SELECT count(DISTINCT {ident(c.nome)}) FROM {t}").fetchone()[0]
    papel = {c.nome: c.papel for c in colunas}
    unicas = [nome for nome, d in distintos.items() if d >= 0.98 * linhas]
    repetidas = [nome for nome, d in distintos.items() if papel[nome] == "id" and 0.05 * linhas < d < 0.9 * linhas]
    eventos = [c.nome for c in colunas if EVENTO.search(c.nome)]

    # A mesma coisa em várias linhas vem antes de tudo: é evidência mais forte que a hora.
    if repetidas and not eventos:
        return Grao("foto", f"{repetidas[0]} se repete em várias linhas")

    so_ano = []
    tipos = {c.nome: c.tipo_duckdb for c in colunas}
    for nome in datas:
        if not any(palavra in tipos[nome] for palavra in ("DATE", "TIME")):
            so_ano.append(nome)  # ano guardado como número, como 1990: não tem mês nem hora
            continue
        c = ident(nome)
        com_hora, primeiro_de_janeiro = con.execute(f"""
            SELECT avg(CASE WHEN CAST({c} AS TIMESTAMP)::TIME <> TIME '00:00:00' THEN 1 ELSE 0 END),
                   avg(CASE WHEN month({c}) = 1 AND day({c}) = 1 THEN 1 ELSE 0 END)
            FROM {t} WHERE {c} IS NOT NULL""").fetchone()
        if com_hora and com_hora > 0.5:
            return Grao("evento", f"{nome} tem hora: cada linha é um acontecimento")
        if primeiro_de_janeiro is not None and primeiro_de_janeiro > 0.95:
            so_ano.append(nome)

    if eventos and not unicas:
        return Grao("evento", f"colunas de acontecimento ({', '.join(eventos[:3])}) e nenhuma chave única")
    if unicas and len(so_ano) == len(datas):
        return Grao("entidade", f"{unicas[0]} identifica cada linha e a data é só o ano ({', '.join(so_ano)})")
    if unicas and not eventos:
        return Grao("entidade", f"{unicas[0]} identifica cada linha")
    if len(so_ano) == len(datas):
        return Grao("entidade", f"a data é só o ano ({', '.join(so_ano)})")
    return Grao("evento", "data com dia e mês, sem uma chave que identifique cada linha")


def aplicar(perfil: Perfil, tipo: str) -> None:
    """Evento mantém a soma; entidade e foto trocam a soma pela média."""
    if tipo in {"entidade", "foto"}:
        for coluna in perfil.colunas:
            if coluna.papel == "metrica" and coluna.agregacao == "soma":
                coluna.agregacao = "media"


def resolver(con, tabela: str, perfil: Perfil, escolhido: str | None) -> Grao:
    """O escolhido pelo usuário vale mais que o detectado; o motivo do detectado continua visível."""
    detectado = detectar(con, tabela, perfil)
    if escolhido in TIPOS and escolhido != detectado.tipo:
        return Grao(escolhido, f"escolhido por você (o hub tinha achado {NOMES[detectado.tipo]}: {detectado.motivo})", True)
    return detectado
