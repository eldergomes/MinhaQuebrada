"""Minha Quebrada! — gera os dados estaticos do painel (bairros dos municipios do Acre).

Unidade territorial ("bairro"):
  * municipios com bairros no Censo 2022 (8 no AC): bairros oficiais do IBGE;
  * demais (14): "bairros TSE" — setores censitarios urbanos agrupados pelo bairro
    declarado no local de votacao (TSE) mais proximo; setores rurais -> "Zona rural".
  Em todos os casos a unidade e uma uniao de setores censitarios, de modo que Censo
  (moradores) e urnas (secoes cujo local fica nos setores da unidade) usam o mesmo recorte.

Fontes: IBGE Censo 2022 (agregados por setor, renda do responsavel, entorno; malha
de setores e bairros), TSE 2026 1o turno (boletins de urna, secoes, perfil do eleitorado),
ObrasGov.br (projetos de investimento com geometria), Sinesp VDE/MJSP (seguranca publica por
municipio), curadoria de politicas federais (planilha do projeto eleicoes) e Programa de
Governo Lula 2026 (TSE, 84 p.).

Uso: ~/dev/eleicoes/.venv/bin/python -I pipeline/build.py   (duckdb + shapely)
"""
import csv
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import duckdb
import shapely
from shapely import wkb
from shapely.geometry import Point, mapping

AQUI = Path(__file__).resolve().parent
RAW = AQUI / "raw"
SITE = AQUI.parent
OUT = SITE / "data"
ELEI = Path.home() / "dev/eleicoes/data/current"
DISPUTA = Path.home() / "dev/DisputaTerritorios/data"
LULA, FLAVIO = 13, 22

db = duckdb.connect()


def pq(rel):
    return f"read_parquet('{ELEI / rel}/**/*.parquet', hive_partitioning=false)"


def sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def chave(s):
    s = re.sub(r"\s+", " ", sem_acento(s or "").upper().strip())
    s = re.sub(r"^BAIRRO (DA |DO |DE |DOS |DAS )?", "", s)
    return s


MINUSC = {"da", "de", "do", "das", "dos", "e"}


def titulo(s):
    out = []
    for i, w in enumerate(s.lower().split()):
        if re.fullmatch(r"[ivx]+", w):
            out.append(w.upper())
        elif i and w in MINUSC:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", sem_acento(s).lower()).strip("-")


def r1(v, n=1):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(v, n)


def pct(a, b, n=1):
    return None if not b or a is None else round(100 * a / b, n)


# ------------------------------------------------------------------ Censo por setor
def num(v):
    v = (v or "").strip().strip('"')
    if v in ("", ".", "X", "-"):
        return None
    return float(v.replace(",", "."))


def ler_censo():
    """{cd_setor: {var: valor}} a partir dos CSVs filtrados para o AC."""
    dados = defaultdict(dict)
    for f in sorted((RAW / "ibge/setor").glob("AC_*.csv")):
        with f.open(encoding="utf-8") as fh:
            rd = csv.reader(fh, delimiter=";")
            cab = [c.upper() for c in next(rd)]
            for linha in rd:
                cd = linha[0].strip('"')
                for k, v in zip(cab[1:], linha[1:]):
                    if re.fullmatch(r"V\d+", k):
                        dados[cd][k] = num(v)
    return dados


S = lambda d, *ks: sum(d.get(k) or 0 for k in ks)  # noqa: E731
ALF_TOT = [f"V00{n}" for n in range(644, 657)]
ALF_SIM = [f"V00{n}" for n in range(748, 761)]


def somar(setores, censo):
    """Soma de contagens (e renda ponderada) de uma lista de setores."""
    t = Counter()
    renda_num = renda_den = 0.0
    entorno_mor = 0.0
    for cd in setores:
        d = censo.get(cd, {})
        for k, v in d.items():
            if v is not None and k not in ("V0005", "V0006", "V06003", "V06004", "V06005", "V06006"):
                t[k] += v
        if d.get("V06004") is not None and d.get("V06001"):
            renda_num += d["V06004"] * d["V06001"]
            renda_den += d["V06001"]
        entorno_mor += d.get("V05200") or 0
    t["_renda_num"], t["_renda_den"], t["_entorno"] = renda_num, renda_den, entorno_mor
    return t


def indicadores(t, area):
    pop = t.get("V0001") or 0
    mor = t.get("V01006") or 0
    dppo = t.get("V00001") or 0
    alf_t = S(t, *ALF_TOT)
    ent = t["_entorno"]
    return {
        "pop": int(pop),
        "domicilios": int(t.get("V0007") or 0),
        "area_km2": r1(area, 2),
        "densidade": r1(pop / area if area else None, 0),
        "mor_dom": r1(mor / dppo if dppo else None, 2),
        "mulheres_pct": pct(t.get("V01008"), mor),
        "criancas_pct": pct(S(t, "V01031", "V01032", "V01033"), mor),
        "jovens_pct": pct(S(t, "V01034", "V01035", "V01036"), mor),
        "idosos_pct": pct(S(t, "V01040", "V01041"), mor),
        "pretos_pardos_pct": pct(S(t, "V01318", "V01320"), S(t, *[f"V0131{i}" for i in range(7, 10)], "V01320", "V01321")),
        "indigenas_pct": pct(t.get("V01321"), S(t, *[f"V0131{i}" for i in range(7, 10)], "V01320", "V01321")),
        "alfabetizacao_pct": pct(S(t, *ALF_SIM), alf_t),
        "renda_resp": r1(t["_renda_num"] / t["_renda_den"] if t["_renda_den"] else None, 0),
        "agua_rede_pct": pct(t.get("V00111"), dppo),
        "saneamento_adequado_pct": pct(S(t, "V00309", "V00310"), dppo),
        "saneamento_precario_pct": pct(S(t, "V00312", "V00313", "V00314", "V00315", "V00316"), dppo),
        "sem_banheiro_pct": pct(t.get("V00238"), dppo),
        "lixo_coletado_pct": pct(S(t, "V00397", "V00398"), dppo),
        "pavimentacao_pct": pct(t.get("V05206"), ent) if ent else None,
        "iluminacao_pct": pct(t.get("V05212"), ent) if ent else None,
        "calcada_pct": pct(t.get("V05221"), ent) if ent else None,
        "bueiro_pct": pct(t.get("V05209"), ent) if ent else None,
        # contagens absolutas: dimensionam o que cada compromisso do plano significa no territorio
        "n": {
            "dppo": int(dppo),
            "dom_sem_agua": int(dppo - (t.get("V00111") or 0)),
            "dom_sem_saneamento": int(dppo - S(t, "V00309", "V00310")),
            "dom_sem_banheiro": int(t.get("V00238") or 0),
            "dom_sem_lixo": int(dppo - S(t, "V00397", "V00398")),
            "nao_alfabetizados": int(alf_t - S(t, *ALF_SIM)),
            "criancas_0_4": int(t.get("V01031") or 0),
            "criancas_0_14": int(S(t, "V01031", "V01032", "V01033")),
            "jovens_15_19": int(t.get("V01034") or 0),
            "jovens_15_29": int(S(t, "V01034", "V01035", "V01036")),
            "idosos": int(S(t, "V01040", "V01041")),
            "indigenas": int(t.get("V01321") or 0),
            "mor_entorno": int(ent),
            "mor_sem_pavimento": int(t.get("V05207") or 0),
            "mor_sem_iluminacao": int(t.get("V05213") or 0),
            "mor_sem_bueiro": int(t.get("V05210") or 0),
            "responsaveis": int(t["_renda_den"]),
        },
    }


INDICADORES = [  # k, rotulo, sufixo, grupo, bom (+1 maior e melhor, -1 menor e melhor, 0 neutro)
    ("pop", "Moradores", "", "Demografia", 0),
    ("densidade", "Densidade", " hab/km²", "Demografia", 0),
    ("mulheres_pct", "Mulheres", "%", "Demografia", 0),
    ("criancas_pct", "Crianças (0–14 anos)", "%", "Demografia", 0),
    ("jovens_pct", "Jovens (15–29 anos)", "%", "Demografia", 0),
    ("idosos_pct", "Idosos (60+)", "%", "Demografia", 0),
    ("pretos_pardos_pct", "Pretos e pardos", "%", "Demografia", 0),
    ("indigenas_pct", "Indígenas", "%", "Demografia", 0),
    ("renda_resp", "Renda média do responsável", " R$", "Renda e educação", 1),
    ("alfabetizacao_pct", "Alfabetização (15+)", "%", "Renda e educação", 1),
    ("mor_dom", "Moradores por domicílio", "", "Renda e educação", -1),
    ("agua_rede_pct", "Água da rede geral", "%", "Saneamento", 1),
    ("saneamento_adequado_pct", "Saneamento adequado (rede geral ou fossa ligada à rede)", "%", "Saneamento", 1),
    ("saneamento_precario_pct", "Saneamento precário (fossa rudimentar, vala, rio)", "%", "Saneamento", -1),
    ("sem_banheiro_pct", "Domicílios sem banheiro", "%", "Saneamento", -1),
    ("lixo_coletado_pct", "Lixo coletado", "%", "Saneamento", 1),
    ("pavimentacao_pct", "Rua pavimentada", "%", "Entorno urbano", 1),
    ("iluminacao_pct", "Iluminação pública", "%", "Entorno urbano", 1),
    ("calcada_pct", "Calçada", "%", "Entorno urbano", 1),
    ("bueiro_pct", "Bueiro (drenagem)", "%", "Entorno urbano", 1),
]


# ------------------------------------------------------------------ territorio
def atributos_setor():
    """{cd_setor: (CD_TIPO, NM_AGLOM)} do GeoPackage de setores do IBGE (cache em raw/)."""
    cache = RAW / "ibge/setores_AC_atributos.json"
    if not cache.exists():
        db.sql("install spatial; load spatial")
        gpkg = Path.home() / "dev/eleicoes/data/geo/BR_setores_CD2022.gpkg"
        rows = db.sql(f"select CD_SETOR, CD_TIPO, NM_AGLOM from st_read('{gpkg}') where CD_UF = '12'").fetchall()
        cache.write_text(json.dumps({cd: [t, a] for cd, t, a in rows}, ensure_ascii=False))
    return json.loads(cache.read_text())


def carregar_setores():
    rows = db.sql(f"""select cd_setor, cd_mun, nm_mun, cd_bairro, nm_bairro, situacao, area_km2, wkb
                      from {pq('setores/cd_uf=12')}""").fetchall()
    atrib = atributos_setor()
    setores = {}
    for cd, mun, nm_mun, cdb, nmb, sit, area, g in rows:
        geom = wkb.loads(bytes(g))
        tipo, aglom = atrib.get(cd, [None, None])
        setores[cd] = dict(cd=cd, mun=mun, nm_mun=nm_mun, cd_bairro=cdb, nm_bairro=nmb,
                           urbana=(sit == "Urbana"), area=area or 0, geom=geom,
                           pt=geom.representative_point(), tipo=tipo, aglom=(aglom or "").strip() or None)
    return setores


