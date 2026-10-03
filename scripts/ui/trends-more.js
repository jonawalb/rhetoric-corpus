// Rhetoric Trends: stance & salience heatmap, topics, echoes, coverage and method views (see trends.js).
// Shared verbatim by the private and public pages; keep the copies identical.
import { esc, fmt, fmtN, signed, timeChart, heatColor, heatLegend } from './trends-charts.js';

const LOW_N = 20;
const q95 = (xs) => { const s = xs.filter((v) => v != null).map(Math.abs).sort((a, b) => a - b); return s.length ? s[Math.floor(0.95 * (s.length - 1))] || s[s.length - 1] : 1; };
const sel = (id, label, opts, cur) => `<label class="pick"><span>${label}</span><select id="${id}">${opts.map(([v, n]) => `<option value="${esc(v)}"${String(v) === String(cur) ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></label>`;

// ---- Stance & salience heatmap -----------------------------------------------------------------------------
export async function heatmapView(ctx, host, ov) {
  const { S, L } = ctx;
  const hm = await ctx.get('heatmap');
  const measures = [...hm.metrics.stance.map((m) => [`stance:${m}`, `Stance: ${L.metric(m)}`]), ['salience:share', 'Salience: share of documents mentioning']];
  S.hm = measures.some(([v]) => v === S.hm) ? S.hm : 'stance:hostility';
  S.hd = ['level', 'z', 'change'].includes(S.hd) ? S.hd : 'level';
  S.hs = S.hs === 'official' ? 'official' : 'all';
  S.hl = S.hl === 'month' ? 'month' : 'country';
  S.c = hm.countries.includes(S.c) ? S.c : (hm.countries.includes('RU') ? 'RU' : hm.countries[0]);
  const months = hm.months;
  S.hmo = months.includes(S.hmo) ? S.hmo : months[months.length - 2] || months[months.length - 1];
  S.since = S.since || '2y';
  const [kind, metric] = S.hm.split(':');
  const cells = hm.cells[`${S.hm}:${S.hs}`] || {};
  const F = { level: 1, z: 3, change: 4 }[S.hd];
  host.innerHTML = `<div class="tr-controls">${sel('h-m', 'Measure', measures, S.hm)}
    ${sel('h-d', 'Show', [['level', 'Level'], ['change', 'Change vs baseline'], ['z', 'z vs baseline']], S.hd)}
    ${sel('h-s', 'Streams', [['all', 'All streams'], ['official', 'Official only']], S.hs)}
    ${sel('h-l', 'Layout', [['country', 'One country: targets × months'], ['month', 'One month: countries × targets']], S.hl)}
    ${S.hl === 'country' ? sel('h-c', 'Country', hm.countries.map((c) => [c, L.country(c)]), S.c) + sel('h-since', 'Show', [['all', 'All dates'], ['3y', 'Last 3 years'], ['2y', 'Last 2 years'], ['1y', 'Last year']], S.since)
      : sel('h-mo', 'Month', months.slice().reverse().map((m) => [m, m]), S.hmo)}</div>
    <p class="fine">${esc(hm.note)} Hatched cells rest on fewer than ${LOW_N} documents. Click a cell for ${ctx.cfg.evidence ? 'the sentences behind it' : 'its numbers and a search link'}.</p>
    <div class="card tr-card"><div id="h-legend"></div><div class="hgrid-wrap"><div class="hgrid" id="h-grid"></div></div></div><div class="evbox card" id="h-ev" hidden></div>`;
  host.querySelector('.tr-controls').addEventListener('change', () => {
    const v = (id) => host.querySelector(id)?.value;
    Object.assign(S, { hm: v('#h-m'), hd: v('#h-d'), hs: v('#h-s'), hl: v('#h-l') });
    if (v('#h-c')) S.c = v('#h-c');
    if (v('#h-since')) S.since = v('#h-since');
    if (v('#h-mo')) S.hmo = v('#h-mo');
    ctx.go('heatmap');
  });
  const div = S.hd !== 'level' || metric === 'esc_balance';
  let rows, cols, get;
  if (S.hl === 'country') {
    const cut = sinceCutM(S.since, months[months.length - 1]);
    const mi = months.map((m, i) => (m >= cut ? i : -1)).filter((i) => i >= 0);
    const tg = cells[S.c] || {};
    rows = Object.keys(tg).sort((a, b) => tg[b].reduce((s, r) => s + r[2], 0) - tg[a].reduce((s, r) => s + r[2], 0));
    cols = mi;
    const idx = Object.fromEntries(rows.map((t) => [t, new Map(tg[t].map((r) => [r[0], r]))]));
    get = (t, i) => idx[t].get(i);
  } else {
    const i0 = months.indexOf(S.hmo);
    rows = hm.countries.filter((c) => cells[c]);
    cols = hm.targets;
    get = (c, t) => (cells[c]?.[t] || []).find((r) => r[0] === i0);
  }
  const vals = [];
  for (const r of rows) for (const c of cols) { const x = get(r, c); if (x && x[F] != null) vals.push(x[F]); }
  const scale = S.hd === 'z' ? { div: true, max: 4 } : div ? { div: true, max: q95(vals) || 0.05 } : { min: 0, max: q95(vals) || 0.1 };
  host.querySelector('#h-legend').innerHTML = `<span class="fine">${esc(S.hd === 'level' ? L.metric(metric) : S.hd === 'z' ? 'z against the stream baselines (combined)' : 'change against the stream baselines')}:</span> ${heatLegend(scale, S.hd === 'z' ? (v) => v.toFixed(0) : (v) => v.toFixed(2))}`;
  const colLab = S.hl === 'country'
    ? cols.map((i) => (months[i].endsWith('-01') || i === cols[0] ? `<span class="hc">${months[i].slice(0, 4)}</span>` : '<span class="hc"></span>'))
    : cols.map((t) => `<span class="hc rot">${esc(L.target(t))}</span>`);
  const grid = host.querySelector('#h-grid');
  grid.style.gridTemplateColumns = `minmax(90px, 130px) repeat(${cols.length}, minmax(${S.hl === 'country' ? 7 : 26}px, 1fr))`;
  let h = `<span></span>${colLab.join('')}`;
  for (const r of rows) {
    h += `<span class="hr">${esc(S.hl === 'country' ? L.target(r) : L.country(r))}</span>`;
    for (const c of cols) {
      const x = get(r, c);
      if (!x) { h += '<span class="hx"></span>'; continue; }
      const [mi, v, n, z, dl] = x;
      const lab = `${S.hl === 'country' ? L.target(r) : L.country(r)} ${S.hl === 'country' ? months[mi] : L.target(c)}: level ${fmt(v)}, change ${signed(dl)}, z ${fmt(z, 1)}, ${fmtN(n)} docs`;
      h += `<button type="button" class="hx${n < LOW_N ? ' lown' : ''}" style="background:${heatColor(x[F], scale)}" data-r="${esc(r)}" data-c="${esc(c)}" title="${esc(lab)}" aria-label="${esc(lab)}"></button>`;
    }
  }
  grid.innerHTML = rows.length ? h : '<p class="fine">No cells for this selection.</p>';
  const ev = host.querySelector('#h-ev');
  grid.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-r]');
    if (!b) return;
    const r = b.dataset.r, c = S.hl === 'country' ? Number(b.dataset.c) : b.dataset.c;
    const x = get(r, c);
    const country = S.hl === 'country' ? S.c : r, target = S.hl === 'country' ? r : c, month = months[x[0]];
    const head = `<h3>${esc(L.country(country))} · ${esc(L.target(target))} · ${esc(month)}</h3><p class="num">${esc(kind === 'stance' ? `${L.metric(metric)} of sentences mentioning ${L.target(target)}` : `share of documents mentioning ${L.target(target)}`)}: ${fmt(x[1])} · change ${signed(x[4])} · z ${fmt(x[3], 2)} · ${fmtN(x[2])} docs${kind === 'stance' ? ' mentioning the target' : ''} · ${x[5]} stream${x[5] === 1 ? '' : 's'} (${esc(S.hs)})</p>${ctx.searchLink(country, month, L.target(target).replace(/"/g, ''))}`;
    ctx.showEvidence(ev, { kind, country, period: month, metric: kind === 'salience' ? 'share' : metric, target, scope: S.hs, dir: metric === 'esc_balance' && x[1] < 0 ? 'down' : 'up' }, head);
    ev.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  });
}

function sinceCutM(since, last) {
  const k = { '3y': 36, '2y': 24, '1y': 12 }[since];
  if (!k) return '';
  const [y, m] = last.split('-').map(Number);
  const d = new Date(Date.UTC(y, m - 1 - k, 1));
  return d.toISOString().slice(0, 7);
}

// ---- Topics -------------------------------------------------------------------------------------------------
export async function topicsView(ctx, host) {
  const { S, L } = ctx;
  const t = await ctx.get('topics');
  const topics = t.topics.slice().sort((a, b) => b.n_docs - a.n_docs);
  const cur = topics.find((x) => String(x.topic) === S.tp) || topics[0];
  S.tp = String(cur.topic);
  const countries = Object.keys(t.prevalence).filter((c) => t.prevalence[c].balanced_share[S.tp]);
  host.innerHTML = `<p class="fine">${esc(t.note)} Model ${esc(t.version)}, k = ${esc(t.k)}, ${fmtN(t.docs_assigned)} documents assigned; ${esc(t.multilingual_topics)} topics hold two or more languages.</p>
    <div class="tp-wrap"><ol class="tp-list">${topics.map((x) => `<li><button type="button" data-tp="${x.topic}" aria-pressed="${x.topic === cur.topic}"><b>${x.topic}</b> ${esc(x.label)} <span class="fine">${fmtN(x.n_docs)} docs</span></button></li>`).join('')}</ol>
    <div class="tp-detail"><div class="card tr-card"><h3>Topic ${cur.topic}: ${esc(cur.label)}</h3>
      <p class="fine">${fmtN(cur.n_docs)} documents. Languages: ${Object.entries(cur.langs || {}).map(([k, v]) => `${esc(k)} ${fmtN(v)}`).join(', ')}. Countries: ${Object.entries(cur.countries || {}).map(([k, v]) => `${esc(k)} ${fmtN(v)}`).join(', ')}.</p>
      <div class="tablewrap"><table><thead><tr><th>Language</th><th>Top terms (c-TF-IDF)</th></tr></thead><tbody>${Object.entries(cur.terms || {}).map(([lg, ws]) => `<tr><td>${esc(lg)}</td><td>${esc(ws.join(', '))}</td></tr>`).join('')}</tbody></table></div>
      <h4>Exemplar documents</h4><ol class="ev">${(cur.exemplars || []).map((e) => `<li><div class="m"><b>${esc(e.date)}</b><span>${esc(L.country(e.country))} · ${esc(e.source)} · ${esc(e.lang)}</span></div><p class="mt"><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title || e.url)}</a></p></li>`).join('')}</ol></div>
    <div class="card tr-card"><h3>Balanced share by country and month</h3><p class="fine">Fixed-weight mean of within-stream shares. Hover for document counts.</p><div id="tp-legend"></div><div class="hgrid-wrap"><div class="hgrid" id="tp-grid"></div></div></div></div></div>`;
  host.querySelector('.tp-list').addEventListener('click', (e) => { const b = e.target.closest('[data-tp]'); if (b) { S.tp = b.dataset.tp; ctx.go('topics'); } });
  const months = [...new Set(countries.flatMap((c) => t.prevalence[c].months))].sort().filter((m) => m >= '2021-01');
  const vals = countries.flatMap((c) => t.prevalence[c].balanced_share[S.tp]);
  const scale = { min: 0, max: q95(vals) || 0.05 };
  host.querySelector('#tp-legend').innerHTML = heatLegend(scale);
  const grid = host.querySelector('#tp-grid');
  grid.style.gridTemplateColumns = `minmax(90px, 130px) repeat(${months.length}, minmax(7px, 1fr))`;
  let h = `<span></span>${months.map((m, i) => `<span class="hc">${m.endsWith('-01') || !i ? m.slice(0, 4) : ''}</span>`).join('')}`;
  for (const c of countries) {
    const p = t.prevalence[c], mi = new Map(p.months.map((m, i) => [m, i]));
    h += `<span class="hr">${esc(L.country(c))}</span>`;
    for (const m of months) {
      const i = mi.get(m);
      if (i == null) { h += '<span class="hx"></span>'; continue; }
      const v = p.balanced_share[S.tp][i], n = p.n_docs[S.tp][i];
      h += `<span class="hx${p.country_docs[i] < LOW_N ? ' lown' : ''}" style="background:${heatColor(v, scale)}" title="${esc(`${L.country(c)} ${m}: balanced share ${fmt(v)}; ${n} of ${p.country_docs[i]} documents`)}"></span>`;
    }
  }
  grid.innerHTML = countries.length ? h : '<p class="fine">No prevalence data.</p>';
}

