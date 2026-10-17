import { projetar } from "./js/geo.js";
import { escala, fmt } from "./js/escalas.js";

const SVG = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);
const fmtInd = (v, suf) => (suf.trim().startsWith("R$") && v != null
  ? `R$ ${fmt(Math.round(v))}${suf.replace("R$", "").trimEnd()}` : fmt(v, suf));
const fmtInt = (v) => (v == null ? "—" : new Intl.NumberFormat("pt-BR").format(Math.round(v)));
const brl = (v) => (v == null ? "—" : v >= 1e6 ? `R$ ${fmt(v / 1e6)} mi` : v >= 1e3 ? `R$ ${fmt(Math.round(v / 1e3))} mil` : `R$ ${fmtInt(v)}`);
const dataBR = (s) => (s ? s.slice(0, 10).split("-").reverse().join("/") : "—");

function el(tag, props = {}, ...filhos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") e.className = v; else e.setAttribute(k, v);
  }
  for (const f of filhos.flat()) if (f != null && f !== false) e.append(f instanceof Node ? f : String(f));
  return e;
}
const ns = (tag, attrs = {}, txt) => {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  if (txt != null) n.textContent = txt;
  return n;
};

async function json(caminho) {
  const r = await fetch(caminho, { credentials: "omit" });
  if (!r.ok) throw new Error(`HTTP ${r.status} ${caminho}`);
  return r.json();
}

const CAMADAS = [
  ["lula_pct", "Lula (% dos votos nominais)"],
  ["flavio_pct", "Flávio Bolsonaro (% dos votos nominais)"],
  ["renda_resp", "Renda do responsável"],
  ["esgoto_adequado_pct", "Esgoto adequado"],
  ["agua_rede_pct", "Água da rede geral"],
  ["alfabetizacao_pct", "Alfabetização"],
  ["pavimentacao_pct", "Rua pavimentada"],
  ["pretos_pardos_pct", "Pretos e pardos"],
  ["indigenas_pct", "Indígenas"],
  ["densidade", "Densidade"],
];
// ordem e rotulo dos grupos de localidades rurais no seletor
const TIPOS_RURAIS = [["Aldeia", "Aldeias"], ["Ramal", "Ramais"], ["Seringal", "Seringais e colocações"],
  ["Comunidade ribeirinha", "Comunidades ribeirinhas"], ["Vila ou comunidade", "Vilas e comunidades"],
  ["Assentamento", "Assentamentos e polos"], ["Rodovia", "Às margens de rodovias"], ["Localidade", "Outras localidades"]];
const ZONAS = { urbana: "Zona urbana", rural: "Zona rural" };

const estado = { meta: null, cd: "1200401", zona: null, bid: null, camada: "lula_pct",
                 dados: null, geo: null, paths: new Map(), porId: new Map(), obrasPorId: new Map() };

// ---------------------------------------------------------------- hash
function lerHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (/^12\d{5}$/.test(h.get("mun") ?? "")) estado.cd = h.get("mun");
  estado.zona = ZONAS[h.get("zona")] ? h.get("zona") : null;
  estado.bid = h.get("b") || null;
  if (CAMADAS.some(([k]) => k === h.get("mapa"))) estado.camada = h.get("mapa");
}
const gravarHash = () => history.replaceState(null, "", "#" + new URLSearchParams({
  mun: estado.cd, ...(estado.zona ? { zona: estado.zona } : {}), ...(estado.bid ? { b: estado.bid } : {}),
  mapa: estado.camada }));

// ---------------------------------------------------------------- recorte selecionado
const zonaDe = (u) => (u.rural ? "rural" : "urbana");
/** Unidade em foco (localidade, zona ou municipio), o nivel acima e as irmas comparaveis. */
function recorte() {
  const m = estado.dados;
  const b = estado.porId.get(estado.bid);
  const z = estado.zona ? m.zonas[estado.zona] : null;
  const membros = estado.zona ? m.bairros.filter((u) => zonaDe(u) === estado.zona) : m.bairros;
  if (b) return { alvo: b, nivel: "localidade", pai: z, avo: m, irmas: membros };
  if (z) return { alvo: z, nivel: "zona", pai: m, avo: null, irmas: membros };
  return { alvo: m, nivel: "municipio", pai: null, avo: null, irmas: m.bairros };
}
const nomeTipo = (u) => (u.tipo === "Zona" ? u.nome : u.rural ? u.tipo.toLowerCase() : "bairro");
const refEstado = (alvo, nivel) => (nivel === "municipio" ? ["Acre", estado.meta.estado.censo]
  : alvo.rural ? ["Acre rural", estado.meta.estado.ref_rural] : ["Acre urbano", estado.meta.estado.ref_urbana]);

// ---------------------------------------------------------------- valores
function valor(b, k) {
  if (k === "lula_pct" || k === "flavio_pct") return b.eleicao?.[k] ?? null;
  return b.censo?.[k] ?? null;
}
const sufCamada = (k) => (k.endsWith("_pct") && !k.startsWith("lula") && !k.startsWith("flavio")
  ? "%" : estado.meta.indicadores.find((i) => i.k === k)?.suf ?? "%");

// ---------------------------------------------------------------- carga
async function carregarMun() {
  const [dados, geo] = await Promise.all([json(`data/mun/${estado.cd}.json`), json(`data/geo/${estado.cd}.json`)]);
  estado.dados = dados;
  estado.geo = geo;
  estado.porId = new Map(dados.bairros.map((b) => [b.id, b]));
  estado.obrasPorId = new Map(dados.obras.map((o) => [o.id, o]));
  const b = estado.porId.get(estado.bid);
  if (!b) estado.bid = null;
  else estado.zona = zonaDe(b);
  if (estado.zona && !dados.zonas[estado.zona]) estado.zona = null;
  preencherFiltros();
  desenharMapa();
  atualizar();
}

