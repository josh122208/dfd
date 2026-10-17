/* Runs on fantasy.espn.com (as a bookmarklet). Reads your league with your own ESPN login and hands it to the site.
   ESPN blocks cloud servers like GitHub's, so this is how league data gets out. Nothing is sent anywhere except
   the site window it opens. SITE is replaced with the page's own URL when the bookmarklet link is built.
   Browsers won't let fantasy.espn.com read ESPN's data server (CORS), so the first tap moves to a page on that
   server, where the same requests are same-origin, and the second tap does the sync. Errors are shown in the site
   tab rather than with alert(), which Chrome suppresses for tabs that just lost focus. */
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
  /* Browsers block fantasy.espn.com from reading ESPN's data server, so step 1 just goes there. */
  if (!onApi) {
    alert("Step 1 of 2: ESPN's data page will open next (it looks like a page of raw text).\n\nWhen it loads, use the Sync to Fantasy Edge bookmark again.");
    location.href = base + "?view=mStatus";
    return;
  }
  const win = window.open(SITE + "#sync", "fantasy-edge-sync");
  if (!win) { alert("Your browser blocked the pop-up. Allow pop-ups for lm-api-reads.fantasy.espn.com and tap the bookmark again."); return; }
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
  const fail = (text) => deliver({ type: "espn-league-error", message: text });
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
  try {
    const main = await get("league", ["mSettings", "mTeam", "mRoster", "mMatchup", "mStatus"]);
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
    deliver({ type: "espn-league", payload });
  } catch (err) {
    fail("Couldn't read your league from ESPN (" + err.message + "). Make sure you're logged in to ESPN in this browser, then try again.");
  }
})();
