// Rhetoric Trends: analyst views over the semantic-layer aggregates (alerts, country tone, stance/salience heatmap,
// topics, echoes, coverage, method). Shared verbatim by the private UI (rhetoric-corpus scripts/ui/, data from
// /api/view/*, live evidence from /api/evidence) and the public page (tsm-strait-layers tools/rhetoric-search/js/,
// data from data/trends/*.json.gz, aggregates only). Keep the copies identical.
//
// mount(root, cfg): cfg = { mode: 'private'|'public', get(name) -> Promise<json>, evidence?(params) -> Promise<{docs,note,evidence}>,
//                           searchUrl?({country, from, to, q}) -> url }
import { esc, fmt, fmtN, signed, pRange, timeChart, zChart } from './trends-charts.js';
import { heatmapView, topicsView, echoesView, coverageView, methodView } from './trends-more.js';

const COUNTRY = { RU: 'Russia', CN: 'China', IR: 'Iran', KP: 'North Korea', BY: 'Belarus', US: 'United States', PK: 'Pakistan',
  IN: 'India', TR: 'Türkiye', SY: 'Syria', VE: 'Venezuela', CU: 'Cuba', TW: 'Taiwan' };
const METRIC = { hostility: 'Hostility / confrontation', threat: 'Threat & coercive signalling', conciliation: 'Conciliation / cooperation',
  grievance: 'Grievance / victimhood', escalation: 'Escalation framing', deescalation: 'De-escalation framing',
  esc_balance: 'Escalation minus de-escalation', share: 'Share of documents' };
const TARGET = { US: 'United States', NATO: 'NATO', EU: 'European Union', UK: 'United Kingdom', JAPAN: 'Japan', ROK: 'South Korea',
  TAIWAN: 'Taiwan', PHILIPPINES: 'Philippines', UKRAINE: 'Ukraine', ISRAEL: 'Israel', CHINA: 'China', RUSSIA: 'Russia', IRAN: 'Iran',
  INDIA: 'India', PAKISTAN: 'Pakistan', DPRK: 'North Korea', AUSTRALIA: 'Australia', WEST: '"the West"' };
const OUTLET = { official: 'official', state_media: 'state media', media: 'media' };
const TABS = [['alerts', 'Alerts'], ['country', 'Country tone'], ['heatmap', 'Stance & salience'], ['topics', 'Topics'],
  ['echoes', 'Echoes'], ['coverage', 'Coverage'], ['method', 'Method & validation']];

export const L = {
  country: (c) => COUNTRY[c] || c, metric: (m) => METRIC[m] || m, target: (t) => TARGET[t] || t,
  outlet: (o) => OUTLET[o] || o || '', COUNTRY, METRIC, TARGET,
};

export function mount(root, cfg) {
  const cache = new Map();
  const S = Object.fromEntries(new URLSearchParams(location.hash.slice(1)));
  const ctx = {
    cfg, S, L, root,
    get: (name) => { if (!cache.has(name)) cache.set(name, cfg.get(name)); return cache.get(name); },
    save() { const p = new URLSearchParams(Object.entries(S).filter(([, v]) => v !== '' && v != null)); history.replaceState(null, '', '#' + p); },
    evidenceList, showEvidence: (box, params, fallback) => showEvidence(ctx, box, params, fallback), alertHead, alertCard,
    searchLink: (country, period, q = '') => searchLink(cfg, country, period, q), go,
  };
  root.innerHTML = `<nav class="tr-tabs" role="tablist">${TABS.map(([id, n]) => `<button role="tab" data-v="${id}">${n}</button>`).join('')}</nav>
    <p class="tr-caveat" id="tr-caveat"></p><div id="tr-view" class="tr-view"></div>`;
  root.querySelector('.tr-tabs').addEventListener('click', (e) => { const b = e.target.closest('[data-v]'); if (b) go(b.dataset.v); });
  const views = { alerts: alertsView, country: countryView, heatmap: heatmapView, topics: topicsView, echoes: echoesView,
    coverage: coverageView, method: methodView };
  let resize;
  window.addEventListener('resize', () => { clearTimeout(resize); resize = setTimeout(() => go(S.v, true), 200); });
  async function go(v, keep) {
    S.v = views[v] ? v : 'alerts';
    ctx.save();
    for (const b of root.querySelectorAll('.tr-tabs button')) b.setAttribute('aria-selected', String(b.dataset.v === S.v));
    const host = root.querySelector('#tr-view');
    if (!keep) host.innerHTML = '<p class="fine">Loading…</p>';
    try {
      const ov = await ctx.get('overview');
      root.querySelector('#tr-caveat').innerHTML = `<b>Read with care.</b> ${ov.caveats.map(esc).join(' ')} Data through ${esc(ov.data_last_date)}.`;
      await views[S.v](ctx, host, ov);
    } catch (e) {
      host.innerHTML = `<p class="err">Could not load this view: ${esc(e.message || e)}</p>`;
    }
  }
  go(S.v);
  return ctx;
}

