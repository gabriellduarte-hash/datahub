"""Gera um CSV de vendas com sujeira plantada e o gabarito do que foi plantado.

Cada defeito do arquivo foi colocado de propósito e contado no gabarito. É
assim que os testes sabem se o engine acertou.

O arquivo imita uma exportação do Excel brasileiro: separador ";", vírgula
decimal, "R$" no valor, datas dd/mm/aaaa e linhas vazias no fim.

Além da sujeira, ele tem efeitos de negócio plantados, para o bloco de insights
ter respostas conhecidas: crescimento mensal, um mês de destaque puxado por uma
categoria e quantidades menores nos produtos caros.

Uso:
    python -m samples.gerar_vendas                    # 1.000 linhas em samples/dados/
    python -m samples.gerar_vendas --linhas 10000000 --saida samples/grandes
"""

import argparse
import csv
import json
import random
from calendar import monthrange
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from engine.limites import LOCAL, VAZIOS_DISFARCADOS, numero_br

COLUNAS = [
    "pedido_id", "data_pedido", "cliente", "uf", "categoria", "produto",
    "quantidade", "valor_unitario", "usou_cupom", "data_cancelamento",
    "observacao", "pais",
    "",  # sem nome e sem dados: sobra comum de exportação do Excel
]
LINHA_VAZIA = [""] * len(COLUNAS)

# O que o perfil deve dizer de cada coluna. As que não aparecem aqui são None.
# usou_cupom ("SIM" ou vazio) fica de fora de propósito: tem um valor só, mas
# os nulos querem dizer "não usou". Não é constante, é um indicador.
VAZIO_ESPERADO = {
    "data_cancelamento": "quase_vazia",
    "observacao": "vazia",
    "pais": "constante",
    "": "vazia",
}

PRODUTOS = {  # produto: (categoria, preço base)
    "Notebook 14": ("Informática", 3899.00),
    "Monitor 27": ("Informática", 1499.00),
    "Mouse sem fio": ("Informática", 89.90),
    "Teclado mecânico": ("Informática", 349.90),
    "Cadeira de escritório": ("Móveis", 1199.00),
    "Mesa em L": ("Móveis", 899.00),
    "Estante": ("Móveis", 459.90),
    "Air fryer": ("Eletrodomésticos", 549.90),
    "Liquidificador": ("Eletrodomésticos", 189.90),
    "Cafeteira": ("Eletrodomésticos", 279.00),
    "Romance": ("Livros", 59.90),
    "Livro técnico": ("Livros", 129.90),
    "Tênis de corrida": ("Esporte", 399.90),
    "Bicicleta": ("Esporte", 2199.00),
    "Jogo de panelas": ("Casa", 349.00),
    "Luminária": ("Casa", 119.90),
}

UFS = {"SP": 35, "RJ": 15, "MG": 12, "RS": 8, "PR": 8, "BA": 7, "SC": 6, "PE": 5, "CE": 4}
VARIANTES_UF = {
    "SP": ["sp", "São Paulo", "Sao Paulo", " SP "],
    "RJ": ["rj", "Rio de Janeiro"],
    "MG": ["mg", "Minas Gerais"],
}

NOMES = [
    "Ana", "Bruno", "Carla", "Diego", "Elisa", "Felipe", "Gabriela", "Heitor",
    "Isabela", "João", "Larissa", "Marcos", "Natália", "Otávio", "Paula",
    "Rafael", "Sofia", "Thiago", "Vitória", "William",
]
SOBRENOMES = [
    "Silva", "Santos", "Oliveira", "Souza", "Rodrigues", "Ferreira", "Alves",
    "Pereira", "Lima", "Gomes", "Costa", "Ribeiro", "Martins", "Carvalho", "Almeida",
]

OBSERVACOES = ["", "", "N/A", "-", "#N/D", "null"]  # tudo vazio, a maior parte disfarçada
DATAS_INVALIDAS = ["31/02/2025", "30/02/2024", "00/13/2024"]

INICIO = date(2024, 1, 1)
FIM = date(2025, 9, 18)  # setembro/2025 fica incompleto de propósito

