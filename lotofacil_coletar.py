"""
lotofacil_coletar.py
--------------------
Busca os últimos N sorteios da Lotofácil via API oficial da Caixa e salva em CSV.

Uso:
    python lotofacil_coletar.py              # últimos 500 sorteios (padrão)
    python lotofacil_coletar.py --qtd 300    # últimos 300 sorteios
    python lotofacil_coletar.py --output meus_dados.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.request import urlopen, Request
from urllib.error import URLError

BASE_URL = "https://servicebus2.caixa.gov.br/portaldeloterias/api/lotofacil"
MAX_WORKERS = 10        # requisições paralelas
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2         # segundos entre tentativas


def fetch_concurso(numero: int) -> dict | None:
    """Busca um concurso específico. Retorna None se falhar após retries."""
    url = f"{BASE_URL}/{numero}" if numero else BASE_URL
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})

    for attempt in range(RETRY_ATTEMPTS):
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data
        except (URLError, Exception) as e:
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
            else:
                print(f"  [ERRO] concurso {numero}: {e}", file=sys.stderr)
                return None


def parse_concurso(data: dict) -> dict:
    """Extrai os campos relevantes de um concurso."""
    dezenas = data.get("listaDezenas") or []
    ordem = data.get("dezenasSorteadasOrdemSorteio") or []

    row = {
        "concurso": data.get("numero"),
        "data": data.get("dataApuracao"),
        "acumulado": data.get("acumulado"),
        "valor_premio_1": None,
        "ganhadores_1": None,
    }

    # prêmio da faixa 1 (15 acertos)
    for faixa in data.get("listaRateioPremio") or []:
        if faixa.get("faixa") == 1:
            row["valor_premio_1"] = faixa.get("valorPremio")
            row["ganhadores_1"] = faixa.get("numeroDeGanhadores")
            break

    # dezenas em ordem crescente (d01..d15)
    for i, d in enumerate(sorted(dezenas), start=1):
        row[f"d{i:02d}"] = int(d)

    # dezenas em ordem de sorteio (s01..s15)
    for i, d in enumerate(ordem, start=1):
        row[f"s{i:02d}"] = int(d)

    return row


def coletar(qtd: int = 300, output: str = "lotofacil_sorteios.csv") -> list[dict]:
    # 1. descobre o último concurso
    print("Buscando último concurso...")
    ultimo = fetch_concurso(None)
    if not ultimo:
        print("Falha ao buscar último concurso.", file=sys.stderr)
        sys.exit(1)

    ultimo_num = ultimo["numero"]
    print(f"Último concurso: {ultimo_num} ({ultimo['dataApuracao']})")

    # 2. lista de concursos a buscar
    inicio = max(1, ultimo_num - qtd + 1)
    numeros = list(range(inicio, ultimo_num + 1))
    print(f"Coletando concursos {inicio} → {ultimo_num} ({len(numeros)} sorteios)...")

    resultados: dict[int, dict] = {}

    # 3. coleta paralela
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(fetch_concurso, n): n for n in numeros}
        concluidos = 0
        for future in as_completed(futures):
            n = futures[future]
            data = future.result()
            if data:
                resultados[n] = parse_concurso(data)
            concluidos += 1
            if concluidos % 50 == 0 or concluidos == len(numeros):
                print(f"  {concluidos}/{len(numeros)} concursos coletados")

    # 4. ordena e salva CSV
    rows = [resultados[n] for n in sorted(resultados.keys())]

    if not rows:
        print("Nenhum dado coletado.", file=sys.stderr)
        sys.exit(1)

    fieldnames = list(rows[0].keys())
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n✓ {len(rows)} sorteios salvos em '{output}'")
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Coleta sorteios da Lotofácil")
    parser.add_argument("--qtd", type=int, default=500, help="Quantidade de sorteios (padrão: 500)")
    parser.add_argument("--output", default="lotofacil_sorteios.csv", help="Arquivo de saída CSV")
    args = parser.parse_args()

    coletar(qtd=args.qtd, output=args.output)