function searchLink(cfg, country, period, q) {
  if (!cfg.searchUrl) return '';
  const [from, to] = pRange(period);
  return `<a class="xlink" href="${esc(cfg.searchUrl({ country, from, to, q }))}">Search ${esc(L.country(country))}'s statements, ${esc(from)} to ${esc(to)}${q ? ` for ${esc(q)}` : ''}</a>`;
}

// ---- Evidence ---------------------------------------------------------------------------------------------------
export function evidenceList(items) {
  if (!items || !items.length) return '<p class="fine">No evidence sentences for this period.</p>';
  return `<ol class="ev">${items.map((e) => {
    const withheld = e.text == null || e.text_withheld;
    const meta = `<div class="m"><b>${esc(e.date)}</b><span>${esc(L.country(e.country))} · ${esc(e.source)}</span><span class="o-${esc(e.outlet)}">${esc(L.outlet(e.outlet))}</span>${e.score != null ? `<span>${esc(e.metric || '')} ${esc(e.score)}</span>` : ''}${e.max_sim != null ? `<span>sim ${esc(e.max_sim)}</span>` : ''}</div>`;
    if (withheld) return `<li class="withheld">${meta}<p class="mt"><a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.title || e.url)}</a></p><p class="fine">Media article: text not shown here; headline and link only.</p></li>`;
    return `<li>${meta}<p class="s">“${esc(e.text)}”</p><div class="t">${e.title ? esc(e.title) + ' · ' : ''}<a href="${esc(e.url)}" target="_blank" rel="noopener">Source</a></div></li>`;
  }).join('')}</ol>`;
}

async function showEvidence(ctx, box, params, fallback) {
  box.hidden = false;
  if (!ctx.cfg.evidence) { box.innerHTML = fallback || ''; return; }
  box.innerHTML = '<p class="fine">Loading the sentences behind this number…</p>';
  try {
    const r = await ctx.cfg.evidence(params);
    if (r.error) throw new Error(r.error);
    box.innerHTML = `${fallback || ''}<p class="fine">${esc(r.note)} (${fmtN(r.docs)} documents in the period).</p>${evidenceList(r.evidence)}`;
  } catch (e) { box.innerHTML = `<p class="err">${esc(e.message || e)}</p>`; }
}

// ---- Alerts -----------------------------------------------------------------------------------------------------
export function alertHead(a) {
  const who = a.level === 'country' ? `<b>${esc(L.country(a.country))}</b> (all streams combined)` : `<b>${esc(L.country(a.country))}</b> · <code>${esc(a.stream)}</code> <span class="o-${esc(a.stream_outlet)}">${esc(L.outlet(a.stream_outlet))}</span>`;
  let what;
  if (a.kind === 'tone') what = esc(L.metric(a.metric));
  else if (a.kind === 'stance') what = `${esc(L.metric(a.metric))} in sentences mentioning ${esc(L.target(a.target))}`;
  else if (a.kind === 'salience') what = `share of documents mentioning ${esc(L.target(a.target))}`;
  else what = `share of documents in topic ${esc(a.topic)}${a.topic_label ? ` (${esc(a.topic_label)})` : ''}`;
  const nums = a.base == null
    ? `level ${fmt(a.mean)} in the ${a.period_type} of ${esc(a.period)}; change vs each stream's own baseline ${signed(a.delta)} (combined z = ${fmt(a.z, 1)}; n = ${fmtN(a.n)} docs)`
    : `${fmt(a.mean)} in the ${a.period_type} of ${esc(a.period)} vs baseline ${fmt(a.base)} (${signed(a.delta)}; z = ${fmt(a.z, 1)}; n = ${fmtN(a.n)} docs)`;
  const run = a.n_periods > 1 ? ` <span class="fine">${a.n_periods} consecutive ${a.period_type}s, ${esc(a.start)} to ${esc(a.end)}.</span>` : '';
  return `${who} — ${what}: ${nums}.${run}`;
}

