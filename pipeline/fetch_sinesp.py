"""Coleta os indicadores municipais de seguranca publica do Sinesp VDE (MJSP) para o Acre.

Saida: raw/sinesp/AC.json -> {ano: {"meses": n, "mun": {MUNICIPIO: {evento: vitimas}}}}
Fonte: "Base de Dados e Notas Metodologicas dos Gestores Estaduais - Sinesp VDE", uma planilha
nacional por ano (~25 MB). So o agregado do Acre fica em disco; a planilha e apagada.
Uso: python3 -I pipeline/fetch_sinesp.py [ano ...]   (padrao: 2023 a 2026; tem openpyxl)
"""
import json
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

import openpyxl

BASE = ("https://www.gov.br/mj/pt-br/assuntos/sua-seguranca/seguranca-publica/estatistica/download/"
        "dnsp-base-de-dados/bancovde-{ano}.xlsx/@@download/file")
HOSTS = {"www.gov.br"}  # o download so pode terminar no portal do MJSP (redirecionamentos incluidos)
LIMITE = 80 * 2**20
OUT = Path(__file__).resolve().parent / "raw" / "sinesp"
# eventos com recorte municipal no Sinesp (os demais so existem por UF)
EVENTOS = {
    "Homicídio doloso", "Feminicídio", "Roubo seguido de morte (latrocínio)", "Lesão corporal seguida de morte",
    "Tentativa de homicídio", "Tentativa de feminicídio",
    "Morte no trânsito ou em decorrência dele (exceto homicídio doloso)",
}


def baixar(ano, destino, tentativas=3):
    url = BASE.format(ano=int(ano))
    req = urllib.request.Request(url, headers={"User-Agent": "minha-quebrada/0.1"})
    for n in range(1, tentativas + 1):
        with urllib.request.urlopen(req, timeout=300) as r:
            final = urllib.parse.urlsplit(r.geturl())
            if final.scheme != "https" or final.hostname not in HOSTS:
                raise ValueError(f"redirecionado para fora do MJSP: {r.geturl()}")
            esperado = int(r.headers.get("Content-Length") or 0)
            if esperado > LIMITE:
                raise ValueError(f"{url}: maior que {LIMITE >> 20} MB")
            lidos = 0
            with open(destino, "wb") as f:
                while bloco := r.read(1 << 20):
                    lidos += len(bloco)
                    if lidos > LIMITE:
                        raise ValueError(f"{url}: maior que {LIMITE >> 20} MB")
                    f.write(bloco)
        # o portal as vezes corta a transferencia: confere tamanho e se o zip esta integro
        if (not esperado or lidos == esperado) and zipfile.is_zipfile(destino):
            return
        print(f"{ano}: download incompleto ({lidos} de {esperado} bytes), tentativa {n}", file=sys.stderr)
        time.sleep(10 * n)
    raise ValueError(f"{url}: nao veio uma planilha xlsx integra")


def extrair(caminho):
    wb = openpyxl.load_workbook(caminho, read_only=True)
    ws = wb.active
    linhas = ws.iter_rows(values_only=True)
    cab = [str(c or "") for c in next(linhas)]
    i = {k: cab.index(k) for k in ("uf", "municipio", "evento", "data_referencia", "total_vitima", "total", "abrangencia")}
    mun, meses = defaultdict(lambda: defaultdict(int)), set()
    for r in linhas:
        if r[i["uf"]] != "AC" or r[i["evento"]] not in EVENTOS or r[i["abrangencia"]] != "Estadual":
            continue
        v = r[i["total_vitima"]] if r[i["total_vitima"]] is not None else r[i["total"]]
        if not isinstance(v, (int, float)) or v < 0 or not isinstance(r[i["municipio"]], str):
            continue
        mun[r[i["municipio"]].strip()[:60]][r[i["evento"]]] += int(v)
        if v and hasattr(r[i["data_referencia"]], "month"):
            meses.add(r[i["data_referencia"]].month)
    wb.close()
    return {"meses": max(meses, default=0), "mun": {m: dict(e) for m, e in sorted(mun.items())}}


def main():
    anos = [int(a) for a in sys.argv[1:]] or [2023, 2024, 2025, 2026]
    OUT.mkdir(parents=True, exist_ok=True)
    saida_p = OUT / "AC.json"
    saida = json.loads(saida_p.read_text()) if saida_p.exists() else {}
    for ano in anos:
        with tempfile.TemporaryDirectory(dir=OUT) as tmp:
            xlsx = Path(tmp) / f"{ano}.xlsx"
            baixar(ano, xlsx)
            saida[str(ano)] = extrair(xlsx)
        d = saida[str(ano)]
        print(f"{ano}: {len(d['mun'])} municipios, meses ate {d['meses']}, "
              f"homicidios {sum(e.get('Homicídio doloso', 0) for e in d['mun'].values())}", file=sys.stderr)
        saida_p.write_text(json.dumps(saida, ensure_ascii=False, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