def carregar_secoes():
    """Secoes principais (com urna) + local, bairro TSE, setor e resultados."""
    q = f"""
      with s as (select * from {pq('secoes/uf=AC')}),
           g as (select id_secao, cd_setor, cd_mun_ibge, situacao_espacial from {pq('secao_geo/uf=AC')}),
           u as (select * from {pq('urnas/uf=AC')} where tem_urna_propria),
           v as (select id_secao,
                        sum(votos) filter (numero_candidato = {LULA}) lula,
                        sum(votos) filter (numero_candidato = {FLAVIO}) flavio,
                        sum(votos) validos
                 from {pq('votos/uf=AC')} where cargo = 'Presidente' and turno = 1 group by 1)
      select u.id_secao, s.codigo_municipio, s.municipio, s.codigo_local, s.local_votacao, s.bairro,
             s.latitude, s.longitude, g.cd_setor, g.cd_mun_ibge,
             u.aptos_bu, u.comparecimento, u.abstencoes, u.votos_brancos, u.votos_nulos,
             coalesce(v.lula, 0), coalesce(v.flavio, 0), coalesce(v.validos, 0)
      from u join s using (id_secao) left join g using (id_secao) left join v using (id_secao)"""
    cols = "id mun_tse municipio local nome_local bairro lat lon cd_setor cd_mun aptos comp abst brancos nulos lula flavio validos".split()
    return [dict(zip(cols, r)) for r in db.sql(q).fetchall()]


def carregar_locais():
    """Todos os locais de votacao (inclui os so com secoes agregadas) com bairro TSE e setor."""
    q = f"""select s.codigo_municipio, s.codigo_local, any_value(s.bairro), any_value(g.cd_setor),
                   any_value(s.latitude), any_value(s.longitude), any_value(g.cd_mun_ibge),
                   any_value(s.local_votacao), any_value(s.endereco)
            from {pq('secoes/uf=AC')} s left join {pq('secao_geo/uf=AC')} g using (id_secao)
            group by 1, 2"""
    return [dict(zip("mun_tse local bairro cd_setor lat lon cd_mun nome endereco".split(), r)) for r in db.sql(q).fetchall()]


RURAL_TSE = {"ZONA RURAL", "RURAL", "AREA RURAL", "COLONIA", ""}


def unidades(setores, locais):
    """Atribui cada setor a uma unidade (bairro). Retorna {cd_setor: uid}, {uid: meta}."""
    por_mun = defaultdict(list)
    for s in setores.values():
        por_mun[s["mun"]].append(s)
    setor_uid, meta = {}, {}
    for mun, lista in por_mun.items():
        ibge = any(s["cd_bairro"] for s in lista)
        rural_uid = f"{mun}-zona-rural"
        if ibge:
            for s in lista:
                if s["cd_bairro"]:
                    uid = s["cd_bairro"]
                    meta.setdefault(uid, dict(id=uid, mun=mun, nome=s["nm_bairro"], fonte="IBGE", rural=False))
                else:
                    uid = rural_uid
                    meta.setdefault(uid, dict(id=uid, mun=mun, nome="Zona rural", fonte="IBGE", rural=True))
                setor_uid[s["cd"]] = uid
            continue
        # sem bairros IBGE: ancoras = locais em setor urbano com bairro TSE nao rural
        ancoras, grafias = [], defaultdict(Counter)
        for l in locais:
            if l["cd_mun"] != mun or l["cd_setor"] not in setores or not setores[l["cd_setor"]]["urbana"]:
                continue
            k = chave(l["bairro"])
            if k in RURAL_TSE:
                continue
            grafias[k][l["bairro"].strip()] += 1
            ancoras.append((k, l["lon"], l["lat"]))
        kx = math.cos(math.radians(-9))
        for s in lista:
            if not s["urbana"] or not ancoras:
                uid = rural_uid
                meta.setdefault(uid, dict(id=uid, mun=mun, nome="Zona rural", fonte="TSE", rural=True))
            else:
                x, y = s["pt"].x, s["pt"].y
                k = min(ancoras, key=lambda a: ((a[1] - x) * kx) ** 2 + (a[2] - y) ** 2)[0]
                uid = f"{mun}-{slug(k)}"
                nome = titulo(grafias[k].most_common(1)[0][0])
                meta.setdefault(uid, dict(id=uid, mun=mun, nome=nome, fonte="TSE", rural=False))
            setor_uid[s["cd"]] = uid
    return setor_uid, meta


# Tipos de localidade rural, na ordem de prioridade, com o trecho do endereco que da o nome.
TIPOS_RURAIS = [
    ("Aldeia", r"ALDEIA(?: IND[IÍ]GENA)? [^,;()\-]+|TERRA IND[IÍ]GENA [^,;()\-]+"),
    ("Assentamento", r"AGROVILA[^,;()]*|PROJETO DE ASSENTAMENTO [^,;()\-]+|\bPAD? [A-ZÀ-Ú][^,;()\-]+|POLO [^,;()\-]+"),
    ("Ramal", r"RAMAL(?! S/N)(?: D[AOE]S?)? [^,;()\-]+?(?= KM|,| -|$| S/N)|\bRM [^,;()\-]+?(?= KM|,| -|$)"),
    ("Seringal", r"SERINGAL [^,;()\-]+|COLOCA[CÇ][AÃ]O [^,;()\-]+"),
    ("Comunidade ribeirinha", r"(?:COMUNIDADE|VILA) [^,;()\-]+(?= ?- ?RIO| RIO| IGARAP)|(?:ALTO |BAIXO |M[EÉ]DIO )?(?:RIO|IGARAP[EÉ]|PARAN[AÁ]) [^,;()\-]+"),
    ("Vila ou comunidade", r"(?:COMUNIDADE|VILA|POVOADO|DISTRITO) [^,;()\-]+"),
    ("Rodovia", r"(?:BR|AC|RODOVIA AC|RODOVIA BR)[ -]?\d{2,3}[ ,]*(?:KM[ -]?\d+)?"),
]
TIPO_AGLOM = [("Aldeia", "ALDEIA"), ("Assentamento", "AGROVILA"), ("Assentamento", "POLO"), ("Assentamento", "PÓLO"),
              ("Vila ou comunidade", "")]


def limpar_nome(seg):
    n = titulo(re.sub(r"\s+", " ", seg).strip(" -,.;"))
    n = re.sub(r"\b(Br|Ac|Pad?|Ii|Iii)\b", lambda m: m.group(1).upper(), n)
    n = re.sub(r"\bKm\b", "km", n)
    return re.sub(r"\s+km$", "", n)


MUNICIPIOS_AC = set()  # chaves dos nomes de municipio (preenchido em main): "RIO BRANCO" nao e rio


def nome_rural(local, setor):
    """(tipo, nome) de uma localidade rural a partir do aglomerado IBGE ou do endereco TSE."""
    if setor.get("aglom"):
        a = setor["aglom"]
        tipo = next(t for t, p in TIPO_AGLOM if p in sem_acento(a).upper() or not p)
        if setor.get("tipo") == "5":
            tipo = "Aldeia"
        return tipo, a
    for texto in (local.get("endereco") or "", local.get("nome") or ""):
        t = re.sub(r"\s+", " ", texto.upper())
        for tipo, pat in TIPOS_RURAIS:
            achados = [a for a in re.findall(pat, t) if chave(a) not in MUNICIPIOS_AC and
                       chave(re.sub(r"^(ALTO |BAIXO |M[EÉ]DIO )?(RIO|COMUNIDADE|VILA) ", "", a)) not in MUNICIPIOS_AC]
            if achados:
                return tipo, limpar_nome(achados[-1] if tipo == "Ramal" else achados[0])
    return "Localidade", limpar_nome(local.get("nome") or local.get("endereco") or "Localidade rural")


def localidades_rurais(setores, locais, setor_uid, meta_u, locais_com_urna):
    """Subdivide a zona rural: cada setor rural recebe a localidade (ramal, aldeia, seringal,
    comunidade...) do local de votacao rural mais proximo. Altera setor_uid e meta_u."""
    kx = math.cos(math.radians(-9))
    for rural_uid in [u for u, m in meta_u.items() if m["rural"]]:
        mun = meta_u[rural_uid]["mun"]
        cds = [cd for cd, u in setor_uid.items() if u == rural_uid]
        ancoras = []
        for l in locais:
            if (l["cd_mun"] == mun and setor_uid.get(l["cd_setor"]) == rural_uid and l["lat"] is not None
                    and (l["mun_tse"], l["local"]) in locais_com_urna):
                tipo, nome = nome_rural(l, setores[l["cd_setor"]])
                ancoras.append((f"{mun}-r-{slug(nome)}"[:80], tipo, nome, l["lon"], l["lat"], l["cd_setor"]))
        if not ancoras:
            continue
        for cd in cds:
            x, y = setores[cd]["pt"].x, setores[cd]["pt"].y
            # setor que contem local de votacao fica com ele (as secoes desse local caem neste setor)
            proprias = [a for a in ancoras if a[5] == cd] or ancoras
            uid, tipo, nome, *_ = min(proprias, key=lambda a: ((a[3] - x) * kx) ** 2 + (a[4] - y) ** 2)
            setor_uid[cd] = uid
            meta_u.setdefault(uid, dict(id=uid, mun=mun, nome=nome, tipo=tipo, fonte=meta_u[rural_uid]["fonte"],
                                        rural=True, nome_base=nome, agrega=[]))
        # dois locais no mesmo setor: o que ficou sem o setor (e sem as proprias secoes) e
        # agregado a localidade dona do setor
        for a in ancoras:
            dono = setor_uid[a[5]]
            if dono != a[0] and a[0] in meta_u:
                for cd in cds:
                    if setor_uid[cd] == a[0]:
                        setor_uid[cd] = dono
                meta_u[dono]["agrega"].append(meta_u.pop(a[0])["nome"])
            elif dono != a[0] and a[2] not in meta_u[dono]["agrega"] and a[2] != meta_u[dono]["nome_base"]:
                meta_u[dono]["agrega"].append(a[2])
        for uid in {a[0] for a in ancoras} & meta_u.keys():
            ag = sorted(set(meta_u[uid]["agrega"]))
            meta_u[uid]["agrega"] = ag
            if ag:
                meta_u[uid]["nome"] = f"{meta_u[uid]['nome_base']} + {ag[0]}" if len(ag) == 1 else \
                    f"{meta_u[uid]['nome_base']} + {len(ag)} localidades"
        del meta_u[rural_uid]