// ---- Echoes -------------------------------------------------------------------------------------------------
export async function echoesView(ctx, host) {
  const { S, L } = ctx;
  const e = await ctx.get('echoes');
  const types = ['official-official', 'mixed', 'media-media'];
  const on = S.et ? S.et.split(',') : ['official-official', 'mixed'];
  const ccs = [...new Set(e.clusters.flatMap((c) => c.countries_in_order))].sort();
  const pairs = Object.entries(e.meta.pairs_by_country || {}).slice(0, 12);
  host.innerHTML = `<p class="fine">${esc(e.note)} Threshold cosine ≥ ${esc(e.meta.threshold)}, window ±${esc(e.meta.window_days)} days; ${fmtN(e.meta.clusters)} clusters found, ${fmtN(e.meta.clusters_written)} kept (largest first).</p>
    <div class="tr-controls"><div class="seg">${types.map((t) => `<label class="chk"><input type="checkbox" data-et="${t}"${on.includes(t) ? ' checked' : ''}> ${t}</label>`).join('')}</div>
      ${sel('e-c', 'Country', [['', 'Any'], ...ccs.map((c) => [c, L.country(c)])], S.ec || '')}${sel('e-o', 'First seen', [['', 'Any'], ...ccs.map((c) => [c, L.country(c)])], S.eo || '')}</div>
    <details class="fine"><summary>Passage pairs by country order (A dated no later than B)</summary><p>${pairs.map(([k, v]) => `${esc(k.replace('>', ' → '))}: ${fmtN(v)}`).join(' · ')}</p></details>
    <p class="count" id="e-count"></p><ol class="clusters" id="e-list"></ol><button type="button" class="btn" id="e-more" hidden>Show more</button>`;
  let rows = [], shown = 0;
  const render = () => {
    const ts = [...host.querySelectorAll('[data-et]')].filter((c) => c.checked).map((c) => c.dataset.et);
    S.et = ts.join(','); S.ec = host.querySelector('#e-c').value; S.eo = host.querySelector('#e-o').value; ctx.save();
    rows = e.clusters.filter((c) => ts.includes(c.type) && (!S.ec || c.countries_in_order.includes(S.ec)) && (!S.eo || c.first_seen.country === S.eo));
    shown = 0; host.querySelector('#e-list').innerHTML = '';
    host.querySelector('#e-count').textContent = `${fmtN(rows.length)} cluster${rows.length === 1 ? '' : 's'}`;
    more();
  };
  const more = () => {
    host.querySelector('#e-list').insertAdjacentHTML('beforeend', rows.slice(shown, shown + 25).map((c) => clusterCard(ctx, c)).join(''));
    shown += 25; host.querySelector('#e-more').hidden = shown >= rows.length;
  };
  host.querySelector('.tr-controls').addEventListener('change', render);
  host.querySelector('#e-more').addEventListener('click', more);
  render();
}

