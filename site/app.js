/* Fantasy Edge front end: reads data.json (or data.enc.json) built by the pipeline and renders every tab. */
"use strict";

let D, P, ME;
const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const f1 = (x) => (x == null || isNaN(x) ? "–" : Number(x).toFixed(1));
const f0 = (x) => (x == null || isNaN(x) ? "–" : Math.round(x).toLocaleString());
const pct = (x) => (x == null || isNaN(x) ? "–" : Math.round(x * 100) + "%");
const sign = (x, d = 1) => (x == null ? "–" : (x > 0 ? "+" : "") + Number(x).toFixed(d));
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

/* ---------- loading & password gate ---------- */
async function boot() {
  const theme = store.get("theme");
  if (theme) document.documentElement.dataset.theme = theme;
  if (location.hash === "#sync") return syncPage();
  let res = await fetch("data/data.json", { cache: "no-store" });
  if (res.ok) return start(await res.json());
  res = await fetch("data/data.enc.json", { cache: "no-store" });
  if (!res.ok) {
    document.body.innerHTML = '<div class="gate"><h1>🏈 Fantasy Edge</h1><p class="muted">No data yet. The first GitHub Actions run will create it.</p></div>';
    return;
  }
  const blob = await res.json();
  const saved = store.get("pw");
  if (saved) {
    try { return start(await decrypt(blob, saved)); } catch { /* password changed */ }
  }
  $("#gate").hidden = false;
  $("#gate-form").onsubmit = async (e) => {
    e.preventDefault();
    const pw = $("#pw").value;
    try {
      const data = await decrypt(blob, pw);
      store.set("pw", pw);
      $("#gate").hidden = true;
      start(data);
    } catch {
      $("#gate-err").textContent = "Wrong password.";
    }
  };
}

async function decrypt(b, pw) {
  const u = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt: u(b.salt), iterations: b.iter, hash: "SHA-256" },
    base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: u(b.iv) }, key, u(b.ct));
  const stream = new Blob([plain]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

function start(data) {
  D = data; P = D.players; ME = D.meta.me;
  $("#app").hidden = false;
  const m = D.meta;
  const when = new Date(m.generated);
  $("#sub").textContent = `${m.league_name || "NFL"} · ${m.season} Week ${m.week} · updated ${when.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" })}`;
  $("#theme").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store.set("theme", next);
  };
  document.querySelectorAll("#tabs button").forEach((b) => (b.onclick = () => show(b.dataset.v)));
  banners();
  const views = { week: renderWeek, trades: renderTrades, waivers: renderWaivers, analyzer: renderAnalyzer,
    playoffs: renderPlayoffs, league: renderLeague, players: renderPlayers, playbook: renderPlaybook, connect: renderConnect };
  for (const [k, fn] of Object.entries(views)) {
    const fail = (e) => { $("#v-" + k).innerHTML = `<div class="banner bad">This tab failed to render: ${esc(e.message)}</div>`; console.error(e); };
    try { Promise.resolve(fn($("#v-" + k))).catch(fail); } catch (e) { fail(e); }
  }
  const src = Object.entries(m.sources || {}).map(([k, v]) => `${k}: ${v}`).join(" · ");
  $("#foot").innerHTML = `Sources: ${esc(src)}<br>Projections blend ESPN, Sleeper, FantasyPros and a usage model (nflverse). Trade values: FantasyCalc. Not affiliated with any of them.`;
  const hash = location.hash.slice(1);
  if (hash && $("#v-" + hash)) show(hash);
}

function show(v) {
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
  document.querySelectorAll("section.view").forEach((s) => s.classList.toggle("on", s.id === "v-" + v));
  history.replaceState(null, "", "#" + v);
  window.scrollTo({ top: 0 });
}

function banners() {
  const m = D.meta, out = [];
  if (m.league_error) out.push(`<div class="banner bad"><b>League didn't load.</b> ${esc(m.league_error)}</div>`);
  if (m.mode === "public") out.push(`<div class="banner info"><b>No ESPN league connected yet.</b> Showing league-wide rankings, values and pickups. <a href="#connect" onclick="show('connect');return false">Connect your ESPN league</a> to get your lineup, trade partners and pitches.</div>`);
  if (m.league_synced) {
    const days = (Date.now() - new Date(m.league_synced)) / 864e5;
    if (days > 3) out.push(`<div class="banner warn">Your ESPN rosters were last synced ${Math.floor(days)} days ago. <a href="#connect" onclick="show('connect');return false">Re-sync</a> after trades, pickups or lineup changes.</div>`);
  }
  const down = Object.entries(m.sources || {}).filter(([, v]) => /unavailable|fail/i.test(v));
  if (down.length) out.push(`<div class="banner warn">Some sources didn't respond this run, so projections lean on the others: ${down.map(([k]) => esc(k)).join(", ")}.</div>`);
  $("#banners").innerHTML = out.join("");
}

