# Fantasy Edge

A fantasy football assistant for an ESPN league. It runs on GitHub Actions and publishes a private-ish website (GitHub Pages) with:

- **This Week**: optimal lineup, the exact START/SIT moves to make in ESPN, win probability vs. your opponent, close calls, late-swap tips, and a "why" for every player (matchup, Vegas, weather, QB changes, injuries, usage trends, TD luck).
- **Trades**: sell-high and buy-low lists, plus concrete offers to specific managers. Each offer is fair by trade-value charts, improves your rest-of-season lineup, and leaves theirs about even. Every offer comes with an opening ask, a fair deal, a walk-away price, and a ready-to-send message built on negotiation tactics (need framing, box-score framing, anchoring, two-option choice, urgency).
- **Waivers**: pickups ranked by how much they improve *your* lineup, who to drop, FAAB bids, and one-week streamers.
- **Offer Analyzer**: tick the players on each side of any offer and get accept/counter/decline, plus a suggested counter.
- **Playoffs**: Weeks 15-17 matchup grades for your players and trade targets, plus playoff odds for every team (Monte Carlo).
- **League / Players / Playbook**: every team's needs and surpluses, NFL team context (pace, pass rate over expected, EPA, red-zone trips, QB/coach), a searchable player table, and the negotiation playbook.

## Data sources

| Source | Used for |
|---|---|
| ESPN fantasy API | Your league: rosters, lineup slots, scoring, schedule, standings, free agents, ESPN projections, injury status, ownership |
| ESPN scoreboard | Live spreads and totals (implied team points) |
| Sleeper | Weekly projections for every remaining week, injury details, practice participation, trending adds/drops |
| FantasyPros (via dynastyprocess) | Expert consensus weekly and rest-of-season ranks |
| FantasyCalc | Redraft trade values (12-team, half PPR), built from real trades |
| nflverse / ffopportunity | Play-by-play, snap counts, target/air-yard shares, red-zone usage, expected fantasy points, official injury reports, depth charts, schedules, starting QBs, coaches |
| Open-Meteo | Game-time wind, rain, snow and temperature for outdoor stadiums |

## Setup (one time)

1. **Turn on Pages:** repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. **Set a password:** **Settings → Secrets and variables → Actions → New repository secret**. Name it `SITE_PASSWORD` and pick any password. It encrypts your published data and synced league so leaguemates can't read your trade plans. The site asks for it once per browser.
3. **Run it:** **Actions → Update fantasy data and site → Run workflow**. The site is at `https://<your-username>.github.io/dfd/`.
4. **Connect ESPN** (the site's Connect ESPN tab has the same steps):
   - **Automatic (recommended):** add secrets `ESPN_LEAGUE_ID` (the number after `leagueId=`), `ESPN_S2` and `ESPN_SWID`. For a private league, get the two cookies in Chrome on a computer: log in to fantasy.espn.com → F12 → Application → Cookies → `https://fantasy.espn.com`. Copy `espn_s2`, and `SWID` including its `{braces}`. The SWID also tells the app which team is yours.
   - **Browser sync (no cookies):** make a GitHub fine-grained token (this repo only, Contents read/write), add the sync bookmark from the Connect ESPN tab, and click it on your league page. The site encrypts the league and saves it to `league/espn.enc.json`, which triggers a rebuild. Re-sync after roster changes.

Everything else (projections, injuries, Vegas lines, weather, trade values) refreshes on its own every morning and at key moments in game week: Thursday before TNF, Friday after injury reports, Sunday after inactives, and Monday before MNF.

Optional secret: `MY_TEAM_ID` if the app picks the wrong team as yours.

## Run locally

```bash
pip install -r requirements.txt
ESPN_LEAGUE_ID=123456 ESPN_S2=... ESPN_SWID='{...}' python -m pipeline.build --out site/data
cd site && python -m http.server 8000   # open http://localhost:8000
python -m tests.fake_league --out site/data   # offline demo with a fake 12-team league
```

## Notes

- The ESPN API is undocumented and can change. If a run fails, the site keeps working with the other sources and shows a banner.
- ESPN cookies expire after about a year. If the league stops loading, copy fresh ones.
- Acceptance odds and the usage model's adjustments are hand-tuned heuristics, not fitted models. Use them to rank options, not as guarantees.