def agregar_sem_secao(setores, setor_uid, meta_u, secoes):
    """Bairro urbano sem local de votacao nao tem resultado proprio: e agregado ao bairro
    vizinho com quem divide a maior fronteira (ou, sem vizinho, ao mais proximo).
    Repete ate que todo bairro urbano tenha secoes. Altera setor_uid e meta_u."""
    n_sec = Counter(setor_uid.get(s["cd_setor"]) for s in secoes)
    partes = defaultdict(list)
    for cd, uid in setor_uid.items():
        partes[uid].append(setores[cd]["geom"])
    geo = {u: shapely.union_all(g) for u, g in partes.items()}
    for u in meta_u.values():
        u.setdefault("nome_base", u["nome"])
        u.setdefault("agrega", [])
    while True:
        orfas = [u for u in geo if not meta_u[u]["rural"] and not n_sec[u]]
        if not orfas:
            break
        u = min(orfas, key=lambda x: geo[x].area)
        mun = meta_u[u]["mun"]
        cands = [v for v in geo if v != u and meta_u[v]["mun"] == mun and not meta_u[v]["rural"]]
        if cands:
            borda = {v: geo[u].boundary.intersection(geo[v].buffer(1e-7)).length for v in cands}
            host = max(borda, key=borda.get) if max(borda.values()) > 0 else \
                min(cands, key=lambda v: geo[u].distance(geo[v]))
        else:
            host = f"{mun}-zona-rural"
        for cd, uid in setor_uid.items():
            if uid == u:
                setor_uid[cd] = host
        geo[host] = shapely.union_all([geo[host], geo.pop(u)])
        meta_u[host]["agrega"] += [meta_u[u]["nome_base"]] + meta_u[u]["agrega"]
        del meta_u[u]
    for u in meta_u.values():
        if u["agrega"] and not u["rural"]:
            ag = sorted(u["agrega"])
            u["nome"] = f"{u['nome_base']} + {ag[0]}" if len(ag) == 1 else \
                f"{u['nome_base']} + {', '.join(ag[:-1])} e {ag[-1]}" if len(ag) <= 3 else \
                f"{u['nome_base']} + {len(ag)} bairros vizinhos"


# ------------------------------------------------------------------ perfil do eleitorado
FAIXAS = [("16–24", 16, 24), ("25–34", 25, 34), ("35–44", 35, 44), ("45–59", 45, 59), ("60+", 60, 200)]
ESCOL = {
    "ANALFABETO": "Até fundamental incompleto", "LÊ E ESCREVE": "Até fundamental incompleto",
    "ENSINO FUNDAMENTAL INCOMPLETO": "Até fundamental incompleto",
    "ENSINO FUNDAMENTAL COMPLETO": "Fundamental completo ou médio incompleto",
    "ENSINO MÉDIO INCOMPLETO": "Fundamental completo ou médio incompleto",
    "ENSINO MÉDIO COMPLETO": "Médio completo ou superior incompleto",
    "SUPERIOR INCOMPLETO": "Médio completo ou superior incompleto",
    "SUPERIOR COMPLETO": "Superior completo",
}
DIMENSOES = {
    "genero": ["Mulheres", "Homens"],
    "idade": [f[0] for f in FAIXAS],
    "escolaridade": ["Até fundamental incompleto", "Fundamental completo ou médio incompleto",
                     "Médio completo ou superior incompleto", "Superior completo"],
}


def faixa(rotulo):
    m = re.match(r"(\d+)", rotulo or "")
    if not m:
        return None
    i = int(m.group(1))
    return next((f for f, a, b in FAIXAS if a <= i <= b), None)


def perfil_por_secao():
    """{id_secao_principal: {dim: {cat: eleitores}}} (agregadas somadas a principal)."""
    q = f"""select 'AC|' || codigo_municipio || '|' || zona || '|' || secao_principal_geo, genero, faixa_etaria,
                   grau_escolaridade, sum(eleitores_perfil)
            from {pq('perfil_secao/uf=AC')} group by all"""
    out = defaultdict(lambda: {d: Counter() for d in DIMENSOES})
    for sid, gen, fx, esc, n in db.sql(q).fetchall():
        p = out[sid]
        p["genero"]["Mulheres" if gen == "FEMININO" else "Homens"] += n
        if (f := faixa(fx)):
            p["idade"][f] += n
        if esc in ESCOL:
            p["escolaridade"][ESCOL[esc]] += n
    return out


def perfil_unidade(secs, perfis):
    """Composicao do eleitorado e voto estimado de cada grupo em Lula e Flavio.

    voto_X[cat] = sum_s n_s(cat) * share_X(s) / sum_s n_s(cat): resultado medio das secoes
    (% dos votos nominais), ponderado pelo numero de eleitores do grupo em cada secao.
    Inferencia ecologica: o voto e secreto; a estimativa diz como votaram os lugares onde o
    grupo esta e tende a encolher as diferencas reais entre grupos.
    """
    res = {}
    for dim, cats in DIMENSOES.items():
        tot, wl, wf = Counter(), Counter(), Counter()
        for s in secs:
            p = perfis.get(s["id"], {}).get(dim)
            if not p or not s["validos"]:
                continue
            for c in cats:
                tot[c] += p[c]
                wl[c] += p[c] * s["lula"] / s["validos"]
                wf[c] += p[c] * s["flavio"] / s["validos"]
        nt = sum(tot.values())
        res[dim] = {
            "eleitorado": [pct(tot[c], nt) for c in cats],
            "lula": [pct(wl[c], tot[c]) for c in cats],
            "flavio": [pct(wf[c], tot[c]) for c in cats],
        }
    return res


def resultado(secs):
    t = Counter()
    for s in secs:
        for k in ("aptos", "comp", "abst", "brancos", "nulos", "lula", "flavio", "validos"):
            t[k] += s[k] or 0
    ab = t["lula"] + t["flavio"]
    return {
        "secoes": len(secs), "locais": len({s["local"] for s in secs}),
        "aptos": t["aptos"], "comparecimento": t["comp"], "validos": t["validos"],
        "lula": t["lula"], "flavio": t["flavio"], "outros": t["validos"] - ab,
        "brancos": t["brancos"], "nulos": t["nulos"],
        "lula_pct": pct(t["lula"], t["validos"]), "flavio_pct": pct(t["flavio"], t["validos"]),
        "lula_ab_pct": pct(t["lula"], ab),  # Lula no confronto Lula x Flavio
        "abstencao_pct": pct(t["abst"], t["aptos"]),
        "brancos_nulos_pct": pct(t["brancos"] + t["nulos"], t["comp"]),
    } if secs else None


# ------------------------------------------------------------------ politicas
def ler_obras(setores, setor_uid, nomes_mun):
    pj = RAW / "obrasgov/projetos_AC.json"
    if not pj.exists():
        print("AVISO: ObrasGov ainda nao coletado; rode fetch_obrasgov.py")
        return []
    projetos = json.loads(pj.read_text())
    lo = RAW / "links_obras.json"  # check_links_obras.py
    links_obras = json.loads(lo.read_text()) if lo.exists() else {}
    gj = RAW / "obrasgov/geometrias_AC.json"
    geoms = json.loads(gj.read_text()) if gj.exists() else {}
    arvore = shapely.STRtree([s["geom"] for s in setores.values()])
    lista_set = list(setores.values())
    por_nome = {chave(v): k for k, v in nomes_mun.items()}

    def ponto(p):
        for g in geoms.get(p["idUnico"]) or []:
            try:
                geo = shapely.from_wkt(g["geometriaWkt"])
                return geo if geo.geom_type == "Point" else geo.representative_point()
            except Exception:
                pass
        return None

    # ponto repetido em 5+ projetos = endereco do orgao (ex.: FNDE), nao o local da obra
    repetidos = Counter((round(q.x, 4), round(q.y, 4)) for q in map(ponto, projetos) if q is not None)
    obras = []
    for p in projetos:
        fed = sum((f.get("valorInvestimentoPrevisto") or 0) for f in p.get("fontesDeRecurso") or []
                  if (f.get("origem") or "").lower() == "federal")
        sit = p.get("situacao") or ""
        # so 2023-2026: inicio efetivo, ou previsto, ou (sem datas de inicio) o cadastro
        inicio = p.get("dataInicialEfetiva") or p.get("dataInicialPrevista") or p.get("dataCadastro")
        if sit in ("Cancelada", "Inativada") or not inicio or not "2023-01-01" <= inicio <= "2026-12-31" or fed <= 0:
            continue
        pt = ponto(p)
        generico = pt is not None and repetidos[(round(pt.x, 4), round(pt.y, 4))] >= 5
        # municipio declarado no nome do projeto ("... - Brasileia/AC")
        m_nome = re.search(r"[-–]\s*([A-Za-zÀ-ÿ' ]+?)\s*/\s*AC\b", p.get("nome") or "")
        mun_nome = por_nome.get(chave(m_nome.group(1))) if m_nome else None
        mun = uid = None
        if pt is not None and not generico:
            for i in arvore.query(pt, predicate="intersects"):
                mun, uid = lista_set[i]["mun"], setor_uid[lista_set[i]["cd"]]
                break
        if mun_nome and mun != mun_nome:  # ponto contradiz o municipio do nome: vale o nome
            mun, uid, pt, generico = mun_nome, None, None, True
        if mun is None:  # sem geometria valida: municipio pelo executor ("MUNICIPIO DE X")
            for e in p.get("executores") or []:
                k = chave(re.sub(r"^(MUNICIPIO|PREFEITURA MUNICIPAL) DE ", "", sem_acento(e.get("nome") or "").upper()))
                if k in por_nome:
                    mun = por_nome[k]
        eixo = ", ".join(sorted({t["descricao"] for t in p.get("tipos") or []})) or \
            ", ".join(sorted({e["descricao"] for e in p.get("eixos") or []}))
        obras.append({
            "id": p["idUnico"], "nome": (p.get("nome") or "").strip()[:180], "tipo": eixo,
            "situacao": p.get("situacao"), "valor_federal": round(fed, 2),
            "inicio": inicio, "fim_previsto": p.get("dataFinalPrevista"),
            "repassador": ", ".join(r["nome"] for r in p.get("repassadores") or [])[:120],
            "executor": ", ".join(e["nome"] for e in p.get("executores") or [])[:120],
            "links": links_obras.get(p["idUnico"], []),
            "mun": mun, "bairro": uid, "local_generico": generico,
            "lon": r1(pt.x, 5) if pt is not None and not generico else None,
            "lat": r1(pt.y, 5) if pt is not None and not generico else None,
            "ano": int(inicio[:4]),
        })
    return obras


def fontes(d, verif, ordem):
    """Links da linha da curadoria ja verificados (check_links.py), do melhor ao pior."""
    urls = [u.strip() for u in str(d.get("URL") or "").replace(" ;", ";").split(";") if u.strip()]
    nomes = [n.strip() for n in str(d.get("Fonte") or "").split(";")]
    out = []
    for i, u in enumerate(urls):
        v = verif.get(u) or {"situacao": "indisponivel", "href": u, "nota": "não verificado"}
        nome = nomes[i] if len(nomes) == len(urls) else (nomes[0] if len(urls) == 1 else f"fonte {i + 1}")
        out.append({"rotulo": nome or "fonte", "href": v["href"], "situacao": v["situacao"], "nota": v["nota"]})
    return sorted(out, key=lambda x: ordem[x["situacao"]])


# Alcance territorial dos programas da curadoria: "urbana" e "rural" so aparecem nas areas daquela
# zona (municipio mostra todos, com o selo). Regra por nome do programa; o resto vale para ambas.
ZONA_POLITICA = [
    # fichas do MCMV no Acre citam so empreendimentos urbanos; a Lei 14.620 (inclui o MCMV Rural) vale para ambas
    ("urbana", r"Periferia Viva|Reforma Casa Brasil|Cozinha Solid|^Minha Casa Minha Vida"),
    ("rural", r"Plano Safra|Pronaf|Desenrola Rural|\bPAA\b|Programa de Aquisi|Luz para Todos|Terra da Gente|"
              r"reforma agr|Bolsa Verde|PGPMBio|Homologa[cç][aã]o da Terra Ind"),
]


