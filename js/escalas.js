// Escalas de cor (paleta de referencia validada: azul sequencial; divergente
// vermelho<->azul com ponto medio cinza). Compartilhado com o painel estatico.

export const SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"];
// polo A (PT) = vermelho; polo B = azul; meio = cinza neutro (sem matiz). Cada braco
// foi validado como rampa ordinal (claro e escuro) com scripts/validate_palette.js.
// Cores sao aplicadas via style (fill), entao var(--x) funciona e segue o tema.
export const DIV = ["#9e2323", "#c53a39", "#eb8a89", "var(--neutro)", "#86b6ef", "#2a78d6", "#184f95"];
export const SEM_DADO = "var(--sem-dado)";

const fmtN = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 });
export const fmt = (v, suf = "") => (v == null || Number.isNaN(v) ? "—" : fmtN.format(v) + suf);

/** Descreve como colorir uma camada. Retorna {cor(v), legenda:[{cor, rotulo}]}. */
export function escala(camada, valores) {
  if (camada.startsWith("share_a:")) return divergente([-Infinity, 30, 40, 48, 52, 60, 70, Infinity], "%");
  if (camada.startsWith("desvio:")) return divergente([-Infinity, -20, -10, -3, 3, 10, 20, Infinity], " p.p.");
  // faixas fixas: cores comparaveis entre UFs
  if (camada === "score") return sequencialFixo([0, 30, 40, 50, 60, 70, 80, 100.01], "");
  if (camada === "taxa_acerto") return sequencialFixo([0, 0.2, 0.4, 0.6, 0.8, 1, 1.01], "");
  // sequencial por quantis (robusto a distribuicoes assimetricas como PIB e populacao)
  const vs = valores.filter((v) => v != null).sort((a, b) => a - b);
  if (!vs.length) return { cor: () => SEM_DADO, legenda: [] };
  const n = SEQ.length;
  // cortes repetidos (muitos valores iguais) sao removidos para nao gerar classes vazias
  const cortes = [...new Set(Array.from({ length: n - 1 }, (_, i) => vs[Math.floor(((i + 1) * vs.length) / n)]))]
    .filter((c) => c > vs[0]);
  return sequencialFixo([vs[0], ...cortes, vs[vs.length - 1] + 1e-9], "");
}

function sequencialFixo(lim, suf) {
  const k = lim.length - 1; // numero de classes; cores espalhadas pela rampa
  const cores = Array.from({ length: k }, (_, i) => SEQ[k === 1 ? SEQ.length - 1 : Math.round((i * (SEQ.length - 1)) / (k - 1))]);
  const cor = (v) => {
    if (v == null) return SEM_DADO;
    let i = 0;
    while (i < k - 1 && v >= lim[i + 1]) i++;
    return cores[i];
  };
  const legenda = cores.map((c, i) => ({ cor: c, rotulo: `${fmt(lim[i])}${suf} – ${fmt(Math.min(lim[i + 1], lim[k]))}${suf}` }));
  return { cor, legenda };
}

function divergente(lim, suf) {
  // lim tem 8 bordas -> 7 classes. Para share_a valores altos = A (vermelho), entao invertemos.
  const cores = [...DIV].reverse();
  const cor = (v) => {
    if (v == null) return SEM_DADO;
    for (let i = 0; i < 7; i++) if (v < lim[i + 1]) return cores[i];
    return cores[6];
  };
  const rot = (i) => (i === 0 ? `< ${fmt(lim[1])}${suf}` : i === 6 ? `≥ ${fmt(lim[6])}${suf}`
    : `${fmt(lim[i])} – ${fmt(lim[i + 1])}${suf}`);
  return { cor, legenda: cores.map((c, i) => ({ cor: c, rotulo: rot(i) })) };
}
