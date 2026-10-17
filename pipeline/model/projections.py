"""Blended weekly and rest-of-season projections with the reasons behind them."""
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..sources import weather as wx
from . import adjust, injury
from .context import qb_change

CV = {"QB": 0.38, "RB": 0.55, "WR": 0.62, "TE": 0.68, "K": 0.45, "DST": 0.6}
WEIGHTS = {"sleeper": 0.30, "espn": 0.25, "fp": 0.20, "model": 0.25}
SEVERITY = {"IR": 6, "PUP": 6, "SUSP": 5, "O": 4, "D": 3, "Q": 2, "DTD": 1}
WEEK_MULT = {"IR": 0.0, "PUP": 0.0, "SUSP": 0.0, "O": 0.0, "D": 0.25, "Q": 0.9, "DTD": 0.95}
ET = ZoneInfo("America/New_York")


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _sleeper_inj(s):
    return {"Out": "O", "Doubtful": "D", "Questionable": "Q", "IR": "IR", "PUP": "PUP", "Sus": "SUSP",
            "NA": None, "DNR": "O"}.get(s or "", None)


def injury_status(pid, p, inj_week, sleeper):
    """Most severe status across the official report, ESPN and Sleeper, plus a short description."""
    cands, detail = [], []
    r = inj_week.get(pid)
    if r is not None:
        rs = {"Out": "O", "Doubtful": "D", "Questionable": "Q"}.get(r.get("report_status"))
        if rs:
            cands.append(rs)
        if isinstance(r.get("report_primary_injury"), str):
            detail.append(r["report_primary_injury"].lower())
        if isinstance(r.get("practice_status"), str):
            detail.append(r["practice_status"].replace(" Participation in Practice", "").replace(
                "Did Not Participate In Practice", "DNP").lower())
    if p.get("espn_injury"):
        cands.append(p["espn_injury"])
    sl = sleeper.get(p.get("sleeper_id") or "", {})
    si = _sleeper_inj(sl.get("injury_status"))
    if si:
        cands.append(si)
        if sl.get("injury_body_part") and not detail:
            detail.append(str(sl["injury_body_part"]).lower())
    if not cands:
        return None, None
    status = max(cands, key=lambda c: SEVERITY.get(c, 0))
    return status, ", ".join(dict.fromkeys(detail)) or None


def kickoff_dt(row):
    try:
        return dt.datetime.fromisoformat(f"{row['gameday']}T{row['gametime']}").replace(tzinfo=ET)
    except Exception:
        return None