CRESCIMENTO_MENSAL = 0.03
MES_DESTAQUE = (2025, 8)
CATEGORIA_DESTAQUE = "Informática"


def _meses() -> list[tuple[int, int, float]]:
    """(ano, mês, peso) de cada mês. O peso cresce todo mês e o destaque recebe um salto."""
    meses = []
    ano, mes, indice = INICIO.year, INICIO.month, 0
    while (ano, mes) <= (FIM.year, FIM.month):
        dias = monthrange(ano, mes)[1]
        peso = (1 + CRESCIMENTO_MENSAL) ** indice
        if (ano, mes) == (FIM.year, FIM.month):
            peso *= FIM.day / dias
        if (ano, mes) == MES_DESTAQUE:
            peso *= 1.5
        meses.append((ano, mes, peso))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
        indice += 1
    return meses


MESES = _meses()
PESOS_MESES = [peso for _, _, peso in MESES]


def moeda_br(valor: float) -> str:
    inteiro, centavos = f"{valor:,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def _pedido(i: int, rng: random.Random, cancelado: bool, data_invalida: bool,
            outlier: bool) -> tuple[list[str], list[str]]:
    """Uma linha de pedido e a lista das sujeiras que ela carrega."""
    marcas = []
    ano, mes, _ = rng.choices(MESES, weights=PESOS_MESES)[0]
    ultimo_dia = FIM.day if (ano, mes) == (FIM.year, FIM.month) else monthrange(ano, mes)[1]
    dia = date(ano, mes, rng.randint(1, ultimo_dia))

    if data_invalida:
        data_txt = rng.choice(DATAS_INVALIDAS)
        marcas.append("datas_invalidas")
    elif rng.random() < 0.10:
        data_txt = dia.isoformat()
        marcas.append("datas_formato_iso")
    else:
        data_txt = dia.strftime("%d/%m/%Y")

    cliente = f"{rng.choice(NOMES)} {rng.choice(SOBRENOMES)}"
    sorteio = rng.random()
    if sorteio < 0.03:
        cliente = f"  {cliente} "
        marcas.append("clientes_com_espacos")
    elif sorteio < 0.05:
        cliente = cliente.upper()
        marcas.append("clientes_maiusculos")

    uf = rng.choices(list(UFS), weights=list(UFS.values()))[0]
    if uf in VARIANTES_UF and rng.random() < 0.08:
        uf = rng.choice(VARIANTES_UF[uf])
        marcas.append("uf_variantes")

    pesos_produtos = [
        5 if (ano, mes) == MES_DESTAQUE and categoria == CATEGORIA_DESTAQUE else 1
        for categoria, _ in PRODUTOS.values()
    ]
    produto = rng.choices(list(PRODUTOS), weights=pesos_produtos)[0]
    categoria, preco_base = PRODUTOS[produto]
    preco = round(preco_base * rng.uniform(0.9, 1.1), 2)
    sorteio = rng.random()
    if sorteio < 0.80:
        valor = moeda_br(preco)
        marcas.append("valor_moeda_br")
    elif sorteio < 0.92:
        valor = moeda_br(preco).removeprefix("R$ ")
        marcas.append("valor_numero_br")
    else:
        valor = f"{preco:.2f}"
        marcas.append("valor_numero_us")

    if outlier:
        quantidade = 999
        marcas.append("quantidade_outliers")
    else:
        # Produto caro sai em quantidade pequena.
        maximo = 2 if preco_base >= 1000 else 5 if preco_base >= 200 else 10
        quantidade = rng.randint(1, maximo)

    cancelamento = ""
    if cancelado:
        cancelamento = (dia + timedelta(days=rng.randint(1, 15))).strftime("%d/%m/%Y")

    linha = [
        str(100_000 + i), data_txt, cliente, uf, categoria, produto,
        str(quantidade), valor, "SIM" if rng.random() < 0.2 else "", cancelamento,
        rng.choice(OBSERVACOES), "Brasil", "",
    ]
    return linha, marcas