function preencherFiltros() {
  const m = estado.dados;
  const sz = $("f-zona");
  sz.replaceChildren(el("option", { value: "" }, "— todo o município —"),
    ...Object.entries(ZONAS).filter(([z]) => m.zonas[z]).map(([z, r]) => {
      const n = m.bairros.filter((u) => zonaDe(u) === z).length;
      return el("option", { value: z }, `${r} (${n} ${z === "urbana" ? (n === 1 ? "bairro" : "bairros") : (n === 1 ? "localidade" : "localidades")})`);
    }));
  sz.value = estado.zona ?? "";
  const sb = $("f-bairro");
  $("rot-bairro").firstChild.textContent = estado.zona === "rural" ? "Ramal, aldeia ou comunidade " : "Bairro ";
  if (!estado.zona) {
    sb.replaceChildren(el("option", { value: "" }, "— escolha antes a zona —"));
    sb.disabled = true;
    return;
  }
  sb.disabled = false;
  const lista = m.bairros.filter((u) => zonaDe(u) === estado.zona);
  const porNome = (a, c) => a.nome.localeCompare(c.nome, "pt-BR");
  const todos = el("option", { value: "" }, `— toda a ${ZONAS[estado.zona].toLowerCase()} —`);
  if (estado.zona === "urbana") sb.replaceChildren(todos, ...lista.sort(porNome).map((u) => el("option", { value: u.id }, u.nome)));
  else {
    sb.replaceChildren(todos, ...TIPOS_RURAIS.map(([t, rot]) => {
      const g = lista.filter((u) => u.tipo === t).sort(porNome);
      return g.length ? el("optgroup", { label: rot }, ...g.map((u) => el("option", { value: u.id }, u.nome))) : null;
    }).filter(Boolean));
  }
  sb.value = estado.bid ?? "";
}

// ---------------------------------------------------------------- mapa
function desenharMapa() {
  const { dados, geo } = estado;
  const k = estado.camada;
  // zona urbana: enquadra os bairros (zona rural ao fundo, esmaecida); demais: municipio inteiro
  const daZona = (f) => !estado.zona || (estado.zona === "rural") === Boolean(f.properties.rural);
  const urbanos = geo.features.filter((f) => !f.properties.rural);
  const enquadrar = estado.zona === "urbana" && urbanos.length ? urbanos : geo.features;
  const { paths, largura, altura, ponto, dentro } = projetar({ type: "FeatureCollection", features: enquadrar }, 800, null);
  const fundo = enquadrar === geo.features ? [] : geo.features.filter((f) => f.properties.rural).map((f) => caminho(f, ponto));
  const vals = Object.fromEntries(dados.bairros.map((b) => [b.id, valor(b, k)]));
  const escopo = dados.bairros.filter((b) => !estado.zona || zonaDe(b) === estado.zona);
  const esc = k === "lula_pct" || k === "flavio_pct"
    ? escalaVoto(k) : escala(k, escopo.map((b) => vals[b.id]));
  const svg = ns("svg", { viewBox: `0 0 ${largura} ${altura}`, role: "img", overflow: "hidden",
    "aria-label": `Mapa de ${dados.nome}: ${CAMADAS.find((c) => c[0] === k)[1]}` });
  const tip = el("div", { class: "tooltip" });
  tip.hidden = true;
  const mostrar = (e, ...linhas) => {
    tip.replaceChildren(el("strong", {}, linhas[0]), ...linhas.slice(1).filter(Boolean).map((l) => el("span", {}, l)));
    const r = $("mapa").getBoundingClientRect();
    tip.style.left = Math.min(e.clientX - r.left + 14, r.width - 230) + "px";
    tip.style.top = e.clientY - r.top + 14 + "px";
    tip.hidden = false;
  };
  estado.paths = new Map();
  const featById = new Map(geo.features.map((f) => [String(f.id), f]));
  for (const p of [...fundo.map((x) => ({ ...x, fundo: true })), ...paths]) {
    const b = estado.porId.get(p.id);
    const foraDaZona = p.fundo || !daZona(featById.get(p.id));
    const path = ns("path", { d: p.d, class: "mun" + (foraDaZona ? " fundo" : ""), tabindex: foraDaZona ? "-1" : "0",
      "aria-label": b?.nome ?? p.id });
    path.style.fill = esc.cor(vals[p.id]); // CSSOM: permitido pela CSP (sem style inline)
    path.addEventListener("click", () => selecionar(p.id));
    path.addEventListener("keydown", (e) => { if (e.key === "Enter") selecionar(p.id); });
    path.addEventListener("mousemove", (e) => mostrar(e, b?.nome ?? p.id,
      b ? `${b.rural ? b.tipo : "Bairro"} · ${fmtInt(b.censo.pop)} moradores` : null,
      `${CAMADAS.find((c) => c[0] === k)[1]}: ${fmtInd(vals[p.id], sufCamada(k))}`,
      b?.eleicao ? `Lula ${fmt(b.eleicao.lula_pct, "%")} · Flávio ${fmt(b.eleicao.flavio_pct, "%")}` : null));
    svg.append(path);
    estado.paths.set(p.id, path);
  }
  const obras = dados.obras.filter((o) => o.lon != null && dentro([o.lon, o.lat])
    && (!estado.zona || (o.bairro && zonaDe(estado.porId.get(o.bairro) ?? {}) === estado.zona)));
  for (const o of obras) {
    const [x, y] = ponto([o.lon, o.lat]);
    const c = ns("circle", { cx: x.toFixed(1), cy: y.toFixed(1), r: 5, class: "obra" });
    c.addEventListener("mousemove", (e) => mostrar(e, o.nome.slice(0, 70) + (o.nome.length > 70 ? "…" : ""),
      `${brl(o.valor_federal)} federais · ${o.situacao}`, o.tipo || ""));
    c.addEventListener("click", () => o.bairro && selecionar(o.bairro));
    svg.append(c);
  }
  svg.addEventListener("mouseleave", () => { tip.hidden = true; });
  $("mapa").replaceChildren(svg, tip);
  $("titulo-mapa").textContent = estado.zona ? `${ZONAS[estado.zona]} de ${dados.nome}` : `${dados.nome}: bairros e zona rural`;
  const leg = esc.legenda.map((l) => {
    const i = el("i"); i.style.background = l.cor; return el("span", {}, i, l.rotulo);
  });
  if (obras.length) leg.push(el("span", {}, el("i", { class: "ponto-obra" }), `obra federal (${obras.length})`));
  $("legenda").replaceChildren(...leg);
  $("mapa-nota").textContent = (k === "lula_pct" || k === "flavio_pct"
    ? "1º turno de 2026, atribuído pelo local de votação. " : "Censo 2022 (IBGE). ")
    + (estado.zona === "urbana" ? "Zona rural ao fundo, esmaecida. " : estado.zona === "rural" ? "Bairros urbanos esmaecidos. " : "")
    + "Clique numa área para abrir o perfil.";
}

