"""The master's front end: one page, no build step, no external assets.

Served as a string rather than from a template directory or a bundler, because
the deploy is source-based and every file that is not code is one more thing
``.gcloudignore`` can quietly drop. This project has already lost work to that
exact mechanism.

Two things on this page are load-bearing rather than decorative:

**The brain banner.** When the drafts came from ``direct``-mode agents they are
canned text, identical on all three clouds, and the ranking is a latency
tie-break. A page that renders that the same way it renders a model comparison
manufactures the one claim this repo is careful never to make, so a non-``llm``
draft is called out above the result and not in a footnote.

**The margin warning.** The judge's own narrow-margin warning is shown next to
the winner, not below the drafts. A verdict read without it is "azure won"; a
verdict read with it is "azure and gcp tied and azure was faster".
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>three clouds, one brief</title>
<style>
  :root {
    --bg: #0f1115; --panel: #161922; --line: #262b38; --text: #e6e9ef;
    --dim: #98a0b3; --accent: #7aa2f7; --good: #9ece6a; --warn: #e0af68;
    --bad: #f7768e; --gcp: #7aa2f7; --aws: #e0af68; --azure: #bb9af7;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  main { max-width: 980px; margin: 0 auto; padding: 32px 20px 80px; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--dim); font-size: 13px; margin: 0 0 24px; }
  .panel {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 10px; padding: 18px; margin-bottom: 18px;
  }
  label { display: block; font-size: 12px; color: var(--dim);
          text-transform: uppercase; letter-spacing: .06em; margin-bottom: 6px; }
  input, textarea, select {
    width: 100%; background: #0d0f14; color: var(--text);
    border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px;
    font: inherit;
  }
  textarea { resize: vertical; min-height: 68px; }
  .row { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 14px; }
  .row > div { flex: 1 1 150px; }
  .clouds { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 6px; }
  .clouds label { display: flex; align-items: center; gap: 7px;
                  text-transform: none; letter-spacing: 0; font-size: 14px;
                  color: var(--text); margin: 0; }
  .clouds input { width: auto; }
  button {
    margin-top: 18px; background: var(--accent); color: #0b0d12; border: 0;
    border-radius: 6px; padding: 10px 20px; font: 600 15px/1 inherit;
    cursor: pointer;
  }
  button[disabled] { opacity: .5; cursor: progress; }
  .status { margin-top: 12px; color: var(--dim); font-size: 13px; min-height: 18px; }
  .banner {
    border-left: 3px solid var(--warn); background: #1d1a12;
    padding: 11px 14px; border-radius: 0 6px 6px 0; margin-bottom: 16px;
    font-size: 13.5px;
  }
  .banner.bad { border-color: var(--bad); background: #1d1216; }
  .winner { font-size: 19px; font-weight: 600; margin: 0 0 2px; }
  .meta { color: var(--dim); font-size: 12.5px; margin-bottom: 16px; }
  .card { border: 1px solid var(--line); border-radius: 8px;
          padding: 14px; margin-bottom: 10px; }
  .card.first { border-color: var(--good); }
  .card h3 { margin: 0; font-size: 15px; display: flex;
             align-items: center; gap: 9px; flex-wrap: wrap; }
  .tag { font: 600 10.5px/1 ui-monospace, monospace; padding: 4px 7px;
         border-radius: 4px; background: var(--line); color: var(--dim);
         text-transform: uppercase; letter-spacing: .05em; }
  .tag.gcp { background: #1b2438; color: var(--gcp); }
  .tag.aws { background: #302713; color: var(--aws); }
  .tag.azure { background: #251d38; color: var(--azure); }
  .tag.direct { background: #302713; color: var(--warn); }
  .tag.llm { background: #16240f; color: var(--good); }
  .score { margin-left: auto; font: 600 15px ui-monospace, monospace; }
  .dims { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
          gap: 8px 16px; margin-top: 12px; }
  .dim { font-size: 11.5px; color: var(--dim); }
  .bar { height: 4px; background: var(--line); border-radius: 2px; margin-top: 4px; }
  .bar span { display: block; height: 100%; background: var(--accent);
              border-radius: 2px; }
  details { margin-top: 12px; }
  summary { cursor: pointer; color: var(--dim); font-size: 12.5px; }
  pre { white-space: pre-wrap; word-wrap: break-word; background: #0d0f14;
        border: 1px solid var(--line); border-radius: 6px; padding: 13px;
        font: 12.5px/1.6 ui-monospace, monospace; overflow-x: auto; margin: 10px 0 0; }
  .fail { color: var(--bad); font-size: 13px; margin-top: 8px;
          font-family: ui-monospace, monospace; }
  .flow { display: flex; flex-direction: column; gap: 10px; }
  .lane { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .lane > .tag:first-child { min-width: 58px; text-align: center; }
  .hop {
    display: flex; align-items: baseline; gap: 8px; padding: 6px 10px;
    border: 1px solid var(--line); border-left-width: 3px; border-radius: 5px;
    font: 11.5px/1.35 ui-monospace, monospace; white-space: nowrap;
  }
  .hop.credential { border-left-color: var(--azure); }
  .hop.discovery  { border-left-color: var(--gcp); }
  .hop.invoke     { border-left-color: var(--good); }
  .hop.err        { border-left-color: var(--bad); background: #1d1216; }
  .hop b { font-weight: 600; color: var(--text); }
  .hop i { font-style: normal; color: var(--dim); }
  .arrow { color: var(--line); }
  .legend { display: flex; gap: 14px; flex-wrap: wrap; margin-top: 14px;
            font-size: 11.5px; color: var(--dim); }
  .swatch { display: inline-block; width: 9px; height: 9px; border-radius: 2px;
            margin-right: 5px; vertical-align: baseline; }
  table.cmp { width: 100%; border-collapse: collapse; margin-top: 12px;
              font: 12.5px ui-monospace, monospace; }
  table.cmp th, table.cmp td { text-align: right; padding: 6px 8px;
                               border-bottom: 1px solid var(--line); }
  table.cmp th:first-child, table.cmp td:first-child { text-align: left; color: var(--dim); }
  table.cmp th { color: var(--dim); font-weight: 500; }
  table.cmp td.best { color: var(--good); font-weight: 600; }
  table.cmp tr.total td { border-bottom: 0; font-weight: 600; }
  h2 { font-size: 14px; text-transform: uppercase; letter-spacing: .07em;
       color: var(--dim); margin: 0 0 12px; font-weight: 600; }
  .tabs { display: flex; gap: 4px; margin-bottom: 18px; }
  .tabs button { margin: 0; background: transparent; color: var(--dim);
                 border: 1px solid var(--line); font-weight: 500; padding: 7px 15px; }
  .tabs button.on { background: var(--line); color: var(--text); }
  .peers { font: 12px ui-monospace, monospace; color: var(--dim); }
  .peers b { color: var(--text); font-weight: 500; }
</style>
</head>
<body>
<main>
  <h1>three clouds, one brief</h1>
  <p class="sub">Google, AWS and Azure each write a draft with their own
     vendor's framework and model. A judge reads all three blind and ranks
     them.</p>

  <div class="tabs">
    <button class="on" data-tab="run">run a brief</button>
    <button data-tab="last">last run</button>
    <button data-tab="audit">audit</button>
  </div>

  <section id="tab-run">
    <div class="panel">
      <label for="topic">brief</label>
      <input id="topic" placeholder="the state of solid-state batteries in 2026">

      <div style="margin-top:14px">
        <label for="questions">focus questions &mdash; one per line, optional</label>
        <textarea id="questions" placeholder="who ships at scale?"></textarea>
      </div>

      <div class="row">
        <div>
          <label for="max_words">max words</label>
          <input id="max_words" type="number" value="600" min="50" max="5000">
        </div>
        <div>
          <label for="client">client stack</label>
          <select id="client">
            <option value="a2a-sdk">a2a-sdk</option>
            <option value="agent-framework">agent-framework</option>
            <option value="google-adk">google-adk</option>
          </select>
        </div>
        <div>
          <label for="judge">judge</label>
          <select id="judge">
            <option value="">default</option>
            <option value="rubric">rubric (deterministic)</option>
            <option value="llm">llm</option>
          </select>
        </div>
      </div>

      <div style="margin-top:14px">
        <label>clouds</label>
        <div class="clouds" id="clouds"></div>
      </div>

      <button id="go">send the brief</button>
      <div class="status" id="status"></div>
    </div>

    <div id="result"></div>
  </section>

  <section id="tab-last" hidden>
    <p class="sub">The most recent run read back out of the append-only store,
       rendered exactly as it was live. This is the shareable one: it survives
       the instance that produced it.</p>
    <div id="last"></div>
  </section>

  <section id="tab-audit" hidden>
    <div class="panel">
      <p class="sub" style="margin:0 0 10px">Every recorded run, aggregated per
         cloud and model. Rows with fewer than five runs behind them are
         withheld rather than shown thin.</p>
      <pre id="audit">loading&hellip;</pre>
    </div>
  </section>
</main>

<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

let health = {peers: []};

async function loadHealth() {
  try {
    health = await (await fetch('api/health')).json();
  } catch (e) { return; }
  $('clouds').innerHTML = health.peers.map(p => `
    <label title="${esc(p.endpoint)}">
      <input type="checkbox" value="${esc(p.cloud)}" checked>
      <span class="tag ${esc(p.cloud)}">${esc(p.cloud)}</span>
      <span class="peers"><b>${esc(p.auth)}</b> &middot; ${esc(p.reachable_as)}</span>
    </label>`).join('');
  if (health.judge) $('judge').value = '';
}

$('go').onclick = async () => {
  const clouds = [...document.querySelectorAll('#clouds input:checked')].map(i => i.value);
  if (!clouds.length) { $('status').textContent = 'pick at least one cloud'; return; }

  $('go').disabled = true;
  $('status').textContent = `sending the brief to ${clouds.join(', ')}…`;
  $('result').innerHTML = '';
  const started = Date.now();
  const tick = setInterval(() => {
    $('status').textContent =
      `waiting on ${clouds.join(', ')}… ${((Date.now()-started)/1000).toFixed(0)}s`;
  }, 1000);

  try {
    const res = await fetch('api/research', {
      method: 'POST',
      headers: {'content-type': 'application/json'},
      body: JSON.stringify({
        topic: $('topic').value,
        questions: $('questions').value.split('\\n').map(q => q.trim()).filter(Boolean),
        max_words: Number($('max_words').value),
        client: $('client').value,
        judge: $('judge').value || null,
        clouds,
      }),
    });
    const body = await res.json();
    if (!res.ok) { $('status').textContent = body.error || `HTTP ${res.status}`; return; }
    $('status').textContent = '';
    render(body);
  } catch (e) {
    $('status').textContent = String(e);
  } finally {
    clearInterval(tick);
    $('go').disabled = false;
  }
};

function render(run, into = 'result') {
  const drafts = Object.fromEntries((run.drafts || []).map(d => [d.source, d]));
  const verdict = run.verdict;
  const out = [];

  // Ranking canned text reads exactly like ranking models unless the page
  // says otherwise, so this goes above the winner rather than below the drafts.
  const brains = new Set((run.drafts || []).map(d => d.brain));
  if (run.drafts && run.drafts.length && !(brains.size === 1 && brains.has('llm'))) {
    out.push(`<div class="banner">These drafts were not all written by a model
      (brain: ${[...brains].map(esc).join(', ')}). <b>This is not a model
      comparison</b> &mdash; direct-mode agents return canned text, so the
      ranking is a tie-break, not a result.</div>`);
  }

  if (!verdict || !run.drafts.length) {
    out.push('<div class="banner bad">No cloud returned a draft.</div>');
  } else {
    out.push(`<div class="panel">
      <p class="winner">winner: ${esc(verdict.winner || 'none')}</p>
      <p class="meta">${run.drafts.length}/${run.participants.length} clouds
        &middot; judge ${esc(verdict.judge)}${verdict.blind ? ' &middot; blind' : ''}
        &middot; rubric v${verdict.rubric_version}
        &middot; ${Math.round(run.elapsed_ms)}ms elapsed</p>
      ${(verdict.warnings || []).map(w =>
        `<div class="banner">${esc(w)}</div>`).join('')}
      ${[...verdict.verdicts].sort((a,b) => a.rank - b.rank).map(v => {
        const d = drafts[v.source] || {};
        return `<div class="card ${v.rank === 1 ? 'first' : ''}">
          <h3>
            <span class="tag ${esc(v.source)}">${esc(v.source)}</span>
            ${v.rank}. ${esc(d.title || '(no title)')}
            <span class="tag ${esc(d.brain || '')}">${esc(d.brain || '?')}</span>
            <span class="score">${v.total.toFixed(1)}/25</span>
          </h3>
          <p class="meta" style="margin:8px 0 0">${esc(d.model || 'unknown')}
             &middot; ${d.word_count ?? (d.body || '').split(/\\s+/).filter(Boolean).length}w
             &middot; ${Math.round(d.latency_ms || 0)}ms</p>
          <div class="dims">${(v.scores || []).map(s => `
            <div class="dim">${esc(s.dimension)} ${s.score.toFixed(1)}
              <div class="bar"><span style="width:${(s.score/5)*100}%"></span></div>
            </div>`).join('')}</div>
          ${v.notes ? `<p class="meta" style="margin-top:10px">${esc(v.notes)}</p>` : ''}
          <details><summary>read the draft</summary>
            <pre>${esc(d.body || '(empty)')}</pre></details>
        </div>`;
      }).join('')}
      ${verdict.rationale ? `<p class="meta">${esc(verdict.rationale)}</p>` : ''}
    </div>`);
  }

  out.push(renderFlow(run));
  out.push(renderComparison(run));

  const failures = Object.entries(run.failures || {});
  if (failures.length) {
    out.push(`<div class="panel">${failures.map(([name, why]) =>
      `<div class="fail">${esc(name)}: ${esc(why)}</div>`).join('')}
      <p class="meta" style="margin-top:10px">A cloud that fails degrades the
      run to the remaining clouds rather than failing it.</p></div>`);
  }

  out.push(`<p class="peers">auth per leg: ${
    Object.entries(run.auth_modes || {}).map(([k, v]) =>
      `<b>${esc(k)}</b> ${esc(v)}`).join(' &middot; ')}</p>`);

  $(into).innerHTML = out.join('');

  // Fetched rather than rendered here: the timeline is generated by
  // coordinator/timeline.py, and a second implementation in JavaScript would
  // be a second thing to keep true.
  const pre = document.getElementById('timeline');
  if (pre) {
    fetch('api/timeline')
      .then(r => r.ok ? r.text() : 'not recorded, so there is no stored timeline')
      .then(t => { pre.textContent = t; })
      .catch(e => { pre.textContent = String(e); });
  }
}

// The flow. Every HTTP round trip each leg actually made, in order, with the
// host it was made to -- which is the whole evidence. "This is cross-cloud"
// is not a claim the page should assert next to a logo; it is a hostname of
// bedrock-agentcore.us-west-2.amazonaws.com sitting in the trace, or it is
// nothing. The page deliberately does not editorialise beyond showing them.
function renderFlow(run) {
  const traces = run.traces || {};
  const legs = (run.participants || []).filter(name => (traces[name] || []).length);
  if (!legs.length) {
    return `<div class="panel"><h2>agent flow</h2>
      <p class="meta" style="margin:0">No network was crossed. These drafts came
      from in-process adapters, so there is no flow to show &mdash; which is the
      honest answer rather than an empty diagram.</p></div>`;
  }

  const hosts = new Set();
  let hops = 0;
  legs.forEach(name => (traces[name] || []).forEach(s => { hosts.add(s.host); hops++; }));

  const lanes = legs.map(name => {
    const steps = traces[name] || [];
    const total = steps.reduce((sum, s) => sum + (s.elapsed_ms || 0), 0);
    return `<div class="lane">
      <span class="tag ${esc(name)}">${esc(name)}</span>
      ${steps.map(s => `
        <span class="hop ${esc(s.phase)}${s.ok ? '' : ' err'}"
              title="${esc(s.method)} ${esc(s.host)}${esc(s.path)}${
                s.detail ? ' — ' + esc(s.detail) : ''}">
          <b>${esc(s.host)}</b>
          <i>${esc(s.path)}</i>
          <i>${s.status ?? '-'}</i>
          <i>${Math.round(s.elapsed_ms)}ms</i>
        </span>`).join('<span class="arrow">&rarr;</span>')}
      <span class="peers">&Sigma; ${Math.round(total)}ms</span>
    </div>`;
  }).join('');

  return `<div class="panel">
    <h2>agent flow</h2>
    <div class="flow">${lanes}</div>
    <div class="legend">
      <span><span class="swatch" style="background:var(--azure)"></span>credential
        &mdash; an identity provider</span>
      <span><span class="swatch" style="background:var(--gcp)"></span>discovery
        &mdash; agent-card fetch</span>
      <span><span class="swatch" style="background:var(--good)"></span>invoke
        &mdash; the A2A call</span>
    </div>
    <p class="meta" style="margin-top:12px">${hops} round trip${hops === 1 ? '' : 's'}
      across ${hosts.size} host${hosts.size === 1 ? '' : 's'}:
      ${[...hosts].map(h => `<b>${esc(h)}</b>`).join(', ')}.</p>
    <details><summary>the same calls in wall-clock order, as plain text</summary>
      <pre id="timeline">loading&hellip;</pre>
      <p class="meta">Also at <b>GET /api/timeline</b> &mdash; one curl, no browser.
         Sorted by time rather than grouped by leg, because grouped by leg three
         concurrent legs look exactly like three sequential ones.</p>
    </details>
  </div>`;
}

// Content review. The per-cloud cards above show each draft against the rubric;
// this shows them against *each other*, which is the only view in which "azure
// scored 3.2 on specificity" means anything. Best-in-row is marked rather than
// left to be eyeballed across columns.
function renderComparison(run) {
  const verdict = run.verdict;
  if (!verdict || (verdict.verdicts || []).length < 2) return '';

  const ranked = [...verdict.verdicts].sort((a, b) => a.rank - b.rank);
  const drafts = Object.fromEntries((run.drafts || []).map(d => [d.source, d]));
  const dims = (ranked[0].scores || []).map(s => s.dimension);

  const row = (label, values, fmt = v => v, best = null) => {
    const top = best === 'high' ? Math.max(...values)
              : best === 'low'  ? Math.min(...values) : null;
    return `<tr${label === 'total' ? ' class="total"' : ''}>
      <td>${esc(label)}</td>
      ${values.map(v => `<td class="${top !== null && v === top ? 'best' : ''}">${
        fmt(v)}</td>`).join('')}
    </tr>`;
  };

  return `<div class="panel">
    <h2>content review</h2>
    <table class="cmp">
      <tr><th>dimension</th>${ranked.map(v =>
        `<th>${esc(v.source)}</th>`).join('')}</tr>
      ${dims.map(dim => row(
        dim,
        ranked.map(v => (v.scores.find(s => s.dimension === dim) || {}).score ?? 0),
        v => v.toFixed(1),
        'high',
      )).join('')}
      ${row('total', ranked.map(v => v.total), v => v.toFixed(1) + '/25', 'high')}
      ${row('words', ranked.map(v => (drafts[v.source] || {}).word_count ?? 0), v => v + 'w')}
      ${row('latency', ranked.map(v => (drafts[v.source] || {}).latency_ms ?? 0),
        v => Math.round(v) + 'ms', 'low')}
    </table>
    <p class="meta" style="margin-top:12px">The rubric measures <b>form, not
      truth</b> &mdash; headings, figures, citation markers, length. A confidently
      wrong draft in tidy markdown outscores a hedged correct one and the rubric
      cannot tell. Read this as a comparison of shape.</p>
  </div>`;
}

document.querySelectorAll('.tabs button').forEach(btn => {
  btn.onclick = async () => {
    document.querySelectorAll('.tabs button').forEach(b =>
      b.classList.toggle('on', b === btn));
    ['run', 'last', 'audit'].forEach(tab => {
      $('tab-' + tab).hidden = btn.dataset.tab !== tab;
    });
    if (btn.dataset.tab === 'audit') {
      try { $('audit').textContent = await (await fetch('api/audit')).text(); }
      catch (e) { $('audit').textContent = String(e); }
    }
    if (btn.dataset.tab === 'last') {
      $('last').innerHTML = '<p class="sub">loading&hellip;</p>';
      try {
        const res = await fetch('api/last');
        if (res.status === 404) {
          $('last').innerHTML = `<div class="panel"><p class="meta"
            style="margin:0">Nothing recorded yet. Send a brief.</p></div>`;
          return;
        }
        render(await res.json(), 'last');
      } catch (e) { $('last').innerHTML = `<p class="fail">${esc(e)}</p>`; }
    }
  };
});

loadHealth();
</script>
</body>
</html>
"""