function clusterCard(ctx, c) {
  const { L } = ctx;
  const d0 = Date.parse(c.members[0].date), d1 = Math.max(d0 + 864e5, Date.parse(c.last_date));
  const rowsC = c.countries_in_order;
  const W = 300, H = 14 * rowsC.length + 16;
  const dots = c.members.map((m) => `<circle cx="${50 + (W - 60) * (Date.parse(m.date) - d0) / (d1 - d0)}" cy="${10 + 14 * rowsC.indexOf(m.country)}" r="4" class="${m.outlet === 'official' ? 'off' : 'med'}"><title>${esc(`${m.date} ${m.country} ${m.source} (${m.outlet})`)}</title></circle>`).join('');
  const labels = rowsC.map((cc, i) => `<text class="tsm-axis" x="0" y="${14 + 14 * i}">${esc(cc)}</text>`).join('');
  const svg = `<svg class="etl" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Member dates by country">${labels}${dots}<text class="tsm-axis" x="50" y="${H - 1}">${esc(c.members[0].date)}</text><text class="tsm-axis" x="${W - 10}" y="${H - 1}" text-anchor="end">${esc(c.last_date)}</text></svg>`;
  return `<li class="cluster card"><p class="hd"><b>${rowsC.map((x) => esc(L.country(x))).join(' → ')}</b> <span class="pill">${esc(c.type)}</span> ${fmtN(c.n_members)} passages, first seen ${esc(L.country(c.first_seen.country))} (${esc(c.first_seen.source)}) ${esc(c.first_seen.date)}, span ${c.span_days} days. <span class="fine">Official members from: ${esc((c.official_countries || []).join(', ') || 'none')}.</span></p>
    ${svg}<p class="fine">Filled dots: official outlets; open dots: media.</p><details><summary>Passages (${c.members.length}${c.n_members > c.members.length ? ` of ${c.n_members}` : ''})</summary>${ctx.evidenceList(c.members)}</details></li>`;
}