// escala fixa para % de votos nominais (comparavel entre municipios): sequencial por candidato
function escalaVoto(k) {
  const lim = [0, 15, 25, 35, 45, 55, 65, 100.01];
  const rampa = k === "lula_pct"
    ? ["#fbe3e2", "#f2b6b4", "#e5807e", "#d4524f", "#b8322f", "#8f1f1d", "#5e1211"]
    : ["#dceafb", "#b4d1f5", "#86b6ef", "#5797e6", "#2a78d6", "#1d5aa6", "#123b70"];
  const cor = (v) => {
    if (v == null) return "var(--sem-dado)";
    let i = 0;
    while (i < rampa.length - 1 && v >= lim[i + 1]) i++;
    return rampa[i];
  };
  return { cor, legenda: rampa.map((c, i) => ({ cor: c, rotulo: i === rampa.length - 1 ? `≥ ${lim[i]}%` : `${lim[i]}–${lim[i + 1]}%` })) };
}

// path SVG de uma feature com a projecao (funcao ponto) de outro enquadramento
function caminho(f, ponto) {
  const g = f.geometry;
  const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
  const d = polys.map((p) => p.map((r) => "M" + r.map((c) => ponto(c).map((v) => v.toFixed(1)).join(",")).join("L") + "Z").join("")).join("");
  return { id: String(f.id), props: f.properties ?? {}, d };
}

function selecionar(id) {
  const b = estado.porId.get(id);
  if (!b) return;
  if (estado.bid === id) { estado.bid = null; } else { estado.bid = id; }
  const zonaNova = zonaDe(b);
  const mudouZona = estado.zona !== zonaNova;
  estado.zona = zonaNova;
  preencherFiltros();
  if (mudouZona) desenharMapa();
  atualizar();
}

function atualizar() {
  gravarHash();
  for (const [id, p] of estado.paths) p.classList.toggle("sel", id === estado.bid);
  const sp = estado.paths.get(estado.bid);
  if (sp) sp.parentNode.insertBefore(sp, sp.parentNode.querySelector("circle")); // frente, abaixo das obras
  const r = recorte();
  $("painel-lateral").replaceChildren(...perfil(r).filter(Boolean));
  cardTexto(r);
  cardUrna(r);
  cardEleitor(r);
  cardSocio(r);
  cardPoliticas(r);
}

// ---------------------------------------------------------------- cards
const tile = (r, v) => el("div", {}, el("span", {}, r), el("b", {}, v));
const obrasDe = (r) => {
  const m = estado.dados;
  if (r.nivel === "localidade") return m.obras.filter((o) => o.bairro === r.alvo.id);
  if (r.nivel === "zona") { const ids = new Set(r.irmas.map((u) => u.id)); return m.obras.filter((o) => ids.has(o.bairro)); }
  return m.obras;
};

function linhaLider(e) {
  if (!e || !e.validos) return el("p", { class: "lider" }, "Sem seções eleitorais nesta área.");
  const [nv, pv, vv, np, pp, vp] = e.lula >= e.flavio
    ? ["Lula", e.lula_pct, e.lula, "Flávio Bolsonaro", e.flavio_pct, e.flavio]
    : ["Flávio Bolsonaro", e.flavio_pct, e.flavio, "Lula", e.lula_pct, e.lula];
  return el("p", { class: "lider" }, "À frente no 1º turno de 2026: ", el("b", {}, nv),
    `, com ${fmt(pv, "%")} dos votos nominais (${fmtInt(vv)} votos), contra ${fmt(pp, "%")} de ${np} (${fmtInt(vp)}) — `,
    `diferença de ${fmt(Math.abs(pv - pp))} pontos percentuais, em ${fmtInt(e.secoes)} ${e.secoes === 1 ? "seção" : "seções"} de ${fmtInt(e.locais)} ${e.locais === 1 ? "local" : "locais"} de votação.`);
}

