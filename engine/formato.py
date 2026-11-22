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


def compacto(numero_bruto: float, unidade: str | None = None) -> str:
    """Para os números grandes do painel: 9.321.770,80 vira "9,32 mi"."""
    absoluto = abs(numero_bruto)
    for limite, sufixo in ((1e9, " bi"), (1e6, " mi"), (1e4, " mil")):
        if absoluto >= limite:
            texto = numero(numero_bruto / limite, 2 if absoluto < limite * 100 else 1) + sufixo
            break
    else:
        texto = inteiro(numero_bruto) if float(numero_bruto).is_integer() else numero(numero_bruto)
    if unidade == "BRL":
        return f"R$ {texto}"
    if unidade == "%":
        return f"{texto}%"
    return texto
