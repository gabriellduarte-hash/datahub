"""Limites do sistema.

São dois tipos, com objetivos diferentes:

- Carga: protegem a máquina (memória, disco, tempo de upload). Quem decide o
  custo é o número de células (linhas × colunas), não só o de linhas: 10
  milhões de linhas com 5 colunas é leve, com 200 colunas não é.
- Análise: protegem a qualidade dos insights. Muitas linhas não atrapalham a
  estatística. O que gera conclusão errada é ter poucas linhas, muitas colunas
  e muitas comparações: testando centenas de pares, alguns parecem relevantes
  só por acaso.

Os números são pontos de partida, para ajustar com os benchmarks da Fase 6.
"""

from dataclasses import dataclass


def numero_br(n: int) -> str:
    """10000000 -> "10.000.000"."""
    return f"{n:_}".replace("_", ".")


class LimiteExcedido(Exception):
    """O arquivo passa de um limite de carga.

    `limite` é o nome do campo de Limites sem o "max_" (ex.: "linhas").
    """

    def __init__(self, limite: str, valor: int, maximo: int):
        self.limite = limite
        self.valor = valor
        self.maximo = maximo
        super().__init__(f"{limite}: {numero_br(valor)} acima do máximo de {numero_br(maximo)}")


@dataclass(frozen=True)
class Limites:
    # Carga. Verifique dos mais baratos para os mais caros: tamanho do arquivo
    # e número de colunas saem sem ler os dados; contar as linhas de um CSV
    # exige ler o arquivo inteiro.
    max_bytes_arquivo: int | None
    max_linhas: int
    max_colunas: int
    max_celulas: int

    # Análise.
    min_linhas_estatistica: int = 30    # abaixo disso, só estatística descritiva
    max_colunas_insights: int = 30      # as mais relevantes; o resto fica fora dos insights
    max_metricas_correlacao: int = 15   # 15 métricas = 105 pares
    max_categorias_grafico: int = 10    # o resto vira "Outros"
    max_insights_exibidos: int = 20
    linhas_amostra: int = 1_000_000     # análises pesadas acima disso usam amostra, sempre avisada
    timeout_analisador_s: int = 15      # um analisador lento é pulado, os outros seguem
    linhas_reanalise_automatica: int = 200_000  # até aqui, gráficos e insights se refazem sozinhos após uma edição
    timeout_consulta_s: int = 15        # uma consulta SQL do usuário é interrompida depois disso
    max_linhas_consulta: int = 500      # linhas por página no resultado de uma consulta

    # A partir desta fração de nulos a coluna é "quase vazia". Ela só é
    # sinalizada, nunca sugerida para remoção: o nulo pode ter significado
    # (data_cancelamento vazia = pedido não cancelado).
    limite_quase_vazia: float = 0.95

    # Memória que o DuckDB pode usar antes de escrever no disco.
    memoria_max: str = "4GB"


# Site público: hospedagem gratuita, arquivo chegando pela internet.
PUBLICO = Limites(
    max_bytes_arquivo=100 * 1024**2,
    max_linhas=10_000_000,
    max_colunas=500,
    max_celulas=100_000_000,
    memoria_max="2GB",
)

# Rodando na sua máquina: o arquivo já está no disco, o que pesa é a memória.
LOCAL = Limites(
    max_bytes_arquivo=None,
    max_linhas=10_000_000,
    max_colunas=500,
    max_celulas=500_000_000,
)

# Textos que significam "vazio", comparados depois de strip() e lower().
# "0" fica de fora de propósito: zero é um valor, não ausência de valor.
VAZIOS_DISFARCADOS = frozenset({
    "", "-", "--", "?", "na", "n/a", "n/d", "#n/a", "#n/d", "null", "none", "nan",
})
