/* Runs on fantasy.espn.com (as a bookmarklet). Reads your league with your own ESPN login and hands it to the site.
   ESPN blocks cloud servers like GitHub's, so this is how league data gets out. Nothing is sent anywhere except
   the site window it opens. SITE is replaced with the page's own URL when the bookmarklet link is built.
   If the browser won't let fantasy.espn.com read ESPN's data server (CORS), it switches to a page on that server,
   where the same requests are same-origin, and asks you to tap the bookmark once more. */
(async () => {
  const SITE = "__SITE__";
  const API = "lm-api-reads.fantasy.espn.com";
  const onApi = location.hostname === API;
  const q = new URLSearchParams(location.search);
  const pm = location.pathname.match(/seasons\/(\d+)\/segments\/0\/leagues\/(\d+)/);
  const league = q.get("leagueId") || (pm && pm[2]);
  if (!/espn\.com$/.test(location.hostname) || !league) {
    alert("Open your league on fantasy.espn.com first (any page whose address has leagueId=), then tap this bookmark again.");
    return;
  }
  const now = new Date();
  const season = (pm && pm[1]) || q.get("seasonId") || (now.getMonth() < 2 ? now.getFullYear() - 1 : now.getFullYear());
  const base = `https://${API}/apps/fantasy/ffl/seasons/${season}/segments/0/leagues/${league}`;
  const win = window.open(SITE + "#sync", "fantasy-edge-sync");
  if (!win) { alert("Your browser blocked the pop-up. Allow pop-ups for this site and tap the bookmark again."); return; }
  const get = async (step, views, extra = "", filter) => {
    const url = base + "?" + views.map((v) => "view=" + v).join("&") + extra;
    let r;
    try {
      r = await fetch(url, { credentials: "include", headers: filter ? { "x-fantasy-filter": JSON.stringify(filter) } : {} });
    } catch (e) {
      const err = new Error(`${step}: ${e.message}`);
      err.network = true;
      throw err;
    }
    if (!r.ok) throw new Error(`${step}: ESPN said ${r.status}${r.status === 401 ? " (not logged in on this browser?)" : ""}`);
    return r.json();
  };
  let main;
  try {
    main = await get("league", ["mSettings", "mTeam", "mRoster", "mMatchup", "mStatus"]);
  } catch (err) {
    win.close();
    if (err.network && !onApi) {
      if (confirm("Your browser won't let this ESPN page read the league data directly.\n\nPress OK to open ESPN's data page instead. When it finishes loading (it's a page of raw text), tap the Sync to Fantasy Edge bookmark one more time.")) {
        location.href = base + "?view=mStatus";
      }
      return;
    }
    alert("Couldn't read your league from ESPN: " + err.message + "\nMake sure you're logged in to ESPN on this browser.");
    return;
  }
  try {
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
    const payload = { v: 1, synced: new Date().toISOString(), league, season, swid, main, roster, players };
    let tries = 0, timer;
    const send = () => {
      win.postMessage({ type: "espn-league", payload }, new URL(SITE).origin);
      if (++tries < 40) timer = setTimeout(send, 500);
    };
    window.addEventListener("message", (e) => {
      if (e.data && e.data.type === "espn-league-received") clearTimeout(timer);
    });
    send();
  } catch (err) {
    alert("Couldn't read your league from ESPN: " + err.message);
    win.close();
  }
})();
