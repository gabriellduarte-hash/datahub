"""Mede quanto tempo cada bloco leva, com arquivos de vários tamanhos.

Uso:
    python -m samples.benchmark 100000 1000000 10000000
"""

import shutil
import sys
import tempfile
import time
from dataclasses import replace
from pathlib import Path

import duckdb

from engine import pipeline
from engine.limites import LOCAL, numero_br
from samples.gerar_vendas import gerar


def medir(linhas: int, pasta: Path) -> None:
    csv = pasta / str(linhas) / "vendas_sujas.csv"
    if not csv.exists():
        inicio = time.perf_counter()
        gerar(csv.parent, linhas)
        print(f"gerar: {time.perf_counter() - inicio:.1f} s")

    trabalho = Path(tempfile.mkdtemp(prefix="datahub-bench-", dir=pasta))
    con = duckdb.connect(str(trabalho / "dados.duckdb"), config={"memory_limit": LOCAL.memoria_max})
    try:
        inicio = time.perf_counter()
        # As duplicatas plantadas somam 1%: o teto de linhas não pode barrar a medição.
        limites = replace(LOCAL, max_linhas=linhas * 2, max_celulas=linhas * 50)
        aberto = pipeline.abrir(con, csv, limites)
        receita = [s["passo"] for s in aberto["sugestoes"] if s["marcada"]]
        analisado = pipeline.normalizar(con, receita, limites=limites, estados_anteriores=aberto["blocos"])
        total = time.perf_counter() - inicio

        print(f"\n{numero_br(linhas)} linhas, {csv.stat().st_size / 1024 ** 2:,.0f} MB, total {total:.1f} s")
        for bloco in analisado["blocos"]:
            print(f"  {bloco['bloco']:13} {bloco['status']:8} {bloco['duracao_ms'] / 1000:7.1f} s")
        for execucao in analisado["insights"]["execucoes"]:
            if execucao["status"] != "ok":
                print(f"  insight {execucao['analisador']}: {execucao['status']} ({execucao['motivo']})")
        print(f"  insights: {len(analisado['insights']['insights'])}, gráficos: {len(analisado['graficos'])}")
    finally:
        con.close()
        shutil.rmtree(trabalho, ignore_errors=True)


if __name__ == "__main__":
    pasta = Path(__file__).parent / "grandes"
    pasta.mkdir(exist_ok=True)
    for tamanho in [int(valor) for valor in sys.argv[1:]] or [100_000, 1_000_000]:
        medir(tamanho, pasta)