// ---- Coverage -----------------------------------------------------------------------------------------------
export async function coverageView(ctx, host, ov) {
  const { S, L } = ctx;
  const cov = ov.coverage;
  const ccs = Object.keys(cov.by_country).sort();
  S.c = ccs.includes(S.c) ? S.c : (ccs.includes('RU') ? 'RU' : ccs[0]);
  const T = ov.totals || {};
  host.innerHTML = `<p class="fine">${fmtN(T.docs)} documents, ${fmtN(T.passages)} passages embedded, ${fmtN(T.sentences_scored)} sentences tone-scored, ${fmtN(T.mentions)} target mentions. Generated ${esc(ov.generated)}.</p>
    <div class="tablewrap"><table><thead><tr><th>Country</th><th class="num">Documents</th><th class="num">Embedded</th><th class="num">Scored</th><th>First</th><th>Last</th></tr></thead><tbody>
    ${Object.entries(ov.countries).map(([c, r]) => `<tr><td>${esc(L.country(c))}</td><td class="num">${fmtN(r.docs)}</td><td class="num">${fmtN(r.embedded)}</td><td class="num">${fmtN(r.scored)}</td><td>${esc(r.first)}</td><td>${esc(r.last)}</td></tr>`).join('')}</tbody></table></div>
    <h3>Known coverage notes</h3><ul class="src">${ov.known_notes.map((n) => `<li><b>${esc(L.country(n.country))}${n.period ? ` · ${esc(n.period)}` : ''}</b>: ${esc(n.note)}</li>`).join('')}</ul>
    <div class="tr-controls">${sel('v-c', 'Country', ccs.map((c) => [c, L.country(c)]), S.c)}</div>
    <div class="card tr-card"><h3>Documents per month entering the series: ${esc(L.country(S.c))}</h3><p class="fine">${esc(cov.note)}</p><div class="chart" id="v-chart"></div>
      <div class="tablewrap"><table><thead><tr><th>Stream</th><th>Outlet</th><th class="num">Docs</th><th>First</th><th>Last</th><th class="num">Weight</th></tr></thead><tbody id="v-streams"></tbody></table></div></div>
    <h3>Flags: ${esc(L.country(S.c))}</h3><ul class="src" id="v-flags"></ul>`;
  host.querySelector('#v-c').addEventListener('change', (e) => { S.c = e.target.value; ctx.go('coverage'); });
  const m = cov.by_country[S.c];
  const keep = cov.months.map((p, i) => (m.total[i] ? i : -1)).filter((i) => i >= 0);
  const first = keep[0] ?? 0;
  const P = cov.months.slice(first), tot = m.total.slice(first);
  const flags = cov.flags.filter((f) => f.country === S.c);
  timeChart(host.querySelector('#v-chart'), { periods: P, height: 170, aria: 'documents per month', series: [{ y: tot, color: 'var(--c7)' }],
    markers: flags.map((f) => ({ period: f.period, label: f.detail })), yFmt: (v) => fmtN(v),
    tip: (i) => `<b>${esc(P[i])}${P[i] === cov.partial_month ? ' (partial)' : ''}</b><span class="tt-d">${fmtN(tot[i])} documents<br>${Object.entries(m.streams).filter(([, a]) => a[first + i]).map(([s, a]) => `${esc(s)}: ${fmtN(a[first + i])}`).join('<br>')}</span>` });
  host.querySelector('#v-streams').innerHTML = ov.streams.filter((s) => s.country === S.c).map((s) => `<tr><td><code>${esc(s.stream)}</code></td><td>${esc(L.outlet(s.outlet))}</td><td class="num">${fmtN(s.n_docs)}</td><td>${esc(s.first)}</td><td>${esc(s.last)}</td><td class="num">${fmt(s.weight, 1)}</td></tr>`).join('');
  host.querySelector('#v-flags').innerHTML = flags.length ? flags.map((f) => `<li><b>${esc(f.period)}</b> ${esc(f.kind.replace('_', ' '))}: ${esc(f.detail)}</li>`).join('') : '<li>No coverage flags.</li>';
}

