"""Números e datas no formato brasileiro, para os textos que o usuário lê."""

from datetime import date

from engine.limites import numero_br

MESES = [
    "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
    "agosto", "setembro", "outubro", "novembro", "dezembro",
]


NOME_GRAO = {"dia": "dia", "semana": "semana", "mes": "mês", "trimestre": "trimestre", "ano": "ano"}


def numero(valor: float, casas: int = 2) -> str:
    """1234.5 -> "1.234,50"."""
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "_").replace(".", ",").replace("_", ".")


def inteiro(valor: int) -> str:
    return numero_br(int(valor))


def pct(fracao: float, casas: int = 1) -> str:
    """0.1364 -> "13,6%"."""
    return f"{numero(fracao * 100, casas)}%"


def valor(numero_bruto: float, unidade: str | None = None) -> str:
    """Formata um valor conforme a unidade da coluna."""
    if unidade == "BRL":
        sinal = "-" if numero_bruto < 0 else ""
        return f"{sinal}R$ {numero(abs(numero_bruto))}"
    if unidade == "%":
        return f"{numero(numero_bruto)}%"
    if float(numero_bruto).is_integer():
        return inteiro(numero_bruto)
    return numero(numero_bruto)


def periodo(dia: date, grao: str) -> str:
    """Nome do período que começa em `dia`."""
    if grao == "ano":
        return str(dia.year)
    if grao == "trimestre":
        return f"T{(dia.month - 1) // 3 + 1}/{dia.year}"
    if grao == "mes":
        return f"{MESES[dia.month - 1]}/{dia.year}"
    if grao == "semana":
        return f"semana de {dia.strftime('%d/%m/%Y')}"
    return dia.strftime("%d/%m/%Y")