/* ---------- small renderers ---------- */
function inj(r) { return r && r.injury ? `<span class="inj ${esc(r.injury)}" title="${esc(r.injury_detail || "")}">${esc(r.injury)}</span>` : ""; }
function teamName(id) { const t = (D.teams || []).find((x) => x.id === id); return t ? t.name : ""; }
function ownerName(id) { const p = (D.profiles || {})[id]; return p ? p.owner : teamName(id); }
function opp(r) {
  if (!r) return "";
  if (r.bye) return "BYE";
  if (!r.opp) return "";
  return (r.home ? "vs " : "@ ") + r.opp;
}
function kick(r) {
  if (!r || !r.kickoff) return "";
  const d = new Date(r.kickoff);
  return d.toLocaleString([], { weekday: "short", hour: "numeric", minute: "2-digit" });
}
function pn(pid, extra = "") {
  const r = P[pid];
  if (!r) return `<span class="muted">${esc(pid || "—")}</span>`;
  return `<a href="#" class="pname" data-pid="${esc(pid)}">${esc(r.name)}</a>${inj(r)} <span class="pmeta">${esc(r.pos)} ${esc(r.team || "")}${extra}</span>`;
}
function range(r, max = 30) {
  if (!r || r.proj == null) return "";
  const lo = Math.min(r.floor ?? 0, max), hi = Math.min(r.ceil ?? 0, max), mid = Math.min(r.proj, max);
  return `<div class="range" title="Floor ${f1(r.floor)} · Ceiling ${f1(r.ceil)}"><span style="left:${(lo / max) * 100}%;width:${((hi - lo) / max) * 100}%"></span><i style="left:${(mid / max) * 100}%"></i></div>`;
}
function pts(r) {
  if (!r) return "–";
  if (r.locked && r.actual != null) return `<b>${f1(r.actual)}</b> <span class="pmeta">final</span>`;
  return `<b>${f1(r.proj)}</b>`;
}
function notesList(r, max = 4) {
  const n = (r && r.notes) || [];
  return n.length ? `<ul class="notes">${n.slice(0, max).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : "";
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-pid]");
  if (a) { e.preventDefault(); openPlayer(a.dataset.pid); }
});

/* ---------- This Week ---------- */
function renderWeek(el) {
  if (!D.lineup) return renderRankings(el);
  const L = D.lineup;
  let h = "";
  if (L.matchup) {
    const wp = L.matchup.win_prob;
    h += `<div class="card"><div class="matchup">
      <div><div class="muted small">You</div><div class="score">${f1(L.matchup.me)}</div></div>
      <div class="muted">vs</div>
      <div><div class="muted small">${esc(ownerName(D.matchup_opp))}</div><div class="score">${f1(L.matchup.opp)}</div></div></div>
      <div class="bar"><div style="width:${wp * 100}%"></div></div>
      <div class="row small" style="margin-top:6px"><span><b>${pct(wp)}</b> to win with the lineup below</span><span class="spacer"></span>
      <span class="muted">${wp < 0.4 ? "Underdog: lean toward high-ceiling options." : wp > 0.65 ? "Favorite: lean toward safe floors." : "Toss-up: start the best projections."}</span></div></div>`;
  }
  if (L.alerts.length) h += `<div class="banner bad"><b>Fix before kickoff</b><ul>${L.alerts.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></div>`;
  h += `<div class="card"><h3>Moves to make in ESPN</h3>`;
  if (!L.moves.length) h += `<p class="muted small" style="margin:0">Your ESPN lineup already matches the optimal one. ✅</p>`;
  for (const m of L.moves) {
    h += `<div class="move">${m.start ? `<span class="tag in">START</span> ${pn(m.start)}` : ""}
      ${m.sit ? `<span class="tag out">SIT</span> ${pn(m.sit)}` : ""}<span class="spacer"></span>
      ${m.gain ? `<span class="chip good">${sign(m.gain)} pts</span>` : ""}</div>`;
  }
  h += `</div>`;
  h += `<h2>Optimal lineup <span class="muted small">projected ${f1(L.total)} ± ${f1(L.sd)}</span></h2><div class="card"><div class="table-wrap"><table>
    <tr><th>Slot</th><th>Player</th><th class="num">Proj</th><th class="hide-sm">Range</th></tr>`;
  for (const s of L.lineup) {
    const r = P[s.pid];
    h += `<tr><td class="slot">${esc(s.slot)}</td><td>${r ? pn(s.pid, ` · ${opp(r)} ${kick(r)}`) + notesList(r, 3) : '<span class="muted">Empty: pick someone up</span>'}</td>
      <td class="num">${pts(r)}</td><td class="hide-sm" style="width:110px">${range(r)}</td></tr>`;
  }
  h += `</table></div></div>`;
  h += `<h2>Bench</h2><div class="card"><div class="table-wrap"><table>`;
  for (const pid of L.bench) {
    const r = P[pid];
    if (!r) continue;
    h += `<tr><td>${pn(pid, ` · ${opp(r)}`)}${notesList(r, 2)}</td><td class="num">${pts(r)}</td><td class="hide-sm" style="width:110px">${range(r)}</td></tr>`;
  }
  h += `</table></div></div>`;
  if (L.close.length || L.tips.length) {
    h += `<h2>Close calls</h2><div class="card">`;
    for (const c of L.close) {
      h += `<div class="move"><span>${pn(c.start)} over ${pn(c.over)}</span><span class="spacer"></span><span class="chip acc">${pct(c.prob)} to outscore</span></div>`;
      if (c.tip) h += `<p class="small muted" style="margin:2px 0 6px">${esc(c.tip)}</p>`;
    }
    for (const t of L.tips) h += `<p class="small" style="margin:8px 0 0">💡 ${esc(t)}</p>`;
    h += `</div>`;
  }
  el.innerHTML = h;
}

function renderRankings(el) {
  const posList = ["QB", "RB", "WR", "TE", "K", "DST"];
  let cur = store.get("rankpos") || "RB";
  const draw = () => {
    const rows = Object.values(P).filter((r) => r.pos === cur && !r.bye && (r.proj || 0) > 0).sort((a, b) => b.proj - a.proj).slice(0, 60);
    el.innerHTML = `<h2>Week ${D.meta.week} start/sit rankings</h2>
      <div class="seg" id="rk">${posList.map((p) => `<button data-p="${p}" class="${p === cur ? "on" : ""}">${p}</button>`).join("")}</div>
      <div class="card" style="margin-top:10px"><div class="table-wrap"><table>
      <tr><th>#</th><th>Player</th><th class="num">Proj</th><th class="hide-sm">Range</th></tr>
      ${rows.map((r, i) => `<tr><td class="mono">${i + 1}</td><td>${pn(r.pid, ` · ${opp(r)}`)}${notesList(r, 3)}</td><td class="num">${pts(r)}</td><td class="hide-sm" style="width:110px">${range(r)}</td></tr>`).join("")}
      </table></div></div>`;
    el.querySelectorAll("#rk button").forEach((b) => (b.onclick = () => { cur = b.dataset.p; store.set("rankpos", cur); draw(); }));
  };
  draw();
}

/* ---------- Trades ---------- */
function sideHtml(title, pids) {
  return `<div class="side"><h4>${esc(title)}</h4>${pids.map((p) => {
    const r = P[p] || {};
    return `<div class="p"><span>${pn(p)}</span><span class="mono small muted">${f0(r.fc_value)}</span></div>`;
  }).join("")}</div>`;
}
function renderTrades(el) {
  let h = "";
  if (D.sell_high && D.sell_high.length) {
    h += `<h2>Sell-high candidates on your roster</h2><div class="card"><div class="table-wrap"><table>
      <tr><th>Player</th><th class="num">Market</th><th class="num hide-sm">Our model</th><th>Why sell</th></tr>
      ${D.sell_high.map((s) => { const r = P[s.pid] || {}; return `<tr><td>${pn(s.pid)}</td><td class="num">${f0(r.fc_value)}</td><td class="num hide-sm">${f0(r.model_value)}</td><td class="small">${s.why.map(esc).join("<br>")}</td></tr>`; }).join("")}
      </table></div></div>`;
  }
  if (D.trades) {
    h += `<h2>Recommended trades</h2><p class="muted small">Each offer is fair by trade-value charts (FantasyCalc), improves your rest-of-season lineup, and doesn't wreck theirs, so it can actually get accepted. Acceptance odds are an estimate.</p>`;
    if (!D.trades.length) h += `<div class="card empty">No trades clear the bar right now. Check back after this week's games.</div>`;
    D.trades.forEach((e, i) => { h += tradeCard(e, i); });
  }
  if (D.buy_low && D.buy_low.length) {
    h += `<h2>Buy-low targets around the league</h2><div class="card"><div class="table-wrap"><table>
      <tr><th>Player</th><th>Manager</th><th class="num">Market</th><th>Why buy</th></tr>
      ${D.buy_low.slice(0, 20).map((s) => { const r = P[s.pid] || {}; return `<tr><td>${pn(s.pid)}</td><td class="small">${esc(ownerName(s.team))}</td><td class="num">${f0(r.fc_value)}</td><td class="small">${s.why.slice(0, 3).map(esc).join("<br>")}</td></tr>`; }).join("")}
      </table></div></div>`;
  }
  if (D.meta.mode === "public") h += marketTables();
  el.innerHTML = h || `<div class="empty">Trade tools need your ESPN league.</div>`;
  el.querySelectorAll("[data-copy]").forEach((b) => (b.onclick = async () => {
    const txt = D.trades[+b.dataset.copy].pitch.message;
    try { await navigator.clipboard.writeText(txt); b.textContent = "Copied ✓"; } catch { b.textContent = "Select & copy"; }
    setTimeout(() => (b.textContent = "Copy message"), 1800);
  }));
}
function tradeCard(e, i) {
  const pr = (D.profiles || {})[e.team] || {};
  const pi = e.pitch || {};
  const moodChip = pr.mood === "desperate" ? "bad" : pr.mood === "contender" ? "good" : "";
  let h = `<div class="card"><div class="trade-head"><div><h3>Trade with ${esc(pr.owner || teamName(e.team))}</h3>
    <div class="small muted">${esc(pr.name || "")} · ${esc(pr.record || "")} · needs ${esc(pr.need || "?")}</div></div><span class="spacer"></span>
    <span class="chip ${moodChip}">${esc(pr.mood || "")}</span>${pr.playoff_odds != null ? `<span class="chip">playoff odds ${pct(pr.playoff_odds)}</span>` : ""}</div>
    <div class="sides">${sideHtml("You give", e.give)}${sideHtml("You get", e.get)}</div>
    <div class="stats">
      <div><b style="color:var(--good)">${sign(e.my_gain)}</b>your pts/wk</div>
      <div><b>${sign(e.their_gain)}</b>their pts/wk</div>
      <div><b>${pct(e.fair)}</b>value to them</div>
      <div><b>${pct(e.p_accept)}</b>est. accept</div>
    </div>`;
  if (e.my_drops.length || e.their_drops.length) {
    h += `<p class="small muted" style="margin:6px 0 0">${e.my_drops.length ? `You'd drop ${e.my_drops.map((p) => esc(P[p]?.name || p)).join(", ")}. ` : ""}${e.their_drops.length ? `They'd drop ${e.their_drops.map((p) => esc(P[p]?.name || p)).join(", ")}.` : ""}</p>`;
  }
  if (e.ladder && e.ladder.length > 1) {
    h += `<div class="ladder">${e.ladder.map((s) => `<div><b>${esc(s.label)}</b><span>${s.give.map((p) => esc(P[p]?.name || p)).join(" + ")} → ${s.get.map((p) => esc(P[p]?.name || p)).join(" + ")}<br><span class="muted">${esc(s.note)}</span></span><span class="mono small">${pct(s.fair)} · ${sign(s.my_gain)}</span></div>`).join("")}</div>`;
  }
  h += `<div class="msg">${esc(pi.message || "")}</div><div class="row"><button class="btn" data-copy="${i}">Copy message</button>
    ${(pi.tactics || []).map((t) => `<span class="chip acc">${esc(t)}</span>`).join("")}</div>`;
  const more = [];
  if ((pi.profile || []).length) more.push(`<b>About this manager</b><ul>${pi.profile.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`);
  if ((pi.timing || []).length) more.push(`<b>Timing</b><ul>${pi.timing.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`);
  if ((pi.talking_points || []).length) more.push(`<b>Your back-pocket points</b><ul>${pi.talking_points.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>`);
  if ((pi.objections || []).length) more.push(`<b>If they say…</b><ul>${pi.objections.map((o) => `<li><i>${esc(o.if)}</i>: ${esc(o.say)}</li>`).join("")}</ul>`);
  if (more.length) h += `<details><summary>Negotiation notes</summary>${more.join("")}</details>`;
  return h + `</div>`;
}
function marketTables() {
  const rows = Object.values(P).filter((r) => ["QB", "RB", "WR", "TE"].includes(r.pos) && r.fc_value > 1500 && r.model_value);
  const under = [...rows].sort((a, b) => b.edge / b.fc_value - a.edge / a.fc_value).slice(0, 15);
  const over = [...rows].sort((a, b) => a.edge / a.fc_value - b.edge / b.fc_value).slice(0, 15);
  const t = (title, list, why) => `<h2>${title}</h2><p class="muted small">${why}</p><div class="card"><div class="table-wrap"><table>
    <tr><th>Player</th><th class="num">Market</th><th class="num">Model</th><th class="num hide-sm">Pts/g</th><th class="num hide-sm">Exp/g</th></tr>
    ${list.map((r) => `<tr><td>${pn(r.pid)}</td><td class="num">${f0(r.fc_value)}</td><td class="num">${f0(r.model_value)}</td><td class="num hide-sm">${f1(r.usage?.ppg)}</td><td class="num hide-sm">${f1(r.usage?.exp_ppg)}</td></tr>`).join("")}
    </table></div></div>`;
  return t("Buy: model likes more than the market", under, "Usage, schedule and projections say these players are worth more than their trade price.")
    + t("Sell: market likes more than the model", over, "Their trade price is running ahead of their projected production.");
}