def zona_politica(nome):
    for zona, padrao in ZONA_POLITICA:
        if re.search(padrao, nome, re.I):
            return zona
    return "ambas"


def ler_politicas_curadas(nomes_mun, meta_u):
    abas = json.loads((RAW / "politicas_xlsx.json").read_text())  # export_politicas.py
    lk = RAW / "links_politicas.json"  # check_links.py
    verif = json.loads(lk.read_text()) if lk.exists() else {}
    ordem = {"ok": 0, "substituto": 1, "arquivo": 2, "indisponivel": 3}
    pol = []
    por_mun_nome = {chave(v): k for k, v in nomes_mun.items()}
    bairros_mun = defaultdict(dict)
    for u in meta_u.values():
        for nome in [u["nome_base"], *u["agrega"]]:
            k = chave(nome)
            if not u["rural"] and (len(k) >= 6 or " " in k) and k != "CENTRO":
                bairros_mun[u["mun"]][k] = u["id"]
    for aba, linhas in abas.items():
        for d in linhas:
            if not d.get("Política pública"):
                continue
            cit = str(d.get("Municípios citados") or "")
            if re.search(r"Todos|Estado|Nacional", cit, re.I):
                muns = ["*"]
            else:
                muns = [por_mun_nome[k] for k in (chave(x) for x in re.split(r"[;,]", cit)) if k in por_mun_nome]
            data = str(d.get("Data") or "")
            anos = {int(a) for a in re.findall(r"20\d\d", data)}
            for a, b in re.findall(r"(20\d\d)\s*[–-]\s*(20\d\d)", data):
                anos |= set(range(int(a), int(b) + 1))
            if not any(2023 <= a <= 2026 for a in anos):  # so 2023-2026
                continue
            texto = chave(f"{d.get('Descrição')} {d.get('Evidência')}")
            bairros = sorted({uid for m in muns if m != "*" for k, uid in bairros_mun[m].items()
                              if re.search(rf"\b{re.escape(k)}\b", texto)})
            pol.append({
                "tema": aba if aba != "Cury" else (d.get("Tema (campanha Cury)") or "Geral"),
                "subtema": d.get("Tema") or d.get("Tema (campanha Cury)"),
                "politica": d["Política pública"], "esfera": d.get("Esfera / responsável"),
                "descricao": d.get("Descrição"), "evidencia": d.get("Evidência"), "data": d.get("Data"),
                "fonte": d.get("Fonte"), "links": fontes(d, verif, ordem),
                "muns": muns, "bairros": bairros, "zona": zona_politica(d["Política pública"]), "anos": sorted(a for a in anos if 2023 <= a <= 2026),
            })
    return pol


# ------------------------------------------------------------------ seguranca publica (Sinesp VDE)
# CVLI (crimes violentos letais intencionais) = homicidio doloso + latrocinio + lesao corporal
# seguida de morte. Feminicidio e mostrado a parte e nao entra na soma (e homicidio qualificado).
# O Sinesp so publica por municipio: o dado nunca e atribuido a bairro ou localidade.
CVLI = ("Homicídio doloso", "Roubo seguido de morte (latrocínio)", "Lesão corporal seguida de morte")
EV_SEG = {"cvli": CVLI, "homicidio": ("Homicídio doloso",), "feminicidio": ("Feminicídio",),
          "tentativa_homicidio": ("Tentativa de homicídio",), "tentativa_feminicidio": ("Tentativa de feminicídio",),
          "transito": ("Morte no trânsito ou em decorrência dele (exceto homicídio doloso)",)}
ANOS_TAXA = ("2023", "2024", "2025")  # anos completos: a taxa usa a media, estavel em municipio pequeno


def ler_seguranca(nomes_mun, pop_mun):
    """{cd_mun: {...}, "_estado": {...}}: vitimas por ano e taxa media de CVLI por 100 mil hab."""
    sj = RAW / "sinesp/AC.json"  # fetch_sinesp.py
    if not sj.exists():
        print("AVISO: Sinesp ainda nao coletado; rode fetch_sinesp.py")
        return {}
    dados = json.loads(sj.read_text())
    por_nome = {chave(v): k for k, v in nomes_mun.items()}
    anos = sorted(a for a in dados if a.isdigit())
    out = {}
    for alvo in [*nomes_mun, "_estado"]:
        serie = {}
        for a in anos:
            ev = defaultdict(int)
            for nome, e in dados[a]["mun"].items():
                if alvo == "_estado" or por_nome.get(chave(nome)) == alvo:
                    for k, v in e.items():
                        ev[k] += int(v)
            serie[a] = {k: sum(ev[x] for x in evs) for k, evs in EV_SEG.items()}
        pop = sum(pop_mun.values()) if alvo == "_estado" else pop_mun[alvo]
        com = [a for a in ANOS_TAXA if a in serie]
        media = sum(serie[a]["cvli"] for a in com) / len(com) if com else None
        out[alvo] = {"serie": serie, "meses": {a: dados[a]["meses"] for a in anos}, "pop": pop,
                     "anos_taxa": com, "cvli_media": r1(media), "cvli_taxa": r1(media / pop * 1e5 if media is not None and pop else None)}
    sem = sum(sum(e.values()) for a in anos for n, e in dados[a]["mun"].items() if chave(n) not in por_nome)
    print(f"seguranca: {len(anos)} anos; vitimas sem municipio informado (so no total do estado): {sem}")
    return out


# a taxa municipal so vira carencia quando passa a do Acre com folga (10%): em municipio pequeno
# poucas mortes mexem muito na taxa, por isso a media dos anos completos
FOLGA_SEG = 1.10


def carencia_seguranca(seg, est):
    if not seg or seg.get("cvli_taxa") is None or not est or not est.get("cvli_taxa"):
        return False
    return seg["cvli_taxa"] >= est["cvli_taxa"] * FOLGA_SEG


def frase_seguranca(seg, est, nome):
    a0, a1 = seg["anos_taxa"][0], seg["anos_taxa"][-1]
    return (f"{nome} teve {fmt(seg['cvli_taxa'], 1)} mortes violentas por 100 mil habitantes ao ano de {a0} a {a1} "
            f"(cerca de {fmt(seg['cvli_media'], 0)} por ano), acima da média do Acre ({fmt(est['cvli_taxa'], 1)}). "
            "O dado do Sinesp existe só para o município inteiro, não por bairro")


def compromisso_seguranca(seg, est, nome):
    """Compromisso de seguranca do plano, somado aos demais quando o municipio tem carencia."""
    if not carencia_seguranca(seg, est):
        return []
    c = next(x for x in PLANO if x["id"] == "seguranca")
    return [{"id": "seguranca", "motivo": {"k": "cvli_taxa", "valor": seg["cvli_taxa"], "ref": est["cvli_taxa"]},
             "tema": c["tema"], "pagina": c["pagina"], "promessa": c["promessa"], "meta": c["meta"],
             "ref_rot": "no Acre", "falta": f"Segurança: {frase_seguranca(seg, est, nome)}.",
             "territorio": f"o dado existe só para o município: {nome} teve {fmt(seg['cvli_taxa'], 1)} mortes violentas "
                           f"intencionais por 100 mil habitantes ao ano em {seg['anos_taxa'][0]}–{seg['anos_taxa'][-1]}, "
                           f"contra {fmt(est['cvli_taxa'], 1)} no Acre (Sinesp/MJSP).",
             "significa": f"enfrentar a violência em {nome}, onde a taxa de mortes violentas é maior que a do Acre"}]


# Compromissos do Programa de Governo Lula 2026 (Coligacao Brasil Pronto Pra Mais, TSE, 84 p.).
# Texto conferido no PDF (pdftotext, paginas impressas). Campos:
#   promessa  o que o plano diz que sera feito (parafrase fiel, termos do plano entre aspas)
#   meta      metas e escala numerica citadas no plano
#   gatilho   indicador e sentido da carencia que torna o compromisso prioritario no bairro
#   territorio(n, ind, ref) -> o que o compromisso significa em numeros no territorio
def _n(v):
    return f"{v:,}".replace(",", ".")


