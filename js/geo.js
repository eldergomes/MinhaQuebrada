// Projecao e paths SVG sem dependencias (derivado do painel Territorios em Disputa).
// Ids sao strings (codigo do bairro IBGE ou "<municipio>-<slug>" para bairros TSE).

/** Projecao equiretangular corrigida por cos(latitude media), adequada a recortes
 *  municipais. `altura = null` ajusta a altura a proporcao do recorte (45%–110% da largura).
 *  Retorna os paths e `ponto([lon, lat]) -> [x, y]` para sobrepor marcadores. */
export function projetar(fc, largura = 800, altura = null, margem = 10) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const anel = (r) => r.forEach(([x, y]) => {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y;
  });
  const poligonos = (g) => (g.type === "Polygon" ? [g.coordinates]
    : g.type === "MultiPolygon" ? g.coordinates
    : (g.geometries ?? []).flatMap(poligonos));
  fc.features.forEach((f) => poligonos(f.geometry).forEach((p) => p.forEach(anel)));
  const kx = Math.cos(((y0 + y1) / 2) * Math.PI / 180);
  const w = Math.max((x1 - x0) * kx, 1e-9), h = Math.max(y1 - y0, 1e-9);
  if (altura == null) {
    altura = Math.round(Math.min(1.1, Math.max(0.45, h / w)) * (largura - 2 * margem) + 2 * margem);
  }
  const s = Math.min((largura - 2 * margem) / w, (altura - 2 * margem) / h);
  const ox = (largura - w * s) / 2, oy = (altura - h * s) / 2;
  const px = (x) => ox + (x - x0) * kx * s;
  const py = (y) => oy + (y1 - y) * s;
  const paths = fc.features.map((f) => ({
    id: String(f.id),
    props: f.properties ?? {},
    d: poligonos(f.geometry)
      .map((p) => p.map((r) => "M" + r.map(([x, y]) => px(x).toFixed(1) + "," + py(y).toFixed(1)).join("L") + "Z").join(""))
      .join(""),
  }));
  const ponto = ([x, y]) => [px(x), py(y)];
  const dentro = ([x, y]) => x >= x0 && x <= x1 && y >= y0 && y <= y1;
  return { paths, largura, altura, ponto, dentro };
}