export function alertCard(ctx, a) {
  const lead = a.tier === 'weak' ? '<span class="lead">lead only</span>' : '';
  const ev = a.evidence ? `<p class="fine">${esc(a.evidence_note || '')}</p>${evidenceList(a.evidence)}` : '<p class="fine">No stored evidence for this alert (older, lower-ranked).</p>';
  const live = !a.evidence && ctx.cfg.evidence && a.kind !== 'topic_share' ? `<button type="button" class="btn sm" data-live="${esc(a.id)}">Load evidence</button><div class="evbox" hidden></div>` : '';
  const toCountry = a.kind === 'tone' ? `<button type="button" class="btn sm" data-chart="${esc(a.id)}">Show in country chart</button>` : '';
  return `<li class="alert t-${esc(a.tier)}" data-id="${esc(a.id)}"><div class="ah"><span class="tier">${esc(a.tier)}</span>${lead}${a.recent ? '<span class="pill">recent</span>' : ''}<span class="dir">${a.direction === 'up' ? '▲ up' : '▼ down'}</span></div>
    <p class="hd">${alertHead(a)}</p><details><summary>Evidence${a.evidence ? ` (${a.evidence.length})` : ''}</summary>${ev}${live}</details>${toCountry}</li>`;
}

async function alertsView(ctx, host) {
  const S = ctx.S;
  const data = await ctx.get('alerts');
  const A = data.alerts, meta = data.meta;
  const uniq = (k) => [...new Set(A.map((a) => a[k]).filter((v) => v != null))].sort();
  const opt = (k, label, vals, name = (v) => v) => `<label class="pick"><span>${label}</span><select data-f="${k}"><option value="">All</option>${vals.map((v) => `<option value="${esc(v)}">${esc(name(v))}</option>`).join('')}</select></label>`;
  const tiers = meta.tiers_shown;
  const tc = meta.tail_check || {};
  host.innerHTML = `<div class="tr-controls">
      <div class="seg" role="group" aria-label="Tier">${tiers.map((t) => `<label class="chk"><input type="checkbox" data-tier="${t}" checked> ${t}${t === 'weak' ? ' (lead only)' : ''}</label>`).join('')}</div>
      ${opt('country', 'Country', uniq('country'), L.country)}${opt('kind', 'Kind', uniq('kind'))}${opt('target', 'Target', uniq('target'), L.target)}${opt('metric', 'Dimension', uniq('metric'), L.metric)}
      <label class="chk"><input type="checkbox" data-recent> last 12 weeks only</label></div>
    <p class="fine">${fmtN(meta.n_tests)} period tests. Periods beyond |z|: ${Object.entries(tc).map(([k, v]) => `${esc(k.replace('|z|>=', ''))}: ${fmtN(v.observed_periods)} observed vs ${fmt(v.expected_if_normal, 1)} by chance`).join(' · ')}.
      Tiers: ${esc(meta.tiers)}.${tiers.includes('weak') ? ' Weak alerts are about as frequent as chance and are leads only.' : ' Weak alerts (about as frequent as chance) are not shown here.'}</p>
    <p id="al-count" class="count"></p><ol class="alerts" id="al-list"></ol><button type="button" class="btn" id="al-more" hidden>Show more</button>`;
  for (const sel of host.querySelectorAll('select[data-f]')) sel.value = S['a_' + sel.dataset.f] || '';
  for (const c of host.querySelectorAll('[data-tier]')) c.checked = !(S.a_tiers && !S.a_tiers.split(',').includes(c.dataset.tier));
  host.querySelector('[data-recent]').checked = S.a_recent === '1';
  let rows = [], shown = 0;
  const render = () => {
    const f = Object.fromEntries([...host.querySelectorAll('select[data-f]')].map((s) => [s.dataset.f, s.value]));
    const on = [...host.querySelectorAll('[data-tier]')].filter((c) => c.checked).map((c) => c.dataset.tier);
    const recent = host.querySelector('[data-recent]').checked;
    for (const [k, v] of Object.entries(f)) S['a_' + k] = v;
    S.a_tiers = on.length === tiers.length ? '' : on.join(',');
    S.a_recent = recent ? '1' : '';
    ctx.save();
    rows = A.filter((a) => on.includes(a.tier) && (!recent || a.recent) && Object.entries(f).every(([k, v]) => !v || String(a[k]) === v));
    shown = 0;
    host.querySelector('#al-list').innerHTML = '';
    host.querySelector('#al-count').textContent = `${fmtN(rows.length)} alert${rows.length === 1 ? '' : 's'}`;
    more();
  };
  const more = () => {
    host.querySelector('#al-list').insertAdjacentHTML('beforeend', rows.slice(shown, shown + 40).map((a) => alertCard(ctx, a)).join(''));
    shown += 40;
    host.querySelector('#al-more').hidden = shown >= rows.length;
  };
  host.querySelector('.tr-controls').addEventListener('change', render);
  host.querySelector('#al-more').addEventListener('click', more);
  host.querySelector('#al-list').addEventListener('click', (e) => {
    const b = e.target.closest('button');
    if (!b) return;
    const a = A.find((x) => x.id === (b.dataset.live || b.dataset.chart));
    if (b.dataset.chart) { Object.assign(S, { c: a.country, m: a.metric, pt: a.period_type, cp: a.period }); ctx.go('country'); return; }
    ctx.showEvidence(b.nextElementSibling, { kind: a.kind, country: a.country, period: a.period, metric: a.metric, target: a.target || '',
      stream: a.level === 'stream' ? a.stream : '', dir: a.direction });
  });
  render();
}

