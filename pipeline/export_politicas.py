"""Exporta a curadoria de politicas federais no AC (planilha do projeto eleicoes) para JSON.

Roda com o python do sistema (tem openpyxl): python3 -I pipeline/export_politicas.py
Saida: raw/politicas_xlsx.json  ->  {aba: [ {coluna: valor} ]}
"""
import json
from pathlib import Path

import openpyxl

XLSX = Path.home() / "dev/eleicoes/data/relatorios/AC_politicas_temas_cury.xlsx"
OUT = Path(__file__).resolve().parent / "raw" / "politicas_xlsx.json"

wb = openpyxl.load_workbook(XLSX, read_only=True)
abas = {}
for ws in wb:
    rows = list(ws.iter_rows(values_only=True))
    cab = [str(c or "") for c in rows[0]]
    abas[ws.title] = [dict(zip(cab, r)) for r in rows[1:] if any(r)]
OUT.write_text(json.dumps(abas, ensure_ascii=False, default=str, indent=1))
print(f"{sum(len(v) for v in abas.values())} linhas em {len(abas)} abas -> {OUT}")