function perfil(r) {
  const { alvo, nivel } = r;
  const m = estado.dados, e = alvo.eleicao;
  const ROT = Object.fromEntries(estado.meta.indicadores.map((i) => [i.k, i]));
  const obras = obrasDe(r);
  const valObras = obras.reduce((s, o) => s + o.valor_federal, 0);
  const selo = nivel === "municipio" ? `${m.zonas.urbana ? r.irmas.filter((u) => !u.rural).length : 0} bairros · ${r.irmas.filter((u) => u.rural).length} localidades rurais`
    : nivel === "zona" ? ZONAS[estado.zona].toLowerCase() : alvo.rural ? alvo.tipo.toLowerCase() : `bairro ${alvo.fonte}`;
  const titulo = nivel === "municipio" ? m.nome : nivel === "zona" ? `${alvo.nome}` : alvo.nome;
  const out = [
    el("div", { class: "perfil-topo" },
      el("h2", {}, titulo, " ", el("small", {}, nivel === "municipio" ? "AC" : m.nome)),
      el("span", { class: "selo " + (alvo.rural ? "selo-rural" : "fonte-ibge") }, selo)),
    el("div", { class: "tiles" },
      tile("Moradores", fmtInt(alvo.censo.pop)),
      tile("Renda resp.", fmtInd(alvo.censo.renda_resp, " R$")),
      tile("Esgoto adequado", fmt(alvo.censo.esgoto_adequado_pct, "%")),
      tile("Lula (% nominais)", e ? fmt(e.lula_pct, "%") : "—"),
      tile("Flávio (% nominais)", e ? fmt(e.flavio_pct, "%") : "—"),
      tile("Obras 2023–2026", String(obras.length))),
    obras.length ? el("p", { class: "valor-obras" }, `${brl(valObras)} previstos de fonte federal nessas obras.`) : null,
    alvo.plano?.some((c) => c.motivo) ? el("ul", { class: "tags", "aria-label": "Carências relativas" },
      ...alvo.plano.filter((c) => c.motivo).map((c) => el("li", {}, `${ROT[c.motivo.k].rotulo}: ${fmtInd(c.motivo.valor, ROT[c.motivo.k].suf)}`))) : null,
    linhaLider(e),
  ];
  if (nivel === "localidade") {
    out.push(el("div", { class: "def-classe" },
      el("b", {}, alvo.rural ? `Localidade rural · ${alvo.tipo}` : alvo.fonte === "IBGE" ? "Bairro oficial do IBGE" : "Bairro aproximado (TSE)"),
      el("span", {}, alvo.rural
        ? `Setores censitários rurais (${alvo.setores}) cujo local de votação rural mais próximo fica nesta localidade; nome do aglomerado do IBGE ou do endereço do local no TSE.`
        : alvo.fonte === "IBGE" ? `Bairro delimitado pelo IBGE no Censo 2022 (${alvo.setores} setores censitários).`
        : `União de ${alvo.setores} setores censitários urbanos cujo local de votação mais próximo declara este bairro no TSE.`,
        alvo.agrega?.length ? ` Inclui ${alvo.agrega.join(", ")}, sem local de votação próprio.` : "")));
  }
  if (nivel !== "localidade") out.push(ranking(r));
  out.push(el("div", { class: "chave-valor" },
    ...[["Domicílios", fmtInt(alvo.censo.domicilios)], ["Área", fmt(alvo.censo.area_km2, " km²")],
        ["Eleitores aptos", e ? fmtInt(e.aptos) : "—"]].map(([a, v]) => el("div", {}, el("span", {}, a), el("b", {}, v)))));
  if (nivel !== "municipio") out.push(el("p", { class: "rodape-card" }, el("button", { type: "button", class: "link-voltar" },
    nivel === "localidade" ? `← voltar à ${ZONAS[estado.zona].toLowerCase()}` : "← voltar ao município")));
  return out;
}

function ranking(r) {
  const lista = [...r.irmas].sort((a, c) => c.censo.pop - a.censo.pop);
  const tbody = el("tbody");
  for (const b of lista) {
    const tr = el("tr", { tabindex: "0" },
      el("td", {}, b.nome, el("small", {}, ` ${b.rural ? b.tipo.toLowerCase() : "bairro"}`)),
      el("td", { class: "num" }, fmtInt(b.censo.pop)),
      el("td", { class: "num a" }, b.eleicao ? fmt(b.eleicao.lula_pct, "%") : "—"),
      el("td", { class: "num b" }, b.eleicao ? fmt(b.eleicao.flavio_pct, "%") : "—"));
    tr.addEventListener("click", () => selecionar(b.id));
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") selecionar(b.id); });
    tbody.append(tr);
  }
  return el("div", { class: "tabela-rolagem" }, el("table", { class: "ranking" },
    el("caption", { class: "vazio" }, "Clique numa linha (ou no mapa) para abrir o perfil. % dos votos nominais, 1º turno de 2026."),
    el("thead", {}, el("tr", {}, el("th", {}, r.nivel === "zona" && estado.zona === "rural" ? "Localidade" : r.nivel === "zona" ? "Bairro" : "Bairro ou localidade"),
      el("th", { class: "num" }, "Moradores"), el("th", { class: "num" }, "Lula"), el("th", { class: "num" }, "Flávio"))), tbody));
}

function cardTexto(r) {
  const pars = r.alvo.texto ?? [];
  $("card-texto").replaceChildren(
    el("span", { class: "etiqueta-ia" }, "Leitura do território"),
    el("h2", { class: "titulo-ia" }, `${r.nivel === "zona" ? `${r.alvo.nome} de ${estado.dados.nome}` : r.alvo.nome}: principais achados`),
    el("div", { class: "texto" }, ...pars.map((p) => el("p", {}, p))),
    el("p", { class: "aviso-ia" }, el("b", {}, "Como este texto é feito: "),
      "gerado automaticamente por regras fixas a partir dos dados do painel (Censo 2022, TSE 2026, ObrasGov.br e Programa de Governo Lula 2026). Não é análise de IA nem opinião; números arredondados."),
  );
}