PLANO = [
    dict(id="agua", tema="Água tratada", pagina="46", gatilho=("agua_rede_pct", -1),
         promessa="Apoiar estados e municípios a “universalizar acesso à água tratada e ao esgotamento sanitário, buscando priorizar periferias historicamente negligenciadas”.",
         meta="Dá sequência aos R$ 23,3 bilhões aplicados desde 2023 em novas obras de abastecimento de água, saneamento e resíduos sólidos e aos R$ 3,5 bilhões para concluir obras inacabadas.",
         territorio=lambda n, i, r: f"{_n(n['dom_sem_agua'])} dos {_n(n['dppo'])} domicílios não são abastecidos pela rede geral ({fmt(i['agua_rede_pct'])}% com rede, contra {fmt(r['agua_rede_pct'])}% {r['_rot']})."),
    dict(id="saneamento", tema="Saneamento", pagina="46–47", gatilho=("saneamento_adequado_pct", -1),
         promessa="Universalizar o saneamento priorizando periferias; “a gestão dos resíduos sólidos e o tratamento de esgotos continuarão a ter o apoio da União”, com soluções como biogás e biometano.",
         meta="Mesma carteira de R$ 23,3 bilhões em saneamento do Novo PAC desde 2023, mantida no próximo mandato.",
         territorio=lambda n, i, r: f"{_n(n['dom_sem_saneamento'])} domicílios não têm saneamento adequado, isto é, ligação à rede geral ou fossa ligada à rede ({fmt(i['saneamento_adequado_pct'])}% adequados, contra {fmt(r['saneamento_adequado_pct'])}% {r['_rot']})."),
    dict(id="lixo", tema="Coleta de lixo", pagina="46–47", gatilho=("lixo_coletado_pct", -1),
         promessa="Manter o apoio da União à gestão de resíduos sólidos, dentro da agenda de saneamento do Novo PAC.",
         meta="Resíduos sólidos integram os R$ 23,3 bilhões de novas obras de saneamento desde 2023.",
         territorio=lambda n, i, r: f"{_n(n['dom_sem_lixo'])} domicílios não têm o lixo coletado ({fmt(i['lixo_coletado_pct'])}% com coleta, contra {fmt(r['lixo_coletado_pct'])}% {r['_rot']})."),
    dict(id="drenagem", tema="Drenagem e enchentes", pagina="47 e 72", gatilho=("bueiro_pct", -1),
         promessa="Seguir investindo em contenção de encostas e drenagem urbana sustentável, priorizando áreas de risco alto ou muito alto com inundações recorrentes; aprimorar a Defesa Civil e ampliar a “proteção contra cheias e enchentes”.",
         meta="R$ 25 bilhões já destinados pelo Novo PAC a encostas e drenagem urbana (p. 47); adaptação climática institucionalizada como “eixo permanente do planejamento público” (p. 72).",
         territorio=lambda n, i, r: f"{_n(n['mor_sem_bueiro'])} moradores vivem em ruas sem bueiro ({fmt(i['bueiro_pct'])}% com bueiro, contra {fmt(r['bueiro_pct'])}% {r['_rot']})."),
    dict(id="periferia_viva", tema="Urbanização (Periferia Viva)", pagina="45–46", gatilho=("pavimentacao_pct", -1),
         promessa="Periferia Viva: urbanização de favelas e regularização fundiária; “todas estas obras terão recursos assegurados para sua conclusão” e haverá “novas rodadas de seleção”.",
         meta="85 obras retomadas e 59 novas propostas selecionadas no atual mandato.",
         territorio=lambda n, i, r: f"{_n(n['mor_sem_pavimento'])} moradores vivem em ruas sem pavimentação ({fmt(i['pavimentacao_pct'])}% em rua pavimentada, contra {fmt(r['pavimentacao_pct'])}% {r['_rot']})."),
    dict(id="espaco_publico", tema="Espaço público e segurança", pagina="28", gatilho=("iluminacao_pct", -1),
         promessa="Consolidar a presença do Estado no território, priorizando “investimentos continuados na urbanização de favelas e periferias, na requalificação de espaços públicos e na ampliação do acesso a serviços públicos essenciais” para reduzir mercados explorados pelo crime organizado.",
         meta="Ampliação das Forças Integradas de Combate ao Crime Organizado (FICCOs) e do Fundo de Infraestrutura Social para estados e municípios.",
         territorio=lambda n, i, r: f"{_n(n['mor_sem_iluminacao'])} moradores vivem em ruas sem iluminação pública ({fmt(i['iluminacao_pct'])}% com iluminação, contra {fmt(r['iluminacao_pct'])}% {r['_rot']})."),
    dict(id="moradia", tema="Moradia (Minha Casa Minha Vida)", pagina="45", gatilho=("renda_resp", -1),
         promessa="Manter e ampliar o MCMV “para todas as faixas de renda e preservando seu foco nas famílias de mais baixa renda”, com novos instrumentos (Compra Assistida, áreas da União e Fundo Imobiliário da União) e apoio aos movimentos sociais.",
         meta="Meta elevada para 3 milhões de moradias contratadas até o fim de 2026 (a de 2 milhões foi atingida com um ano de antecedência); 1 milhão de moradias quitadas para beneficiários do Bolsa Família e do BPC.",
         territorio=lambda n, i, r: f"a renda média do responsável pelo domicílio é de R$ {_n(int(i['renda_resp']))}, {fmt(100 * (1 - i['renda_resp'] / r['renda_resp']), 0)}% abaixo do Acre urbano (R$ {_n(int(r['renda_resp']))}), em {_n(n['dppo'])} domicílios."),
    dict(id="adensamento", tema="Moradia (adensamento)", pagina="45", gatilho=("mor_dom", 1),
         promessa="Ampliar o acesso à moradia com o MCMV e o Compra Assistida (aquisição de imóveis prontos) e melhorar o padrão construtivo.",
         meta="Meta de 3 milhões de moradias contratadas até o fim de 2026.",
         territorio=lambda n, i, r: f"há {fmt(i['mor_dom'], 2)} moradores por domicílio, contra {fmt(r['mor_dom'], 2)} {r['_rot']}."),
    dict(id="banheiro", tema="Melhoria habitacional", pagina="45", gatilho=("sem_banheiro_pct", 1),
         promessa="“Aprimoraremos e ampliaremos o Reforma Casa Brasil”, programa de reforma e melhoria de moradias.",
         meta="O plano não fixa meta numérica para o Reforma Casa Brasil.",
         territorio=lambda n, i, r: f"{_n(n['dom_sem_banheiro'])} domicílios não têm banheiro nem sanitário ({fmt(i['sem_banheiro_pct'])}%, contra {fmt(r['sem_banheiro_pct'])}% {r['_rot']})."),
    dict(id="alfabetizacao", tema="Alfabetização", pagina="31", gatilho=("alfabetizacao_pct", -1),
         promessa="Fortalecer “as políticas de enfrentamento do analfabetismo na população adulta” e instituir uma estratégia nacional permanente de recomposição e aceleração das aprendizagens.",
         meta="Chegar a 80% das crianças alfabetizadas na idade certa (eram 66% em 2025, acima da meta de 64%).",
         territorio=lambda n, i, r: f"{_n(n['nao_alfabetizados'])} pessoas de 15 anos ou mais não são alfabetizadas ({fmt(i['alfabetizacao_pct'])}% alfabetizadas, contra {fmt(r['alfabetizacao_pct'])}% {r['_rot']})."),
    dict(id="creche", tema="Creche e tempo integral", pagina="31–32", gatilho=("criancas_pct", 1),
         promessa="Na segunda edição do Novo PAC, “ampliaremos ainda mais o fomento” à construção de creches; a expansão do tempo integral “terá absoluta prioridade”, agora financiada pelo Fundeb.",
         meta="3.562 creches e escolas infantis apoiadas e 686 escolas de tempo integral financiadas no 1º ciclo do Novo PAC; 1,8 milhão de novas matrículas em tempo integral (2023–2025).",
         territorio=lambda n, i, r: f"vivem {_n(n['criancas_0_4'])} crianças de 0 a 4 anos e {_n(n['criancas_0_14'])} de 0 a 14 ({fmt(i['criancas_pct'])}% da população, contra {fmt(r['criancas_pct'])}% {r['_rot']})."),
    dict(id="juventude", tema="Juventude (Pé-de-Meia e IFs)", pagina="32–33", gatilho=("jovens_pct", 1),
         promessa="Continuar e fortalecer o Pé-de-Meia — “nenhum jovem em situação de pobreza ou vulnerabilidade social no Brasil deve deixar a escola por falta de renda” — e expandir os institutos federais priorizando “as periferias urbanas” e os “vazios educacionais”.",
         meta="7,3 milhões de jovens já beneficiados pelo Pé-de-Meia; mais 111 IFs com novos campi em 106 municípios; cursinho Partiu IF.",
         territorio=lambda n, i, r: f"vivem {_n(n['jovens_15_19'])} jovens de 15 a 19 anos e {_n(n['jovens_15_29'])} de 15 a 29 ({fmt(i['jovens_pct'])}% da população, contra {fmt(r['jovens_pct'])}% {r['_rot']})."),
    dict(id="idosos", tema="Pessoa idosa", pagina="22 e 36", gatilho=("idosos_pct", 1),
         promessa="Farmácia Popular mantida “100% gratuita”, com mais locais de retirada “nas periferias das grandes cidades” e inclusão de exames para hipertensão e diabetes; levar o atendimento domiciliar ao idoso (PADI) a todo o país e ampliar Centros-Dia.",
         meta="41 medicamentos gratuitos e 27,3 milhões de pessoas atendidas em 2025 (p. 36); PADI hoje em 2.655 municípios (p. 22).",
         territorio=lambda n, i, r: f"vivem {_n(n['idosos'])} pessoas com 60 anos ou mais ({fmt(i['idosos_pct'])}% da população, contra {fmt(r['idosos_pct'])}% {r['_rot']})."),
    dict(id="cuidados", tema="Cuidados", pagina="26", gatilho=("renda_resp", -1),
         promessa="Política Nacional de Cuidados com escala: apoio às Cuidotecas (crianças de 3 a 12 anos) e construção de lavanderias públicas, cozinhas solidárias e restaurantes populares; programa de capacitação de cuidadores.",
         meta="O plano não fixa meta numérica.",
         territorio=lambda n, i, r: f"a renda média do responsável é de R$ {_n(int(i['renda_resp']))}, contra R$ {_n(int(r['renda_resp']))} {r['_rot']}."),
    dict(id="indigenas", tema="Povos indígenas", pagina="20", gatilho=("indigenas_pct", 1),
         promessa="Proteger territórios indígenas “com ações para demarcação e desintrusão de terras sempre que necessárias”, educação escolar indígena intercultural e implantação plena da Universidade Indígena; saúde indígena com atenção diferenciada.",
         meta="20 terras indígenas homologadas (3,2 milhões de hectares) e 21 portarias declaratórias no atual mandato.",
         territorio=lambda n, i, r: f"vivem {_n(n['indigenas'])} pessoas indígenas ({fmt(i['indigenas_pct'])}% da população, contra {fmt(r['indigenas_pct'])}% {r['_rot']})."),
    dict(id="rural", tema="Água e energia no campo", pagina="60 e 65", gatilho=None,
         promessa="Buscar a universalização da água potável e da energia elétrica no campo, priorizando fontes renováveis (Luz para Todos com “foco em áreas rurais e Amazônia Legal”); ampliar saneamento rural ecológico, Cisternas, reforma agrária e regularização fundiária, “especialmente na região Norte”, e o PRONERA.",
         meta="O plano não fixa meta numérica para o campo; o Luz do Povo zera a conta de luz de famílias do CadÚnico que consomem até 80 kWh/mês (p. 65).",
         territorio=lambda n, i, r: f"{_n(n['dom_sem_agua'])} dos {_n(n['dppo'])} domicílios da área não recebem água da rede geral, {_n(n['dom_sem_saneamento'])} não têm saneamento adequado e {_n(n['dom_sem_banheiro'])} não têm banheiro."),
    dict(id="agricultura", tema="Agricultura familiar", pagina="58–60", gatilho=None,
         promessa="Expandir o Plano Safra da Agricultura Familiar, com prioridade para alimentos da cesta básica e crédito para jovens e mulheres agricultoras; ampliar PAA e ATER, fortalecer o Garantia-Safra e o seguro rural.",
         meta="Apoiar estados e municípios a cumprir 45% da alimentação escolar comprada da agricultura familiar e 80% de alimentos in natura ou minimamente processados.",
         territorio=lambda n, i, r: f"são {_n(i['pop'])} moradores na zona rural, em {_n(n['dppo'])} domicílios."),
    dict(id="seguranca", tema="Segurança pública", pagina="27–29", gatilho=None,
         promessa="Fortalecer o Programa Brasil Contra o Crime Organizado (tráfico de armas, asfixia financeira de facções e milícias, “qualificação da investigação de homicídios”, “retomada de territórios”), ampliar as FICCOs e a presença do Estado na Amazônia Legal e expandir a prevenção “com foco na proteção da juventude negra”, a mediação comunitária e a justiça restaurativa.",
         meta="R$ 10 bilhões do Fundo Nacional de Investimento em Infraestrutura Social (FIIS) para estados e municípios investirem em equipamentos e infraestrutura (p. 27–28); PEC da Segurança Pública enviada ao Congresso e Lei Antifacção sancionada em 2026 (p. 27).",
         territorio=lambda n, i, r: ""),
    dict(id="conectividade", tema="Internet", pagina="32 e 55", gatilho=None,
         promessa="Reduzir o déficit de fibra óptica e ampliar o 5G no campo “com prioridade para Norte, Nordeste e periferias”; universalizar a internet de qualidade nas escolas.",
         meta="Fibra óptica ainda falta em 11% dos municípios (p. 55); 75% das escolas já conectadas nos parâmetros adequados (p. 32).",
         territorio=lambda n, i, r: f"área de {_n(i['pop'])} moradores fora da malha urbana."),
]