// ---- Method & validation ----------------------------------------------------------------------------------------
export async function methodView(ctx, host, ov) {
  const v = ov.validation || {};
  const dims = Object.entries(v.dims || {});
  const langs = [...new Set(dims.flatMap(([, d]) => Object.keys(d.by_lang || {})))].sort();
  const am = ov.alerts_meta || {};
  const human = v.human && Object.keys(v.human).length;
  host.innerHTML = `<div class="callout"><b>Validation status.</b> ${human ? 'A human-coded sample has been compared (table below), in addition to' : 'Tone is validated <b>only against an AI teacher model</b>, not yet against human coders. The numbers below measure'} how well the fast classifier reproduces the zero-shot teacher (${esc(ov.versions?.nli_teacher)}) on ${fmtN(v.n_heldout)} held-out sentences from documents not used in training. ${esc(v.note || '')}</div>
    ${ov.method.map((s) => `<section class="tr-card"><h3>${esc(s.h)}</h3>${s.paragraphs.map((p) => `<p>${esc(p)}</p>`).join('')}</section>`).join('')}
    <h3>Teacher–student agreement (held-out)</h3>
    <div class="tablewrap"><table><thead><tr><th>Dimension</th><th class="num">n</th><th class="num">Teacher pos.</th><th class="num">Student pos.</th><th class="num">AUC</th><th class="num">Pearson r</th><th class="num">Kappa</th><th class="num">F1</th><th class="num">Random-arm r</th><th class="num">Random-arm AUC</th></tr></thead><tbody>
    ${dims.map(([k, d]) => { const o = d.overall, r = d.random_arm; return `<tr><td>${esc(ctx.L.metric(k))}</td><td class="num">${fmtN(o.n)}</td><td class="num">${fmt(o.teacher_pos)}</td><td class="num">${fmt(o.student_pos)}</td><td class="num">${fmt(o.auc)}</td><td class="num">${fmt(o.pearson)}</td><td class="num">${fmt(o.kappa)}</td><td class="num">${fmt(o.f1)}</td><td class="num">${fmt(r.pearson)}</td><td class="num">${fmt(r.auc)}</td></tr>`; }).join('')}</tbody></table></div>
    <p class="fine">Binary metrics at p = 0.5 for both teacher and student; document tone uses the mean probability, so AUC and Pearson r matter more than kappa for rare dimensions.</p>
    <h3>By language (Pearson r · n)</h3>
    <div class="tablewrap"><table><thead><tr><th>Dimension</th>${langs.map((l) => `<th class="num">${esc(l)}</th>`).join('')}</tr></thead><tbody>
    ${dims.map(([k, d]) => `<tr><td>${esc(ctx.L.metric(k))}</td>${langs.map((l) => { const x = d.by_lang?.[l]; return `<td class="num${x && x.n < 50 ? ' lown-t' : ''}">${x ? `${fmt(x.pearson, 2)} · ${fmtN(x.n)}` : '–'}</td>`; }).join('')}</tr>`).join('')}</tbody></table></div>
    <p class="fine">Greyed: fewer than 50 held-out sentences; indicative only.</p>
    ${human ? `<h3>Human coding</h3><pre class="fine">${esc(JSON.stringify(v.human, null, 1))}</pre>` : ''}
    <h3>Alert screen</h3><p class="fine">${fmtN(am.n_tests)} period tests; thresholds ${esc(JSON.stringify(am.z_threshold))}, minimum change ${esc(am.min_effect)}. ${esc(am.expected_note || '')}</p>
    <div class="tablewrap"><table><thead><tr><th>Threshold</th><th class="num">Observed periods</th><th class="num">Expected if N(0,1)</th><th class="num">Ratio</th></tr></thead><tbody>
    ${Object.entries(am.tail_check || {}).map(([k, x]) => `<tr><td>${esc(k)}</td><td class="num">${fmtN(x.observed_periods)}</td><td class="num">${fmt(x.expected_if_normal, 1)}</td><td class="num">${fmt(x.observed_periods / x.expected_if_normal, 1)}×</td></tr>`).join('')}</tbody></table></div>
    <h3>Versions</h3><p class="fine num">${Object.entries(ov.versions || {}).map(([k, x]) => `${esc(k)}: ${esc(typeof x === 'object' ? JSON.stringify(x) : x)}`).join(' · ')}</p>`;
}