function cardUrna(r) {
  const card = $("card-urna");
  const e = r.alvo.eleicao;
  const head = el("h2", { class: "titulo-card" }, "Lula × Flávio — 1º turno 2026");
  if (!e || !e.validos) {
    card.replaceChildren(head, el("p", { class: "vazio" }, "Sem seções eleitorais nesta área."));
    return;
  }
  const outros = Math.max(0, 100 - e.lula_pct - e.flavio_pct);
  const ia = el("i"); ia.style.background = "var(--a)";
  const ib = el("i"); ib.style.background = "var(--b)";
  const barra = el("div", { class: "barra-ab", role: "img",
    "aria-label": `Lula ${fmt(e.lula_pct)}%, Flávio ${fmt(e.flavio_pct)}%, outros ${fmt(outros)}% dos votos nominais` });
  for (const [cls, v, cor] of [["ba", e.lula_pct, "var(--a)"], ["bb", e.flavio_pct, "var(--b)"], ["bo", outros, null]]) {
    const d = el("div", { class: cls, title: `${fmt(v)}%` });
    d.style.flexBasis = `${v}%`;
    if (cor) d.style.background = cor;
    barra.append(d);
  }
  // colunas: o setor em foco, os niveis acima e o Acre
  const cols = [["Setor", e]];
  if (r.nivel === "localidade" && r.pai) cols.push([ZONAS[estado.zona], r.pai.eleicao]);
  if (r.nivel !== "municipio") cols.push([estado.dados.nome, estado.dados.eleicao]);
  cols.push(["Acre", estado.meta.estado.eleicao]);
  const linha = (rot, f) => el("tr", {}, el("td", {}, rot), ...cols.map(([, x]) => el("td", { class: "num" }, x ? f(x) : "—")));
  card.replaceChildren(
    head,
    el("div", { class: "placar" },
      el("div", {}, el("span", {}, ia, "Lula (PT)"), el("b", {}, fmt(e.lula_pct, "%")), el("small", {}, ` ${fmtInt(e.lula)} votos`)),
      el("div", {}, el("span", {}, ib, "Flávio Bolsonaro (PL)"), el("b", {}, fmt(e.flavio_pct, "%")), el("small", {}, ` ${fmtInt(e.flavio)} votos`))),
    barra,
    el("div", { class: "barra-rot" }, el("span", {}, `Lula ${fmt(e.lula_pct, "%")}`), el("span", {}, `Flávio ${fmt(e.flavio_pct, "%")}`), el("span", {}, `outros ${fmt(outros, "%")}`)),
    el("div", { class: "tabela-rolagem curta" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "% dos votos nominais"), ...cols.map(([c]) => el("th", { class: "num" }, c)))),
      el("tbody", {},
        linha("Lula", (x) => fmt(x.lula_pct, "%")),
        linha("Flávio Bolsonaro", (x) => fmt(x.flavio_pct, "%")),
        linha("Outros candidatos", (x) => fmt(Math.max(0, 100 - x.lula_pct - x.flavio_pct), "%")),
        linha("Votos em Lula", (x) => fmtInt(x.lula)),
        linha("Votos em Flávio", (x) => fmtInt(x.flavio)),
        linha("Abstenção (% aptos)", (x) => fmt(x.abstencao_pct, "%")),
        linha("Brancos e nulos (% comparec.)", (x) => fmt(x.brancos_nulos_pct, "%")),
        linha("Seções", (x) => fmtInt(x.secoes)),
        linha("Eleitores aptos", (x) => fmtInt(x.aptos))))),
    el("p", { class: "rodape-card" }, "Votos nominais = votos dados a candidatos (válidos). Fonte: boletins de urna do TSE (04/10/2026). O 2º turno ocorre em 25/10/2026."),
  );
}