/* ---------- Waivers ---------- */
function renderWaivers(el) {
  let h = "";
  if (D.waivers) {
    const W = D.waivers;
    h += `<h2>Best pickups for your team</h2><p class="muted small">Ranked by how much each player improves your lineup for the rest of the season, after dropping your least useful player.${D.meta.faab_budget ? " FAAB bids scale with impact and how hot the player is." : ""}</p>`;
    h += `<div class="card"><div class="table-wrap"><table><tr><th>Add</th><th>Drop</th><th class="num">ROS +/wk</th><th class="num hide-sm">This wk</th>${D.meta.faab_budget ? '<th class="num">Bid</th>' : ""}<th class="hide-sm">Why</th></tr>
      ${W.adds.map((w) => `<tr><td>${pn(w.pid)}</td><td>${pn(w.drop)}</td><td class="num">${sign(w.gain_ros)}</td><td class="num hide-sm">${sign(w.gain_week)}</td>${D.meta.faab_budget ? `<td class="num">$${w.faab ?? "–"}</td>` : ""}<td class="small hide-sm">${w.why.map(esc).join("<br>")}</td></tr>`).join("") || '<tr><td colspan="6" class="empty">Nobody on waivers beats your current roster.</td></tr>'}
      </table></div></div>`;
    if (W.streamers.length) {
      h += `<h2>One-week streamers</h2><div class="card"><div class="table-wrap"><table><tr><th>Player</th><th>Matchup</th><th class="num">This wk</th></tr>
        ${W.streamers.map((w) => `<tr><td>${pn(w.pid)}</td><td class="small">${esc(opp(P[w.pid]))}</td><td class="num">${sign(w.gain_week)}</td></tr>`).join("")}</table></div></div>`;
    }
  } else if (D.rising) {
    h += `<h2>Rising players (widely available)</h2><div class="card"><div class="table-wrap"><table><tr><th>Player</th><th class="num">ROS pts/g</th><th class="num hide-sm">Adds 48h</th><th class="hide-sm">Why</th></tr>
      ${D.rising.map((w) => `<tr><td>${pn(w.pid)}</td><td class="num">${f1(P[w.pid]?.ros_ppg)}</td><td class="num hide-sm">${f0(w.adds_48h)}</td><td class="small hide-sm">${(w.why || []).map(esc).join("<br>")}</td></tr>`).join("")}</table></div></div>`;
  }
  el.innerHTML = h || `<div class="empty">No waiver data.</div>`;
}

