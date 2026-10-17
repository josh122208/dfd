"""ESPN fantasy league API (undocumented, read-only) and the public NFL scoreboard (Vegas lines)."""
import json

from ..util import get_json, log, norm_team, safe

FANTASY = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"
DEFAULTS = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/{kind}"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DST"}
SLOT = {0: "QB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE", 7: "OP", 16: "DST", 17: "K",
        20: "BE", 21: "IR", 23: "FLEX"}
# Which positions may fill each starting slot.
SLOT_ELIGIBLE = {"QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "DST": {"DST"},
                 "RB/WR": {"RB", "WR"}, "WR/TE": {"WR", "TE"}, "FLEX": {"RB", "WR", "TE"},
                 "OP": {"QB", "RB", "WR", "TE"}}
PRO_TEAM = {0: None, 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB",
            10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LA", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO",
            19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB",
            28: "WAS", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU"}
INJ = {"ACTIVE": None, "NORMAL": None, "QUESTIONABLE": "Q", "DOUBTFUL": "D", "OUT": "O", "INJURY_RESERVE": "IR",
       "SUSPENSION": "SUSP", "DAY_TO_DAY": "DTD"}


class EspnLeague:
    def __init__(self, cfg):
        self.cfg = cfg
        self.url = FANTASY.format(season=cfg.season, league=cfg.league_id)

    def _get(self, views, params=None, filt=None, max_age=900):
        p = [("view", v) for v in views] + list((params or {}).items())
        headers = {"x-fantasy-filter": json.dumps(filt)} if filt else None
        # requests accepts a list of tuples to repeat ?view=
        return get_json(self.url, params=p, headers=headers, cookies=self.cfg.cookies, max_age=max_age)

    def load(self):
        base = self._get(["mSettings", "mTeam", "mRoster", "mMatchup", "mStatus"])
        week = base.get("scoringPeriodId") or base.get("status", {}).get("currentMatchupPeriod") or 1
        # Roster entries carry this week's projections only when scoringPeriodId is passed.
        rost = self._get(["mRoster"], {"scoringPeriodId": week})
        players = safe(lambda: self._players(week), [], "espn players")
        tx = safe(lambda: self._transactions(week), [], "espn transactions")
        return parse_league(base, rost, players, tx, self.cfg)

    def _players(self, week):
        filt = {"players": {"limit": 1200, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                            "filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]}}}
        return self._get(["kona_player_info"], {"scoringPeriodId": week}, filt).get("players", [])

    def _transactions(self, week):
        out = []
        for w in range(1, week + 1):
            d = self._get(["mTransactions2"], {"scoringPeriodId": w}, max_age=6 * 3600)
            out.extend(d.get("transactions", []) or [])
        return out


def _stat(player, season, week, source, split):
    for s in player.get("stats", []) or []:
        if (s.get("seasonId") == season and s.get("statSourceId") == source and s.get("statSplitTypeId") == split
                and (week is None or s.get("scoringPeriodId") == week)):
            return s.get("appliedTotal")
    return None


def parse_player(entry, season, week):
    """Normalize an ESPN player object (roster entry or kona_player_info row)."""
    pool = entry.get("playerPoolEntry") or entry
    p = pool.get("player") or entry.get("player") or {}
    pid = p.get("id") or entry.get("playerId") or pool.get("id")
    pos = POS.get(p.get("defaultPositionId"))
    team = PRO_TEAM.get(p.get("proTeamId"))
    own = p.get("ownership") or {}
    name = p.get("fullName") or ""
    if pos == "DST" and team:
        name = f"{team} D/ST"
    return {
        "espn_id": pid,
        "name": name,
        "pos": pos,
        "team": norm_team(team),
        "injury": INJ.get(p.get("injuryStatus") or entry.get("injuryStatus") or "ACTIVE"),
        "espn_proj": _stat(p, season, week, 1, 1),
        "espn_season_proj": _stat(p, season, 0, 1, 0),
        "espn_season_pts": _stat(p, season, 0, 0, 0),
        "pct_owned": own.get("percentOwned"),
        "pct_change": own.get("percentChange"),
        "pct_started": own.get("percentStarted"),
        "on_team": pool.get("onTeamId") if pool.get("onTeamId") is not None else entry.get("onTeamId"),
        "espn_status": pool.get("status") or entry.get("status"),
        "slot": SLOT.get(entry.get("lineupSlotId")),
    }


def parse_league(base, rost, players_raw, tx, cfg):
    season = base.get("seasonId", cfg.season)
    week = base.get("scoringPeriodId") or 1
    st = base.get("settings", {})
    rs = st.get("rosterSettings", {})
    slots = {}
    for sid, n in (rs.get("lineupSlotCounts") or {}).items():
        name = SLOT.get(int(sid))
        if n and name and name not in ("BE", "IR"):
            slots[name] = slots.get(name, 0) + n
    bench = (rs.get("lineupSlotCounts") or {}).get("20", 6)
    scoring = {int(i["statId"]): i.get("points", 0) for i in st.get("scoringSettings", {}).get("scoringItems", [])}
    ppr = scoring.get(53, 0.5)  # stat 53 = receptions
    sched = st.get("scheduleSettings", {})
    reg_weeks = sched.get("matchupPeriodCount") or 14
    playoff_len = sched.get("playoffMatchupPeriodLength") or 1
    playoff_teams = sched.get("playoffTeamCount") or 6
    rounds = {2: 1, 4: 2, 6: 3, 8: 3}.get(playoff_teams, 3)
    # Matchup periods map to scoring periods (NFL weeks); usually 1:1 in the regular season.
    mp = {int(k): v for k, v in (sched.get("matchupPeriods") or {}).items()}
    playoff_weeks = []
    for m in range(reg_weeks + 1, reg_weeks + 1 + rounds):
        playoff_weeks.extend(mp.get(m) or [])
    if not playoff_weeks:
        start = reg_weeks + 1
        playoff_weeks = list(range(start, start + rounds * playoff_len))
    final_week = max(playoff_weeks) if playoff_weeks else reg_weeks
    trade = st.get("tradeSettings", {})
    acq = st.get("acquisitionSettings", {})

    members = {m.get("id"): m for m in base.get("members", []) or []}
    roster_by_team = {t["id"]: t for t in rost.get("teams", []) or []}
    teams = []
    for t in base.get("teams", []) or []:
        owners = t.get("owners") or []
        mem = members.get(owners[0], {}) if owners else {}
        rec = (t.get("record") or {}).get("overall", {})
        rt = roster_by_team.get(t["id"], t)
        roster = [parse_player(e, season, week) for e in (rt.get("roster") or {}).get("entries", [])]
        teams.append({
            "id": t["id"],
            "name": t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}".strip() or t.get("abbrev"),
            "abbrev": t.get("abbrev"),
            "owner": " ".join(x for x in [mem.get("firstName"), mem.get("lastName")] if x) or mem.get("displayName") or "",
            "owner_first": mem.get("firstName") or (mem.get("displayName") or "").split(" ")[0],
            "owners": owners,
            "wins": rec.get("wins", 0), "losses": rec.get("losses", 0), "ties": rec.get("ties", 0),
            "points_for": rec.get("pointsFor", 0), "points_against": rec.get("pointsAgainst", 0),
            "streak": f"{rec.get('streakType', '')[:1]}{rec.get('streakLength', '')}",
            "seed": t.get("playoffSeed"),
            "waiver_rank": t.get("waiverRank"),
            "faab_left": (acq.get("acquisitionBudget") or 0) - (t.get("transactionCounter") or {}).get("acquisitionBudgetSpent", 0),
            "trades": (t.get("transactionCounter") or {}).get("trades", 0),
            "acquisitions": (t.get("transactionCounter") or {}).get("acquisitions", 0),
            "roster": roster,
        })

    me = None
    if cfg.my_team_id:
        me = int(cfg.my_team_id)
    elif cfg.swid:
        swid = cfg.swid if cfg.swid.startswith("{") else "{" + cfg.swid + "}"
        me = next((t["id"] for t in teams if swid.upper() in [o.upper() for o in t["owners"]]), None)

    schedule = []
    for m in base.get("schedule", []) or []:
        schedule.append({"period": m.get("matchupPeriodId"),
                         "home": (m.get("home") or {}).get("teamId"), "away": (m.get("away") or {}).get("teamId"),
                         "home_pts": (m.get("home") or {}).get("totalPoints"),
                         "away_pts": (m.get("away") or {}).get("totalPoints"),
                         "winner": m.get("winner")})

    trades = []
    for t in tx:
        if t.get("type") in ("TRADE_ACCEPT", "TRADE_UPHOLD") or (
                t.get("status") == "EXECUTED" and any(i.get("type") == "TRADE" for i in t.get("items", []))):
            trades.append({"date": t.get("processDate") or t.get("proposedDate"),
                           "items": [{"player": i.get("playerId"), "from": i.get("fromTeamId"), "to": i.get("toTeamId")}
                                     for i in t.get("items", [])]})

    # Slim copy of every transaction (proposals, declines, waivers, adds/drops) for manager-tendency profiles.
    tx_slim = [{"type": t.get("type"), "status": t.get("status"), "team": t.get("teamId"),
                "bid": t.get("bidAmount") or 0, "period": t.get("scoringPeriodId"), "related": t.get("relatedTransactionId"),
                "id": t.get("id"),
                "items": [{"type": i.get("type"), "player": i.get("playerId"), "from": i.get("fromTeamId"),
                           "to": i.get("toTeamId")} for i in t.get("items", []) or [] if i.get("type") != "LINEUP"]}
               for t in tx if t.get("type") != "ROSTER"]
    players = [parse_player(p, season, week) for p in players_raw]
    log.info("espn league: %d teams, %d players, week %s, me=%s", len(teams), len(players), week, me)
    return {
        "name": st.get("name"), "season": season, "week": week, "size": len(teams),
        "slots": slots or None, "bench": bench, "ppr": ppr, "scoring": scoring,
        "reg_weeks": reg_weeks, "playoff_weeks": playoff_weeks, "playoff_teams": playoff_teams,
        "final_week": final_week,
        "trade_deadline": trade.get("deadlineDate"),
        "faab_budget": acq.get("acquisitionBudget") if acq.get("isUsingAcquisitionBudget") else None,
        "teams": teams, "me": me, "schedule": schedule, "trades": trades, "players": players, "tx": tx_slim,
    }


SNAPSHOT_FILES = ("league/espn.enc.json", "league/espn.json")


def load_snapshot(cfg):
    """League data saved by the site's ESPN sync (ESPN blocks cloud servers, so the browser fetches it).
    Returns (league, synced_iso) or (None, reason)."""
    import copy
    import os

    from .. import crypto

    for path in SNAPSHOT_FILES:
        if not os.path.exists(path):
            continue
        raw = open(path).read()
        if path.endswith(".enc.json"):
            if not cfg.site_password:
                return None, "A synced league file exists but the SITE_PASSWORD secret isn't set, so it can't be decrypted."
            try:
                raw = crypto.decrypt(raw, cfg.site_password)
            except Exception:  # noqa: BLE001
                return None, "Couldn't decrypt the synced league: the password used when syncing doesn't match SITE_PASSWORD."
        snap = json.loads(raw)
        c = copy.copy(cfg)
        c.league_id = cfg.league_id or str(snap.get("league"))
        if not c.swid and snap.get("swid"):
            c.swid = snap["swid"]
        players = [p for p in snap.get("players", [])]
        league = parse_league(snap["main"], snap["roster"], players, [], c)
        league["synced"] = snap.get("synced")
        return league, snap.get("synced")
    return None, None


def load_default_players(season, week):
    """Projections and ownership without a league (PPR default scoring); used before a league ID is set."""
    url = DEFAULTS.format(season=season, kind=3)
    filt = {"players": {"limit": 1000, "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
                        "filterSlotIds": {"value": [0, 2, 4, 6, 16, 17]}}}
    d = get_json(url, params=[("view", "kona_player_info"), ("scoringPeriodId", week)],
                 headers={"x-fantasy-filter": json.dumps(filt)}, max_age=1800)
    return [parse_player(p, season, week) for p in d.get("players", [])]


def scoreboard(season, week):
    """Current spreads/totals per game from ESPN's public scoreboard."""
    d = get_json(SCOREBOARD, params={"dates": season, "seasontype": 2, "week": week}, max_age=1800)
    out = {}
    for ev in d.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): norm_team((c.get("team") or {}).get("abbreviation")) for c in comp.get("competitors", [])}
        odds = (comp.get("odds") or [{}])[0]
        spread, total = odds.get("spread"), odds.get("overUnder")
        if spread is None and odds.get("details"):
            # e.g. "KC -7.5"
            try:
                fav, num = odds["details"].rsplit(" ", 1)
                spread = float(num) if norm_team(fav) == teams.get("home") else -float(num)
            except ValueError:
                pass
        out[(teams.get("away"), teams.get("home"))] = {
            "spread_home": spread, "total": total,
            "status": ((comp.get("status") or {}).get("type") or {}).get("state"),
            "date": comp.get("date")}
    return out