function cardEleitor(r) {
  const { alvo } = r;
  const p = alvo.perfil, e = alvo.eleicao;
  const card = $("card-eleitor");
  const nome = r.nivel === "zona" ? `${alvo.nome} de ${estado.dados.nome}` : alvo.nome;
  const head = el("h2", { class: "titulo-card" }, `Como votou cada grupo — ${nome}`);
  if (!p || !e || !e.validos) {
    card.replaceChildren(head, el("p", { class: "vazio" }, "Sem seções eleitorais nesta área."));
    return;
  }
  // barra de 100% dos votos nominais: Lula | outros | Flavio, com rotulos diretos
  const barra = (l, f) => {
    const o = Math.max(0, 100 - l - f);
    const b = el("div", { class: "pilha", role: "img", "aria-label": `Lula ${fmt(l)}%, Flávio ${fmt(f)}%, outros ${fmt(o)}%` });
    for (const [cls, v] of [["sa", l], ["so", o], ["sb", f]]) {
      const d = el("div", { class: cls });
      d.style.flexBasis = `${v}%`;
      if (cls !== "so" && v >= 9) d.append(el("span", {}, fmt(v, "%")));
      b.append(d);
    }
    return b;
  };
  const linha = (rot, sub, l, f, ref) => {
    const dif = ref == null ? null : l - ref;
    return el("div", { class: "grupo-voto" + (ref == null ? " total" : "") },
      el("div", { class: "gv-rot" }, el("span", {}, rot), sub ? el("small", {}, sub) : null),
      barra(l, f),
      el("div", { class: "gv-dif" }, dif == null ? "referência"
        : Math.abs(dif) < 0.5 ? "igual ao total"
        : el("span", { class: dif > 0 ? "a" : "b" }, `Lula ${dif > 0 ? "+" : "−"}${fmt(Math.abs(dif))} p.p.`)));
  };
  const NOMES = { genero: "Gênero", idade: "Faixa etária", escolaridade: "Escolaridade" };
  const dims = Object.entries(estado.meta.dimensoes).map(([dim, cats]) => {
    const d = p[dim];
    return el("div", { class: "dim" }, el("h3", {}, NOMES[dim]),
      ...cats.map((c, i) => linha(c, `${fmt(d.eleitorado[i], "%")} do eleitorado`, d.lula[i] ?? 0, d.flavio[i] ?? 0, e.lula_pct)));
  });
  card.replaceChildren(head,
    el("div", { class: "chaves" }, el("span", {}, el("i", { class: "ka" }), "Lula"), el("span", {}, el("i", { class: "ko" }), "outros candidatos"),
      el("span", {}, el("i", { class: "kb" }), "Flávio Bolsonaro")),
    linha("Todos os eleitores", `${fmtInt(e.validos)} votos nominais`, e.lula_pct, e.flavio_pct, null),
    el("div", { class: "dims" }, ...dims),
    el("p", { class: "metodo" }, el("b", {}, "Como ler: "),
      "cada barra estima como votou o grupo (% dos votos nominais no 1º turno de 2026). “Lula +2 p.p.” quer dizer que o grupo deu a Lula 2 pontos a mais do que o total da área. ",
      el("b", {}, "Como é calculado: "),
      `o voto é secreto, então a estimativa é o resultado médio das ${fmtInt(e.secoes)} ${e.secoes === 1 ? "seção" : "seções"} da área, ponderado pelo número de eleitores do grupo em cada uma (perfil do eleitorado do TSE). `
      + "Ela mostra como votaram os lugares onde o grupo está e tende a encolher as diferenças reais entre grupos; trate-a como direção, não como medida exata. "
      + (e.secoes < 3 ? "Com menos de três seções, todos os grupos ficam praticamente iguais ao total. " : "")
      + "Cor/raça não é usada: cerca de 71% do eleitorado do AC não a declarou ao TSE."));
}

function cardSocio(r) {
  const { alvo, nivel, pai, irmas } = r;
  const [rotRef, ref] = refEstado(alvo, nivel);
  const card = $("card-socio");
  const grupos = {};
  for (const i of estado.meta.indicadores) {
    if (alvo.rural && i.grupo === "Entorno urbano") continue; // entorno nao e levantado na zona rural
    (grupos[i.grupo] ??= []).push(i);
  }
  const rotPai = pai ? (nivel === "localidade" ? ZONAS[estado.zona] : estado.dados.nome) : null;
  const blocos = Object.entries(grupos).map(([g, inds]) => el("div", { class: "grupo" }, el("h3", {}, g),
    ...inds.map((i) => {
      const v = alvo.censo[i.k];
      const vs = irmas.map((x) => x.censo[i.k]).filter((x) => x != null);
      const ext = [...vs, v, pai?.censo[i.k], ref[i.k]].filter((x) => x != null);
      const lo = Math.min(...ext), hi = Math.max(...ext);
      const pos = (x) => (hi > lo ? (100 * (x - lo)) / (hi - lo) : 50);
      const trilho = el("div", { class: "trilho", "aria-hidden": "true" });
      if (pai && pai.censo[i.k] != null) { const t = el("span", { class: "mediana" }); t.style.left = pos(pai.censo[i.k]) + "%"; trilho.append(t); }
      if (ref[i.k] != null) { const t = el("span", { class: "mediana br" }); t.style.left = pos(ref[i.k]) + "%"; trilho.append(t); }
      if (v != null) { const t = el("span", { class: "marca" }); t.style.left = pos(v) + "%"; trilho.append(t); }
      const comp = [pai ? `${rotPai}: ${fmtInd(pai.censo[i.k], i.suf)}` : null, `${rotRef}: ${fmtInd(ref[i.k], i.suf)}`]
        .filter(Boolean).join(" · ");
      return el("div", { class: "ind" }, el("span", { class: "rot" }, i.rotulo), el("span", { class: "val" }, fmtInd(v, i.suf)),
        trilho, el("span", { class: "comp" }, comp));
    })));
  const escopo = nivel === "municipio" ? "bairros e localidades rurais do município"
    : estado.zona === "rural" ? "localidades rurais do município" : "bairros do município";
  card.replaceChildren(
    el("h2", { class: "titulo-card" }, `Perfil socioeconômico e demográfico — ${nivel === "zona" ? `${alvo.nome} de ${estado.dados.nome}` : alvo.nome}`),
    el("p", { class: "legenda-trilho" }, `Escala: do menor ao maior valor entre os ${escopo}.`,
      el("i", { class: "m" }), nivel === "localidade" ? (alvo.rural ? "localidade" : "bairro") : nivel === "zona" ? "zona" : "município",
      ...(pai ? [el("i", { class: "u" }), rotPai] : []), el("i", { class: "r" }), rotRef),
    el("div", { class: "grupos" }, ...blocos),
    el("p", { class: "metodo" }, "Fonte: IBGE, Censo Demográfico 2022, agregados por setor censitário somados para a área (inclui rendimento do responsável, publicado em 2026). "
      + "Renda: média do rendimento nominal mensal dos responsáveis com rendimento, ponderada pelo nº de responsáveis. Entorno urbano: só setores urbanos pesquisados no levantamento do entorno. "
      + "Células com sigilo estatístico (setores muito pequenos) são omitidas da soma."),
  );
}

