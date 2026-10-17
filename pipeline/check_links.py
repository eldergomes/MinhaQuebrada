"""Verifica os links da curadoria de politicas (raw/politicas_xlsx.json) e escolhe o melhor
link de cada fonte. Saida: raw/links_politicas.json -> {url: {situacao, href, nota}}.

situacao:
  ok            abre normalmente
  arquivo       original fora do ar; aponta para a copia do Internet Archive (Wayback Machine)
  substituto    original fora do ar e sem copia; fonte alternativa conferida manualmente
  indisponivel  original fora do ar, sem copia nem substituto verificado

Fora do ar inclui redirecionamento para tela de login (paginas despublicadas no gov.br) e o
portal agencia.ac.gov.br, suspenso pela legislacao eleitoral (Lei 9.504/1997) em out/2026.
Uso: python3 -I pipeline/check_links.py
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

# Conferidos manualmente em 16/10/2026 (a pagina abre e confirma o fato citado).
SUBSTITUTOS = {
    "https://www.gov.br/mulheres/pt-br/central-de-conteudos/noticias/2025/janeiro/primeiro-centro-de-referencia-da-mulher-brasileira-da-regiao-norte-e-inaugurado-em-cruzeiro-do-sul-ac":
        ("https://pt.org.br/regiao-norte-ganha-primeiro-centro-de-referencia-da-mulher-brasileira/",
         "fonte alternativa (o original do Ministério das Mulheres está fora do ar)"),
    "https://www.gov.br/secom/pt-br/assuntos/noticias-regionalizadas/mais-medicos/mai.25/novo-edital-do-mais-medicos-preve-25-vagas-em-12-localidades-do-acre":
        ("https://portal.estrategia.com/medicina/mais-medicos/mais-medicos-2025-edital-e-divulgado-com-3-255-vagas-previstas/",
         "fonte do edital nacional; o detalhamento por localidade do Acre não foi reconfirmado"),
}


def abre(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            final = r.geturl()
            return r.status == 200 and "login" not in final and "require_login" not in final
    except Exception:
        return False


CACHE = RAW / "wayback_cache.json"  # {url: snapshot|null}; a API do Wayback e instavel


def wayback(url, cache):
    if cache.get(url):
        return cache[url]
    q = "https://archive.org/wayback/available?" + urllib.parse.urlencode({"url": url, "timestamp": "20260601"})
    for tentativa in range(5):
        try:
            req = urllib.request.Request(q, headers={"User-Agent": "minha-quebrada/0.1"})
            with urllib.request.urlopen(req, timeout=60) as r:
                s = (json.load(r).get("archived_snapshots") or {}).get("closest")
            cache[url] = s["url"].replace("http://", "https://", 1) if s and s.get("status") == "200" else None
            time.sleep(1.5)
            return cache[url]
        except Exception:
            time.sleep(5 * (tentativa + 1))
    return None


def main():
    abas = json.loads((RAW / "politicas_xlsx.json").read_text())
    urls = sorted({u.strip() for linhas in abas.values() for d in linhas
                   for u in str(d.get("URL") or "").replace(" ;", ";").split(";") if u.strip()})
    with ThreadPoolExecutor(8) as ex:
        status = dict(zip(urls, ex.map(abre, urls)))
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    saida = {}
    for u in urls:
        if status[u]:
            saida[u] = {"situacao": "ok", "href": u, "nota": None}
        elif u in SUBSTITUTOS:
            href, nota = SUBSTITUTOS[u]
            saida[u] = {"situacao": "substituto", "href": href, "nota": nota}
        elif (w := wayback(u, cache)):
            saida[u] = {"situacao": "arquivo", "href": w,
                        "nota": "cópia arquivada; o original está fora do ar (período eleitoral ou página despublicada)"}
        else:
            saida[u] = {"situacao": "indisponivel", "href": u,
                        "nota": "original fora do ar e sem cópia arquivada: não foi possível verificar"}
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    (RAW / "links_politicas.json").write_text(json.dumps(saida, ensure_ascii=False, indent=1))
    from collections import Counter
    print(Counter(v["situacao"] for v in saida.values()), f"({len(urls)} URLs)")


if __name__ == "__main__":
    main()