def _carencias(ind, ref, excluir=()):
    """[(gap, id, motivo)] dos compromissos com gatilho, ordenados pela carencia relativa."""
    notas = []
    for c in PLANO:
        if not c["gatilho"] or c["id"] in excluir:
            continue
        k, sentido = c["gatilho"]
        v, r = ind.get(k), ref.get(k)
        if v is None or not r:
            continue
        # >0 = carencia. Percentuais: distancia em p.p. (unidade = 10 p.p.); renda e
        # moradores/domicilio: distancia relativa (unidade = 25%). Limiar: 3 p.p. ou 7,5%.
        escala = 10 if k.endswith("_pct") else abs(r) * 0.25
        gap = (v - r) / escala * (1 if sentido > 0 else -1)
        if gap > 0.3:
            notas.append((gap, c["id"], {"k": k, "valor": v, "ref": r}))
    notas.sort(key=lambda x: -x[0])
    vistos, out = set(), []
    for gap, cid, mot in notas:  # um compromisso por indicador
        if mot["k"] not in vistos:
            vistos.add(mot["k"])
            out.append((cid, mot))
    return out


# na zona rural, agua/saneamento entram no compromisso do campo e nao ha levantamento do entorno.
# Habitacao: os instrumentos do plano (p. 45) sao urbanos: MCMV com Compra Assistida e areas da
# Uniao "das cidades"; o Reforma Casa Brasil vale so para imovel em area urbana (cartilha do
# MCidades). Sem banheiro na zona rural cai no "saneamento rural ecologico" do compromisso do campo.
EXCLUIR_RURAL = ("agua", "saneamento", "periferia_viva", "espaco_publico", "drenagem",
                 "moradia", "adensamento", "banheiro")


def compromissos(ind, ref, rural, tipo="Bairro"):
    """Ate 3 compromissos do plano mais aderentes as carencias da unidade (vs Acre urbano ou
    zona rural do Acre), ja com promessa, meta e o que significam no territorio."""
    if rural:
        aldeia = tipo == "Aldeia" or (ind.get("indigenas_pct") or 0) >= 20
        base = [("indigenas", None), ("rural", None)] if aldeia else [("rural", None), ("agricultura", None)]
        extra = [x for x in _carencias(ind, ref, EXCLUIR_RURAL) if x[0] not in {b for b, _ in base}]
        escolhidos = base + (extra[:1] or [("agricultura" if aldeia else "conectividade", None)])
    else:
        escolhidos = _carencias(ind, ref)[:3]
    por_id = {c["id"]: c for c in PLANO}
    return [{"id": cid, "motivo": mot, "tema": por_id[cid]["tema"], "pagina": por_id[cid]["pagina"],
             "promessa": por_id[cid]["promessa"], "meta": por_id[cid]["meta"], "ref_rot": ref["_rot"],
             "territorio": por_id[cid]["territorio"](ind["n"], ind, ref),
             "significa": PLANO_DIDATICO[cid][1](ind["n"], ind)} for cid, mot in escolhidos]


# ------------------------------------------------------------------ textos (insights)
ROT = {k: (rot, suf) for k, rot, suf, _, _ in INDICADORES}


def fmt(v, n=1):
    if v is None:
        return "—"
    s = f"{v:,.{n}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def fmt_ind(k, v):
    rot, suf = ROT[k]
    if suf.strip() == "R$":
        return f"R$ {fmt(v, 0)}"
    return f"{fmt(v, 0 if k in ('pop', 'densidade') else 1)}{suf}"


def brl(v):
    if v >= 1e6:
        return f"R$ {fmt(v / 1e6, 1)} {'milhão' if v < 2e6 else 'milhões'}"
    if v >= 1e3:
        return f"R$ {fmt(v / 1e3, 0)} mil"
    return f"R$ {fmt(v, 0)}"


def maior_gap(perf, lula_total):
    """[(desvio do voto estimado de Lula no grupo vs. total, categoria, dimensao)]."""
    out = []
    for dim, cats in DIMENSOES.items():
        p = perf.get(dim)
        if not p:
            continue
        out += [(v - lula_total, c, dim) for v, c in zip(p["lula"], cats) if v is not None]
    return out


ROT_CAT = {"Mulheres": "mulheres", "Homens": "homens", "16–24": "jovens de 16 a 24 anos",
           "25–34": "eleitores de 25 a 34 anos", "35–44": "eleitores de 35 a 44 anos",
           "45–59": "eleitores de 45 a 59 anos", "60+": "idosos (60+)",
           "Até fundamental incompleto": "eleitores com até o fundamental incompleto",
           "Fundamental completo ou médio incompleto": "eleitores com fundamental completo",
           "Médio completo ou superior incompleto": "eleitores com ensino médio",
           "Superior completo": "eleitores com ensino superior"}


# ---- textos didaticos da "Leitura do territorio": secoes curtas, linguagem simples ----
def proporcao(p):
    """52,3 -> 'cerca de 5 em cada 10'; 3,1 -> '3 em cada 100'."""
    if p is None:
        return "—"
    if p >= 95:
        return "mais de 9 em cada 10"
    if p >= 9.5:
        return f"cerca de {round(p / 10)} em cada 10"
    return "menos de 1 em cada 100" if p < 0.5 else f"{round(p)} em cada 100"


def de_cada_10(p):
    return proporcao(p).replace("cerca de ", "")


# o que cada compromisso do plano preve, em uma frase, e o que significa aqui
PLANO_DIDATICO = {
    "agua": ("levar água tratada e saneamento a todos, começando pelas periferias",
             lambda n, i: f"chegar a cerca de {_n(n['dom_sem_agua'])} casas que ainda não recebem água da rede geral"),
    "saneamento": ("garantir saneamento adequado a todas as casas, com prioridade para as periferias",
                   lambda n, i: f"ligar cerca de {_n(n['dom_sem_saneamento'])} casas que hoje não têm saneamento adequado"),
    "lixo": ("apoiar a coleta e o tratamento do lixo",
             lambda n, i: f"atender cerca de {_n(n['dom_sem_lixo'])} casas que não têm o lixo coletado"),
    "drenagem": ("fazer obras de drenagem e de proteção contra enchentes nas áreas de risco",
                 lambda n, i: f"levar drenagem às ruas onde vivem cerca de {_n(n['mor_sem_bueiro'])} moradores sem bueiro"),
    "periferia_viva": ("urbanizar periferias com o Periferia Viva, com novas seleções de obras",
                       lambda n, i: f"pavimentar e urbanizar as ruas onde vivem cerca de {_n(n['mor_sem_pavimento'])} moradores"),
    "espaco_publico": ("requalificar praças e espaços públicos e levar serviços às periferias, para enfrentar o crime",
                       lambda n, i: f"iluminar as ruas onde vivem cerca de {_n(n['mor_sem_iluminacao'])} moradores"),
    "moradia": ("ampliar o Minha Casa Minha Vida, com foco em quem ganha menos",
                lambda n, i: f"atender famílias de renda baixa como as daqui, onde quem sustenta a casa ganha em média R$ {_n(int(i['renda_resp']))} por mês"),
    "adensamento": ("ampliar o acesso à moradia e diminuir as casas superlotadas",
                    lambda n, i: f"aliviar casas que têm, em média, {fmt(i['mor_dom'], 1)} moradores"),
    "banheiro": ("ampliar o Reforma Casa Brasil, que ajuda a reformar e melhorar moradias",
                 lambda n, i: f"atender {_n(n['dom_sem_banheiro'])} casas que não têm banheiro"),
    "alfabetizacao": ("enfrentar o analfabetismo de jovens e adultos",
                      lambda n, i: f"alcançar {_n(n['nao_alfabetizados'])} pessoas com 15 anos ou mais que ainda não sabem ler e escrever"),
    "creche": ("construir mais creches e ampliar a escola em tempo integral",
               lambda n, i: f"beneficiar {_n(n['criancas_0_4'])} crianças de até 4 anos e {_n(n['criancas_0_14'])} de até 14 anos"),
    "juventude": ("manter o Pé-de-Meia e abrir institutos federais nas periferias",
                  lambda n, i: f"alcançar {_n(n['jovens_15_19'])} jovens de 15 a 19 anos"),
    "idosos": ("manter a Farmácia Popular 100% gratuita e levar atendimento em casa aos idosos",
               lambda n, i: f"atender {_n(n['idosos'])} pessoas com 60 anos ou mais"),
    "cuidados": ("criar Cuidotecas, lavanderias públicas e cozinhas solidárias",
                 lambda n, i: f"apoiar famílias que vivem com renda média de R$ {_n(int(i['renda_resp']))} por mês"),
    "indigenas": ("proteger as terras indígenas e garantir saúde e educação indígenas",
                  lambda n, i: f"atender {_n(n['indigenas'])} indígenas que moram aqui"),
    "rural": ("levar água potável e energia (Luz para Todos) a todo o campo",
              lambda n, i: f"chegar a {_n(n['dom_sem_agua'])} casas sem água da rede e às famílias ainda sem energia"),
    "agricultura": ("dar mais crédito à agricultura familiar e comprar alimentos dela para a merenda escolar",
                    lambda n, i: f"apoiar os {_n(i['pop'])} moradores da área, em grande parte agricultores e extrativistas"),
    "seguranca": ("enfrentar o crime organizado, investigar melhor os homicídios e prevenir a violência contra os jovens",
                  None),  # o que significa vem de compromisso_seguranca (dado municipal)
    "conectividade": ("levar internet de fibra e 5G ao campo e às periferias do Norte",
                      lambda n, i: f"conectar os {_n(i['pop'])} moradores da área"),
}

# como contar cada carencia em linguagem simples: (titulo, frase com a proporcao)
CARENCIA = {
    "agua_rede_pct": ("Água encanada", lambda v: f"{proporcao(v)} casas recebem água da rede geral"),
    "saneamento_adequado_pct": ("Saneamento", lambda v: f"{proporcao(v)} casas têm saneamento adequado"),
    "lixo_coletado_pct": ("Coleta de lixo", lambda v: f"{proporcao(v)} casas têm o lixo coletado"),
    "bueiro_pct": ("Drenagem", lambda v: f"{proporcao(v)} moradores vivem em rua com bueiro"),
    "pavimentacao_pct": ("Rua asfaltada", lambda v: f"{proporcao(v)} moradores vivem em rua pavimentada"),
    "iluminacao_pct": ("Iluminação", lambda v: f"{proporcao(v)} moradores vivem em rua com iluminação pública"),
    "alfabetizacao_pct": ("Leitura e escrita", lambda v: f"{proporcao(100 - v)} pessoas com 15 anos ou mais não sabem ler e escrever"),
    "sem_banheiro_pct": ("Banheiro", lambda v: f"{proporcao(v)} casas não têm banheiro"),
    "renda_resp": ("Renda", lambda v: f"quem sustenta a casa ganha em média R$ {_n(int(v))} por mês"),
    "mor_dom": ("Casas cheias", lambda v: f"vivem em média {fmt(v, 1)} pessoas por casa"),
    "criancas_pct": ("Muitas crianças", lambda v: f"{proporcao(v)} moradores têm até 14 anos"),
    "jovens_pct": ("Muitos jovens", lambda v: f"{proporcao(v)} moradores têm de 15 a 29 anos"),
    "idosos_pct": ("Muitos idosos", lambda v: f"{proporcao(v)} moradores têm 60 anos ou mais"),
    "indigenas_pct": ("População indígena", lambda v: f"{proporcao(v)} moradores são indígenas"),
}