function cardPoliticas(r) {
  const { alvo, nivel } = r;
  const m = estado.dados, meta = estado.meta;
  const card = $("card-politicas");
  const planoIdx = Object.fromEntries(meta.plano.map((c) => [c.id, c]));
  const nomeAlvo = nivel === "zona" ? `${alvo.nome.toLowerCase()} de ${m.nome}` : alvo.nome;
  // localidade e zona: compromissos ja dimensionados; municipio: os mais frequentes entre as areas
  let itens;
  if (nivel !== "municipio") itens = alvo.plano.map((c) => ({ ...c, ter: `${nivel === "zona" ? "Na" : "Em"} ${nomeAlvo}, ${c.territorio}` }));
  else {
    const cont = {};
    for (const x of m.bairros) {
      for (const c of x.plano) {
        const t = (cont[c.id] ??= { pop: 0, nomes: [] });
        t.pop += x.censo.pop;
        t.nomes.push(x.nome);
      }
    }
    itens = Object.entries(cont).sort((a, c) => c[1].pop - a[1].pop).slice(0, 5).map(([id, v]) => ({
      ...planoIdx[id], ter: `Prioritário em ${v.nomes.length} bairro(s) ou localidade(s) que somam ${fmtInt(v.pop)} moradores: ${v.nomes.slice(0, 6).join(", ")}${v.nomes.length > 6 ? "…" : ""}.` }));
  }
  const plano = el("ul", { class: "plano" }, ...itens.map((c) => el("li", {},
    el("b", {}, `${c.tema} · p. ${c.pagina}`),
    el("span", {}, el("strong", {}, "Promessa: "), c.promessa),
    el("span", { class: "meta" }, el("strong", {}, "Meta e escala: "), c.meta),
    el("span", { class: "ter" }, c.ter))));

  const pols = m.politicas.map((i) => meta.politicas[i]);
  const ids = new Set(nivel === "localidade" ? [alvo.id] : r.irmas.map((u) => u.id));
  const naArea = nivel === "municipio" ? [] : pols.filter((p) => p.bairros.some((b) => ids.has(b)));
  const locais = pols.filter((p) => !p.muns.includes("*") && !naArea.includes(p));
  const gerais = pols.filter((p) => p.muns.includes("*"));
  // fontes ja verificadas no pipeline (check_links.py): ok, substituto, copia arquivada ou fora do ar
  const SIT = { substituto: "fonte alternativa", arquivo: "cópia arquivada", indisponivel: "fora do ar" };
  const link = (l) => (l.situacao === "indisponivel"
    ? el("span", { class: "fonte-off", title: l.nota }, `${l.rotulo} (fora do ar, não verificado)`)
    : el("a", { href: l.href, target: "_blank", rel: "noopener noreferrer", ...(l.nota ? { title: l.nota } : {}) },
      l.rotulo, l.situacao !== "ok" ? ` (${SIT[l.situacao]})` : ""));
  const item = (p, marca) => {
    const links = p.links ?? (p.url ? [{ rotulo: p.fonte || "fonte", href: p.url, situacao: "ok" }] : []);
    const usaveis = links.filter((l) => l.situacao !== "indisponivel");
    return el("li", {}, el("b", {}, p.politica), marca ? el("span", { class: "local" }, marca) : null,
      usaveis.length ? null : el("span", { class: "selo-nv" }, "fonte não verificada"),
      el("span", {}, `${p.evidencia ?? p.descricao ?? ""}`.slice(0, 320)),
      el("span", { class: "fontes" }, `${p.esfera ?? ""} · ${p.data ?? ""} · `,
        ...(usaveis.length ? usaveis : links).flatMap((l, i) => [i ? " · " : "", link(l)])));
  };
  const progs = el("ul", { class: "programas" },
    ...naArea.map((p) => item(p, nivel === "localidade" ? "nesta área" : "na zona")),
    ...locais.map((p) => item(p, m.nome)),
    ...gerais.map((p) => item(p, null)));

  const obras = obrasDe(r);
  const nomeB = (id) => estado.porId.get(id)?.nome ?? "sem localização";
  const tb = el("tbody", {}, ...obras.map((o) => el("tr", {},
    el("td", {}, o.nome, el("br"), el("small", {}, `${o.tipo || "—"} · ${o.repassador || ""}`)),
    el("td", { class: "sit" }, o.situacao), el("td", { class: "nowrap" }, dataBR(o.inicio)),
    el("td", { class: "num nowrap" }, brl(o.valor_federal)), el("td", {}, o.bairro ? nomeB(o.bairro) : "sem localização"),
    el("td", { class: "consulta" }, ...(o.links ?? []).flatMap((l, i) => [i ? el("br") : null,
      el("a", { href: l.href, target: "_blank", rel: "noopener noreferrer",
        ...(l.conferido === false ? { title: "Link montado pelo número do instrumento no Transferegov; não conferido individualmente (o Portal exige verificação humana)" } : {}) },
        l.rotulo.startsWith("Ficha") ? "ObrasGov.br" : "Portal da Transparência"),
      l.conferido === false ? el("small", { class: "nc" }, " (não conferido)") : null])))));
  const total = obras.reduce((s, o) => s + o.valor_federal, 0);
  const s = m.social;
  card.replaceChildren(
    el("h2", { class: "titulo-card" }, `Políticas públicas federais 2023–2026 — ${nomeAlvo}`),
    el("div", { class: "totais-pol" },
      el("span", {}, el("b", {}, obras.length), " obras federais iniciadas em 2023–2026"),
      el("span", {}, el("b", {}, brl(total)), " previstos de fonte federal"),
      el("span", {}, el("b", {}, fmtInt(s.pbf_familias)), " famílias no Bolsa Família (município, ago/2026)"),
      el("span", {}, el("b", {}, fmtInd(s.pbf_valor_medio, " R$")), " benefício médio"),
      el("span", {}, el("b", {}, fmt(s.bpc_por_mil_hab)), " BPC por mil hab.")),
    el("div", { class: "pol-grade" },
      el("div", {}, el("h3", {}, "O que o plano de governo de Lula 2026 oferece a este território"), plano,
        el("p", { class: "metodo" }, "Compromissos do Programa de Governo Lula 2026 (Coligação Brasil Pronto Pra Mais, 84 p.), escolhidos por regra: os indicadores em que a área está mais distante do Acre urbano (bairros) ou da zona rural do Acre (localidades rurais), com pelo menos 3 p.p. ou 7,5% em renda e adensamento. Nas localidades rurais entram os compromissos para o campo; nas aldeias, o compromisso com os povos indígenas. Trechos entre aspas são citações do plano.")),
      el("div", {}, el("h3", {}, "Programas federais de 2023 a 2026"),
        el("div", { class: "tabela-rolagem" }, progs),
        el("p", { class: "metodo" }, `Curadoria de políticas federais no Acre com fonte, só com evidência datada de 2023 a 2026. Programas de alcance estadual ou nacional valem para todas as áreas; o selo indica citação nominal da área ou do município. Links verificados em ${dataBR(meta.gerado_em)}: durante o período eleitoral, o portal do Governo do Acre e várias notícias do gov.br saíram do ar (Lei 9.504/1997); nesses casos o link aponta para a cópia arquivada no Internet Archive ou para fonte alternativa conferida, e “fonte não verificada” indica que nenhuma delas foi encontrada.`))),
    el("h3", {}, `Obras e projetos federais ${nivel === "localidade" ? "georreferenciados nesta área" : nivel === "zona" ? "georreferenciados na zona" : "no município"}`),
    obras.length ? el("div", { class: "tabela-rolagem obras" }, el("table", {},
      el("thead", {}, el("tr", {}, el("th", {}, "Projeto"), el("th", {}, "Situação"), el("th", {}, "Início"),
        el("th", { class: "num" }, "Valor federal"), el("th", {}, "Bairro ou localidade"), el("th", {}, "Consultar"))), tb))
      : el("p", { class: "vazio" }, "Nenhum projeto do ObrasGov.br iniciado em 2023–2026 localizado nesta área."),
    el("p", { class: "metodo" }, "ObrasGov.br: só projetos com fonte de recurso federal iniciados entre 2023 e 2026 (data de início efetiva ou prevista), exceto cancelados. "
      + "A localização é a geometria informada pelo órgão executor; pontos repetidos por dezenas de projetos (endereço do órgão) são descartados e o projeto fica no município indicado no nome. Valor = investimento previsto, não pago. "
      + "Consultar: ficha do projeto nos dados abertos do ObrasGov.br (todas abertas e conferidas uma a uma) e, quando há instrumento no Transferegov, o convênio no Portal da Transparência. O Portal bloqueia acessos automatizados com verificação humana; os convênios marcados “não conferido” seguem o mesmo endereço padrão, mas não foram abertos um a um."),
  );
}

