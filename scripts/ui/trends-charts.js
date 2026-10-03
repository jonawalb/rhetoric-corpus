// Rhetoric Trends: small SVG chart helpers (no dependencies). Shared verbatim by the private UI
// (rhetoric-corpus scripts/ui/) and the public page (tsm-strait-layers tools/rhetoric-search/js/); keep the copies identical.
// Colours come from CSS tokens (--c1 …, --rule, --muted), so light and dark themes follow the page.

export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const fmt = (x, d = 3) => (x == null || Number.isNaN(x) ? '–' : Number(x).toFixed(d));
export const fmtN = (n) => (n == null ? '–' : Number(n).toLocaleString('en-US'));
export const signed = (x, d = 3) => (x == null ? '–' : (x > 0 ? '+' : '') + Number(x).toFixed(d));
// Categorical order checked with the dataviz palette validator (light mode: all checks pass).
export const SERIES = ['var(--c1)', 'var(--c2)', 'var(--c6)', 'var(--c5)'];

/** Period string -> ms. Weeks are Monday dates (YYYY-MM-DD), months YYYY-MM. */
export function pTime(p) {
  return Date.parse(p.length === 7 ? `${p}-01T00:00:00Z` : `${p}T00:00:00Z`);
}
/** [from, to] dates of a period. */
export function pRange(p) {
  if (p.length === 7) {
    const [y, m] = p.split('-').map(Number);
    return [`${p}-01`, new Date(Date.UTC(y, m, 0)).toISOString().slice(0, 10)];
  }
  const d = new Date(`${p}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + 6);
  return [p, d.toISOString().slice(0, 10)];
}

function niceTicks(lo, hi, n = 4) {
  if (!(hi > lo)) return [lo];
  const step0 = (hi - lo) / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => s >= step0) || step0;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-12; v += step) out.push(+v.toFixed(10));
  return out;
}

function timeTicks(t0, t1, width) {
  const years = new Date(t1).getUTCFullYear() - new Date(t0).getUTCFullYear();
  const out = [];
  if (years >= 2) {
    for (let y = new Date(t0).getUTCFullYear() + 1; y <= new Date(t1).getUTCFullYear(); y++) out.push([Date.UTC(y, 0, 1), String(y)]);
    const every = Math.ceil(out.length / Math.max(2, Math.floor(width / 60)));
    return out.filter((_, i) => i % every === 0);
  }
  const d = new Date(t0);
  d.setUTCDate(1);
  while (d.getTime() <= t1) {
    if (d.getTime() >= t0) out.push([d.getTime(), d.toISOString().slice(0, 7)]);
    d.setUTCMonth(d.getUTCMonth() + 1);
  }
  const every = Math.ceil(out.length / Math.max(2, Math.floor(width / 70)));
  return out.filter((_, i) => i % every === 0);
}

/**
 * Time-series chart. opts: periods, series [{y, lo?, hi?, color?, dash?, label?}], bars? {y, label} (drawn as
 * columns under the lines, own scale shown on the right only as a max label), refLines [{y, label}], markers
 * [{period, label}], height, yFmt, tip(i) -> html, onPick(i), yDomain [lo, hi].
 * Gaps longer than 1.6 periods break the lines (no documents = no value).
 */
export function timeChart(host, o) {
  const W = Math.max(280, host.clientWidth || 640), H = o.height || 190, L = 46, R = 10, T = 10, B = 22;
  const P = o.periods;
  host.innerHTML = '';
  if (!P.length) { host.innerHTML = '<p class="fine">No data for this selection.</p>'; return; }
  const ts = P.map(pTime);
  const t0 = ts[0], t1 = ts[ts.length - 1] + (P[0].length === 7 ? 28 : 6) * 864e5;
  const x = (t) => L + (W - L - R) * (t - t0) / Math.max(1, t1 - t0);
  const vals = [];
  // Domain from the lines (and bands unless o.bandsClip: then bands are clipped to the plot instead of stretching it).
  for (const s of o.series) for (let i = 0; i < P.length; i++) for (const v of o.bandsClip ? [s.y[i]] : [s.y[i], s.lo?.[i], s.hi?.[i]]) if (v != null) vals.push(v);
  for (const r of o.refLines || []) vals.push(r.y);
  let [lo, hi] = o.yDomain || [Math.min(...vals, 0), Math.max(...vals, 0)];
  if (!(hi > lo)) { hi = lo + 1; }
  const pad = (hi - lo) * 0.06; lo -= o.yDomain ? 0 : pad; hi += pad;
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo));
  const step = ts.length > 1 ? Math.min(...ts.slice(1).map((t, i) => t - ts[i])) : 7 * 864e5;
  const bw = Math.min(24, Math.max(1, (x(t0 + step) - x(t0)) - 1));
  const cid = `clip${Math.random().toString(36).slice(2, 8)}`;
  let s = `<defs><clipPath id="${cid}"><rect x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/></clipPath></defs>`;
  for (const t of niceTicks(lo, hi)) s += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(t)}" y2="${y(t)}"/><text class="tsm-axis" x="${L - 5}" y="${y(t) + 3.5}" text-anchor="end">${(o.yFmt || ((v) => v))(t)}</text>`;
  for (const [t, lab] of timeTicks(t0, t1, W)) s += `<text class="tsm-axis" x="${x(t)}" y="${H - 6}" text-anchor="middle">${lab}</text>`;
  if (o.bars) {
    const bmax = Math.max(1, ...o.bars.y.filter((v) => v != null));
    const yb = (v) => H - B - (H - T - B) * 0.35 * v / bmax;
    o.bars.y.forEach((v, i) => { if (v) s += `<rect class="nbar" x="${x(ts[i])}" y="${yb(v)}" width="${bw}" height="${H - B - yb(v)}"/>`; });
    s += `<text class="tsm-axis" x="${W - R}" y="${H - B - (H - T - B) * 0.35 - 3}" text-anchor="end">${esc(o.bars.label || 'docs')} max ${fmtN(bmax)}</text>`;
  }
  for (const r of o.refLines || []) s += `<line class="ref" x1="${L}" x2="${W - R}" y1="${y(r.y)}" y2="${y(r.y)}"/>${r.label ? `<text class="tsm-axis" x="${W - R - 2}" y="${y(r.y) - 3}" text-anchor="end">${esc(r.label)}</text>` : ''}`;
  for (const m of o.markers || []) {
    const t = pTime(m.period);
    if (t < t0 || t > t1) continue;
    s += `<g class="flag"><line x1="${x(t)}" x2="${x(t)}" y1="${T}" y2="${H - B}"/><path d="M${x(t)},${T} l5,3 l-5,3z"/><title>${esc(m.label)}</title></g>`;
  }
  const segs = (arr) => {
    const out = []; let cur = [];
    arr.forEach((v, i) => {
      if (v == null || (i && ts[i] - ts[i - 1] > step * 1.6)) { if (cur.length) out.push(cur); cur = []; }
      if (v != null) cur.push(i);
    });
    if (cur.length) out.push(cur);
    return out;
  };
  o.series.forEach((sr, k) => {
    const col = sr.color || SERIES[k % SERIES.length];
    if (sr.lo && sr.hi) {
      for (const seg of segs(sr.y.map((v, i) => (v == null || sr.lo[i] == null ? null : v)))) {
        const up = seg.map((i) => `${x(ts[i])},${y(sr.hi[i])}`), dn = seg.slice().reverse().map((i) => `${x(ts[i])},${y(sr.lo[i])}`);
        s += `<polygon class="band" clip-path="url(#${cid})" style="fill:${col}" points="${up.concat(dn).join(' ')}"/>`;
      }
    }
    for (const seg of segs(sr.y)) {
      if (seg.length === 1) { s += `<circle cx="${x(ts[seg[0]])}" cy="${y(sr.y[seg[0]])}" r="2.5" style="fill:${col}"/>`; continue; }
      s += `<polyline class="ln${sr.dash ? ' dash' : ''}" style="stroke:${col}" points="${seg.map((i) => `${x(ts[i])},${y(sr.y[i])}`).join(' ')}"/>`;
    }
    for (const i of sr.dots || []) if (sr.y[i] != null) s += `<circle class="hot" cx="${x(ts[i])}" cy="${y(sr.y[i])}" r="4" style="stroke:${col}"/>`;
    if (sr.label && o.series.length > 1) {
      const last = sr.y.map((v, i) => [v, i]).filter(([v]) => v != null).pop();
      if (last) s += `<text class="dlab" x="${Math.min(W - R - 2, x(ts[last[1]]) + 4)}" y="${y(last[0]) - 4}" text-anchor="end" style="fill:var(--ink)">${esc(sr.label)}</text>`;
    }
  });
  s += `<line class="xhair" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`;
  s += `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}" fill="transparent"/>`;
  host.insertAdjacentHTML('beforeend', `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="${esc(o.aria || 'time series')}">${s}</svg><div class="tooltip" hidden></div>`);
  const svg = host.querySelector('svg'), tip = host.querySelector('.tooltip'), xh = svg.querySelector('.xhair');
  const near = (ev) => {
    const r = svg.getBoundingClientRect();
    const px = (ev.clientX - r.left) * W / r.width;
    let best = 0, bd = Infinity;
    ts.forEach((t, i) => { const d = Math.abs(x(t) - px); if (d < bd) { bd = d; best = i; } });
    return best;
  };
  const hit = svg.querySelector('.hit');
  hit.addEventListener('mousemove', (ev) => {
    const i = near(ev);
    xh.setAttribute('x1', x(ts[i])); xh.setAttribute('x2', x(ts[i])); xh.setAttribute('visibility', 'visible');
    if (o.tip) {
      tip.innerHTML = o.tip(i) + (o.onPick ? '<small>Click for the evidence.</small>' : '');
      tip.hidden = false;
      const r = host.getBoundingClientRect();
      const left = Math.min(ev.clientX - r.left + 12, r.width - 290);
      tip.style.left = `${Math.max(0, left)}px`; tip.style.top = `${Math.max(0, ev.clientY - r.top - 10)}px`;
    }
  });
  hit.addEventListener('mouseleave', () => { tip.hidden = true; xh.setAttribute('visibility', 'hidden'); });
  if (o.onPick) { hit.style.cursor = 'pointer'; hit.addEventListener('click', (ev) => o.onPick(near(ev))); }
}

/** Columns of z-scores with threshold lines (|z| = thr). opts: periods, z, thr, tip, onPick. */
export function zChart(host, o) {
  const pos = o.z.map((v) => (v == null ? null : v));
  timeChart(host, {
    periods: o.periods, height: o.height || 110, yFmt: (v) => v.toFixed(0), aria: 'z-scores against the baseline',
    yDomain: [Math.min(-o.thr - 0.5, ...pos.filter((v) => v != null)), Math.max(o.thr + 0.5, ...pos.filter((v) => v != null))],
    series: [{ y: pos, color: 'var(--muted)', dots: pos.map((v, i) => (v != null && Math.abs(v) >= o.thr ? i : -1)).filter((i) => i >= 0) }],
    refLines: [{ y: o.thr, label: `z = ${o.thr}` }, { y: 0 }, { y: -o.thr, label: `z = −${o.thr}` }], tip: o.tip, onPick: o.onPick,
  });
}

/** Colour for a heat cell: sequential (one hue) or diverging (blue below 0, red above, neutral at 0). */
export function heatColor(v, scale) {
  if (v == null) return 'transparent';
  if (scale.div) {
    const k = Math.min(1, Math.abs(v) / scale.max);
    return `color-mix(in oklab, ${v >= 0 ? 'var(--red)' : 'var(--blue)'} ${Math.round(8 + 82 * k)}%, var(--panel))`;
  }
  const k = Math.min(1, Math.max(0, (v - (scale.min || 0)) / ((scale.max - (scale.min || 0)) || 1)));
  return `color-mix(in oklab, var(--c7) ${Math.round(6 + 88 * k)}%, var(--panel))`;
}

/** Legend strip for heatColor. */
export function heatLegend(scale, f = (v) => v.toFixed(2)) {
  const stops = scale.div ? [-scale.max, -scale.max / 2, 0, scale.max / 2, scale.max] : [scale.min || 0, (scale.min + scale.max) / 2, scale.max];
  return `<span class="hlegend">${stops.map((v) => `<span><i style="background:${heatColor(v, scale)}"></i>${f(v)}</span>`).join('')}</span>`;
}