/* ---------- Offer analyzer (runs the same lineup math as the pipeline, in the browser) ---------- */
const ELIG = { QB: ["QB"], RB: ["RB"], WR: ["WR"], TE: ["TE"], K: ["K"], DST: ["DST"], "RB/WR": ["RB", "WR"], "WR/TE": ["WR", "TE"], FLEX: ["RB", "WR", "TE"], OP: ["QB", "RB", "WR", "TE"] };
const DED = ["QB", "RB", "WR", "TE", "K", "DST"], FLEXES = ["RB/WR", "WR/TE", "FLEX", "OP"];
function strengthTotal(pids) {
  const weeks = D.meta.ros_weeks, slots = D.meta.slots, po = new Set(D.meta.playoff_weeks);
  let total = 0;
  weeks.forEach((w, wi) => {
    const byPos = {};
    for (const p of pids) {
      const r = P[p];
      if (!r || !r.ros) continue;
      let v = (r.ros.find((x) => x[0] === w) || [0, 0])[1];
      if (w === D.meta.week && r.locked) v = 0;
      (byPos[r.pos] = byPos[r.pos] || []).push(v);
    }
    let pts = 0;
    const left = [];
    for (const pos of DED) {
      const arr = (byPos[pos] || []).sort((a, b) => b - a);
      const k = slots[pos] || 0;
      pts += arr.slice(0, k).reduce((a, b) => a + b, 0);
      arr.slice(k).forEach((v) => left.push([pos, v]));
    }
    for (const s of FLEXES) {
      let k = slots[s] || 0;
      if (!k) continue;
      left.sort((a, b) => b[1] - a[1]);
      for (let i = 0; i < left.length && k > 0;) {
        if (ELIG[s].includes(left[i][0])) { pts += left[i][1]; left.splice(i, 1); k--; } else i++;
      }
    }
    total += pts * (po.has(w) ? 1.3 : 1);
  });
  return total;
}
function afterTrade(roster, give, get) {
  const out = roster.filter((p) => !give.includes(p)).concat(get);
  const drops = [];
  while (out.length > roster.length) {
    const cands = out.filter((p) => !get.includes(p) && P[p] && !(["K", "DST"].includes(P[p].pos) && out.filter((x) => P[x]?.pos === P[p].pos).length <= 1));
    cands.sort((a, b) => (P[a].vorp || 0) - (P[b].vorp || 0) || (P[a].fc_value || 0) - (P[b].fc_value || 0));
    const w = cands[0];
    out.splice(out.indexOf(w), 1);
    drops.push(w);
  }
  return [out, drops];
}
function evalTrade(myRoster, theirRoster, give, get) {
  const n = Math.max(D.meta.ros_weeks.length, 1);
  const [mine, myDrops] = afterTrade(myRoster, give, get);
  const [theirs, thDrops] = afterTrade(theirRoster, get, give);
  const val = (ps) => ps.reduce((a, p) => a + (P[p]?.fc_value || 0), 0);
  const myGain = (strengthTotal(mine) - strengthTotal(myRoster)) / n;
  const thGain = (strengthTotal(theirs) - strengthTotal(theirRoster)) / n;
  const fair = (val(give) - val(thDrops)) / Math.max(val(get), 1);
  return { myGain, thGain, fair, myDrops, thDrops, giveV: val(give), getV: val(get) };
}
function renderAnalyzer(el) {
  if (!D.teams || D.teams.length < 2 || ME == null) {
    el.innerHTML = `<div class="empty">The offer analyzer needs your ESPN league (it compares both rosters).</div>`;
    return;
  }
  const mine = D.teams.find((t) => t.id === ME);
  const others = D.teams.filter((t) => t.id !== ME);
  let partner = +(store.get("partner") || others[0].id);
  if (!others.some((t) => t.id === partner)) partner = others[0].id;
  const give = new Set(), get = new Set();
  const list = (pids, set, tag) => pids.filter((p) => P[p]).sort((a, b) => (P[b].fc_value || 0) - (P[a].fc_value || 0))
    .map((p) => `<label class="pick"><input type="checkbox" data-${tag}="${esc(p)}" ${set.has(p) ? "checked" : ""}><span style="flex:1">${pn(p)}</span><span class="mono small muted">${f0(P[p].fc_value)}</span></label>`).join("");
  const draw = () => {
    const th = D.teams.find((t) => t.id === partner);
    el.innerHTML = `<h2>Offer analyzer</h2><p class="muted small">Got an offer, or thinking about sending one? Tick what you'd give and get. Uses the same rest-of-season lineup math and trade values as the recommendations.</p>
      <div class="row" style="margin-bottom:10px"><span class="small">Trading with</span><select id="partner">${others.map((t) => `<option value="${t.id}" ${t.id === partner ? "selected" : ""}>${esc(ownerName(t.id))} · ${esc(t.name)}</option>`).join("")}</select></div>
      <div class="grid"><div class="card"><h3>You give</h3>${list(mine.pids, give, "g")}</div><div class="card"><h3>You get</h3>${list(th.pids, get, "r")}</div></div>
      <div id="verdict"></div>`;
    $("#partner", el).onchange = (e) => { partner = +e.target.value; store.set("partner", partner); give.clear(); get.clear(); draw(); };
    el.querySelectorAll("[data-g]").forEach((c) => (c.onchange = () => { c.checked ? give.add(c.dataset.g) : give.delete(c.dataset.g); verdict(); }));
    el.querySelectorAll("[data-r]").forEach((c) => (c.onchange = () => { c.checked ? get.add(c.dataset.r) : get.delete(c.dataset.r); verdict(); }));
    verdict();
  };
  const verdict = () => {
    const box = $("#verdict", el);
    const th = D.teams.find((t) => t.id === partner);
    if (!give.size || !get.size) { box.innerHTML = `<div class="card muted small">Pick at least one player on each side.</div>`; return; }
    const g = [...give], r = [...get];
    const e = evalTrade(mine.pids, th.pids, g, r);
    let cls, word, advice;
    if (e.myGain >= 0.5 && e.fair <= 1.2) { cls = "good"; word = "Accept / send it"; advice = "Improves your lineup and is priced fairly."; }
    else if (e.myGain >= 0.5) { cls = "warn"; word = "Good for you, but you're overpaying"; advice = "Your lineup improves, but the market says you're giving more value than you get. Try asking for a little more first."; }
    else if (e.myGain > -0.3) { cls = "warn"; word = "Meh: counter"; advice = "Basically neutral for your lineup."; }
    else { cls = "bad"; word = "Decline"; advice = "This makes your lineup worse."; }
    // Counter: one extra player from them (or one fewer from you) that fixes it while staying acceptable.
    let counter = "";
    if (cls !== "good") {
      let best = null;
      for (const x of th.pids.filter((p) => P[p] && !get.has(p))) {
        const ev = evalTrade(mine.pids, th.pids, g, r.concat([x]));
        if (ev.myGain >= 0.5 && ev.fair >= 0.9 && (!best || Math.abs(ev.fair - 1) < Math.abs(best.ev.fair - 1))) best = { ev, txt: `ask for <b>${esc(P[x].name)}</b> too` };
      }
      for (const x of g) {
        if (g.length < 2) break;
        const ev = evalTrade(mine.pids, th.pids, g.filter((p) => p !== x), r);
        if (ev.myGain >= 0.5 && ev.fair >= 0.9 && (!best || Math.abs(ev.fair - 1) < Math.abs(best.ev.fair - 1))) best = { ev, txt: `take <b>${esc(P[x].name)}</b> out of your side` };
      }
      counter = best ? `<p class="small" style="margin:8px 0 0">Counter: ${best.txt}. That makes it ${sign(best.ev.myGain)} pts/wk for you at ${pct(best.ev.fair)} value to them.</p>`
        : `<p class="small muted" style="margin:8px 0 0">No simple one-player counter fixes this one.</p>`;
    }
    box.innerHTML = `<div class="card"><div class="verdict ${cls}">${word}</div><p class="small muted" style="margin:2px 0 8px">${advice}</p>
      <div class="stats"><div><b>${sign(e.myGain)}</b>your pts/wk</div><div><b>${sign(e.thGain)}</b>their pts/wk</div>
      <div><b>${f0(e.giveV)}</b>value you give</div><div><b>${f0(e.getV)}</b>value you get</div><div><b>${pct(e.fair)}</b>value to them</div></div>
      ${e.myDrops.length ? `<p class="small muted" style="margin:6px 0 0">You'd have to drop ${e.myDrops.map((p) => esc(P[p].name)).join(", ")}.</p>` : ""}${counter}</div>`;
  };
  draw();
}

