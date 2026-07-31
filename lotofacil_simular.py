"""
lotofacil_simular.py
---------------------
Lê lotofacil_sorteios.csv e calcula o desempenho histórico de um conjunto fixo
de jogos da Lotofácil, mostrando quantas vezes cada um teria batido 11-15 pontos.

Uso:
    python lotofacil_simular.py
    python lotofacil_simular.py --input meus_dados.csv --output relatorio.txt
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

JOGOS = {
    "Jogo 1":  [1, 2, 5, 6, 7, 8, 9, 10, 12, 16, 17, 19, 20, 23, 25],
    "Jogo 2":  [2, 5, 6, 7, 8, 9, 12, 14, 15, 18, 20, 21, 22, 24, 25],
    "Jogo 3":  [1, 3, 5, 6, 8, 9, 10, 11, 12, 16, 20, 21, 22, 23, 24],
    "Jogo 4":  [2, 3, 4, 6, 9, 12, 14, 15, 17, 18, 19, 20, 21, 22, 23],
    "Jogo 5":  [2, 3, 4, 5, 9, 10, 11, 13, 14, 16, 17, 18, 19, 20, 21],
    "Jogo 6":  [1, 2, 4, 8, 9, 10, 11, 14, 15, 16, 18, 19, 22, 23, 24],
    "Jogo 7":  [1, 3, 5, 8, 9, 10, 11, 14, 16, 17, 18, 20, 23, 24, 25],
    "Jogo 8":  [1, 2, 4, 5, 6, 8, 9, 11, 14, 16, 21, 22, 23, 24, 25],
    "Jogo 9":  [1, 3, 4, 5, 6, 9, 10, 12, 13, 16, 17, 21, 22, 23, 25],
    "Jogo 10": [1, 2, 4, 6, 7, 8, 10, 11, 13, 15, 16, 17, 22, 24, 25],
    "Jogo 11": [1, 2, 3, 4, 6, 7, 9, 10, 11, 13, 19, 20, 23, 24, 25],
}

FAIXAS = {15: "1ª (sena)", 14: "2ª", 13: "3ª", 12: "4ª", 11: "5ª"}


# ─── carrega CSV ──────────────────────────────────────────────────────────────

def carregar(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def dezenas(row: dict) -> set[int]:
    return {int(row[f"d{i:02d}"]) for i in range(1, 16)}


# ─── cálculo por jogo ─────────────────────────────────────────────────────────

def calcular_jogo(conjunto: set[int], rows: list[dict], sorteios: list[set[int]]) -> dict:
    contagem = {p: 0 for p in range(11, 16)}
    melhor = {"acertos": 0, "concurso": None, "data": None}

    for row, sorteio in zip(rows, sorteios):
        acertos = len(conjunto.intersection(sorteio))
        if acertos >= 11:
            contagem[acertos] += 1
        if acertos > melhor["acertos"]:
            melhor = {"acertos": acertos, "concurso": row["concurso"], "data": row["data"]}

    total_premios = sum(contagem.values())
    return {"contagem": contagem, "total_premios": total_premios, "melhor": melhor}


# ─── formatação do relatório ──────────────────────────────────────────────────

def formatar_jogo(nome: str, numeros: list[int], resultado: dict, n_sorteios: int) -> list[str]:
    contagem = resultado["contagem"]
    total = resultado["total_premios"]
    melhor = resultado["melhor"]

    numeros_fmt = " ".join(f"{n:02d}" for n in sorted(numeros))
    titulo = f" {nome}: [{numeros_fmt}]"
    largura = max(len(titulo) + 1, 55)
    linha_dupla = "═" * largura
    linha_simples = "─" * 11 + "┼" + "─" * 7 + "┼" + "─" * 8 + "┼" + "─" * 12

    linhas = [linha_dupla, titulo, linha_dupla]
    linhas.append(f" {'Acertos':<9} │ {'Vezes':>5} │ {'%':>6} │ Faixa")
    linhas.append(linha_simples)

    for pontos in range(15, 10, -1):
        vezes = contagem[pontos]
        pct = (vezes / n_sorteios * 100) if n_sorteios else 0.0
        linhas.append(f" {pontos:<9} │ {vezes:>5} │ {pct:>5.1f}% │ {FAIXAS[pontos]}")

    linhas.append(linha_simples)
    pct_total = (total / n_sorteios * 100) if n_sorteios else 0.0
    linhas.append(f" {'TOTAL ≥11':<9} │ {total:>5} │ {pct_total:>5.1f}% │")

    if melhor["concurso"] is not None:
        linhas.append(
            f" {'Melhor':<9} │ {melhor['acertos']} acertos │ concurso {melhor['concurso']} ({melhor['data']})"
        )
    else:
        linhas.append(f" {'Melhor':<9} │ nenhum acerto ≥11 registrado")

    linhas.append("")
    return linhas


def formatar_ranking(resultados: dict[str, dict], n_sorteios: int) -> list[str]:
    ordenado = sorted(resultados.items(), key=lambda kv: kv[1]["total_premios"], reverse=True)

    # (cabeçalho, largura, alinhamento) — usado para montar cabeçalho, linhas e
    # separador a partir da mesma fonte, evitando desalinhamento entre eles.
    colunas = [
        ("Pos", 3, ">"), ("Jogo", 8, "<"),
        ("15", 3, ">"), ("14", 3, ">"), ("13", 3, ">"), ("12", 3, ">"), ("11", 3, ">"),
        ("TOTAL", 5, ">"), ("%", 6, ">"), ("Melhor", 16, "<"),
    ]

    def montar_linha(valores: list[str]) -> str:
        celulas = [f" {v:{align}{w}} " for v, (_, w, align) in zip(valores, colunas)]
        return "│".join(celulas)

    titulo = " RANKING COMPARATIVO — jogos ordenados por total de prêmios (≥11 acertos)"
    header = montar_linha([nome for nome, _, _ in colunas])
    largura = max(len(titulo) + 1, len(header))
    linha_dupla = "═" * largura
    linha_simples = "┼".join("─" * (w + 2) for _, w, _ in colunas)

    linhas = [linha_dupla, titulo, linha_dupla, header, linha_simples]

    for pos, (nome, res) in enumerate(ordenado, start=1):
        c = res["contagem"]
        total = res["total_premios"]
        pct = (total / n_sorteios * 100) if n_sorteios else 0.0
        melhor = res["melhor"]
        melhor_txt = f"{melhor['acertos']} (c.{melhor['concurso']})" if melhor["concurso"] else "—"
        linhas.append(montar_linha([
            str(pos), nome, str(c[15]), str(c[14]), str(c[13]), str(c[12]), str(c[11]),
            str(total), f"{pct:.1f}%", melhor_txt,
        ]))

    linhas.append("")
    return linhas


# ─── main ──────────────────────────────────────────────────────────────────────

def simular(input_path: str, output_path: str):
    if not Path(input_path).exists():
        print(f"Arquivo '{input_path}' não encontrado. Rode primeiro: python lotofacil_coletar.py", file=sys.stderr)
        sys.exit(1)

    rows = carregar(input_path)
    sorteios = [dezenas(r) for r in rows]
    n_sorteios = len(sorteios)
    print(f"Carregados {n_sorteios} sorteios de '{input_path}'.\n")

    resultados = {}
    saida = [f"Simulação de desempenho — {n_sorteios} sorteios "
             f"(concursos {rows[0]['concurso']} a {rows[-1]['concurso']})\n"]

    for nome, numeros in JOGOS.items():
        conjunto = set(numeros)
        resultado = calcular_jogo(conjunto, rows, sorteios)
        resultados[nome] = resultado
        linhas = formatar_jogo(nome, numeros, resultado, n_sorteios)
        for linha in linhas:
            print(linha)
        saida.extend(linhas)

    linhas_ranking = formatar_ranking(resultados, n_sorteios)
    for linha in linhas_ranking:
        print(linha)
    saida.extend(linhas_ranking)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(saida))

    print(f"✓ Relatório completo salvo em '{output_path}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simula o desempenho histórico de jogos da Lotofácil")
    parser.add_argument("--input", default="lotofacil_sorteios.csv")
    parser.add_argument("--output", default="lotofacil_resultado_jogos.txt")
    args = parser.parse_args()

    simular(args.input, args.output)
