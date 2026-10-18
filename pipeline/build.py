"""Entry point: python -m pipeline.build --out site/data

Pulls every source, runs the model and analyses, and writes the JSON the website reads.
"""
import argparse
import datetime as dt
import json
import logging
import math
import os
import time

import numpy as np
import pandas as pd

from . import crypto
from .analysis import extras, lineup, trades
from .analysis.persuasion import PLAYBOOK
from .config import Config
from .model import context, injury, players as pl, projections, values
from .sources import espn, market, news, nflverse
from .util import log, norm_team, r1, safe

DEFAULT_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "K": 1, "DST": 1}


def clean(o):
    """JSON-safe: numpy -> python, NaN/inf -> None, tuples -> lists."""
    if isinstance(o, dict):
        return {str(k): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else round(f, 3)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="site/data")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    started = time.time()
    cfg = Config()
    status = {}

    t = nflverse.load(cfg.season)
    status["nflverse"] = "ok" if not t["stats"].empty else "no stats"
    nfl_week = context.current_week(t["games"])

    league, league_err, synced = None, None, None
    # Live fetch needs ESPN_LEAGUE_ID (+ cookies for private leagues); the browser-synced snapshot is the fallback.
    if cfg.league_id:
        try:
            league = espn.EspnLeague(cfg).load()
            status["espn_league"] = "ok (live)"
        except PermissionError:
            league_err = ("ESPN refused access. Your league is private, so add (or refresh) the ESPN_S2 and ESPN_SWID "
                          "secrets, or use the sync bookmark.")
        except Exception as e:  # noqa: BLE001
            league_err = f"ESPN league failed to load: {e}"
    if league is None:
        snap, info = espn.load_snapshot(cfg)
        if snap:
            league, synced, league_err = snap, info, None
            status["espn_league"] = f"synced {info}"
        elif info:
            league_err = info
    if league_err:
        status["espn_league"] = league_err
        log.error(league_err)

    # A snapshot from an earlier week still has the right rosters; the NFL calendar decides the week.
    week = max(int(league["week"]), nfl_week) if league else nfl_week
    ppr = float(league["ppr"]) if league else cfg.default_ppr
    slots = (league or {}).get("slots") or DEFAULT_SLOTS
    n_teams = league["size"] if league else cfg.default_teams
    playoff_weeks = league["playoff_weeks"] if league else [15, 16, 17]
    reg_weeks = league["reg_weeks"] if league else 14
    final_week = max(playoff_weeks + [reg_weeks]) if playoff_weeks else 17
    ros_weeks = list(range(week, final_week + 1))

    board = safe(lambda: espn.scoreboard(cfg.season, week), {}, "espn scoreboard")
    status["vegas_live"] = "ok" if board else "using nflverse lines"
    sched = context.schedule_table(t["games"], board)
    ctx = context.team_context(t, sched, week)
    dvp, dvp_meta = context.defense_vs_position(t["stats"], ppr)
    reg = pl.Registry(t)

    sp = safe(market.sleeper_players, {}, "sleeper players")
    status["sleeper"] = "ok" if sp else "unavailable"
    for sid, s in sp.items():
        if s["pos"] == "DST" and s.get("team"):
            reg.add(f"DST_{s['team']}", sleeper_id=sid)
        elif s.get("gsis_id") in reg.players and pl._same_person(s.get("name"), reg.players[s["gsis_id"]].get("name")):
            reg.add(s["gsis_id"], sleeper_id=sid)
    proj_key = {0.5: "pts_half_ppr", 1.0: "pts_ppr", 0.0: "pts_std"}.get(ppr, "pts_half_ppr")
    sleeper_proj = market.sleeper_projections(cfg.season, ros_weeks) if sp else {}
    trending = market.sleeper_trending() if sp else {}

    owner_of, slot_of = {}, {}
    teams = []
    if league:
        for tm in league["teams"]:
            tm["pids"] = []
            for e in tm["roster"]:
                pid = reg.match(espn_id=e["espn_id"], name=e["name"], pos=e["pos"], team=e["team"])
                if not pid:
                    continue
                _attach_espn(reg.players[pid], e)
                reg.players[pid]["rostered"] = True
                tm["pids"].append(pid)
                owner_of[pid] = tm["id"]
                slot_of[pid] = e["slot"]
            teams.append(tm)
        espn_players = league["players"]
    else:
        espn_players = safe(lambda: espn.load_default_players(cfg.season, week), [], "espn default players")
    status["espn_players"] = f"{len(espn_players)} players" if espn_players else "unavailable"
    espn_status = {}
    for e in espn_players:
        pid = reg.match(espn_id=e["espn_id"], name=e["name"], pos=e["pos"], team=e["team"])
        if pid:
            _attach_espn(reg.players[pid], e, keep=True)
            espn_status[pid] = e.get("espn_status")

    fp = market.fantasypros(t["fpecr"])
    status["fantasypros"] = f"{len(fp)} ranked" if fp else "unavailable"
    for fpid, d in fp.items():
        pid = reg.match(fp_id=fpid, name=d["name"], pos=d["pos"], team=d.get("team"))
        if pid:
            p = reg.players[pid]
            for k in ("fp_week_rank", "fp_ros_overall", "fp_ros_pos_rank"):
                if d.get(k) is not None and d[k] == d[k]:
                    p[k] = float(d[k])

    fc = safe(lambda: market.fantasycalc(n_teams, ppr), None, "fantasycalc")
    status["fantasycalc"] = f"{len(fc)} values" if fc is not None else "unavailable (using FantasyPros-based values)"
    if fc is not None:
        for row in fc.itertuples():
            pid = reg.match(sleeper_id=row.sleeper_id, espn_id=row.espn_id, name=row.fc_name, pos=row.pos, team=row.team)
            if pid:
                reg.players[pid].update(fc_value=row.fc_value, fc_trend30=row.fc_trend30, fc_rank=row.fc_rank)

    u = pl.weekly_usage(t, reg, ppr)
    usage, _ = pl.summarize(u, t["stats_prev"], ppr)
    if not league and ppr != 1.0:
        # ESPN's league-less projections use full PPR.
        for pid, p in reg.players.items():
            if p.get("espn_proj") is not None:
                p["espn_proj"] = max(0.0, p["espn_proj"] - (1 - ppr) * (usage.get(pid, {}).get("rec_pg") or 0))
    if proj_key != "pts_half_ppr":
        log.warning("Sleeper projections are half-PPR; league uses %s", ppr)

    manual = None
    if not league and cfg.manual_roster:
        manual = {"id": 1, "name": "My team", "owner_first": "Me", "wins": 0, "losses": 0, "ties": 0,
                  "points_for": 0, "trades": 0, "pids": []}
        for nm in cfg.manual_roster:
            hit = next((pid for (n, _pos), pid in reg.by_name.items() if n == pl.norm_name(nm)), None)
            if hit:
                manual["pids"].append(hit)
                reg.players[hit]["rostered"] = True
                owner_of[hit] = 1
        teams = [manual]

    S = {"t": t, "reg": reg, "usage": usage, "ctx": ctx, "dvp": dvp, "dvp_meta": dvp_meta, "sched": sched,
         "week": week, "ppr": ppr, "ros_weeks": ros_weeks, "playoff_weeks": playoff_weeks, "sleeper_proj": sleeper_proj,
         "sleeper_players": sp, "slots": slots, "teams": n_teams,
         "trade_deadline": (league or {}).get("trade_deadline")}
    S["inj_hist"] = injury.history(t, reg, usage, sched, week)
    S["usage_weekly"] = u
    S["trench"] = context.trenches_and_style(t["pbp"], t.get("ftn", pd.DataFrame()))
    # Coach talk: recent news for anyone rostered or fantasy-relevant (cached for 3 hours between runs).
    want = {pid: {"espn_id": p.get("espn_id"), "name": p.get("name"), "team": p.get("team")}
            for pid, p in reg.players.items()
            if p.get("espn_id") and p.get("pos") in ("QB", "RB", "WR", "TE") and p.get("team")
            and (p.get("rostered") or (p.get("fc_value") or 0) >= 400 or (p.get("fp_ros_overall") or 999) <= 220)}
    others = [(p.get("name"), p.get("team")) for p in reg.players.values()
              if p.get("pos") in ("QB", "RB", "WR", "TE", "K") and p.get("name")]
    S["news"], news_feed = safe(lambda: news.player_news(want, ctx, others), ({}, []), "player news")
    status["coach_news"] = f"{len(S['news'])} players" if S["news"] else "unavailable"
    team_news = safe(lambda: news.team_news(set(sched.team), ctx), {}, "team news")
    P = projections.project(S)
    # Every analysis below indexes P by roster pid; drop anything that couldn't be projected (non-fantasy positions).
    for tm in teams:
        tm["pids"] = [p for p in tm["pids"] if p in P]

    byes = {}
    for team in set(sched.team):
        weeks_played = set(sched[sched.team == team].week)
        b = [w for w in range(1, 19) if w not in weeks_played and w <= int(sched.week.max())]
        byes[team] = b[0] if b else None
    sched_byes = {(team, w) for team, w in ((tm, byes[tm]) for tm in byes) if w}
    for pid, r in P.items():
        p = reg.players[pid]
        for k in ("fc_value", "fc_trend30", "fc_rank", "fp_ros_overall", "fp_ros_pos_rank", "pct_owned",
                  "pct_change", "pct_started", "sleeper_id", "espn_id", "exp"):
            if p.get(k) is not None:
                r[k] = p[k]
        us = usage.get(pid)
        if us:
            r["usage"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in us.items()}
            r["season_pts"] = round(sum(x for _, x in us["weekly"]), 1)
        elif p.get("espn_season_pts"):
            r["season_pts"] = p["espn_season_pts"]
        r["bye_week"] = byes.get(r["team"])
        r["on_team"] = owner_of.get(pid)
        r["slot"] = slot_of.get(pid)
        sl = sp.get(p.get("sleeper_id") or "", {})
        if sl.get("injury_notes"):
            r["injury_notes"] = sl["injury_notes"]
        if sl.get("depth_order"):
            r["depth"] = sl["depth_order"]
        if S["news"].get(pid):
            r["news"] = S["news"][pid]
    values.compute(P, S)

    strength = lineup.Strength(P, slots, ros_weeks, week, playoff_weeks)
    me = (league or {}).get("me") or (1 if manual else None)
    data = {"meta": {}, "players": {}, "teams": [], "playbook": PLAYBOOK}
    lists = set()

    if teams and me is not None and any(tm["id"] == me for tm in teams):
        my = next(tm for tm in teams if tm["id"] == me)
        rows = [dict(P[p], slot=slot_of.get(p)) for p in my["pids"] if p in P]
        opp_rows = None
        if league:
            m = next((g for g in league["schedule"] if g["period"] == _matchup_period(league, week)
                      and me in (g["home"], g["away"])), None)
            if m:
                oid = m["away"] if m["home"] == me else m["home"]
                ot = next((tm for tm in teams if tm["id"] == oid), None)
                if ot:
                    opp_rows = [dict(P[p], slot=slot_of.get(p)) for p in ot["pids"] if p in P]
                    data["matchup_opp"] = oid
        data["lineup"] = lineup.lineup_report(me, rows, slots, opp_rows)
        sells = trades.sell_high(P, my["pids"])
        data["sell_high"] = sells
        lists.update(my["pids"])
        if league and len(teams) > 1:
            # Simulate the season first: each team's playoff odds decide which weeks its trades should help.
            sim = extras.season_sim(teams, league["schedule"], strength, reg_weeks, week, league["playoff_teams"], me)
            odds = sim["odds"]
            lev = {me: {g["week"]: g["leverage"] for g in sim.get("me", {}).get("games", [])}}
            strength.push_weights(odds, lev, reg_weeks)
            prof = trades.team_profiles(teams, P, strength, me, slots, week, sched_byes)
            tend = trades.manager_tendencies(league.get("tx"), teams, reg, league.get("faab_budget"))
            for p_ in prof.values():
                p_["playoff_odds"] = odds.get(p_["id"])
                p_["tendency"] = tend.get(p_["id"], {})
            data["profiles"] = prof
            data["season"] = season_report(sim.get("me"), my["pids"], P, strength, reg_weeks, prof, byes)
            buys = trades.buy_low(P, owner_of, me)
            data["buy_low"] = buys
            finder = trades.TradeFinder(S, P, strength, teams, me, prof)
            data["trades"] = finder.run([s["pid"] for s in sells])
            data["proj_buys"] = trades.projection_buys(finder, owner_of, week)
            # Buy/Sell board: every buy-low (usage-based and projection-based) and sell-high, with trades at
            # perceived prices; plus overpriced players on other rosters to avoid.
            board_buys = list({b["pid"]: b for b in buys[:15]}.values())
            seen_b = {b["pid"] for b in board_buys}
            for x in data["proj_buys"]["ros"]:
                if x["pid"] not in seen_b:
                    board_buys.append({"pid": x["pid"], "why": x["why"]})
                    seen_b.add(x["pid"])
            data["board"] = trades.buy_sell_board(finder, owner_of, board_buys[:18], sells[:10])
            others = [p for p, tid in owner_of.items() if tid != me]
            data["board"]["avoid"] = trades.sell_high(P, others)[:15]
            for side in ("buy", "sell"):
                for x in data["board"][side]:
                    lists.add(x["pid"])
                    for e in x["offers"]:
                        lists.update(e["give"] + e["get"] + e["my_drops"] + e["their_drops"])
                        for st in e.get("ladder", []):
                            lists.update(st["give"] + st["get"])
            lists.update(x["pid"] for x in data["board"]["avoid"])
            for kind in ("ros", "next"):
                for x in data["proj_buys"][kind]:
                    lists.update([x["pid"]] + ((x.get("offer") or {}).get("give") or []))
            data["matchup_trades"] = finder.run_matchups((sim.get("me") or {}).get("games", []), data["trades"])
            for mt in data["matchup_trades"]:
                for e in mt["trades"]:
                    lists.update(e["give"] + e["get"] + e["my_drops"] + e["their_drops"])
                    for st in e.get("ladder", []):
                        lists.update(st["give"] + st["get"])
            for e in data["trades"]:
                lists.update(e["give"] + e["get"] + e["my_drops"] + e["their_drops"])
                for st in e.get("ladder", []):
                    lists.update(st["give"] + st["get"])
            lists.update(b["pid"] for b in buys)
        free = [pid for pid, r in P.items() if pid not in owner_of and r["pos"] in ("QB", "RB", "WR", "TE", "K", "DST")
                and (not league or espn_status.get(pid) in (None, "FREEAGENT", "WAIVERS"))
                and (league or (r.get("pct_owned") is not None and r["pct_owned"] < 50))]
        my_team = next(tm for tm in teams if tm["id"] == me)
        data["waivers"] = extras.waivers(P, free, my["pids"], strength, week, trending,
                                         (league or {}).get("faab_budget"), my_team.get("faab_left"), slots, me,
                                         espn_status, my_team.get("waiver_rank"), n_teams)
        data["waivers"]["waiver_rank"] = my_team.get("waiver_rank")
        for w in data["waivers"]["adds"]:
            lists.update([w["pid"], w["drop"]])
        for x in data["waivers"].get("stashes", []):
            lists.update([x["pid"], x["for"]])
    else:
        data["rising"] = extras.rising(P, trending)
        lists.update(x["pid"] for x in data["rising"])

    targets = set(lists)
    data["playoffs"] = extras.playoff_schedule(P, sched, dvp, playoff_weeks, targets)

    # Publish everyone fantasy-relevant, plus anyone referenced above.
    keep = {pid for pid, r in P.items() if pid in lists or r.get("on_team") is not None or (r.get("vorp") or 0) > 0
            or (r.get("proj") or 0) >= 4 or (r.get("value") or 0) >= 300}
    track_changes(data, P, keep, load_previous(cfg), news_feed, owner_of, me)
    data["players"] = {pid: _public(P[pid]) for pid in keep}
    data["teams"] = [{"id": tm["id"], "name": tm["name"], "owner": tm.get("owner"), "abbrev": tm.get("abbrev"),
                      "record": f"{tm['wins']}-{tm['losses']}" + (f"-{tm['ties']}" if tm["ties"] else ""),
                      "points_for": tm["points_for"], "pids": [p for p in tm["pids"] if p in data["players"]],
                      "faab_left": tm.get("faab_left")} for tm in teams]
    data["team_ctx"] = {k: {kk: vv for kk, vv in v.items() if kk != "qb_epa"} for k, v in ctx.items()}
    tr = S["trench"][0]
    for k, v in data["team_ctx"].items():
        v.update({kk: vv for kk, vv in tr.get(k, {}).items() if kk.endswith("_rank")})
        if team_news.get(k):
            v["coach_news"] = team_news[k]
    data["dvp"] = {"factor": dvp, "rank": dvp_meta.get("ranks", {}), "allowed": dvp_meta.get("allowed", {})}
    data["meta"] = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "season": cfg.season, "week": week, "mode": "league" if league else ("manual" if manual else "public"),
        "league_name": (league or {}).get("name"), "league_error": league_err, "me": me, "league_synced": synced,
        "slots": slots, "ppr": ppr, "teams": n_teams, "playoff_weeks": playoff_weeks, "reg_weeks": reg_weeks,
        "ros_weeks": ros_weeks, "trade_deadline": (league or {}).get("trade_deadline"),
        "faab_budget": (league or {}).get("faab_budget"), "replacement": S.get("replacement"),
        "sources": status, "build_seconds": round(time.time() - started, 1),
    }

    os.makedirs(args.out, exist_ok=True)
    payload = json.dumps(clean(data), separators=(",", ":"))
    for f in ("data.json", "data.enc.json"):
        try:
            os.remove(os.path.join(args.out, f))
        except FileNotFoundError:
            pass
    if cfg.site_password:
        with open(os.path.join(args.out, "data.enc.json"), "w") as f:
            f.write(crypto.encrypt(payload, cfg.site_password))
        log.info("wrote encrypted data (%d KB raw)", len(payload) // 1024)
    else:
        with open(os.path.join(args.out, "data.json"), "w") as f:
            f.write(payload)
        log.info("wrote data.json (%d KB)", len(payload) // 1024)
    log.info("done in %.1fs: %d players, mode=%s, week=%s", time.time() - started, len(data["players"]),
             data["meta"]["mode"], week)


def load_previous(cfg):
    """The last published data (downloaded by the workflow into PREV_DATA), for change tracking."""
    path = os.environ.get("PREV_DATA")
    if not path or not os.path.exists(path) or os.path.getsize(path) < 100:
        return None
    try:
        raw = open(path).read()
        if '"ct"' in raw[:200] and cfg.site_password:
            raw = crypto.decrypt(raw, cfg.site_password)
        return json.loads(raw)
    except Exception as e:  # noqa: BLE001
        log.warning("previous data unreadable: %s", e)
        return None


def track_changes(data, P, keep, prev, feed, owner_of, me):
    """Value history per player, change since the last update and over ~7 days, the news feed with each
    story's model impact, and the biggest movers."""
    today = dt.date.today()
    prev_players = (prev or {}).get("players", {})
    prev_hist = (prev or {}).get("history", {})
    since = ((prev or {}).get("meta") or {}).get("generated")
    history = {}
    for pid in keep:
        r = P[pid]
        h = [x for x in prev_hist.get(pid, []) if x[0] != today.isoformat()][-20:]
        h.append([today.isoformat(), r.get("value") or 0, r.get("ros_ppg") or 0])
        history[pid] = h
        pp = prev_players.get(pid)
        if pp:
            d = {"value": (r.get("value") or 0) - (pp.get("value") or 0),
                 "ros_ppg": round((r.get("ros_ppg") or 0) - (pp.get("ros_ppg") or 0), 2),
                 "proj": round((r.get("proj") or 0) - (pp.get("proj") or 0), 1) if pp.get("opp") == r.get("opp") else None,
                 "fc": (r.get("fc_value") or 0) - (pp.get("fc_value") or 0) if pp.get("fc_value") and r.get("fc_value") else None,
                 "fp": (r.get("fp_value") or 0) - (pp.get("fp_value") or 0) if pp.get("fp_value") and r.get("fp_value") else None,
                 "model": (r.get("model_value") or 0) - (pp.get("model_value") or 0),
                 "since": since}
            if (pp.get("injury") or None) != (r.get("injury") or None):
                d["injury"] = [pp.get("injury"), r.get("injury")]
            r["delta"] = d
        old = [x for x in h[:-1] if (today - dt.date.fromisoformat(x[0])).days >= 6]
        if old:
            o = old[-1]
            r["delta7"] = {"value": (r.get("value") or 0) - o[1], "ros_ppg": round((r.get("ros_ppg") or 0) - o[2], 2),
                           "since": o[0]}
    data["history"] = history
    # News feed: stories about published players, each with that player's model impact attached on the site.
    data["news_feed"] = [f for f in feed if any(p in keep for p in f.get("pids", []))][:150]
    rel = [pid for pid in keep if owner_of.get(pid) is not None or (P[pid].get("value") or 0) >= 1000]
    movers = {}
    for key in ("delta", "delta7"):
        ch = [(pid, P[pid][key]["value"]) for pid in rel if P[pid].get(key) and abs(P[pid][key]["value"]) >= 150]
        ch.sort(key=lambda x: x[1])
        movers[key] = {"up": [p for p, v in reversed(ch) if v > 0][:12], "down": [p for p, v in ch if v < 0][:12]}
    data["movers"] = movers
    data["meta_prev"] = since


def season_report(me_sim, my_pids, P, strength, reg_weeks, prof, byes):
    """Your season outlook: odds, wins needed, must-win weeks, bye crunches, and how the advice adapts."""
    if not me_sim:
        return None
    weeks = strength.weeks
    weekly = strength.weekly(my_pids, played=True)
    reg = [(w, weekly[i]) for i, w in enumerate(weeks) if w <= reg_weeks]
    avg = float(np.mean([v for _, v in reg])) if reg else 0
    crunch = []
    for w, v in reg:
        if v < avg * 0.9:
            out = [p for p in my_pids if P[p].get("bye_week") == w and (P[p].get("ros_ppg") or 0) >= 7]
            crunch.append({"week": w, "proj": round(float(v), 1), "below_avg": round(avg - float(v), 1),
                           "on_bye": out})
    odds = me_sim["odds"]
    mode = "push" if odds < 0.35 else "bubble" if odds < 0.7 else "contender"
    pw = 1.3 * min(1.0, odds / 0.5)
    games = me_sim["games"]
    for g in games:
        g["opp_name"] = prof.get(g["opp"], {}).get("owner")
    key = max(games, key=lambda g: g.get("leverage", 0)) if games else None
    tips = []
    tips.append(f"Projected finish: {me_sim['proj_wins']:.0f}-{me_sim['proj_losses']:.0f}. The last playoff spot usually "
                f"takes about {me_sim['wins_needed']:.0f} wins, so you need roughly {me_sim['need_more']:.0f} more in "
                f"{me_sim['remaining']} games.")
    if mode != "contender":
        tips.append(f"Advice is in {'playoff-push' if mode == 'push' else 'bubble'} mode: trades and pickups favor points in "
                    f"the regular-season weeks that swing your odds most, and playoff-week points count {pw / 1.3:.0%} as "
                    f"much as usual until you're likelier to get there.")
    if key:
        tips.append(f"Biggest swing: Week {key['week']} vs {key.get('opp_name') or 'your opponent'}. A win puts your odds at "
                    f"{key['odds_if_win']:.0%}, a loss at {key['odds_if_lose']:.0%}.")
    if me_sim.get("schedule_rank"):
        r = me_sim["schedule_rank"]
        tips.append(f"Your remaining schedule is the {_ord(r)}-toughest in the league by opponents' projected lineups.")
    for c in crunch[:2]:
        names = ", ".join(P[p]["name"] for p in c["on_bye"]) or "several starters"
        tips.append(f"Week {c['week']} is a crunch ({c['below_avg']:.0f} pts below your average) with {names} on bye. "
                    "Plan a pickup or trade for depth before then.")
    return dict(me_sim, mode=mode, playoff_week_weight=round(pw / 1.3, 2), crunch=crunch, avg_weekly=round(avg, 1),
                tips=tips)


def _ord(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _matchup_period(league, week):
    # Regular season matchup periods line up with NFL weeks in nearly every ESPN league.
    return week if week <= league["reg_weeks"] else league["reg_weeks"] + 1 + (week - league["reg_weeks"] - 1)


def _attach_espn(p, e, keep=False):
    for src, dst in (("espn_proj", "espn_proj"), ("injury", "espn_injury"), ("pct_owned", "pct_owned"),
                     ("pct_change", "pct_change"), ("pct_started", "pct_started"), ("espn_season_pts", "espn_season_pts")):
        if e.get(src) is not None and not (keep and p.get(dst) is not None):
            p[dst] = e[src]


PUBLIC = ("pid", "name", "pos", "team", "age", "headshot", "opp", "home", "implied", "spread", "proj", "floor", "ceil",
          "sd", "sources", "notes", "injury", "injury_detail", "injury_notes", "locked", "actual", "kickoff", "weather",
          "bye", "bye_week", "ros", "ros_pts", "ros_ppg", "playoff_pts", "sos_ros", "vorp", "value", "value_sources", "perceived",
          "fc_value", "fp_value", "delta", "delta7",
          "fc_trend30", "model_value", "edge", "fp_ros_overall", "fp_ros_pos_rank", "fp_week_rank", "pct_owned",
          "pct_change", "season_pts", "pts_rank", "ros_rank", "week_rank", "on_team", "slot", "usage", "depth",
          "inj_risk", "handcuff", "news")


def _public(r):
    return {k: r.get(k) for k in PUBLIC if r.get(k) is not None}


if __name__ == "__main__":
    main()
