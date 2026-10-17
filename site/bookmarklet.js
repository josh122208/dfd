/* Runs on fantasy.espn.com (as a bookmarklet). Reads your league with your own ESPN login and hands it to the site.
   ESPN blocks cloud servers like GitHub's, so this is how league data gets out. Nothing is sent anywhere except
   the site window it opens. SITE is replaced with the page's own URL when the bookmarklet link is built. */
(async () => {
  const SITE = "__SITE__";
  const q = new URLSearchParams(location.search);
  const league = q.get("leagueId");
  if (!/espn\.com$/.test(location.hostname) || !league) {
    alert("Open your league on fantasy.espn.com first (any page whose address has leagueId=), then tap this bookmark again.");
    return;
  }
  const season = q.get("seasonId") || new Date().getFullYear() - (new Date().getMonth() < 2 ? 1 : 0);
  const win = window.open(SITE + "#sync", "fantasy-edge-sync");
  if (!win) { alert("Your browser blocked the pop-up. Allow pop-ups for fantasy.espn.com and try again."); return; }
  const base = `https://lm-api-reads.fantasy.espn.com/apps/fantasy/ffl/seasons/${season}/segments/0/leagues/${league}`;
  const get = async (views, extra = "", filter) => {
    const url = base + "?" + views.map((v) => "view=" + v).join("&") + extra;
    const r = await fetch(url, { credentials: "include", headers: filter ? { "x-fantasy-filter": JSON.stringify(filter) } : {} });
    if (!r.ok) throw new Error(`ESPN said ${r.status} for ${views.join(",")}`);
    return r.json();
  };
  try {
    const main = await get(["mSettings", "mTeam", "mRoster", "mMatchup", "mStatus"]);
    const week = main.scoringPeriodId || 1;
    const roster = await get(["mRoster"], "&scoringPeriodId=" + week);
    const players = await get(["kona_player_info"], "&scoringPeriodId=" + week, {
      players: { limit: 1000, sortPercOwned: { sortPriority: 1, sortAsc: false }, filterSlotIds: { value: [0, 2, 4, 6, 16, 17] } },
    });
    const swid = (document.cookie.match(/SWID=([^;]+)/) || [])[1] || null;
    const payload = { v: 1, synced: new Date().toISOString(), league, season, swid, main, roster, players: players.players || [] };
    let tries = 0;
    const send = () => {
      win.postMessage({ type: "espn-league", payload }, new URL(SITE).origin);
      if (++tries < 40) timer = setTimeout(send, 500);
    };
    let timer;
    window.addEventListener("message", (e) => {
      if (e.data && e.data.type === "espn-league-received") clearTimeout(timer);
    });
    send();
  } catch (err) {
    alert("Couldn't read your league from ESPN: " + err.message + "\nMake sure you're logged in on this browser.");
    win.close();
  }
})();