function rodape() {
  const m = estado.meta;
  $("rodape").replaceChildren(
    el("p", {}, el("b", {}, "Minha Quebrada! "), `Dados gerados em ${dataBR(m.gerado_em)}. Painel estático, sem rastreamento.`),
    el("p", {}, "Método: toda área é uma união de setores censitários do IBGE. Zona urbana: nos 8 municípios em que o IBGE delimitou bairros no Censo 2022 usam-se os bairros oficiais; nos outros 14, inclusive Rio Branco e Cruzeiro do Sul, cada setor urbano recebe o bairro declarado no local de votação do TSE mais próximo. Bairros sem local de votação são agregados ao vizinho de maior fronteira. Zona rural: cada setor rural recebe a localidade (ramal, aldeia, seringal, comunidade, rodovia) do local de votação rural mais próximo, nomeada pelo aglomerado do IBGE ou pelo endereço do TSE. Resultados eleitorais são atribuídos pelo local de votação, que pode diferir de onde o eleitor mora."),
    el("p", {}, "Fontes: ", ...m.proveniencia.flatMap((p, i) => [i ? " · " : "", el("a", { href: p.url, target: "_blank", rel: "noopener noreferrer" }, p.fonte)])),
  );
}

// ---------------------------------------------------------------- inicio
async function iniciar() {
  lerHash();
  estado.meta = await json("data/meta.json");
  const sm = $("f-mun");
  sm.replaceChildren(...estado.meta.municipios.map((m) => el("option", { value: m.cd }, m.nome)));
  sm.value = estado.cd;
  sm.addEventListener("change", () => { estado.cd = sm.value; estado.zona = null; estado.bid = null; carregarMun(); });
  $("f-zona").addEventListener("change", (e) => {
    estado.zona = e.target.value || null;
    estado.bid = null;
    preencherFiltros();
    desenharMapa();
    atualizar();
  });
  $("f-bairro").addEventListener("change", (e) => { estado.bid = e.target.value || null; atualizar(); });
  const sc = $("f-camada");
  sc.replaceChildren(...CAMADAS.map(([k, r]) => el("option", { value: k }, r)));
  sc.value = estado.camada;
  sc.addEventListener("change", () => { estado.camada = sc.value; desenharMapa(); atualizar(); });
  $("painel-lateral").addEventListener("click", (e) => {
    if (!e.target.closest(".link-voltar")) return;
    if (estado.bid) estado.bid = null; else { estado.zona = null; }
    preencherFiltros();
    desenharMapa();
    atualizar();
  });
  rodape();
  await carregarMun();
}

iniciar().catch((e) => {
  $("painel-lateral").replaceChildren(el("p", { class: "vazio" }, `Falha ao carregar os dados: ${e.message}`));
  console.error(e);
});