def project(S):
    """Fill S['players'][pid] with projections. S carries every loaded table (see build.py)."""
    reg, usage, ctx, dvp, dvp_meta = S["reg"], S["usage"], S["ctx"], S["dvp"], S["dvp_meta"]
    sched, week, ppr, weeks = S["sched"], S["week"], S["ppr"], S["ros_weeks"]
    playoff_weeks = set(S["playoff_weeks"])
    now = dt.datetime.now(dt.timezone.utc)
    by_team_week = {(r.team, r.week): r for r in sched.itertuples()}
    weather_cache = {}

    inj = S["t"]["injuries"]
    inj_week = {}
    if not inj.empty:
        cur = inj[inj.week == inj.week.max()] if week not in set(inj.week) else inj[inj.week == week]
        inj_week = {r.gsis_id: r._asdict() for r in cur.itertuples()}

    actual_this_week = {}
    st = S["t"]["stats"]
    if not st.empty:
        cw = st[st.week == week]
        actual_this_week = dict(zip(cw.player_id, cw.fantasy_points.fillna(0) + ppr * cw.receptions.fillna(0)))

    statuses = {pid: injury_status(pid, p, inj_week, S["sleeper_players"]) for pid, p in reg.players.items()
                if p.get("pos") in CV and p.get("team")}
    S["starting_qb"] = {}
    for r in sched[sched.week == week].itertuples():
        qid = r.row.get("home_qb_id") if r.home else r.row.get("away_qb_id")
        if isinstance(qid, str):
            S["starting_qb"][r.team] = qid
    vac = adjust.vacated(S, statuses)
    S["vacated"] = vac

    out = {}
    for pid, p in reg.players.items():
        pos, team = p.get("pos"), p.get("team")
        if pos not in CV:
            continue
        u = usage.get(pid, {})
        if not team:
            # Unsigned/retired players can still sit on a fantasy roster: keep them, projected at zero.
            if p.get("rostered"):
                out[pid] = _row(p, u, {}, 0.0, 0.0, 0.0, 0.0, ["No NFL team right now"], None, None, None, True)
                out[pid].update(bye=True)
            continue
        src = {k: v for k, v in (("espn", p.get("espn_proj")), ("sleeper", S["sleeper_proj"].get(week, {}).get(p.get("sleeper_id") or "")))
               if v is not None}
        if not u and not src and not p.get("fp_week_rank") and not p.get("fc_value") and not p.get("rostered"):
            continue  # nobody projects or values him: irrelevant for fantasy
        c = ctx.get(team, {})
        g = by_team_week.get((team, week))
        notes, mult = [], 1.0
        status, inj_detail = statuses.get(pid, (None, None))

        # Our own usage-based number.
        model = None
        if pos in ("QB", "RB", "WR", "TE") and u:
            model = u["base"]
            if pid in vac:
                model += vac[pid]["pts"]
                notes.extend(vac[pid]["notes"])
        elif pos == "K" and g is not None and pd.notna(g.implied):
            model = 3.0 + 0.24 * g.implied
        elif pos == "DST" and g is not None and pd.notna(g.opp_implied):
            model = max(1.0, 13.0 - 0.33 * g.opp_implied)
        if g is None:
            out[pid] = _row(p, u, {}, 0.0, 0.0, 0.0, 0.0, ["On bye this week"], status, inj_detail, None, True)
            out[pid].update(bye=True)
            continue

        # Context multipliers apply to our own number; outside projections already bake these in.
        f_team = 1.0
        if pd.notna(g.implied) and c.get("avg_implied"):
            f_team = float(np.clip((g.implied / c["avg_implied"]) ** 0.8, 0.75, 1.3))
            diff = g.implied - c["avg_implied"]
            if abs(diff) >= 2.5:
                notes.append(f"Vegas has {team} at {g.implied:.1f} pts, {'up' if diff > 0 else 'down'} from its "
                             f"{c['avg_implied']:.1f} average")
        f_dvp = 1.0
        if pos in dvp.get(g.opp, {}):
            f_dvp = dvp[g.opp][pos] ** 0.5
            rank = dvp_meta["ranks"][g.opp][pos]
            if rank <= 8:
                notes.append(f"Soft matchup: {g.opp} is the {ordinal(rank)}-easiest defense for {pos}s (schedule-adjusted)")
            elif rank >= 25:
                notes.append(f"Tough matchup: {g.opp} is the {ordinal(33 - rank)}-hardest defense for {pos}s")
        f_script = 1.0
        if pd.notna(g.spread):
            if pos == "RB":
                f_script = 1 + float(np.clip(g.spread, -10, 10)) * 0.006
            elif pos in ("WR", "TE", "QB"):
                f_script = 1 - float(np.clip(g.spread, -10, 10)) * 0.002
            if g.spread >= 7 and pos == "RB":
                notes.append(f"{team} favored by {g.spread:g}: positive game script for the run game")
            elif g.spread <= -7 and pos in ("WR", "TE"):
                notes.append(f"{team} is a {-g.spread:g}-pt underdog: likely throwing from behind")
        if g.game_id not in weather_cache:
            weather_cache[g.game_id] = wx.game_weather(g.row)
        w = weather_cache[g.game_id]
        f_wx, wx_note = wx.impact(w, pos)
        if wx_note and f_wx < 1:
            notes.append(f"Weather: {wx_note}")
        f_qb = 1.0
        if pos in ("WR", "TE", "RB"):
            changed, f_qb, qb_note = qb_change(c)
            if changed and pos == "RB":
                f_qb = 1 - (1 - f_qb) * 0.4
            if qb_note:
                notes.append(qb_note)
        f_tr, tr_notes = adjust.trench(S, pos, team, g.opp, pid, u) if pos in ("QB", "RB", "WR", "TE") else (1.0, [])
        notes.extend(tr_notes)
        f_coach, coach_note = adjust.coach(S, pid)
        if coach_note:
            notes.append(coach_note)
        if model is not None and pos in ("QB", "RB", "WR", "TE"):
            model *= f_team * f_dvp * f_script * f_wx * f_qb * f_tr * f_coach
        elif model is not None:
            model *= f_wx

        # Expert-rank-implied points are filled in a second pass (needs the whole position pool).
        if model is not None:
            src["model"] = model
        mult = WEEK_MULT.get(status, 1.0)
        if status:
            notes.insert(0, {"O": "Ruled OUT", "IR": "On injured reserve", "PUP": "On PUP list", "SUSP": "Suspended",
                             "D": "Doubtful", "Q": "Questionable", "DTD": "Day-to-day"}[status]
                         + (f" ({inj_detail})" if inj_detail else ""))
        if u:
            _usage_notes(u, notes)
        kick = kickoff_dt({"gameday": g.gameday, "gametime": g.gametime})
        locked = bool(g.final) or (kick is not None and kick <= now)
        row = _row(p, u, src, None, None, None, None, notes, status, inj_detail, g, locked)
        row.update(mult=mult, weather=w if w and not w.get("indoor") else None, f_wx=f_wx, f_coach=f_coach,
                   kickoff=kick.isoformat() if kick else None,
                   actual=round(float(actual_this_week[pid]), 1) if locked and pid in actual_this_week else None)
        out[pid] = row

    _fp_points(out, S)
    for pid, r in out.items():
        if r.get("bye"):
            continue
        srcs = r["sources"]
        if not srcs:
            r.update(proj=0.0, floor=0.0, ceil=0.0, sd=0.0)
            continue
        tot = sum(WEIGHTS[k] for k in srcs)
        mean = sum(WEIGHTS[k] * v for k, v in srcs.items()) / tot
        # ESPN/Sleeper already zero out players ruled out, but not every Q/D nuance; apply ours once.
        mean *= r["mult"]
        r["proj"] = round(mean, 2)
        u = usage.get(pid, {})
        cv_sd = CV[r["pos"]] * max(mean, 1)
        own = u.get("sd") if u.get("games", 0) >= 3 else u.get("prev_sd")
        n = u.get("games", 0)
        sd = np.sqrt((n * own ** 2 + 6 * cv_sd ** 2) / (n + 6)) if own else cv_sd
        r["sd"] = round(float(sd), 2)
        r["floor"] = round(max(0.0, mean - 1.28 * sd), 1)
        r["ceil"] = round(mean + 1.28 * sd, 1)
        if r["mult"] == 0:
            r.update(proj=0.0, floor=0.0, ceil=0.0)
        r["sources"] = {k: round(v, 1) for k, v in srcs.items()}

    _rest_of_season(out, S, by_team_week, weeks, playoff_weeks)
    return out