def _ref_frase(k, ref, rot):
    v = ref.get(k)
    if v is None:
        return ""
    if k == "renda_resp":
        return f" (média {rot.replace('no ', 'do ').replace('na ', 'da ')}: R$ {_n(int(v))})"
    if k == "mor_dom":
        return f" ({fmt(v, 1)} {rot})"
    if k == "alfabetizacao_pct":
        return f" ({de_cada_10(100 - v)} {rot})"
    return f" ({de_cada_10(v)} {rot})"


def secao_quem_mora(u, mun, zona_nome=None):
    ind, mi = u["censo"], mun["censo"]
    nome = u["nome"] if not zona_nome else zona_nome
    t = [f"{nome} tem {fmt(ind['pop'], 0)} moradores, cerca de {fmt(pct(ind['pop'], mi['pop']), 0)}% da população "
         f"de {mun['nome']}, vivendo em {fmt(ind['domicilios'], 0)} casas."]
    if ind.get("renda_resp") and mi.get("renda_resp"):
        dif = ind["renda_resp"] / mi["renda_resp"] - 1
        comp = ("perto da média da cidade" if abs(dif) < 0.1 else
                f"{'acima' if dif > 0 else 'abaixo'} da média da cidade (R$ {_n(int(mi['renda_resp']))})")
        t.append(f"Quem sustenta a casa ganha, em média, R$ {_n(int(ind['renda_resp']))} por mês — {comp}.")
    if (ind.get("indigenas_pct") or 0) >= 20:
        t.append(f"É uma área indígena: {proporcao(ind['indigenas_pct'])} moradores se declaram indígenas.")
    elif (ind.get("pretos_pardos_pct") or 0) >= 50:
        t.append(f"A maior parte da população é negra: {proporcao(ind['pretos_pardos_pct'])} moradores se declaram pretos ou pardos.")
    return {"t": "Quem mora aqui", "p": [" ".join(t)]}


def secao_falta(u, ref):
    itens = []
    for c in u["plano"]:
        k = (c.get("motivo") or {}).get("k")
        if c.get("falta"):
            itens.append(c["falta"])
        elif k in CARENCIA:
            tit, frase = CARENCIA[k]
            itens.append(f"{tit}: {frase(c['motivo']['valor'])}{_ref_frase(k, ref, ref['_rot'])}.")
    if not itens:
        return None
    return {"t": "O que mais falta", "intro": "Comparando com o restante do Acre, os pontos mais fracos são:", "itens": itens}


def secao_voto(u, mun, onde):
    el, me = u["eleicao"], mun["eleicao"]
    if not el or not el["validos"]:
        return None
    vence, perde = (("Lula", "Flávio Bolsonaro") if el["lula"] > el["flavio"] else ("Flávio Bolsonaro", "Lula"))
    pv = max(el["lula_pct"], el["flavio_pct"]); pp = min(el["lula_pct"], el["flavio_pct"])
    t = [f"No 1º turno de 2026, {vence} venceu {onde} com {fmt(pv)}% dos votos válidos; {perde} teve {fmt(pp)}%. "
         f"Na prática, de cada 10 eleitores que escolheram um candidato, {round(pv / 10)} votaram em {vence.split()[0]} "
         f"e {round(pp / 10)} em {perde.split()[0]}."]
    if u is not mun and me:
        dif = el["lula_pct"] - me["lula_pct"]
        t.append("Lula foi " + ("praticamente igual ao resto da cidade" if abs(dif) < 1 else
                 f"{fmt(abs(dif))} pontos {'melhor' if dif > 0 else 'pior'} do que no conjunto de {mun['nome']} ({fmt(me['lula_pct'])}%)") + ".")
    g = maior_gap(u["perfil"], el["lula_pct"]) if u.get("perfil") and el["secoes"] >= 3 else []
    if g and max(g)[0] >= 1:
        t.append(f"Lula foi um pouco melhor entre {ROT_CAT[max(g)[1]]}.")
    if el.get("abstencao_pct") and el["abstencao_pct"] >= 15:
        t.append(f"{de_cada_10(el['abstencao_pct']).capitalize()} eleitores não foram votar.")
    return {"t": "Como votou em 2026", "p": [" ".join(t)]}


def secao_feito(u, mun, onde, obras):
    t = []
    if obras:
        tot = sum(o["valor_federal"] for o in obras)
        maior = max(obras, key=lambda o: o["valor_federal"])
        t.append(f"Desde 2023, {len(obras)} {'obra começou' if len(obras) == 1 else 'obras começaram'} {onde} com "
                 f"dinheiro federal, somando {brl(tot)} previstos. A maior é “{titulo(maior['nome'][:90])}” ({brl(maior['valor_federal'])}).")
    else:
        t.append(f"Nenhuma obra federal começada desde 2023 está registrada {onde}.")
    s = mun.get("social") or {}
    if s.get("pbf_familias"):
        t.append(f"Os moradores também são alcançados pelos programas que valem para toda a cidade, como o Bolsa Família, "
                 f"que atende {_n(int(s['pbf_familias']))} famílias em {mun['nome']} (benefício médio de R$ {_n(int(s['pbf_valor_medio']))}).")
    if u.get("politicas"):
        t.append("Ações citadas por nome para este lugar: " + "; ".join(u["politicas"]) + ".")
    return {"t": "O que o governo federal já fez (2023–2026)", "p": [" ".join(t)]}


def secao_plano(itens_plano, onde):
    if not itens_plano:
        return None
    itens = []
    for c in itens_plano:
        curta, signif = PLANO_DIDATICO[c["id"]]
        frase = f"{c['tema']} — o plano prevê {curta} (p. {c['pagina']})."
        if c.get("frase_local"):
            frase += f" {c['frase_local']}"
        elif c.get("significa"):
            frase += f" {onde.capitalize()}, isso significa {c['significa']}."
        itens.append(frase)
    return {"t": "O que o governo Lula planeja", "itens": itens}


def texto_unidade(u, mun):
    """Leitura de um bairro, localidade rural ou zona, em secoes curtas e didaticas."""
    zona = u["tipo"] == "Zona"
    onde = (f"na {u['nome'].lower()} de {mun['nome']}" if zona else
            f"em {u['nome']}")
    ref = u["_ref"]
    obras = u["obras"]
    secoes = [secao_quem_mora(u, mun, f"A {u['nome'].lower()} de {mun['nome']}" if zona else None),
              secao_falta(u, ref), secao_voto(u, mun, "aqui"),
              secao_feito(u, mun, "aqui", obras), secao_plano(u["plano"], "aqui")]
    return [s for s in secoes if s]


def texto_municipio(m, unidades_m, obras_m):
    urb = [u for u in unidades_m if not u["rural"]]
    rur = [u for u in unidades_m if u["rural"]]
    zu, zr = m["zonas"]["urbana"], m["zonas"]["rural"]
    quem = (f"{m['nome']} tem {fmt(m['censo']['pop'], 0)} moradores. {fmt(zu['censo']['pop'], 0) if zu else 0} vivem na cidade, "
            f"em {len(urb)} {'bairro' if len(urb) == 1 else 'bairros'}, e {fmt(zr['censo']['pop'], 0) if zr else 0} na zona rural, "
            f"espalhados por {len(rur)} {'localidade' if len(rur) == 1 else 'localidades'} — ramais, aldeias, seringais e comunidades.")
    if m["censo"].get("renda_resp"):
        quem += f" Quem sustenta a casa ganha, em média, R$ {_n(int(m['censo']['renda_resp']))} por mês."
    secoes = [{"t": "Quem mora aqui", "p": [quem]}]
    # onde falta mais: bairros com menos saneamento adequado
    piores = sorted([u for u in urb if u["censo"]["pop"] >= 300 and u["censo"].get("saneamento_adequado_pct") is not None],
                    key=lambda u: u["censo"]["saneamento_adequado_pct"])[:3]
    itens = []
    if carencia_seguranca(m.get("seguranca"), m.get("_seg_estado")):
        itens.append(f"Segurança: {frase_seguranca(m['seguranca'], m['_seg_estado'], m['nome'])}.")
    if len(urb) >= 3 and piores:
        itens.append("Saneamento: os bairros com menos casas com saneamento adequado são " + ", ".join(
            f"{u['nome']} ({proporcao(u['censo']['saneamento_adequado_pct'])} casas)" for u in piores) + ".")
    if itens:
        secoes.append({"t": "Onde falta mais", "intro": "Comparando com o restante do Acre:", "itens": itens})
    v = secao_voto(m, m, "no município")
    if v:
        com = [u for u in unidades_m if u["eleicao"] and u["eleicao"]["validos"] >= 200]
        if len(com) >= 3:
            mel = max(com, key=lambda u: u["eleicao"]["lula_pct"])
            pio = min(com, key=lambda u: u["eleicao"]["lula_pct"])
            v["p"][0] += (f" O melhor lugar para Lula foi {mel['nome']} ({fmt(mel['eleicao']['lula_pct'])}%) e o pior, "
                          f"{pio['nome']} ({fmt(pio['eleicao']['lula_pct'])}%).")
        secoes.append(v)
    feito = secao_feito(m, m, "no município", obras_m)
    if obras_m:
        por_tipo = Counter()
        for o in obras_m:
            por_tipo[o["tipo"] or "Outros"] += o["valor_federal"]
        feito["p"][0] += " O dinheiro foi sobretudo para " + ", ".join(
            f"{t.lower()} ({brl(x)})" for t, x in por_tipo.most_common(3)) + "."
    secoes.append(feito)
    cont = Counter()
    for u in unidades_m:
        for c in u["plano"]:
            if c["id"] != "seguranca":  # municipal: entra a parte, abaixo
                cont[c["id"]] += u["censo"]["pop"]
    por_id = {c["id"]: c for c in PLANO}
    top = [{"id": i, "tema": por_id[i]["tema"], "pagina": por_id[i]["pagina"],
            "frase_local": f"É uma das maiores carências em bairros e localidades onde vivem {_n(p)} pessoas."}
           for i, p in cont.most_common(3)]
    if carencia_seguranca(m.get("seguranca"), m.get("_seg_estado")):
        top.append({"id": "seguranca", "tema": por_id["seguranca"]["tema"], "pagina": por_id["seguranca"]["pagina"],
                    "frase_local": "A taxa de mortes violentas do município está acima da média do Acre."})
    secoes.append(secao_plano(top, "no município"))
    return [s for s in secoes if s]


# ------------------------------------------------------------------ geometria
def geo_features(itens):
    """[(uid, geom, rural)] -> features. Simplificacao conjunta (coverage_simplify): bairros
    vizinhos continuam com a mesma fronteira, sem frestas nem sobreposicao no mapa."""
    geoms = shapely.coverage_simplify([g for _, g, _ in itens], 0.0002)
    out = []
    for (uid, orig, rural), g in zip(itens, geoms):
        g = shapely.set_precision(g, 1e-5)
        if g.is_empty:
            g = shapely.set_precision(orig, 1e-5)
        out.append({"type": "Feature", "id": uid, "properties": {"rural": int(rural)}, "geometry": mapping(g)})
    return out


def arred(o):
    if isinstance(o, float):
        return round(o, 5)
    if isinstance(o, (list, tuple)):
        return [arred(x) for x in o]
    if isinstance(o, dict):
        return {k: arred(v) for k, v in o.items()}
    return o


