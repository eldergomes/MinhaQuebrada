"""Coleta projetos de investimento do ObrasGov.br (Ministerio da Gestao) para o Acre,
com a geometria (ponto/poligono) de cada projeto.

Saida: raw/obrasgov/projetos_AC.json e raw/obrasgov/geometrias_AC.json
API publica, sem chave: https://api.obrasgov.gestao.gov.br/obrasgov/api
A API limita a taxa (HTTP 429 sem Retry-After): coleta sequencial, com pausa e cache
incremental — rodar de novo retoma de onde parou. `--projetos` recoleta a lista.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.obrasgov.gestao.gov.br/obrasgov/api"
OUT = Path(__file__).resolve().parent / "raw" / "obrasgov"
PAUSA = 0.6


def get(path, **params):
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    espera = 20
    for _ in range(12):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "minha-quebrada/0.1"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            if e.code != 429 and e.code < 500:
                print(f"HTTP {e.code} {url}", file=sys.stderr)
                return None
        except Exception as e:  # rede instavel
            print(f"erro {e} {url}", file=sys.stderr)
        time.sleep(espera)
        espera = min(espera * 2, 300)
    print(f"desisti {url}", file=sys.stderr)
    return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    pj = OUT / "projetos_AC.json"
    if "--projetos" in sys.argv or not pj.exists():
        projetos, pagina = [], 0
        while True:
            itens = (get("projeto-investimento", uf="AC", pagina=pagina, tamanhoDaPagina=100) or {}).get("content") or []
            projetos += itens
            print(f"pagina {pagina}: {len(itens)} (total {len(projetos)})", file=sys.stderr)
            if len(itens) < 100:
                break
            pagina += 1
            time.sleep(PAUSA)
        pj.write_text(json.dumps(projetos, ensure_ascii=False))
    projetos = json.loads(pj.read_text())

    gj = OUT / "geometrias_AC.json"
    geoms = json.loads(gj.read_text()) if gj.exists() else {}
    faltam = sorted({p["idUnico"] for p in projetos} - {k for k, v in geoms.items() if v is not None})
    print(f"{len(faltam)} geometrias a coletar", file=sys.stderr)
    for i, id_ in enumerate(faltam, 1):
        geoms[id_] = get("geometria", idUnico=id_)
        if i % 50 == 0 or i == len(faltam):
            gj.write_text(json.dumps(geoms, ensure_ascii=False))
            print(f"{i}/{len(faltam)}", file=sys.stderr, flush=True)
        time.sleep(PAUSA)
    gj.write_text(json.dumps(geoms, ensure_ascii=False))
    print(f"{len(projetos)} projetos, {sum(1 for g in geoms.values() if g)} com geometria", file=sys.stderr)


if __name__ == "__main__":
    main()
