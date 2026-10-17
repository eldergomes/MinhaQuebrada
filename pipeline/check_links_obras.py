"""Monta e verifica os links de consulta das obras federais (ObrasGov.br).

Para cada projeto do periodo 2023-2026:
  * ficha do projeto nos dados abertos do ObrasGov.br (API publica, pelo idUnico);
  * convenio no Portal da Transparencia, quando a geometria traz o numero do instrumento
    do Transferegov ("... da proposta 025782/2018 e instrumento 867157").
Cada link e aberto e conferido (ficha: o JSON traz o idUnico; convenio: a pagina e o
detalhamento do registro). Saida: raw/links_obras.json -> {idUnico: [{rotulo, href}]}.
Cache incremental: rodar de novo so confere o que falta. Uso: python3 -I pipeline/check_links_obras.py [--sem-rede]
"""
import json
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

RAW = Path(__file__).resolve().parent / "raw"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
API = "https://api.obrasgov.gestao.gov.br/obrasgov/api/projeto-investimento"
TRANSP = "https://portaldatransparencia.gov.br/convenios/"


def baixar(url):
    for tentativa in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            time.sleep(10 * (tentativa + 1))  # a API do ObrasGov limita a taxa (429)
    return None


def no_navegador(url):
    """HTML renderizado pelo Chromium headless: o Portal da Transparencia responde 202 com
    desafio em JavaScript para clientes HTTP simples."""
    try:
        r = subprocess.run(["chromium", "--headless=new", "--disable-gpu", "--no-sandbox",
                            "--virtual-time-budget=12000", "--dump-dom", url],
                           capture_output=True, text=True, timeout=90)
        return r.stdout
    except Exception:
        return ""


def confere(item):
    """True = conferido; False = a pagina abriu e nao e o registro; None = bloqueio, tentar depois."""
    tipo, chave, url = item
    if tipo == "ficha":
        corpo = baixar(url)
        return bool(corpo) and f'"idUnico":"{chave}"' in corpo
    corpo = no_navegador(url)
    if f"Registro: {chave}" in corpo:
        return True
    if "Human Verification" in corpo or "verify" in corpo.lower() and "human" in corpo.lower():
        return None  # CAPTCHA anti-robo: pendente, nao e falha
    return False if "Portal da Transparência" in corpo and "<title>" in corpo else None


def main():
    projetos = {p["idUnico"]: p for p in json.loads((RAW / "obrasgov/projetos_AC.json").read_text())}
    geoms = json.loads((RAW / "obrasgov/geometrias_AC.json").read_text())
    saida_p = RAW / "links_obras.json"
    cache_p = RAW / "links_obras_cache.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    ids = [i for i, p in projetos.items()
           if "2023-01-01" <= (p.get("dataInicialEfetiva") or p.get("dataInicialPrevista") or p.get("dataCadastro") or "") <= "2026-12-31"]
    itens = []
    for i in ids:
        itens.append(("ficha", i, f"{API}?{urllib.parse.urlencode({'idUnico': i})}"))
        info = " ".join((g.get("infoAdicionais") or "") for g in geoms.get(i) or [])
        if (m := re.search(r"instrumento (\d{6,})", info)):
            itens.append(("convenio", m.group(1), TRANSP + m.group(1)))
    faltam = [x for x in itens if x[2] not in cache or cache[x[2]] is None]  # refaz pendentes
    if "--sem-rede" in sys.argv:  # so regera a saida a partir do cache
        faltam = []
    print(f"{len(itens)} links, {len(faltam)} a conferir", file=sys.stderr)
    fichas = [x for x in faltam if x[0] == "ficha"]
    convenios = [x for x in faltam if x[0] == "convenio"]
    for n, x in enumerate(fichas, 1):  # sequencial: a API do ObrasGov limita a taxa
        cache[x[2]] = confere(x)
        time.sleep(0.6)
        if n % 50 == 0:
            cache_p.write_text(json.dumps(cache))
            print(f"fichas {n}/{len(fichas)}", file=sys.stderr, flush=True)
    with ThreadPoolExecutor(3) as ex:
        for n, (x, ok) in enumerate(zip(convenios, ex.map(confere, convenios)), 1):
            cache[x[2]] = ok  # None = pendente (bloqueio/CAPTCHA)
            if n % 25 == 0:
                cache_p.write_text(json.dumps(cache))
                print(f"convenios {n}/{len(convenios)}", file=sys.stderr, flush=True)
    cache_p.write_text(json.dumps(cache))
    saida = {}
    for i in ids:
        links = [{"rotulo": "Ficha no ObrasGov.br (dados abertos)", "href": f"{API}?{urllib.parse.urlencode({'idUnico': i})}"}]
        info = " ".join((g.get("infoAdicionais") or "") for g in geoms.get(i) or [])
        if (m := re.search(r"instrumento (\d{6,})", info)):
            links.append({"rotulo": f"Convênio {m.group(1)} no Portal da Transparência", "href": TRANSP + m.group(1)})
        # ficha: so entra conferida. Convenio: conferido ou pendente (CAPTCHA do Portal); sai se a
        # pagina abriu e nao era o registro (False)
        saida[i] = [{**l, "conferido": bool(cache.get(l["href"]))} for l in links
                    if cache.get(l["href"]) or ("convenios" in l["href"] and cache.get(l["href"]) is None)]
    saida_p.write_text(json.dumps(saida, ensure_ascii=False))
    ok = sum(1 for v in cache.values() if v)
    print(f"{ok}/{len(cache)} links conferidos; {sum(1 for v in saida.values() if not v)} projetos sem link valido",
          file=sys.stderr)


if __name__ == "__main__":
    main()
