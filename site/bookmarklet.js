/* Runs on fantasy.espn.com (as a bookmarklet). Reads your league with your own ESPN login and hands it to the site.
   ESPN blocks cloud servers like GitHub's, so this is how league data gets out. Nothing is sent anywhere except
   the site window it opens. SITE is replaced with the page's own URL when the bookmarklet link is built.
   ESPN's data server rejects requests that don't look like they come from ESPN's own app, so this copies what the
   page itself does: it finds the data hosts the page already used (performance entries) and sends ESPN's app
   headers. Every attempt is recorded and shown in the site tab if all of them fail. */
(async () => {
  const SITE = "__SITE__";
  const V = "v4";
  const q = new URLSearchParams(location.search);
  const league = q.get("leagueId");
  if (!/(^|\.)espn\.com$/.test(location.hostname) || !league) {
    alert("Open your league on fantasy.espn.com first (any page whose address has leagueId=), then use this bookmark again.");
    return;
  }
  const now = new Date();
  const season = q.get("seasonId") || (now.getMonth() < 2 ? now.getFullYear() - 1 : now.getFullYear());
  const win = window.open(SITE + "#sync", "fantasy-edge-sync");
  if (!win) { alert("Your browser blocked the pop-up. Allow pop-ups for fantasy.espn.com and use the bookmark again."); return; }
  const target = new URL(SITE).origin;
  let tries = 0, timer;
  /* Keep sending until the site tab says it got it (it may still be loading). */
  const deliver = (msg) => {
    clearTimeout(timer);
    tries = 0;
    const send = () => { win.postMessage(msg, target); if (++tries < 40) timer = setTimeout(send, 500); };
    send();
  };
  window.addEventListener("message", (e) => {
    if (e.data && e.data.type === "espn-league-received") clearTimeout(timer);
  });

  /* Data URLs the ESPN page itself loaded: tells us the current host and app version. */
  const used = performance.getEntriesByType("resource").map((e) => e.name)
    .filter((u) => /espn/.test(u) && /(apps\/fantasy|\/ffl\/|leagues\/)/.test(u));
  const hosts = [...new Set(used.filter((u) => /\/apps\/fantasy\/ffl\//.test(u)).map((u) => new URL(u).origin))];
  ["https://lm-api-reads.fantasy.espn.com", "https://fantasy.espn.com"].forEach((h) => { if (!hosts.includes(h)) hosts.push(h); });
  const ver = (document.documentElement.innerHTML.match(/kona-PROD-[\w.-]+/) || ["kona-PROD-m.fc.latest.kona-hyper"])[0];
  const headerSets = [
    { "x-fantasy-source": "kona", "x-fantasy-platform": ver, accept: "application/json" },
    {},
  ];
  const log = [];
  let base = null, hdrs = null;
  for (const h of hosts) {
    for (const hs of headerSets) {
      const b = `${h}/apps/fantasy/ffl/seasons/${season}/segments/0/leagues/${league}`;
      const label = `${new URL(h).hostname} ${Object.keys(hs).length ? "+app headers" : "plain"}`;
      try {
        const r = await fetch(b + "?view=mStatus", { credentials: "include", headers: hs });
        const ct = r.headers.get("content-type") || "";
        log.push(`${label}: ${r.status} ${ct.split(";")[0]}`);
        if (r.ok && ct.includes("json")) { base = b; hdrs = hs; break; }
      } catch (e) {
        log.push(`${label}: ${e.message}`);
      }
    }
    if (base) break;
  }
  if (!base) {
    deliver({ type: "espn-league-error", message: `ESPN didn't let the bookmark (${V}) read your league.`,
      details: log.concat(["ESPN page used: " + (used.slice(0, 6).map((u) => u.split("?")[0]).join(" , ") || "(none found)"),
        "app version: " + ver]) });
    return;
  }
  const get = async (step, views, extra = "", filter) => {
    const url = base + "?" + views.map((v) => "view=" + v).join("&") + extra;
    const headers = Object.assign({}, hdrs, filter ? { "x-fantasy-filter": JSON.stringify(filter) } : {});
    const r = await fetch(url, { credentials: "include", headers });
    if (!r.ok) throw new Error(`${step}: ESPN said ${r.status}${r.status === 401 ? " (not logged in on this browser?)" : ""}`);
    return r.json();
  };
  try {
    let main;
    try {
      main = await get("league", ["mSettings", "mTeam", "mRoster", "mMatchup", "mStatus"]);
    } catch (e) {
      /* Some setups refuse multi-view requests: fetch each view alone and merge teams by id. */
      log.push("combined views: " + e.message);
      main = {};
      for (const v of ["mStatus", "mSettings", "mTeam", "mRoster", "mMatchup"]) {
        const part = await get(v, [v]);
        for (const [k, val] of Object.entries(part)) {
          if (k === "teams" && Array.isArray(main.teams)) {
            val.forEach((t) => { const m = main.teams.find((x) => x.id === t.id); m ? Object.assign(m, t) : main.teams.push(t); });
          } else {
            main[k] = val;
          }
        }
      }
    }
    const week = main.scoringPeriodId || 1;
    const roster = await get("rosters", ["mRoster"], "&scoringPeriodId=" + week);
    /* Free agents are a bonus: without them the site treats every unrostered player as available. */
    let players = [];
    try {
      players = (await get("players", ["kona_player_info"], "&scoringPeriodId=" + week, {
        players: { limit: 1000, sortPercOwned: { sortPriority: 1, sortAsc: false }, filterSlotIds: { value: [0, 2, 4, 6, 16, 17] } },
      })).players || [];
    } catch (e) { /* keep going */ }
    const swid = (document.cookie.match(/SWID=([^;]+)/) || [])[1] || null;
    const payload = { v: 1, bookmark: V, synced: new Date().toISOString(), league, season, swid, main, roster, players };
    deliver({ type: "espn-league", payload });
  } catch (err) {
    deliver({ type: "espn-league-error", message: `Couldn't read your league from ESPN (${err.message}). Bookmark ${V}.`, details: log });
  }
})();