def gerar(pasta: Path, linhas: int = 1000, semente: int = 42) -> tuple[Path, dict]:
    """Escreve vendas_sujas.csv e vendas_sujas.gabarito.json em `pasta`."""
    if linhas < 100:
        raise ValueError("use pelo menos 100 linhas; com menos, as proporções do gabarito não se sustentam")

    rng = random.Random(semente)
    # Quantidades fixas, só as posições são sorteadas: o gabarito não depende da sorte.
    cancelados = set(rng.sample(range(linhas), round(linhas * 0.03)))
    duplicados = set(rng.sample(range(linhas), round(linhas * 0.01)))
    invalidas = set(rng.sample(range(linhas), max(1, round(linhas * 0.001))))
    outliers = set(rng.sample(range(linhas), 3))
    vazias_no_meio = {linhas // 3, 2 * linhas // 3}

    pasta.mkdir(parents=True, exist_ok=True)
    caminho = pasta / "vendas_sujas.csv"
    sujeira, nulos, disfarcados = Counter(), Counter(), Counter()
    cheias = vazias = 0

    with caminho.open("w", newline="", encoding="utf-8") as f:
        escritor = csv.writer(f, delimiter=";")
        escritor.writerow(COLUNAS)
        for i in range(linhas):
            if i in vazias_no_meio:
                escritor.writerow(LINHA_VAZIA)
                vazias += 1
            linha, marcas = _pedido(i, rng, i in cancelados, i in invalidas, i in outliers)
            # A duplicata é uma cópia exata, como num pedido enviado duas vezes.
            for _ in range(2 if i in duplicados else 1):
                escritor.writerow(linha)
                cheias += 1
                sujeira.update(marcas)
                for nome, valor in zip(COLUNAS, linha):
                    if valor.strip().lower() in VAZIOS_DISFARCADOS:
                        nulos[nome] += 1
                        if valor != "":
                            disfarcados[nome] += 1
        for _ in range(3):
            escritor.writerow(LINHA_VAZIA)
            vazias += 1

    fracao = nulos["data_cancelamento"] / cheias
    assert LOCAL.limite_quase_vazia <= fracao < 1, f"data_cancelamento com {fracao:.1%} de nulos"

    gabarito = {
        "arquivo": caminho.name,
        "semente": semente,
        "linhas_total": cheias + vazias,
        "linhas_vazias": vazias,
        "linhas_duplicadas": len(duplicados),
        # Contagens sobre as linhas não vazias, que é o que o perfil analisa.
        "colunas": [
            {
                "posicao": posicao,
                "nome": nome,
                "nulos": nulos[nome],
                "vazios_disfarcados": disfarcados[nome],
                "vazio": VAZIO_ESPERADO.get(nome),
                "sugerir_remover": VAZIO_ESPERADO.get(nome) in {"vazia", "constante"},
            }
            for posicao, nome in enumerate(COLUNAS)
        ],
        # Para as próximas fases (limpeza e insights). Duplicatas contam de
        # novo, porque o engine também vai vê-las.
        "sujeira": dict(sorted(sujeira.items())) | {"ultimo_dia": FIM.isoformat()},
        "efeitos": {
            "crescimento_mensal": CRESCIMENTO_MENSAL,
            "mes_destaque": f"{MES_DESTAQUE[0]}-{MES_DESTAQUE[1]:02d}",
            "categoria_destaque": CATEGORIA_DESTAQUE,
        },
    }
    with (pasta / "vendas_sujas.gabarito.json").open("w", encoding="utf-8") as f:
        json.dump(gabarito, f, ensure_ascii=False, indent=2)

    return caminho, gabarito


def main():
    parser = argparse.ArgumentParser(description="Gera o CSV de vendas sujo e o gabarito.")
    parser.add_argument("--linhas", type=int, default=1000)
    parser.add_argument("--saida", type=Path, default=Path(__file__).parent / "dados")
    parser.add_argument("--semente", type=int, default=42)
    args = parser.parse_args()

    caminho, gabarito = gerar(args.saida, args.linhas, args.semente)
    print(f"{caminho}: {numero_br(gabarito['linhas_total'])} linhas")


if __name__ == "__main__":
    main()