/* ---------- Playoffs ---------- */
function renderPlayoffs(el) {
  const PO = D.playoffs || {};
  const weeks = D.meta.playoff_weeks;
  const myIds = new Set(((D.teams || []).find((t) => t.id === ME) || {}).pids || []);
  let filter = myIds.size ? "mine" : "all";
  const color = (f) => (f === 0 ? "background:var(--surface-2);color:var(--muted)" : f >= 1.08 ? "background:var(--good-soft);color:var(--good)" : f <= 0.92 ? "background:var(--bad-soft);color:var(--bad)" : "background:var(--surface-2)");
  const draw = () => {
    const rows = Object.entries(PO).filter(([pid]) => P[pid] && (filter === "all" ? true : filter === "mine" ? myIds.has(pid) : P[pid].on_team !== ME))
      .sort((a, b) => (b[1].points || 0) - (a[1].points || 0)).slice(0, 120);
    el.innerHTML = `<h2>Fantasy playoff schedule (Weeks ${weeks.join(", ")})</h2>
      <p class="muted small">Green = soft matchup for that position, red = tough (schedule-adjusted points allowed). Grade compares each player's playoff slate to others at his position.</p>
      <div class="seg" id="pf">${[["mine", "My roster"], ["others", "Trade targets"], ["all", "Everyone"]].filter(([k]) => myIds.size || k === "all").map(([k, l]) => `<button data-k="${k}" class="${k === filter ? "on" : ""}">${l}</button>`).join("")}</div>
      <div class="card" style="margin-top:10px"><div class="table-wrap"><table><tr><th>Player</th><th>Grade</th>${weeks.map((w) => `<th>Wk ${w}</th>`).join("")}<th class="num">Proj pts</th></tr>
      ${rows.map(([pid, x]) => `<tr><td>${pn(pid)}${P[pid].on_team != null && P[pid].on_team !== ME ? `<div class="pmeta">${esc(ownerName(P[pid].on_team))}</div>` : ""}</td><td><span class="grade ${x.grade}">${x.grade}</span></td>
        ${x.games.map((g) => `<td><span class="wk" style="${color(g.factor)}">${esc(g.opp)}</span></td>`).join("")}<td class="num">${f1(x.points)}</td></tr>`).join("")}
      </table></div></div>`;
    el.querySelectorAll("#pf button").forEach((b) => (b.onclick = () => { filter = b.dataset.k; draw(); }));
  };
  draw();
}

/* ---------- League ---------- */
function renderLeague(el) {
  const prof = D.profiles;
  let h = "";
  if (prof) {
    const rows = Object.values(prof).sort((a, b) => a.standing - b.standing);
    h += `<h2>League overview</h2><div class="card"><div class="table-wrap"><table><tr><th>#</th><th>Manager</th><th>Record</th><th class="num hide-sm">PF</th><th class="num">ROS pts/wk</th><th class="num">Playoffs</th><th>Needs</th><th class="hide-sm">Extra</th><th class="num hide-sm">Trades</th></tr>
      ${rows.map((p) => `<tr class="click" data-team="${p.id}"><td class="mono">${p.standing}</td><td><b>${esc(p.owner)}</b>${p.is_me ? ' <span class="chip acc">you</span>' : ""}<div class="pmeta">${esc(p.name)}</div></td><td>${esc(p.record)}</td><td class="num hide-sm">${f0(p.points_for)}</td><td class="num">${f1(p.strength)}</td><td class="num">${pct(p.playoff_odds)}</td><td><span class="chip bad">${esc(p.need)}</span></td><td class="hide-sm"><span class="chip good">${esc(p.surplus)}</span></td><td class="num hide-sm">${p.trades}</td></tr>`).join("")}
      </table></div></div><div id="roster"></div>`;
  }
  const ctx = D.team_ctx || {};
  const teams = Object.values(ctx).filter((c) => c.games).sort((a, b) => (a.epa_play_rank || 99) - (b.epa_play_rank || 99));
  h += `<h2>NFL team context, Week ${D.meta.week}</h2><p class="muted small">Implied points from Vegas. PROE = pass rate over expected in neutral game states. Rank 1 = best.</p>
    <div class="card"><div class="table-wrap"><table><tr><th>Team</th><th>Opp</th><th class="num">Implied</th><th class="num hide-sm">Pts/g</th><th class="num hide-sm">Off EPA</th><th class="num hide-sm">PROE</th><th class="num hide-sm">Pace</th><th class="num hide-sm">Pass D</th><th class="num hide-sm">Run D</th><th>QB / notes</th></tr>
    ${teams.map((c) => `<tr><td><b>${esc(c.team)}</b></td><td>${c.bye ? "BYE" : (c.home ? "vs " : "@") + esc(c.opp)}</td><td class="num">${f1(c.implied)}</td><td class="num hide-sm">${f1(c.ppg)}</td><td class="num hide-sm">${c.epa_play_rank || "–"}</td><td class="num hide-sm">${c.proe_rank || "–"}</td><td class="num hide-sm">${c.plays_pg_rank || "–"}</td><td class="num hide-sm">${c.def_pass_epa_rank || "–"}</td><td class="num hide-sm">${c.def_rush_epa_rank || "–"}</td><td class="small">${esc(c.qb_this_week || "")}${c.primary_qb && c.qb_this_week && !c.qb_this_week.includes(String(c.primary_qb).split(".").pop()) ? ' <span class="chip warn">QB change</span>' : ""}${c.new_coach ? ' <span class="chip">new HC</span>' : ""}<div class="pmeta">${esc(c.coach || "")}</div></td></tr>`).join("")}
    </table></div></div>`;
  el.innerHTML = h;
  el.querySelectorAll("[data-team]").forEach((tr) => (tr.onclick = () => {
    const t = D.teams.find((x) => x.id === +tr.dataset.team);
    const p = prof[t.id];
    $("#roster", el).innerHTML = `<h2>${esc(p.owner)}'s roster</h2><div class="card"><div class="table-wrap"><table><tr><th>Player</th><th class="num">ROS pts/g</th><th class="num">Value</th></tr>
      ${t.pids.filter((x) => P[x]).sort((a, b) => (P[b].fc_value || 0) - (P[a].fc_value || 0)).map((x) => `<tr><td>${pn(x)}</td><td class="num">${f1(P[x].ros_ppg)}</td><td class="num">${f0(P[x].fc_value)}</td></tr>`).join("")}</table></div></div>`;
    $("#roster", el).scrollIntoView({ behavior: "smooth" });
  }));
}