def _row(p, u, src, proj, floor, ceil, sd, notes, status, inj_detail, g, locked):
    return {"pid": p["pid"], "name": p.get("name"), "pos": p.get("pos"), "team": p.get("team"),
            "age": p.get("age"), "headshot": p.get("headshot"),
            "opp": getattr(g, "opp", None), "home": bool(getattr(g, "home", False)) if g is not None else None,
            "implied": round(float(g.implied), 1) if g is not None and pd.notna(g.implied) else None,
            "spread": float(g.spread) if g is not None and pd.notna(g.spread) else None,
            "sources": src, "proj": proj, "floor": floor, "ceil": ceil, "sd": sd, "notes": notes,
            "injury": status, "injury_detail": inj_detail, "locked": locked, "mult": 1.0}


def _usage_notes(u, notes):
    def pct(x):
        return f"{x * 100:.0f}%"
    if u.get("tgt_share") and u.get("tgt_share_last2") and u["games"] >= 4:
        d = u["tgt_share_last2"] - u["tgt_share"]
        if abs(d) >= 0.06:
            notes.append(f"Target share {'up' if d > 0 else 'down'}: {pct(u['tgt_share_last2'])} last 2 games vs "
                         f"{pct(u['tgt_share'])} on the season")
    if u.get("snap") and u.get("snap_last2") and u["games"] >= 4:
        d = u["snap_last2"] - u["snap"]
        if abs(d) >= 0.12:
            notes.append(f"Snap share {'climbing' if d > 0 else 'falling'}: {pct(u['snap_last2'])} last 2 vs {pct(u['snap'])}")
    if u.get("carry_share") and u.get("carry_share_last2") and u["games"] >= 4:
        d = u["carry_share_last2"] - u["carry_share"]
        if abs(d) >= 0.12:
            notes.append(f"Carry share {'up' if d > 0 else 'down'}: {pct(u['carry_share_last2'])} last 2 vs {pct(u['carry_share'])}")
    if u.get("td") is not None and u.get("td_exp") is not None:
        d = u["td"] - u["td_exp"]
        if d >= 2:
            notes.append(f"TD-lucky: {u['td']:.0f} TDs on {u['td_exp']:.1f} expected, so scoring should cool off")
        elif d <= -1.5:
            notes.append(f"Due for TDs: {u['td']:.0f} on {u['td_exp']:.1f} expected")
    if u.get("exp_ppg") and u["games"] >= 3:
        d = u["ppg"] - u["exp_ppg"]
        if d <= -3:
            notes.append(f"Usage says {u['exp_ppg']:.1f} pts/game but he's scored {u['ppg']:.1f}: buy-low profile")
        elif d >= 4:
            notes.append(f"Scoring {u['ppg']:.1f} pts/game on usage worth {u['exp_ppg']:.1f}: sell-high profile")