# ------------------------------------------------------------------ main
def main():
    OUT.mkdir(exist_ok=True)
    (OUT / "mun").mkdir(exist_ok=True)
    (OUT / "geo").mkdir(exist_ok=True)

    censo = ler_censo()
    setores = carregar_setores()
    locais = carregar_locais()
    secoes = carregar_secoes()
    perfis = perfil_por_secao()
    setor_uid, meta_u = unidades(setores, locais)
    agregar_sem_secao(setores, setor_uid, meta_u, secoes)
    MUNICIPIOS_AC.update(chave(s["nm_mun"]) for s in setores.values())
    localidades_rurais(setores, locais, setor_uid, meta_u, {(s["mun_tse"], s["local"]) for s in secoes})
    for u in meta_u.values():
        u.setdefault("tipo", "Bairro")
    nomes_mun = {s["mun"]: s["nm_mun"] for s in setores.values()}
    disputa = {str(m["cd"]): m for m in json.loads((DISPUTA / "AC.json").read_text())}

    # setores e secoes por unidade
    set_por_u = defaultdict(list)
    for cd, uid in setor_uid.items():
        set_por_u[uid].append(cd)
    sec_por_u, sem_setor = defaultdict(list), 0
    for s in secoes:
        uid = setor_uid.get(s["cd_setor"])
        if uid is None:  # sem coordenada: zona rural do municipio (registrado na proveniencia)
            sem_setor += 1
            uid = f"{s['cd_mun']}-zona-rural" if s["cd_mun"] else None
            if uid not in meta_u:
                continue
        sec_por_u[uid].append(s)

    # referencias estaduais por zona (a zona e a da unidade: bairros x localidades rurais)
    def ref_zona(rural, rot):
        cds = [cd for cd, u in setor_uid.items() if meta_u[u]["rural"] == rural]
        r = indicadores(somar(cds, censo), sum(setores[c]["area"] for c in cds))
        r["_rot"] = rot
        return r
    ref_estado = ref_zona(False, "no Acre urbano")
    ref_rural = ref_zona(True, "na zona rural do Acre")
    ind_estado = indicadores(somar(list(setores), censo), sum(s["area"] for s in setores.values()))

    obras = ler_obras(setores, setor_uid, nomes_mun)
    politicas = ler_politicas_curadas(nomes_mun, meta_u)
    pop_mun = Counter()
    for cd, s in setores.items():
        pop_mun[s["mun"]] += int((censo.get(cd) or {}).get("V0001") or 0)
    seguranca = ler_seguranca(nomes_mun, pop_mun)

    municipios = []
    for mun in sorted(nomes_mun, key=lambda m: nomes_mun[m]):
        uids = [u for u in meta_u if meta_u[u]["mun"] == mun]
        set_m = [cd for u in uids for cd in set_por_u[u]]
        sec_m = [s for u in uids for s in sec_por_u[u]]
        m = {
            "cd": mun, "nome": nomes_mun[mun], "fonte": meta_u[uids[0]]["fonte"],
            "censo": indicadores(somar(set_m, censo), sum(setores[c]["area"] for c in set_m)),
            "eleicao": resultado(sec_m),
        }
        m["perfil"] = perfil_unidade(sec_m, perfis)
        d = disputa.get(mun, {})
        m["social"] = {k: d.get("ind", {}).get(k) for k in
                       ("pbf_familias", "pbf_valor_medio", "pbf_pessoas_pct", "bpc_por_mil_hab", "pib_per_capita",
                        "evangelicos_pct", "renda_pc_media")}
        m["historico_lula_ab"] = d.get("share")  # % polo A (PT) 2006-2026, painel Territorios em Disputa
        m["seguranca"] = seguranca.get(mun)
        m["_seg_estado"] = seguranca.get("_estado")
        obras_m = [o for o in obras if o["mun"] == mun]
        unidades_m, feats = [], []
        for uid in uids:
            u = dict(meta_u[uid])
            sets = set_por_u[uid]
            area = sum(setores[c]["area"] for c in sets)
            u["censo"] = indicadores(somar(sets, censo), area)
            u["setores"] = len(sets)
            secs = sec_por_u[uid]
            u["eleicao"] = resultado(secs)
            u["perfil"] = perfil_unidade(secs, perfis) if secs else None
            u["obras"] = [o for o in obras_m if o["bairro"] == uid]
            u["politicas"] = [p["politica"] for p in politicas if uid in p["bairros"]]
            u["plano"] = compromissos(u["censo"], ref_rural if u["rural"] else ref_estado, u["rural"], u["tipo"]) + \
                compromisso_seguranca(m["seguranca"], m["_seg_estado"], m["nome"])
            u["_ref"] = ref_rural if u["rural"] else ref_estado
            unidades_m.append(u)
            feats.append((uid, shapely.union_all([setores[c]["geom"] for c in sets]), u["rural"]))
        # agregados por zona (filtro "Zona urbana" / "Zona rural" sem localidade escolhida)
        m["zonas"] = {}
        for zona, rural in (("urbana", False), ("rural", True)):
            membros = [u for u in unidades_m if u["rural"] == rural]
            if not membros:
                m["zonas"][zona] = None
                continue
            sets = [cd for u in membros for cd in set_por_u[u["id"]]]
            secs = [x for u in membros for x in sec_por_u[u["id"]]]
            z = dict(id=f"{mun}-zona-{zona}", mun=mun, nome=f"Zona {zona}", tipo="Zona", fonte=m["fonte"], rural=rural,
                     nome_base=f"Zona {zona}", agrega=[], setores=len(sets), localidades=len(membros))
            z["censo"] = indicadores(somar(sets, censo), sum(setores[c]["area"] for c in sets))
            z["eleicao"] = resultado(secs)
            z["perfil"] = perfil_unidade(secs, perfis) if secs else None
            z["obras"] = [o for o in obras_m if o["bairro"] in {u["id"] for u in membros}]
            z["politicas"] = []
            z["plano"] = compromissos(z["censo"], ref_rural if rural else ref_estado, rural, "Zona") + \
                compromisso_seguranca(m["seguranca"], m["_seg_estado"], m["nome"])
            z["_ref"] = ref_rural if rural else ref_estado
            m["zonas"][zona] = z
        for u in [*unidades_m, *filter(None, m["zonas"].values())]:
            u["texto"] = texto_unidade(u, m)
            u["obras"] = [o["id"] for o in u["obras"]]
            u.pop("_ref", None)
        m["texto"] = texto_municipio(m, unidades_m, obras_m)
        m.pop("_seg_estado")
        m.pop("seguranca")  # ja virou texto e compromisso; o painel nao usa a serie
        m["bairros"] = sorted(unidades_m, key=lambda u: (u["rural"], -u["censo"]["pop"]))
        m["obras"] = sorted(obras_m, key=lambda o: -o["valor_federal"])
        m["politicas"] = [i for i, p in enumerate(politicas) if "*" in p["muns"] or mun in p["muns"]]
        (OUT / "mun" / f"{mun}.json").write_text(json.dumps(m, ensure_ascii=False, separators=(",", ":")))
        (OUT / "geo" / f"{mun}.json").write_text(json.dumps(arred({"type": "FeatureCollection", "features": geo_features(feats)}),
                                                            separators=(",", ":")))
        municipios.append({
            "cd": mun, "nome": m["nome"], "fonte": m["fonte"], "pop": m["censo"]["pop"],
            "bairros": sum(1 for u in unidades_m if not u["rural"]),
            "localidades": sum(1 for u in unidades_m if u["rural"]),
            "lula_pct": (m["eleicao"] or {}).get("lula_pct"), "flavio_pct": (m["eleicao"] or {}).get("flavio_pct"),
            "obras": len(obras_m), "valor_federal": round(sum(o["valor_federal"] for o in obras_m), 2),
        })
        print(f"{m['nome']:<22} {m['fonte']:<4} bairros={municipios[-1]['bairros']:>3} "
              f"rurais={municipios[-1]['localidades']:>3} obras={len(obras_m):>3} "
              f"secoes={len(sec_m)}")

    meta = {
        "gerado_em": date.today().isoformat(),
        "eleicao": {"ano": 2026, "turno": 1, "data": "2026-10-04", "cand_a": "Lula", "nr_a": LULA,
                    "cand_b": "Flávio Bolsonaro", "nr_b": FLAVIO},
        "estado": {"censo": ind_estado, "ref_urbana": ref_estado, "ref_rural": ref_rural,
                   "eleicao": resultado([s for v in sec_por_u.values() for s in v])},
        "municipios": municipios,
        "indicadores": [dict(k=k, rotulo=r, suf=s, grupo=g, bom=b) for k, r, s, g, b in INDICADORES],
        "dimensoes": DIMENSOES,
        "plano": [{k: v for k, v in c.items() if k not in ("gatilho", "territorio")} for c in PLANO],
        "politicas": politicas,
        "proveniencia": [
            {"fonte": "IBGE — Censo 2022, agregados por setor censitário (básico, demografia, cor ou raça, alfabetização, características do domicílio 1–3, entorno)", "url": "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios/"},
            {"fonte": "IBGE — Censo 2022, rendimento do responsável por setor (publicação 08/05/2026)", "url": "https://ftp.ibge.gov.br/Censos/Censo_Demografico_2022/Agregados_por_Setores_Censitarios_Rendimento_do_Responsavel/"},
            {"fonte": "IBGE — malhas de setores e de bairros do Censo 2022", "url": "https://geoftp.ibge.gov.br/organizacao_do_territorio/malhas_territoriais/malhas_de_setores_censitarios__divisoes_intramunicipais/censo_2022/"},
            {"fonte": "TSE — boletins de urna, seções e locais de votação, 1º turno de 04/10/2026", "url": "https://resultados.tse.jus.br/oficial/app/index.html"},
            {"fonte": "TSE — perfil do eleitorado por seção, 2026", "url": "https://dadosabertos.tse.jus.br/"},
            {"fonte": "ObrasGov.br — projetos de investimento e geometrias (API pública)", "url": "https://api.obrasgov.gestao.gov.br/obrasgov/api/swagger-ui/index.html"},
            {"fonte": "MJSP — Sinesp VDE, indicadores de segurança pública por município, 2023 a ago/2026", "url": "https://www.gov.br/mj/pt-br/assuntos/sua-seguranca/seguranca-publica/estatistica/dados-nacionais-1/base-de-dados-e-notas-metodologicas-dos-gestores-estaduais-sinesp-vde-2022-e-2023"},
            {"fonte": "MDS — MI Social (Bolsa Família, BPC), ago/2026, via painel Territórios em Disputa", "url": "https://aplicacoes.mds.gov.br/sagi/servicos/misocial/"},
            {"fonte": "Programa de Governo Lula 2026 — Coligação Brasil Pronto Pra Mais (84 p.)", "url": "https://static.congressoemfoco.com.br/2026/08/08/attachment/2026/08/08/32ce89_programa_governo_lula_2026.pdf"},
        ],
        "notas": {"secoes_sem_setor": sem_setor},
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":"), default=str))
    print(f"ok: {len(municipios)} municipios, {sum(m['bairros'] for m in municipios)} bairros, "
          f"{sum(m['localidades'] for m in municipios)} localidades rurais, {len(obras)} obras, "
          f"{len(politicas)} politicas curadas; secoes sem setor: {sem_setor}")


if __name__ == "__main__":
    main()