/* ---------- Players ---------- */
function renderPlayers(el) {
  let q = "", pos = "ALL", sortKey = "ros_ppg", dir = -1;
  const cols = [["name", "Player"], ["proj", "Wk"], ["ros_ppg", "ROS/g"], ["fc_value", "Market"], ["model_value", "Model"], ["snap", "Snap%"], ["tgt", "Tgt%"], ["ppg", "Pts/g"], ["exp", "Exp/g"], ["owner", "Team"]];
  const val = (r, k) => ({ snap: r.usage?.snap, tgt: r.usage?.tgt_share, ppg: r.usage?.ppg, exp: r.usage?.exp_ppg, owner: r.on_team == null ? "" : ownerName(r.on_team) }[k] ?? r[k]);
  const draw = () => {
    const rows = Object.values(P).filter((r) => (pos === "ALL" || r.pos === pos) && (!q || r.name.toLowerCase().includes(q)))
      .sort((a, b) => { const x = val(a, sortKey), y = val(b, sortKey); if (typeof x === "string" || typeof y === "string") return dir * String(x ?? "").localeCompare(String(y ?? "")); return dir * ((x ?? -1e9) - (y ?? -1e9)); }).slice(0, 200);
    $("#ptable", el).innerHTML = `<tr>${cols.map(([k, l], i) => `<th class="sort ${i > 0 && k !== "owner" ? "num" : ""} ${["snap", "tgt", "exp", "model_value", "owner"].includes(k) ? "hide-sm" : ""}" data-k="${k}">${l}${k === sortKey ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr>
      ${rows.map((r) => `<tr class="click" data-pid="${esc(r.pid)}"><td>${pn(r.pid)}</td><td class="num">${f1(r.proj)}</td><td class="num">${f1(r.ros_ppg)}</td><td class="num">${f0(r.fc_value)}</td><td class="num hide-sm">${f0(r.model_value)}</td><td class="num hide-sm">${pct(r.usage?.snap)}</td><td class="num hide-sm">${pct(r.usage?.tgt_share)}</td><td class="num">${f1(r.usage?.ppg)}</td><td class="num hide-sm">${f1(r.usage?.exp_ppg)}</td><td class="small hide-sm">${r.on_team === ME ? '<span class="chip acc">you</span>' : r.on_team == null ? (D.meta.mode === "league" ? '<span class="chip good">FA</span>' : "") : esc(ownerName(r.on_team))}</td></tr>`).join("")}`;
    el.querySelectorAll("th.sort").forEach((th) => (th.onclick = () => { const k = th.dataset.k; dir = k === sortKey ? -dir : (k === "name" || k === "owner" ? 1 : -1); sortKey = k; draw(); }));
  };
  el.innerHTML = `<h2>All players</h2><div class="row"><input type="search" id="q" placeholder="Search players" style="flex:1;min-width:160px">
    <div class="seg" id="pp">${["ALL", "QB", "RB", "WR", "TE", "K", "DST"].map((p) => `<button data-p="${p}" class="${p === pos ? "on" : ""}">${p}</button>`).join("")}</div></div>
    <div class="card" style="margin-top:10px"><div class="table-wrap"><table id="ptable"></table></div></div>`;
  $("#q", el).oninput = (e) => { q = e.target.value.toLowerCase(); draw(); };
  el.querySelectorAll("#pp button").forEach((b) => (b.onclick = () => { pos = b.dataset.p; el.querySelectorAll("#pp button").forEach((x) => x.classList.toggle("on", x === b)); draw(); }));
  draw();
}

/* ---------- Playbook ---------- */
function renderPlaybook(el) {
  el.innerHTML = `<h2>Trade negotiation playbook</h2><p class="muted small">The pitches on the Trades tab are built from these. Use them honestly: selective framing of true facts wins trades and keeps your league willing to deal with you.</p>
    <div class="grid">${(D.playbook || []).map((t) => `<div class="card"><h3>${esc(t.name)}</h3><p class="small" style="margin:0 0 6px">${esc(t.how)}</p><span class="chip">${esc(t.source)}</span></div>`).join("")}</div>
    <h2>How the numbers work</h2><div class="card small">
    <p><b>Weekly projection</b> is a weighted blend of Sleeper (30%), ESPN (25%), FantasyPros' expert consensus rank converted to points (20%), and our own usage model (25%). The usage model starts from recency-weighted expected fantasy points (from every target and carry's field position and depth, via nflverse/ffopportunity), blended with actual scoring and last season's rate, then adjusts for Vegas implied team total vs that team's norm, schedule-adjusted defense vs position, game script (spread), wind/rain/cold, and starting-QB changes. Injury designations from the official report, ESPN and Sleeper scale it down.</p>
    <p><b>Floor/ceiling</b> are the 10th and 90th percentiles from each player's weekly volatility.</p>
    <p><b>Rest of season</b> projects every remaining week to the end of your fantasy playoffs (byes zeroed, IR stints respected); playoff weeks count 1.3×.</p>
    <p><b>Trade value</b> is FantasyCalc's redraft value (12-team, half PPR), built from real trades. <b>Model value</b> puts our rest-of-season value over replacement on the same scale, so "edge" shows who's mispriced.</p>
    <p><b>Recommended trades</b> search 1-for-1, 2-for-1, 1-for-2 and 2-for-2 deals with every manager. Each must be fair by market value, raise your optimal lineup's rest-of-season points, and not gut theirs.</p></div>`;
}

/* ---------- Player modal ---------- */
function spark(weekly) {
  if (!weekly || !weekly.length) return "";
  const max = Math.max(...weekly.map((w) => w[1]), 10);
  const W = 100 / weekly.length;
  return `<svg class="spark" viewBox="0 0 100 60" preserveAspectRatio="none">${weekly.map((w, i) => {
    const h = (w[1] / max) * 50;
    return `<rect x="${i * W + W * 0.15}" y="${55 - h}" width="${W * 0.7}" height="${h}" rx="1" fill="var(--accent)" opacity=".8"><title>Wk ${w[0]}: ${w[1]}</title></rect>`;
  }).join("")}</svg><div class="row small muted" style="justify-content:space-between">${weekly.map((w) => `<span>W${w[0]}</span>`).join("")}</div>`;
}
function openPlayer(pid) {
  const r = P[pid];
  if (!r) return;
  const u = r.usage || {};
  const dlg = $("#dlg");
  const srcs = Object.entries(r.sources || {}).map(([k, v]) => `<span class="chip">${esc(k)} ${f1(v)}</span>`).join("");
  dlg.innerHTML = `<div class="dlg-head">${r.headshot ? `<img src="${esc(r.headshot)}" alt="">` : ""}<div><h3 style="margin:0">${esc(r.name)}${inj(r)}</h3>
      <div class="muted small">${esc(r.pos)} · ${esc(r.team || "FA")}${r.age ? ` · age ${Math.floor(r.age)}` : ""}${r.bye_week ? ` · bye wk ${r.bye_week}` : ""}${r.on_team != null ? ` · ${esc(r.on_team === ME ? "your team" : ownerName(r.on_team))}` : ""}</div></div>
      <button class="close" onclick="this.closest('dialog').close()">×</button></div>
    <div class="dlg-body">
      <div class="kv"><div>This week<b>${f1(r.locked && r.actual != null ? r.actual : r.proj)}</b>${r.locked && r.actual != null ? "final" : `${f1(r.floor)}–${f1(r.ceil)}`}</div>
        <div>Matchup<b>${esc(opp(r)) || "–"}</b>${r.implied ? `team implied ${f1(r.implied)}` : ""}</div>
        <div>ROS pts/game<b>${f1(r.ros_ppg)}</b>${r.ros_rank ? `${esc(r.pos)}${r.ros_rank}` : ""}</div>
        <div>Market value<b>${f0(r.fc_value)}</b>${r.fc_estimated ? "estimated" : "FantasyCalc"}</div>
        <div>Model value<b>${f0(r.model_value)}</b>${r.edge ? `edge ${sign(r.edge, 0)}` : ""}</div>
        <div>FantasyPros<b>${r.fp_ros_pos_rank ? esc(r.pos) + Math.round(r.fp_ros_pos_rank) : "–"}</b>ROS rank</div></div>
      ${srcs ? `<div class="small muted">Projection sources: ${srcs}</div>` : ""}
      ${(r.notes || []).length ? `<h3 style="margin-top:12px">Why</h3><ul class="notes" style="color:var(--text)">${r.notes.map((n) => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}
      ${r.injury_notes ? `<p class="small"><b>Injury:</b> ${esc(r.injury_notes)}</p>` : ""}
      ${u.weekly ? `<h3 style="margin-top:12px">Points by week</h3>${spark(u.weekly)}` : ""}
      ${u.games ? `<h3 style="margin-top:12px">Usage</h3><div class="kv">
        <div>Pts/game<b>${f1(u.ppg)}</b>last 3: ${f1(u.last3_ppg)}</div><div>Expected/game<b>${f1(u.exp_ppg)}</b>from usage</div>
        <div>Snap share<b>${pct(u.snap)}</b>last 2: ${pct(u.snap_last2)}</div><div>Target share<b>${pct(u.tgt_share)}</b>last 2: ${pct(u.tgt_share_last2)}</div>
        <div>Carry share<b>${pct(u.carry_share)}</b>last 2: ${pct(u.carry_share_last2)}</div><div>Red-zone opps/g<b>${f1(u.rz_opps_pg)}</b>goal-line carries ${f0(u.gl_carries)}</div>
        <div>TDs<b>${f0(u.td)}</b>expected ${f1(u.td_exp)}</div><div>aDOT<b>${f1(u.adot)}</b>air yards/target</div>
        <div>Last season<b>${f1(u.prev_ppg)}</b>pts/game</div></div>` : ""}
      ${r.ros ? `<h3 style="margin-top:12px">Rest-of-season projection</h3><div class="row small">${r.ros.map((w) => `<span class="wk" style="background:var(--surface-2)">W${w[0]} ${f1(w[1])}</span>`).join("")}</div>` : ""}
    </div>`;
  dlg.showModal();
}
dlgClose: {
  const d = document.getElementById("dlg");
  d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
}

/* ---------- ESPN sync (browser → encrypted file in the repo → GitHub Actions rebuild) ---------- */
function repoSlug() {
  const saved = store.get("repo");
  if (saved) return saved;
  const owner = location.hostname.endsWith(".github.io") ? location.hostname.split(".")[0] : "";
  const repo = location.pathname.split("/").filter(Boolean)[0] || "";
  return owner && repo ? `${owner}/${repo}` : "";
}
const b64 = (buf) => {
  const a = new Uint8Array(buf);
  let s = "";
  for (let i = 0; i < a.length; i += 0x8000) s += String.fromCharCode.apply(null, a.subarray(i, i + 0x8000));
  return btoa(s);
};
async function encryptText(text, pw) {
  const salt = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: 200000, hash: "SHA-256" }, base,
    { name: "AES-GCM", length: 256 }, false, ["encrypt"]);
  const gz = await new Response(new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"))).arrayBuffer();
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, gz);
  return JSON.stringify({ v: 1, iter: 200000, salt: b64(salt), iv: b64(iv), ct: b64(ct) });
}
async function pushLeague(payload, pw, token, slug) {
  const path = "league/espn.enc.json";
  const api = `https://api.github.com/repos/${slug}/contents/${path}`;
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json" };
  const cur = await fetch(api, { headers });
  if (cur.status === 401) throw new Error("GitHub rejected the token. Make a new one (see Connect ESPN).");
  const sha = cur.ok ? (await cur.json()).sha : undefined;
  const body = { message: `Sync ESPN league (${new Date().toLocaleString()})`, content: btoa(await encryptText(JSON.stringify(payload), pw)), sha };
  const r = await fetch(api, { method: "PUT", headers, body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`GitHub said ${r.status}: ${(await r.text()).slice(0, 200)}`);
}
function syncPage() {
  const box = $("#sync");
  box.hidden = false;
  box.innerHTML = `<h1>🏈 Syncing ESPN…</h1><p class="muted" id="sync-msg">Waiting for your league data from ESPN.</p><div id="sync-form"></div>`;
  const msg = (t, bad) => { $("#sync-msg").innerHTML = t; $("#sync-msg").style.color = bad ? "var(--bad)" : ""; };
  let got = null;
  let failed = false;
  window.addEventListener("message", (e) => {
    if (!/(^|\.)espn\.com$/.test(new URL(e.origin).hostname) || !e.data) return;
    if (e.data.type === "espn-league-error" && !got) {
      failed = true;
      e.source.postMessage({ type: "espn-league-received" }, e.origin);
      const det = (e.data.details || []).map((d) => `<li>${esc(d)}</li>`).join("");
      msg("Sync failed: " + esc(e.data.message) + (det ? `<ul class="small" style="text-align:left;color:var(--muted);margin-top:10px">${det}</ul><span class="small muted">Screenshot this and send it to Claude.</span>` : ""), true);
      return;
    }
    if (e.data.type !== "espn-league" || got) return;
    got = e.data.payload;
    e.source.postMessage({ type: "espn-league-received" }, e.origin);
    const teams = (got.main.teams || []).length;
    msg(`Got <b>${esc(got.main.settings?.name || "your league")}</b>: ${teams} teams, week ${got.main.scoringPeriodId}.`);
    ask();
  });
  const ask = async () => {
    let pw = store.get("pw"), token = store.get("ghtoken"), slug = repoSlug();
    if (!pw || !token || !slug) {
      $("#sync-form").innerHTML = `<form id="sf" style="text-align:left">
        ${slug ? "" : `<label class="small">GitHub repo (owner/name)<input id="sf-repo" placeholder="josh122208/dfd" style="width:100%;margin:4px 0 10px"></label>`}
        ${pw ? "" : `<label class="small">Site password (same as your SITE_PASSWORD secret)<input id="sf-pw" type="password" style="width:100%;margin:4px 0 10px"></label>`}
        ${token ? "" : `<label class="small">GitHub token (see Connect ESPN for how to make one)<input id="sf-tok" type="password" style="width:100%;margin:4px 0 10px"></label>`}
        <button class="btn" style="width:100%">Save &amp; sync</button></form>`;
      $("#sf").onsubmit = (ev) => {
        ev.preventDefault();
        if ($("#sf-repo")) store.set("repo", $("#sf-repo").value.trim());
        if ($("#sf-pw")) store.set("pw", $("#sf-pw").value);
        if ($("#sf-tok")) store.set("ghtoken", $("#sf-tok").value.trim());
        $("#sync-form").innerHTML = "";
        ask();
      };
      return;
    }
    msg("Encrypting and saving to GitHub…");
    try {
      await pushLeague(got, pw, token, slug);
      msg(`✅ Synced. Your site rebuilds in about 2 minutes.<br><a href="./">Back to Fantasy Edge</a>`);
    } catch (err) {
      msg("Sync failed: " + esc(err.message), true);
      if (/token/i.test(err.message)) store.set("ghtoken", "");
    }
  };
  setTimeout(() => { if (!got && !failed) msg("No data arrived. Start from your league page on fantasy.espn.com and tap the Fantasy Edge sync bookmark there.", true); }, 15000);
}
async function renderConnect(el) {
  const m = D.meta;
  const src = await (await fetch("bookmarklet.js?t=" + Date.now(), { cache: "no-store" })).text();
  const bmVer = (src.match(/const V = "([^"]+)"/) || [])[1] || "?";
  const site = location.origin + location.pathname;
  const code = "javascript:" + encodeURIComponent(src.replace(/\/\*[\s\S]*?\*\//g, "").replace("__SITE__", site).replace(/\n\s*/g, " "));
  const slug = repoSlug() || "josh122208/dfd";
  el.innerHTML = `<h2>Connect your ESPN league</h2>
    <div class="card small">${m.league_synced ? `✅ Last synced <b>${new Date(m.league_synced).toLocaleString()}</b>. Re-sync after trades, pickups or lineup changes. Projections, injuries and Vegas lines refresh automatically every day either way.` : "Not synced yet."}</div>
    <div class="card small"><p style="margin-top:0"><b>Why the extra step?</b> ESPN blocks cloud servers (including GitHub's) from its fantasy API, so your browser, where you're already logged in, reads the league and saves an encrypted copy to your repo. ESPN cookies never leave your browser.</p>
    <h3>One-time setup</h3><ol>
      <li><b>Make a GitHub token.</b> On GitHub: Settings → Developer settings → Personal access tokens → <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">Fine-grained tokens → Generate new token</a>. Repository access: <b>Only select repositories → ${esc(slug.split("/")[1] || "dfd")}</b>. Permissions → Repository permissions → <b>Contents: Read and write</b>. Copy the token. You'll paste it on the first sync and it stays in this browser only.</li>
      <li><b>Add the bookmark.</b> On a computer, drag this button to your bookmarks bar: <a class="btn" href="${esc(code)}" onclick="alert('Drag this to your bookmarks bar instead of clicking it.');return false">⚡ Sync to Fantasy Edge</a> <span class="chip">bookmark ${esc(bmVer)}</span><br>
        On a phone: bookmark any page, edit that bookmark, and replace its address with the code below.<br>
        <textarea readonly id="bm" style="width:100%;height:70px;margin-top:6px;font:12px var(--mono);background:var(--surface-2);color:var(--text);border:1px solid var(--border);border-radius:8px">${esc(code)}</textarea>
        <button class="btn ghost" id="bm-copy">Copy bookmark code</button></li>
    </ol>
    <h3>Each sync</h3><ol>
      <li>Open your league on <a href="https://fantasy.espn.com/football/" target="_blank" rel="noopener">fantasy.espn.com</a> in your browser (not the app) and log in.</li>
      <li>Click the <b>Sync to Fantasy Edge</b> bookmark in your bookmarks bar.</li>
      <li>A Fantasy Edge tab opens, encrypts your league with your site password, and saves it. The site updates about 2 minutes later.</li>
    </ol></div>
    <div class="card small"><b>Saved in this browser</b>: repo <span class="mono">${esc(repoSlug() || "(auto)")}</span>, token ${store.get("ghtoken") ? "✓" : "—"}, password ${store.get("pw") ? "✓" : "—"}.
      <button class="btn ghost" id="forget" style="margin-left:6px">Forget token</button></div>`;
  $("#bm-copy", el).onclick = async () => { try { await navigator.clipboard.writeText(code); $("#bm-copy", el).textContent = "Copied ✓"; } catch { $("#bm", el).select(); } };
  $("#forget", el).onclick = () => { store.set("ghtoken", ""); renderConnect(el); };
}

boot().catch((e) => {
  document.body.innerHTML = `<div class="gate"><h1>🏈 Fantasy Edge</h1><p class="muted">Couldn't load data: ${esc(e.message)}</p></div>`;
});
