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

  /* the debug views */
  .flownav { display: flex; gap: 8px; align-items: center; margin-bottom: 12px;
             flex-wrap: wrap; }
  .lane { border: 1px solid var(--line); border-radius: 8px; padding: 12px;
          margin-bottom: 10px; }
  .lane h4 { margin: 0 0 8px; display: flex; gap: 10px; align-items: center;
             font-size: 14px; }
  .rounds { display: flex; gap: 8px; flex-wrap: wrap; }
  .rbox { border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px;
          min-width: 92px; }
  .rbox .n { color: var(--dim); font-size: 11px; text-transform: uppercase;
             letter-spacing: .06em; }
  .rbox .v { font-size: 18px; font-weight: 600; }
  .rbox.up { border-color: #2f8f4e; }
  .rbox.same { opacity: .6; }
  .bar { height: 6px; background: var(--line); border-radius: 3px;
         overflow: hidden; margin-top: 3px; }
  .bar i { display: block; height: 100%; background: var(--accent); }
  .dims { display: grid; grid-template-columns: 96px 1fr 42px; gap: 4px 10px;
          align-items: center; font-size: 12px; margin-top: 8px; }
  .crit { border-left: 3px solid var(--accent); padding: 6px 10px;
          margin-top: 8px; white-space: pre-wrap; font-family: var(--mono);
          font-size: 12px; color: var(--dim); }
  .wire { width: 100%; border-collapse: collapse; font-family: var(--mono);
          font-size: 12px; }
  .wire th { text-align: left; color: var(--dim); font-weight: 500;
             border-bottom: 1px solid var(--line); padding: 6px 8px; }
  .wire td { padding: 5px 8px; border-bottom: 1px solid var(--line); }
  .wire tr.bad td { color: #d05a5a; }
  .rid { color: var(--dim); font-size: 11px; }
  .phase { display: inline-block; width: 18px; height: 18px; line-height: 18px;
           text-align: center; border-radius: 4px; background: var(--line);
           font-size: 11px; }

  /* live telemetry + event trace, after frontend/src/Telemetry.jsx in
     ~/way-back-home/level_3_new. Stat tiles with a sparkline each, never
     gauges: none of these numbers has a natural maximum, so a filled meter
     would have to invent a ceiling and would read as "80% of something". */
  .telem { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
           gap: 10px; }
  .tile { border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
  .tile .k { color: var(--dim); font-size: 10px; text-transform: uppercase;
             letter-spacing: .12em; display: flex; justify-content: space-between;
             align-items: center; gap: 6px; }
  /* tabular-nums so the digits stop dancing once a second */
  .tile .v { font-size: 20px; font-weight: 600; font-variant-numeric: tabular-nums; }
  .tile .u { color: var(--dim); font-size: 11px; margin-left: 4px; }
  .tile .d { color: var(--dim); font-size: 11px; font-variant-numeric: tabular-nums; }
  .livedot { font-size: 10px; text-transform: uppercase; letter-spacing: .18em; }
  .evt { font-family: var(--mono); font-size: 12px; max-height: 260px;
         overflow-y: auto; border: 1px solid var(--line); border-radius: 8px;
         padding: 8px 10px; }
  .evt.closed { max-height: 66px; }
  .evt div { display: flex; gap: 8px; line-height: 1.5; }
  .evt .ts { color: var(--dim); font-variant-numeric: tabular-nums; flex: 0 0 auto; }
  .evt .kd { flex: 0 0 52px; text-transform: uppercase; font-size: 10px;
             letter-spacing: .08em; padding-top: 2px; }
  .evt .tx { min-width: 0; word-break: break-word; }

  /* the live topology */
  .viz { width: 100%; height: 300px; display: block; }
  .viz .edge { stroke: var(--line); stroke-width: 1.5; fill: none; }
  .viz .edge.hot { stroke: var(--accent); stroke-width: 2; }
  .viz .node { fill: var(--bg); stroke: var(--line); stroke-width: 1.5; }
  .viz .node.busy { stroke: #e0af68; }
  .viz .node.ok { stroke: #00c176; }
  .viz .node.bad { stroke: #d05a5a; }
  .viz .nlabel { font: 600 12px var(--sans); fill: var(--text); }
  .viz .nmeta { font: 11px var(--mono); fill: var(--dim); }
  .viz .elabel { font: 10px var(--mono); fill: var(--dim); }
  .viz .ring { fill: none; stroke: #e0af68; opacity: .55; }
  .vizkey { display: flex; gap: 14px; flex-wrap: wrap; color: var(--dim);
            font-size: 11px; margin-top: 6px; }
  .vizkey b { font-weight: 600; }

  /* human in the loop */
  .step { border-left: 3px solid var(--line); padding: 0 0 14px 14px; margin-left: 6px; }
  .step.up { border-left-color: #00c176; }
  .step h5 { margin: 0 0 6px; font-size: 13px; display: flex; gap: 10px;
             align-items: baseline; }
  .draftbody { white-space: pre-wrap; font-family: var(--mono); font-size: 12px;
               background: var(--line); border-radius: 6px; padding: 10px;
               max-height: 300px; overflow-y: auto; }
  .cites { margin-top: 8px; font-size: 12px; }
  .cite { display: flex; gap: 8px; align-items: baseline; padding: 3px 0;
          border-bottom: 1px solid var(--line); }
  .cite a { color: var(--accent); text-decoration: none; word-break: break-all; }
  .cite .st { flex: 0 0 76px; font-family: var(--mono); font-size: 11px; }
  .cite select { margin: 0; width: auto; font-size: 11px; padding: 2px 4px; }
  .srcbody { white-space: pre-wrap; font-size: 12px; color: var(--dim);
             border-left: 2px solid var(--accent); padding: 6px 10px; margin: 6px 0;
             max-height: 200px; overflow-y: auto; }
  .hitl input[type=number] { width: 70px; }
  .hitl .row2 { display: flex; gap: 10px; align-items: center; flex-wrap: wrap;
                margin-top: 8px; }
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
    <button data-tab="live">live</button>
    <button data-tab="flow">flow</button>
    <button data-tab="reviews">reviews</button>
    <button data-tab="wire">wire</button>
    <button data-tab="review">review</button>
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

  <section id="tab-live" hidden>
    <p class="sub">What the mesh is doing, now. The transport figure is this
       browser's round trip to the master and nothing else &mdash; subtract it
       from any latency here and what is left is the mesh.</p>
    <div class="panel">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px">
        <span class="sub" style="margin:0">messaging</span>
        <span id="vizRound" class="livedot" style="color:var(--dim)">&mdash;</span>
      </div>
      <svg class="viz" id="viz" viewBox="0 0 720 300" preserveAspectRatio="xMidYMid meet"></svg>
      <div class="vizkey">
        <span><b style="color:#7aa2f7">&#9679;</b> credential</span>
        <span><b style="color:#e0af68">&#9679;</b> discovery</span>
        <span><b style="color:#00c176">&#9679;</b> invocation</span>
        <span><b style="color:#d05a5a">&#9679;</b> failed</span>
        <span>each dot is one round trip that happened, drawn when it completed</span>
      </div>
    </div>
    <div class="panel">
      <div class="k" style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px">
        <span class="sub" style="margin:0">transport</span>
        <span id="liveState" class="livedot">idle</span>
      </div>
      <div class="telem" id="telem"></div>
    </div>
    <div class="panel">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
        <button id="evtToggle" style="margin:0;background:transparent;color:var(--dim)">trace &#9656; <span id="evtCount">0</span></button>
        <span><button id="evtClear" style="margin:0;background:transparent;color:var(--dim)">clear</button>
        <button id="evtSave" style="margin:0;background:transparent;color:var(--dim)">save</button></span>
      </div>
      <div class="evt closed" id="evt"><div class="ts">no events yet</div></div>
    </div>
  </section>

  <section id="tab-flow" hidden>
    <p class="sub">What happened, to whom, and when. One lane per cloud, one
       column per round. Read left to right: a lane that gained a column was
       sent back by the judge.</p>
    <div id="flowNav" class="flownav"></div>
    <div id="flow">loading&hellip;</div>
  </section>

  <section id="tab-reviews" hidden>
    <p class="sub">Every round's verdict, per dimension, and the critique each
       cloud was actually sent. The critique is rebuilt with the same function
       the mesh called, not a second implementation in this page.</p>
    <div id="reviews">loading&hellip;</div>
  </section>

  <section id="tab-wire" hidden>
    <p class="sub">Every HTTP round trip the coordinator made, in wall-clock
       order, with the provider's own request id where it sent one. This is the
       only column here that someone outside this process can check.</p>
    <div id="wire">loading&hellip;</div>
  </section>

  <section id="tab-review" hidden>
    <p class="sub">Read each cloud's chain &mdash; what it wrote, what the judge
       said, what it wrote next &mdash; open the sources it cited, and record
       what you think. Your verdict never changes the judge's: this measures the
       scorer, and a scorer corrected by its reviewers measures nothing.</p>
    <div id="hitlNav" class="flownav"></div>
    <div id="hitl">loading&hellip;</div>
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


// --------------------------------------------------------------------------
// The debug views: flow, reviews, wire
// --------------------------------------------------------------------------
//
// All three read one payload from api/flow, which is shaped on the server. The
// critique in particular is rebuilt there with the same function the mesh
// called when it sent a draft back -- a second implementation here would drift
// and the drift would be invisible, because a plausible-looking critique that
// no agent ever received renders exactly like a real one.

let flowIndex = 1;
let flowData = null;

const fmtMs = (ms) => ms >= 1000 ? (ms / 1000).toFixed(1) + 's' : Math.round(ms) + 'ms';
const PHASE = {credential: 'K', discovery: 'D', invoke: 'I'};

async function loadFlow() {
  const targets = ['flow', 'reviews', 'wire'];
  try {
    const res = await fetch('api/flow?n=' + flowIndex);
    if (res.status === 404) {
      targets.forEach(id => $(id).innerHTML =
        `<div class="panel"><p class="meta" style="margin:0">Nothing recorded
         yet. Send a brief.</p></div>`);
      $('flowNav').innerHTML = '';
      return;
    }
    flowData = await res.json();
  } catch (e) {
    targets.forEach(id => $(id).textContent = String(e));
    return;
  }
  renderFlowNav();
  renderFlow();
  renderReviews();
  renderWire();
}

function renderFlowNav() {
  const d = flowData;
  const total = d.total_runs || 1;
  $('flowNav').innerHTML = `
    <button id="fPrev" ${flowIndex >= total ? 'disabled' : ''}>&larr; older</button>
    <button id="fNext" ${flowIndex <= 1 ? 'disabled' : ''}>newer &rarr;</button>
    <span class="meta"><b>${esc(d.run_id)}</b> &middot; ${esc(d.started_at)}
      &middot; ${fmtMs(d.elapsed_ms)} &middot; ${d.rounds} round(s)
      &middot; run ${flowIndex} of ${total}</span>`;
  $('fPrev').onclick = () => { flowIndex++; loadFlow(); };
  $('fNext').onclick = () => { flowIndex = Math.max(1, flowIndex - 1); loadFlow(); };
}

// A run with no model in it is not a comparison, and the page says so above
// the numbers rather than below them.
function brainBanner(d) {
  if (d.brains.includes('llm') && d.brains.length === 1) return '';
  return `<div class="panel" style="border-color:var(--accent);margin-bottom:12px">
    <p class="meta" style="margin:0"><b>Not a model comparison.</b>
    brain: ${d.brains.map(esc).join(', ')}. A <code>direct</code> draft is
    canned text, identical on every cloud, so any ranking over it is a latency
    tie-break.</p></div>`;
}

function renderFlow() {
  const d = flowData;
  const lanes = d.lanes.map(lane => {
    const dr = lane.draft;
    const boxes = lane.scores.map((s, i) => {
      const prev = i > 0 ? lane.scores[i - 1] : null;
      const cls = s === null ? 'same' : (prev !== null && s > prev ? 'up' : (prev === null ? '' : 'same'));
      const pct = s === null ? 0 : (100 * s / d.max_total);
      return `<div class="rbox ${cls}">
        <div class="n">round ${i + 1}</div>
        <div class="v">${s === null ? '&mdash;' : s.toFixed(1)}</div>
        <div class="bar"><i style="width:${pct}%"></i></div>
      </div>`;
    }).join('');

    const searches = dr
      ? (dr.searches < 0 ? 'not reported' : `${dr.searches} search${dr.searches === 1 ? '' : 'es'}`)
      : '';
    const meta = dr
      ? `<span class="peers">${esc(dr.model)} &middot; ${dr.words}w &middot;
         ${fmtMs(dr.latency_ms)} &middot; ${searches}</span>`
      : `<span class="peers" style="color:#d05a5a">${esc(lane.failure || 'no draft')}</span>`;

    return `<div class="lane">
      <h4><span class="tag ${esc(lane.source)}">${esc(lane.source)}</span>
        <span class="peers"><b>${esc(lane.auth)}</b></span>
        ${lane.source === d.winner ? '<span class="peers">&#9733; winner</span>' : ''}
        ${meta}</h4>
      <div class="rounds">${boxes}</div>
    </div>`;
  }).join('');

  $('flow').innerHTML = brainBanner(d) + `
    <div class="panel">
      <p class="meta" style="margin:0 0 4px"><b>${esc(d.topic)}</b></p>
      <p class="meta" style="margin:0">judge ${esc(d.judge)} &middot;
         winner ${esc(d.winner || 'none')} &middot;
         ${d.complete ? 'every cloud answered' : 'incomplete'}</p>
    </div>` + lanes;
}

function renderReviews() {
  const d = flowData;
  $('reviews').innerHTML = brainBanner(d) + d.reviews.map(r => {
    const entries = r.entries.map(e => {
      const dims = e.scores.map(s => `
        <span class="peers">${esc(s.dimension)}</span>
        <span class="bar"><i style="width:${100 * s.score / 5}%"></i></span>
        <span class="peers">${s.score.toFixed(1)}</span>`).join('');
      const crit = e.critique_sent
        ? `<div class="crit"><b>sent back with:</b>\n${esc(e.critique)}</div>`
        : (e.below_pass_mark
            ? `<div class="crit">below the pass mark, but this was the final
               round &mdash; nothing was sent back.</div>`
            : '');
      return `<div class="lane">
        <h4><span class="tag ${esc(e.source)}">${esc(e.source)}</span>
          <span class="v">${e.total.toFixed(1)}</span>
          <span class="peers">of ${e.max}</span>
          ${e.below_pass_mark ? '<span class="peers">below the bar</span>' : ''}</h4>
        <div class="dims">${dims}</div>
        ${e.notes ? `<p class="meta">${esc(e.notes)}</p>` : ''}
        ${crit}</div>`;
    }).join('');
    return `<div class="panel" style="margin-bottom:14px">
      <p class="meta" style="margin:0 0 8px"><b>round ${r.round}</b> &middot;
        judge ${esc(r.judge)} &middot; ${r.blind ? 'blind' : 'NOT BLIND'} &middot;
        winner ${esc(r.winner || 'none')} &middot; ${fmtMs(r.elapsed_ms)}</p>
      ${r.warnings.map(w => `<p class="meta" style="color:var(--accent)">${esc(w)}</p>`).join('')}
      ${entries}
      ${r.rationale ? `<p class="meta">${esc(r.rationale)}</p>` : ''}
    </div>`;
  }).join('');
}

function renderWire() {
  const d = flowData;
  const rows = [];
  d.lanes.forEach(lane => lane.calls.forEach(c => rows.push({lane: lane.source, ...c})));
  rows.sort((a, b) => a.offset_ms - b.offset_ms);

  if (!rows.length) {
    $('wire').innerHTML = `<div class="panel"><p class="meta" style="margin:0">
      No HTTP calls were made &mdash; every participant answered in process.
      There is nothing to prove here and this says so rather than drawing an
      empty grid.</p></div>`;
    return;
  }

  $('wire').innerHTML = `<div class="panel"><table class="wire">
    <thead><tr><th>at</th><th>leg</th><th></th><th>host</th><th>code</th>
      <th>took</th><th>back</th></tr></thead>
    <tbody>${rows.map(c => `
      <tr class="${c.ok ? '' : 'bad'}">
        <td>+${Math.round(c.offset_ms)}ms</td>
        <td><span class="tag ${esc(c.lane)}">${esc(c.lane)}</span></td>
        <td><span class="phase">${PHASE[c.phase] || '?'}</span></td>
        <td>${esc(c.host)}${esc(c.path)}
          ${c.request_id ? `<div class="rid">id ${esc(c.request_id)}</div>` : ''}
          ${!c.ok && c.detail ? `<div class="rid">${esc(c.detail)}</div>` : ''}</td>
        <td>${c.status === null ? '-' : c.status}</td>
        <td>${fmtMs(c.elapsed_ms)}</td>
        <td>${c.bytes === null ? '-' : c.bytes + 'B'}</td>
      </tr>`).join('')}</tbody></table>
    <p class="meta" style="margin:10px 0 0">K credential &middot; D agent-card
      discovery &middot; I A2A invocation. Ids are the provider's own.</p>
  </div>`;
}


// --------------------------------------------------------------------------
// Live telemetry and the event trace
// --------------------------------------------------------------------------
//
// Both panels follow frontend/src/Telemetry.jsx and EventTrace.jsx in
// ~/way-back-home/level_3_new, and three of that design's decisions are carried
// over deliberately:
//
//   * Sparklines, not gauges. None of these numbers has a natural maximum, so a
//     filled meter would have to invent a ceiling. Each line is scaled to its
//     own peak and shows shape only; the absolute value is the figure beside it.
//   * State reads as a word as well as a colour, so it survives a colourblind
//     viewer and a monochrome projector.
//   * A live/idle header, so a panel of zeroes reads as "not started" rather
//     than "broken".
//
// And one number that only exists because of that design: `net` is this
// browser's round trip to the master, measured against an endpoint that touches
// nothing. Every latency on this page is measured from here, so without it a
// 400ms reading cannot be told apart from a 300ms leg behind a 100ms link.

const HISTORY = 40;           // ~40 samples at 1Hz
const EVT_MAX = 400;

let netHistory = [];
let netMs = null;
let events = [];
let evtOpen = false;
let sse = null;
let runLive = false;

const KIND_COLOR = {
  run: '#7aa2f7', leg: '#00c176', wire: '#8a8f98',
  judge: '#e0af68', round: '#e0af68', error: '#d05a5a',
};

function sparkline(series, color) {
  const w = 84, h = 18;
  if (series.length < 2) return `<svg width="${w}" height="${h}"></svg>`;
  // Scaled to this series' own peak: shape only, never an implied ceiling.
  const peak = Math.max(...series, 1);
  const step = w / (HISTORY - 1);
  const pts = series.map((v, i) => {
    const x = (i + (HISTORY - series.length)) * step;
    const y = h - (v / peak) * (h - 2) - 1;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  return `<svg width="${w}" height="${h}"><polyline points="${pts}" fill="none"
    stroke="${color}" stroke-width="2" stroke-linejoin="round"
    stroke-linecap="round" opacity="0.85"/></svg>`;
}

// Thresholds are about this link, not about the mesh: past ~200ms the transport
// is a real part of what every other number on this page reads as.
function netColor(ms) {
  if (ms == null) return 'var(--dim)';
  if (ms > 200) return '#d05a5a';
  if (ms > 80) return '#e0af68';
  return '#00c176';
}

function tile(label, value, unit, detail, color, spark) {
  return `<div class="tile">
    <div class="k"><span>${esc(label)}</span>${spark || ''}</div>
    <div class="v" style="color:${color || 'var(--text)'}">${value}<span class="u">${esc(unit || '')}</span></div>
    ${detail ? `<div class="d">${detail}</div>` : ''}
  </div>`;
}

function renderTelemetry() {
  const legs = events.filter(e => e.kind === 'leg');
  const answered = legs.filter(e => (e.text || '').includes('answered'));
  const wire = events.filter(e => e.kind === 'wire');
  const errors = events.filter(e => e.kind === 'error');
  const judged = events.filter(e => e.kind === 'judge');

  // Model time with the transport removed. The leg latency is what the
  // coordinator measured end to end; the credential and discovery calls on the
  // same leg are what it spent getting permission to ask. The difference is the
  // model, and it is the number worth arguing about.
  const overhead = wire.filter(e => e.phase === 'credential' || e.phase === 'discovery')
                       .reduce((a, e) => a + (e.elapsed_ms || 0), 0);
  const legMs = answered.reduce((a, e) => a + (e.latency_ms || 0), 0);
  const think = answered.length ? Math.max(0, legMs - overhead) : null;

  const searches = answered.reduce(
    (a, e) => a + (typeof e.searches === 'number' && e.searches > 0 ? e.searches : 0), 0);

  $('telem').innerHTML =
    tile('Net', netMs == null ? '--' : netMs, 'ms',
         'browser &rarr; master, touches nothing',
         netColor(netMs), sparkline(netHistory, '#7aa2f7')) +
    tile('Legs answered', `${answered.length}`, `of ${new Set(legs.map(e => e.cloud)).size || 0}`,
         errors.length ? `${errors.length} failed` : '', answered.length ? '#00c176' : null) +
    tile('Think', think == null ? '--' : Math.round(think), 'ms',
         `leg total minus ${Math.round(overhead)}ms auth + discovery`,
         'var(--text)') +
    tile('Wire calls', `${wire.length}`, '',
         `${wire.filter(e => e.request_id).length} with a provider id`) +
    tile('Searches', `${searches}`, '',
         answered.length && !searches ? 'none &mdash; drafts written from recall' : '') +
    tile('Rounds judged', `${judged.length}`, '',
         judged.length ? esc(judged[judged.length - 1].winner || '') : '');

  $('liveState').textContent = runLive ? 'live' : 'idle';
  $('liveState').style.color = runLive ? '#00c176' : 'var(--dim)';
}

function renderEvents() {
  $('evtCount').textContent = events.length;
  const recent = evtOpen ? events.slice(-120) : events.slice(-3);
  const box = $('evt');
  box.classList.toggle('closed', !evtOpen);
  box.innerHTML = recent.length
    ? recent.map(e => `<div>
        <span class="ts">${esc((e.t || '').slice(11, 19))}</span>
        <span class="kd" style="color:${KIND_COLOR[e.kind] || 'var(--dim)'}">${esc(e.kind)}</span>
        <span class="tx">${esc(e.text)}</span></div>`).join('')
    : '<div class="ts">no events yet</div>';
  // Pinned to the newest line, the way a log viewer should be.
  if (evtOpen) box.scrollTop = box.scrollHeight;
}


// --------------------------------------------------------------------------
// The live topology
// --------------------------------------------------------------------------
//
// Master on the left, one node per cloud on the right, the judge below. Every
// dot that travels an edge is one round trip that actually happened, drawn when
// the coordinator finished it -- `coordinator/trace.py` calls back per step now
// precisely so this can be true. Before that the wire events arrived in a burst
// after each leg had already finished, and animating those would have been a
// replay wearing the costume of a live view.
//
// Nothing here is on a timer or an easing curve pretending to be progress. A
// node is busy because a leg was dialled and has not answered; an edge is hot
// because a call is in flight. When nothing is happening it is still, which is
// the honest rendering of a mesh that is idle.

const VIZ = {w: 720, h: 300, mx: 130, my: 150, cx: 560};
const PHASE_COLOR = {credential: '#7aa2f7', discovery: '#e0af68', invoke: '#00c176'};
const vizNodes = new Map();   // cloud -> {state, meta, round}
let vizPulses = [];           // {path, t, color, dur}
let vizFrame = null;
let vizJudge = {state: 'idle', text: ''};

function vizLayout() {
  const clouds = [...vizNodes.keys()];
  if (!clouds.length) return [];
  const span = Math.min(200, 74 * (clouds.length - 1));
  const top = VIZ.my - span / 2;
  return clouds.map((cloud, i) => ({
    cloud,
    x: VIZ.cx,
    y: clouds.length === 1 ? VIZ.my : top + (span / (clouds.length - 1)) * i,
  }));
}

function edgePath(to) {
  const midX = (VIZ.mx + to.x) / 2;
  return `M ${VIZ.mx + 46} ${VIZ.my} C ${midX} ${VIZ.my}, ${midX} ${to.y}, ${to.x - 46} ${to.y}`;
}

function drawViz() {
  const svg = $('viz');
  if (!svg) return;
  const nodes = vizLayout();

  if (!nodes.length) {
    svg.innerHTML = `<text x="${VIZ.w / 2}" y="${VIZ.my}" text-anchor="middle"
      class="nmeta">no run yet &mdash; send a brief and this fills in</text>`;
    return;
  }

  const edges = nodes.map(n => {
    const st = vizNodes.get(n.cloud);
    const hot = st.state === 'busy';
    return `<path id="edge-${esc(n.cloud)}" class="edge ${hot ? 'hot' : ''}"
              d="${edgePath(n)}"/>
            <text class="elabel" x="${(VIZ.mx + n.x) / 2}" y="${(VIZ.my + n.y) / 2 - 6}"
              text-anchor="middle">${esc(st.auth || '')}</text>`;
  }).join('');

  const cloudNodes = nodes.map(n => {
    const st = vizNodes.get(n.cloud);
    const cls = st.state === 'busy' ? 'busy' : (st.state === 'failed' ? 'bad'
              : (st.state === 'answered' ? 'ok' : ''));
    // A ring only while genuinely waiting on that cloud.
    const ring = st.state === 'busy'
      ? `<circle class="ring" cx="${n.x}" cy="${n.y}" r="30">
           <animate attributeName="r" values="26;40" dur="1.4s" repeatCount="indefinite"/>
           <animate attributeName="opacity" values="0.55;0" dur="1.4s" repeatCount="indefinite"/>
         </circle>` : '';
    return `${ring}
      <circle class="node ${cls}" cx="${n.x}" cy="${n.y}" r="26"/>
      <text class="nlabel" x="${n.x}" y="${n.y + 4}" text-anchor="middle">${esc(n.cloud)}</text>
      <text class="nmeta" x="${n.x + 36}" y="${n.y - 2}">${esc(st.meta || '')}</text>
      <text class="nmeta" x="${n.x + 36}" y="${n.y + 12}">${esc(st.sub || '')}</text>`;
  }).join('');

  const jcls = vizJudge.state === 'busy' ? 'busy' : (vizJudge.state === 'done' ? 'ok' : '');
  const master = `
    <circle class="node ${runLive ? 'busy' : ''}" cx="${VIZ.mx}" cy="${VIZ.my}" r="46"/>
    <text class="nlabel" x="${VIZ.mx}" y="${VIZ.my - 2}" text-anchor="middle">master</text>
    <text class="nmeta" x="${VIZ.mx}" y="${VIZ.my + 14}" text-anchor="middle">gcp</text>
    <path class="edge ${vizJudge.state === 'busy' ? 'hot' : ''}"
      d="M ${VIZ.mx} ${VIZ.my + 46} L ${VIZ.mx} ${VIZ.my + 92}"/>
    <circle class="node ${jcls}" cx="${VIZ.mx}" cy="${VIZ.my + 112}" r="20"/>
    <text class="nlabel" x="${VIZ.mx}" y="${VIZ.my + 116}" text-anchor="middle">judge</text>
    <text class="nmeta" x="${VIZ.mx + 30}" y="${VIZ.my + 116}">${esc(vizJudge.text)}</text>`;

  svg.innerHTML = edges + master + cloudNodes + '<g id="vizPulses"></g>';
  paintPulses();
}

// Pulses are positioned by hand along the path rather than with animateMotion,
// so a dot's colour and lifetime can carry the phase and so they stop dead when
// nothing is in flight instead of looping forever.
function paintPulses() {
  const layer = document.getElementById('vizPulses');
  if (!layer) return;
  const now = performance.now();
  vizPulses = vizPulses.filter(p => now - p.start < p.dur);
  layer.innerHTML = vizPulses.map(p => {
    const el = document.getElementById('edge-' + p.cloud);
    if (!el) return '';
    const frac = Math.min(1, (now - p.start) / p.dur);
    const len = el.getTotalLength();
    const at = el.getPointAtLength(p.back ? len * (1 - frac) : len * frac);
    return `<circle cx="${at.x.toFixed(1)}" cy="${at.y.toFixed(1)}" r="4"
             fill="${p.color}" opacity="${(1 - frac * 0.35).toFixed(2)}"/>`;
  }).join('');

  if (vizPulses.length) {
    vizFrame = requestAnimationFrame(paintPulses);
  } else {
    vizFrame = null;
  }
}

function vizPulse(cloud, phase, ok, back) {
  vizPulses.push({
    cloud, start: performance.now(), dur: 900, back: !!back,
    color: ok === false ? '#d05a5a' : (PHASE_COLOR[phase] || '#8a8f98'),
  });
  if (!vizFrame) vizFrame = requestAnimationFrame(paintPulses);
}

function vizEvent(e) {
  if (e.kind === 'run') {
    vizNodes.clear();
    vizJudge = {state: 'idle', text: ''};
    $('vizRound').textContent = 'round 1';
    $('vizRound').style.color = '#e0af68';
  }
  if (e.kind === 'leg' && e.cloud) {
    const st = vizNodes.get(e.cloud) || {};
    st.auth = e.auth || st.auth;
    if ((e.text || '').includes('dialling')) {
      st.state = 'busy';
      st.meta = 'dialling';
      st.sub = '';
    } else if ((e.text || '').includes('answered')) {
      st.state = 'answered';
      st.meta = `${e.words}w  ${Math.round(e.latency_ms || 0)}ms`;
      st.sub = e.searches >= 0 ? `${e.searches} search(es)` : '';
      // The reply, travelling back. Drawn only for a leg that really answered.
      vizPulse(e.cloud, 'invoke', true, true);
    }
    vizNodes.set(e.cloud, st);
  }
  if (e.kind === 'wire' && e.cloud) {
    if (!vizNodes.has(e.cloud)) vizNodes.set(e.cloud, {state: 'busy', meta: ''});
    vizPulse(e.cloud, e.phase, e.ok);
  }
  if (e.kind === 'error' && e.cloud) {
    const st = vizNodes.get(e.cloud) || {};
    st.state = 'failed';
    st.meta = 'failed';
    st.sub = (e.text || '').slice(0, 40);
    vizNodes.set(e.cloud, st);
  }
  if (e.kind === 'round') {
    const n = e.round || '?';
    $('vizRound').textContent = 'round ' + n;
    // A cloud being sent back is busy again; one that passed keeps its result.
    // Read from the event's own field, never parsed out of its sentence.
    (e.revising || []).forEach(cloud => {
      const st = vizNodes.get(cloud);
      if (st) { st.state = 'busy'; st.meta = 'rewriting'; vizNodes.set(cloud, st); }
    });
  }
  if (e.kind === 'judge') {
    vizJudge = {state: 'done', text: `${e.winner || 'no winner'}`};
    $('vizRound').style.color = 'var(--dim)';
  }
  drawViz();
}

function pushEvent(e) {
  events.push(e);
  if (events.length > EVT_MAX) events = events.slice(-EVT_MAX);
  if (e.kind === 'run') runLive = true;
  if (e.kind === 'judge') runLive = false;
  renderEvents();
  renderTelemetry();
  vizEvent(e);
}

function openStream() {
  if (sse) return;
  try {
    sse = new EventSource('api/stream');
    sse.onmessage = (m) => { try { pushEvent(JSON.parse(m.data)); } catch (err) { /* a torn frame is not worth a broken page */ } };
    // EventSource reconnects on its own; this only stops it claiming to be live
    // while it is not.
    sse.onerror = () => { runLive = false; renderTelemetry(); };
  } catch (err) { /* no stream: the panels still render from a completed run */ }
}

// One probe a second. A few dozen bytes, and it keeps the reading current
// without a second timer.
async function samplePing() {
  const started = performance.now();
  try {
    await fetch('api/ping?t=' + started, {cache: 'no-store'});
    netMs = Math.round(performance.now() - started);
  } catch (e) { netMs = null; }
  netHistory = [...netHistory, netMs == null ? 0 : netMs].slice(-HISTORY);
  renderTelemetry();
}

$('evtToggle').onclick = () => { evtOpen = !evtOpen; renderEvents(); };
$('evtClear').onclick = () => { events = []; renderEvents(); renderTelemetry(); };
$('evtSave').onclick = () => {
  const blob = new Blob([JSON.stringify(events, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'research-events.json';
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
};

openStream();
samplePing();
setInterval(samplePing, 1000);
renderTelemetry();
renderEvents();
drawViz();


// --------------------------------------------------------------------------
// Human in the loop
// --------------------------------------------------------------------------
//
// Read one cloud's chain, open what it cited, say what you think. The verdict
// recorded here never overwrites the judge's -- the point is to measure the
// scorer, and the README has carried the gap this closes since the rubric was
// written: nobody had checked that rubric rank correlates with human rank on
// even one set of drafts.
//
// Every source opens through api/source, which only fetches URLs that appear in
// a draft of the run being read. The caller does not choose the target; the
// corpus does. That is what keeps a public, credential-holding master from
// being a fetch-anything proxy.

const CITE_VERDICTS = ['unchecked', 'verified', 'unreachable', 'unrelated', 'fabricated'];
let hitlIndex = 1;
let hitl = null;
const citeState = {};   // url -> verdict chosen by the reviewer

async function loadHitl() {
  try {
    const res = await fetch('api/lineage?n=' + hitlIndex);
    if (res.status === 404) {
      $('hitl').innerHTML = `<div class="panel"><p class="meta" style="margin:0">
        Nothing recorded yet. Send a brief.</p></div>`;
      $('hitlNav').innerHTML = '';
      return;
    }
    hitl = await res.json();
  } catch (e) { $('hitl').textContent = String(e); return; }
  renderHitl();
}

function renderHitl() {
  const d = hitl;
  $('hitlNav').innerHTML = `
    <button id="hPrev">&larr; older</button>
    <button id="hNext" ${hitlIndex <= 1 ? 'disabled' : ''}>newer &rarr;</button>
    <span class="meta"><b>${esc(d.run_id)}</b> &middot; judge ${esc(d.judge)}
      chose <b>${esc(d.winner || 'none')}</b> &middot; ${d.rounds} round(s)</span>`;
  $('hPrev').onclick = () => { hitlIndex++; loadHitl(); };
  $('hNext').onclick = () => { hitlIndex = Math.max(1, hitlIndex - 1); loadHitl(); };

  const chains = d.chains.map(chain => {
    const steps = chain.steps.map((s, i) => {
      const prev = i > 0 ? chain.steps[i - 1].total : null;
      const better = prev !== null && s.total !== null && s.total > prev;
      const dims = s.scores.map(x =>
        `${esc(x.dimension)} ${x.score.toFixed(1)}`).join(' &middot; ');
      const cites = s.citations.length
        ? `<div class="cites">${s.citations.map(u => citeRow(u)).join('')}</div>`
        : `<p class="meta" style="margin:6px 0 0">no linked sources${
             s.searches === 0 ? ' &mdash; and it made no searches' : ''}</p>`;
      return `<div class="step ${better ? 'up' : ''}">
        <h5>round ${s.round}
          <span class="peers">${s.total === null ? '' : s.total.toFixed(1) + '/25'}</span>
          <span class="peers">${s.words}w &middot; ${
            s.searches < 0 ? 'searches not reported' : s.searches + ' search(es)'}</span>
        </h5>
        <p class="meta" style="margin:0 0 6px">${dims}</p>
        ${s.critique ? `<div class="crit"><b>the judge sent this back with:</b>\n${esc(s.critique)}</div>` : ''}
        <details style="margin-top:8px"><summary class="meta">read the draft</summary>
          <div class="draftbody">${esc(s.body)}</div></details>
        ${cites}
      </div>`;
    }).join('');

    return `<div class="panel hitl" style="margin-bottom:14px">
      <h4 style="margin:0 0 10px"><span class="tag ${esc(chain.source)}">${esc(chain.source)}</span>
        <span class="peers">${esc(chain.auth)}</span></h4>
      ${steps}
      <div class="row2">
        <label style="margin:0">your rank <input type="number" min="1" max="9"
          id="rank-${esc(chain.source)}" placeholder="1"></label>
        <input placeholder="why?" id="note-${esc(chain.source)}" style="flex:1;min-width:160px">
      </div>
    </div>`;
  }).join('');

  const already = (d.feedback || []).length;
  $('hitl').innerHTML = chains + `
    <div class="panel hitl">
      <div class="row2">
        <label style="margin:0">you would pick
          <select id="hWinner">
            <option value="">&mdash;</option>
            ${d.chains.map(c => `<option value="${esc(c.source)}">${esc(c.source)}</option>`).join('')}
          </select></label>
        <input id="hReviewer" placeholder="your name" style="width:140px">
        <input id="hNote" placeholder="overall note" style="flex:1;min-width:160px">
        <button id="hSave" style="margin:0">record</button>
      </div>
      <p class="meta" id="hStatus" style="margin:8px 0 0">${
        already ? `${already} review(s) already recorded for this run` : ''}</p>
    </div>`;

  $('hSave').onclick = saveFeedback;
}

function citeRow(url) {
  const id = 'c' + Math.abs([...url].reduce((a, c) => a * 31 + c.charCodeAt(0) | 0, 7));
  const chosen = citeState[url] || 'unchecked';
  return `<div class="cite">
    <span class="st" id="${id}-st">&mdash;</span>
    <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>
    <button style="margin:0;padding:2px 8px" onclick="openSource('${esc(url)}','${id}')">open</button>
    <select onchange="citeState['${esc(url)}']=this.value">
      ${CITE_VERDICTS.map(v =>
        `<option value="${v}" ${v === chosen ? 'selected' : ''}>${v}</option>`).join('')}
    </select>
  </div><div id="${id}-body"></div>`;
}

// Fetched through the master rather than by the browser, so a citation that is
// dead *from the mesh's own network* is reported as such -- which is the case
// that matters when asking whether an agent could have read what it cited.
async function openSource(url, id) {
  const status = document.getElementById(id + '-st');
  const body = document.getElementById(id + '-body');
  status.textContent = '...';
  try {
    const r = await fetch(`api/source?n=${hitlIndex}&url=` + encodeURIComponent(url));
    const d = await r.json();
    status.textContent = d.ok ? String(d.status) : (d.status || 'dead');
    status.style.color = d.ok ? '#00c176' : '#d05a5a';
    body.innerHTML = `<div class="srcbody"><b>${esc(d.title || d.reason || '')}</b>
      ${d.final_url ? `<br><span class="meta">redirected to ${esc(d.final_url)}</span>` : ''}
      <br>${esc((d.excerpt || '').slice(0, 1200))}</div>`;
    // A dead citation is the finding, so it pre-selects the verdict rather than
    // making the reviewer type it. Still theirs to override: unreachable is not
    // fabricated, and only a person can tell those apart.
    if (!d.ok && !citeState[url]) citeState[url] = 'unreachable';
  } catch (e) {
    status.textContent = 'error';
    body.innerHTML = `<div class="srcbody">${esc(String(e))}</div>`;
  }
}

async function saveFeedback() {
  const drafts = hitl.chains.map(c => {
    const rank = parseInt(($('rank-' + c.source) || {}).value, 10);
    const cites = [...new Set(c.steps.flatMap(s => s.citations))]
      .filter(u => citeState[u] && citeState[u] !== 'unchecked')
      .map(u => ({url: u, verdict: citeState[u]}));
    return {
      source: c.source,
      rank: Number.isFinite(rank) ? rank : null,
      note: ($('note-' + c.source) || {}).value || '',
      citations: cites,
    };
  });
  const body = {
    run_id: hitl.run_id,
    reviewer: $('hReviewer').value || 'anonymous',
    winner: $('hWinner').value || null,
    note: $('hNote').value || '',
    drafts,
  };
  $('hStatus').textContent = 'recording…';
  try {
    const r = await fetch('api/feedback', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    $('hStatus').textContent = d.recorded
      ? `recorded. The judge's verdict is unchanged — that is the point.`
      : `not recorded: ${d.error}`;
  } catch (e) { $('hStatus').textContent = String(e); }
}

document.querySelectorAll('.tabs button').forEach(btn => {
  btn.onclick = async () => {
    document.querySelectorAll('.tabs button').forEach(b =>
      b.classList.toggle('on', b === btn));
    ['run', 'last', 'live', 'flow', 'reviews', 'wire', 'review', 'audit'].forEach(tab => {
      $('tab-' + tab).hidden = btn.dataset.tab !== tab;
    });
    if (['flow', 'reviews', 'wire'].includes(btn.dataset.tab)) { await loadFlow(); }
    if (btn.dataset.tab === 'review') { await loadHitl(); }
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