def _fp_points(out, S):
    """Turn FantasyPros' weekly rank into points: the Nth-ranked player gets the Nth-best consensus projection."""
    for pos in CV:
        pool = [r for r in out.values() if r["pos"] == pos and not r.get("bye")]
        cons = sorted([np.mean([v for k, v in r["sources"].items() if k in ("espn", "sleeper")])
                       for r in pool if any(k in r["sources"] for k in ("espn", "sleeper"))], reverse=True)
        if len(cons) < 5:
            continue
        for r in pool:
            rank = S["reg"].players[r["pid"]].get("fp_week_rank")
            if rank and rank == rank:
                i = min(len(cons) - 1, max(0.0, rank - 1))
                lo = int(np.floor(i))
                hi = min(lo + 1, len(cons) - 1)
                r["sources"]["fp"] = cons[lo] + (cons[hi] - cons[lo]) * (i - lo)
                r["fp_week_rank"] = rank


def _rest_of_season(out, S, by_team_week, weeks, playoff_weeks):
    usage, dvp, week = S["usage"], S["dvp"], S["week"]
    hist_all = S.get("inj_hist", {})
    for pid, r in out.items():
        pos, team = r["pos"], r["team"]
        u = usage.get(pid, {})
        rk = injury.risk(r, u, hist_all.get(pid, {}), r.get("injury"), r.get("injury_detail"), week)
        if rk:
            h, dur, level, why = rk
            r["inj_risk"] = dict(injury.summarize(level, h, dur), why=why)
        sid = S["reg"].players[pid].get("sleeper_id") or ""
        base = u.get("base")
        if base is None and not r.get("bye") and r.get("mult") and r.get("proj"):
            base = r["proj"] / r["mult"]
        if base is None:
            base = float(np.mean([v for v in (S["sleeper_proj"].get(w, {}).get(sid) for w in weeks) if v] or [0]))
        if not base and pos in ("K", "DST"):
            base = {"K": 8.0, "DST": 6.5}[pos]
        status = r.get("injury")
        ros, sos = [], []
        for i, w in enumerate(weeks):
            if w == week:
                ros.append([w, r.get("proj") or 0.0])
                continue
            g = by_team_week.get((team, w))
            if g is None:
                ros.append([w, 0.0])
                continue
            own = base
            if pos == "K" and pd.notna(g.implied):
                own = 3.0 + 0.24 * g.implied
            elif pos == "DST" and pd.notna(g.opp_implied):
                own = max(1.0, 13.0 - 0.33 * g.opp_implied)
            if pos in dvp.get(g.opp, {}):
                f = dvp[g.opp][pos] ** 0.5
                own *= f
                sos.append(dvp[g.opp][pos])
            ahead = w - week
            vb = S.get("vacated", {}).get(pid)
            if vb and ahead < vb["weeks"]:
                own += vb["pts"]
            if ahead <= 2 and r.get("f_coach"):
                own *= r["f_coach"]
            sl = S["sleeper_proj"].get(w, {}).get(sid)
            val = 0.5 * own + 0.5 * sl if sl is not None else own
            # Long injuries: IR is at least 4 games; "Out" mostly costs this week only.
            if status in ("IR", "PUP") and ahead < 4:
                val = 0.0
            elif status in ("IR", "PUP"):
                val *= 0.85
            elif status == "SUSP" and ahead < 2:
                val = 0.0
            elif status in ("O", "D") and ahead == 1:
                val *= 0.85
            if rk:
                # Chance he's hurt by then (beyond what the current designation already covers).
                val *= injury.availability(rk[0], rk[1], ahead)
            ros.append([w, round(float(val), 2)])
        r["ros"] = ros
        r["sos_ros"] = round(float(np.mean(sos)), 3) if sos else None
        _totals(r, playoff_weeks)
    for pid in adjust.handcuffs(out, S):
        _totals(out[pid], playoff_weeks)


def _totals(r, playoff_weeks):
    pts = [v for _, v in r["ros"]]
    r["ros_pts"] = round(float(sum(pts)), 1)
    active = [v for v in pts if v > 0]
    r["ros_ppg"] = round(float(np.mean(active)), 2) if active else 0.0
    r["playoff_pts"] = round(float(sum(v for w, v in r["ros"] if w in playoff_weeks)), 1)