// ---- Country tone ---------------------------------------------------------------------------------------------
async function countryView(ctx, host, ov) {
  const S = ctx.S;
  const countries = Object.keys(ov.countries).sort();
  S.c = countries.includes(S.c) ? S.c : (countries.includes('RU') ? 'RU' : countries[0]);
  S.pt = S.pt === 'month' ? 'month' : 'week';
  S.m = ov.metrics.includes(S.m) ? S.m : 'hostility';
  S.since = S.since || '2y';
  const d = await ctx.get('tone_' + S.c);
  host.innerHTML = `<div class="tr-controls">
      <label class="pick"><span>Country</span><select id="c-c">${countries.map((c) => `<option value="${c}">${esc(L.country(c))}</option>`).join('')}</select></label>
      <label class="pick"><span>Dimension</span><select id="c-m">${ov.metrics.map((m) => `<option value="${m}">${esc(L.metric(m))}</option>`).join('')}</select></label>
      <label class="pick"><span>Period</span><select id="c-pt"><option value="week">Weekly</option><option value="month">Monthly</option></select></label>
      <label class="pick"><span>Show</span><select id="c-since"><option value="all">All dates</option><option value="3y">Last 3 years</option><option value="2y">Last 2 years</option><option value="1y">Last year</option><option value="6m">Last 6 months</option></select></label></div>
    <section class="card tr-card"><h3>Level: ${esc(L.metric(S.m))}, ${esc(L.country(S.c))}, all streams combined</h3>
      <p class="fine">Fixed-weight mean of the country's streams (shaded: ±1.96 standard errors). Grey columns: documents per period. Flags ▸ mark coverage changes (stream starts/ends, volume jumps); levels move when streams enter or leave.</p>
      <div class="chart" id="c-level"></div></section>
    <section class="card tr-card"><h3>Change against each stream's own baseline</h3>
      <p class="fine">Weighted mean of within-stream changes vs the previous ${S.pt === 'week' ? '12 weeks' : '6 months'} (shaded: ±1.96 standard errors, from each stream's baseline spread and period error). Below: combined z (weighted Stouffer); tone alerts need |z| ≥ 3 and a change ≥ 0.03.</p>
      <div class="chart" id="c-delta"></div><div class="chart" id="c-z"></div></section>
    <div class="evbox card" id="c-ev" hidden></div>
    <section class="tr-card"><h3>By stream (${esc(d.streams_note)})</h3><p class="fine">Solid line: stream mean; dashed: its own baseline. Shaded: ±1.96 s.e.; ringed points: |z| ≥ 3. Click a chart for the evidence in that stream.</p><div class="smalls" id="c-streams"></div></section>`;
  host.querySelector('#c-c').value = S.c; host.querySelector('#c-m').value = S.m; host.querySelector('#c-pt').value = S.pt; host.querySelector('#c-since').value = S.since;
  host.querySelector('.tr-controls').addEventListener('change', () => {
    Object.assign(S, { c: host.querySelector('#c-c').value, m: host.querySelector('#c-m').value, pt: host.querySelector('#c-pt').value, since: host.querySelector('#c-since').value, cp: '' });
    ctx.go('country');
  });
  const cut = sinceCut(S.since, ov.data_last_date);
  const ser = d.combined[S.pt][S.m];
  const ev = host.querySelector('#c-ev');
  if (!ser) { host.querySelector('#c-level').innerHTML = '<p class="fine">No combined series for this selection.</p>'; return; }
  const keep = ser.period.map((p, i) => (p >= cut ? i : -1)).filter((i) => i >= 0);
  const pick = (arr) => keep.map((i) => (arr ? arr[i] : null));
  const P = pick(ser.period), V = pick(ser.value), SE = pick(ser.se), DL = pick(ser.adj_delta), Z = pick(ser.z), N = pick(ser.n), NS = pick(ser.n_streams);
  const flags = ov.coverage.flags.filter((f) => f.country === S.c).map((f) => ({ period: S.pt === 'week' ? `${f.period}-01` : f.period, label: `${f.period}: ${f.detail}` }));
  const notes = ov.known_notes.filter((n) => n.country === S.c && n.period).map((n) => ({ period: S.pt === 'week' ? `${n.period}-01` : n.period, label: n.note }));
  const tip = (i) => `<b>${esc(P[i])}</b><span class="tt-d">level ${fmt(V[i])} ± ${fmt(SE[i] != null ? 1.96 * SE[i] : null)}<br>change ${signed(DL[i])} · z ${fmt(Z[i], 2)}<br>${fmtN(N[i])} docs · ${NS[i]} stream${NS[i] === 1 ? '' : 's'}</span>`;
  const alertsData = await ctx.get('alerts');
  const onPick = (i) => {
    const p = P[i];
    const hits = alertsData.alerts.filter((a) => a.kind === 'tone' && a.country === S.c && a.metric === S.m && a.period_type === S.pt && a.start <= p && a.end >= p);
    const head = `<h3>${esc(L.country(S.c))}, ${esc(L.metric(S.m))}, ${S.pt} of ${esc(p)}</h3><p class="num">level ${fmt(V[i])} ± ${fmt(SE[i] != null ? 1.96 * SE[i] : null)} · change ${signed(DL[i])} · z ${fmt(Z[i], 2)} · ${fmtN(N[i])} docs</p>`
      + (hits.length ? `<p class="fine">Alerts covering this period:</p><ol class="alerts">${hits.map((a) => alertCard(ctx, a)).join('')}</ol>` : '<p class="fine">No alert covers this period for this dimension.</p>')
      + ctx.searchLink(S.c, p);
    S.cp = p; ctx.save();
    ctx.showEvidence(ev, { kind: 'tone', country: S.c, period: p, metric: S.m, dir: (DL[i] ?? 0) >= 0 ? 'up' : 'down' }, head);
    ev.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  };
  timeChart(host.querySelector('#c-level'), { periods: P, aria: 'combined level', series: [{ y: V, lo: V.map((v, i) => (v == null || SE[i] == null ? null : v - 1.96 * SE[i])), hi: V.map((v, i) => (v == null || SE[i] == null ? null : v + 1.96 * SE[i])) }],
    bars: { y: N, label: 'docs' }, markers: flags.concat(notes), tip, onPick, yFmt: (v) => v.toFixed(2) });
  const DSE = pick(ser.delta_se);
  const half = DL.map((v, i) => (v == null || DSE[i] == null ? null : 1.96 * DSE[i]));
  timeChart(host.querySelector('#c-delta'), { periods: P, height: 150, aria: 'change against baseline', series: [{ y: DL, lo: DL.map((v, i) => (half[i] == null ? null : v - half[i])), hi: DL.map((v, i) => (half[i] == null ? null : v + half[i])), color: 'var(--c2)' }],
    refLines: [{ y: 0 }], tip, onPick, yFmt: (v) => v.toFixed(2) });
  zChart(host.querySelector('#c-z'), { periods: P, z: Z, thr: 3, tip, onPick });
  const smalls = host.querySelector('#c-streams');
  const few = [];
  for (const info of d.stream_info) {
    const s = d.streams[info.stream]?.[S.pt]?.[S.m];
    if (!s) continue;
    const k = s.period.map((p, i) => (p >= cut ? i : -1)).filter((i) => i >= 0);
    if (k.length < 3) { few.push(`${info.stream} (${k.length})`); continue; }
    const g = (arr) => k.map((i) => arr[i]);
    const sp = g(s.period), sm = g(s.mean), sse = g(s.se), sb = g(s.base), sz = g(s.z), sn = g(s.n), sd = g(s.delta);
    const box = document.createElement('div');
    box.className = 'small card';
    box.innerHTML = `<p class="sh"><code>${esc(info.stream)}</code> <span class="o-${esc(info.outlet)}">${esc(L.outlet(info.outlet))}</span> <span class="fine">${fmtN(info.n_docs)} docs, ${esc(info.first)} to ${esc(info.last)}</span></p><div class="chart"></div>`;
    smalls.appendChild(box);
    timeChart(box.querySelector('.chart'), { periods: sp, height: 140, aria: info.stream,
      series: [{ y: sm, lo: sm.map((v, i) => (sse[i] == null ? null : v - 1.96 * sse[i])), hi: sm.map((v, i) => (sse[i] == null ? null : v + 1.96 * sse[i])), dots: sz.map((z, i) => (z != null && Math.abs(z) >= 3 ? i : -1)).filter((i) => i >= 0) },
        { y: sb, dash: true, color: 'var(--muted)' }],
      bars: { y: sn, label: 'docs' }, yFmt: (v) => v.toFixed(2),
      tip: (i) => `<b>${esc(sp[i])}</b><span class="tt-d">mean ${fmt(sm[i])} ± ${fmt(sse[i] != null ? 1.96 * sse[i] : null)}<br>baseline ${fmt(sb[i])} · change ${signed(sd[i])} · z ${fmt(sz[i], 2)}<br>${fmtN(sn[i])} docs</span>`,
      onPick: (i) => {
        const head = `<h3><code>${esc(info.stream)}</code>, ${esc(L.metric(S.m))}, ${S.pt} of ${esc(sp[i])}</h3><p class="num">mean ${fmt(sm[i])} · baseline ${fmt(sb[i])} · change ${signed(sd[i])} · z ${fmt(sz[i], 2)} · ${fmtN(sn[i])} docs</p>${ctx.searchLink(S.c, sp[i])}`;
        ctx.showEvidence(ev, { kind: 'tone', country: S.c, stream: info.stream, period: sp[i], metric: S.m, dir: (sd[i] ?? 0) >= 0 ? 'up' : 'down' }, head);
        ev.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      } });
  }
  if (!smalls.children.length) smalls.innerHTML = '<p class="fine">No stream series in this range.</p>';
  if (few.length) smalls.insertAdjacentHTML('afterend', `<p class="fine">Not charted (fewer than 3 periods in range): ${few.map(esc).join(', ')}.</p>`);
  if (S.cp) { const i = P.indexOf(S.cp); if (i >= 0) onPick(i); }
}

export function sinceCut(since, last) {
  if (!since || since === 'all') return '';
  const d = new Date(`${last}T00:00:00Z`);
  const months = { '3y': 36, '2y': 24, '1y': 12, '6m': 6 }[since] || 0;
  d.setUTCMonth(d.getUTCMonth() - months);
  return d.toISOString().slice(0, 7);
}
