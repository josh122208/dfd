"""Waiver pickups, playoff schedule strength, and playoff odds."""
import numpy as np

POS_GROUPS = ("QB", "RB", "WR", "TE")


def waivers(players, free_ids, my_pids, strength, week, trending, faab_budget, faab_left, slots):
    """Rank free agents by how much they improve MY lineup, after dropping my least useful player."""
    base = strength.total(my_pids)
    base_week = strength.weekly(my_pids)
    i_now = strength.idx.get(week)
    droppable = sorted([p for p in my_pids if p in players], key=lambda p: (players[p].get("vorp", 0),
                                                                            players[p].get("fc_value", 0)))
    # Never suggest dropping your only K or DST for a skill player.
    def drop_for(pid):
        pos = players[pid]["pos"]
        for d in droppable:
            dpos = players[d]["pos"]
            if dpos in ("K", "DST") and dpos != pos and sum(1 for x in my_pids if players.get(x, {}).get("pos") == dpos) <= 1:
                continue
            return d
        return None

    cands = sorted(free_ids, key=lambda p: -(players[p].get("vorp", 0) + (players[p].get("proj") or 0) * 3
                                              + trending.get(players[p].get("sleeper_id") or "", {}).get("add", 0) / 2000))[:150]
    out = []
    n = max(len(strength.weeks), 1)
    for pid in cands:
        r = players[pid]
        d = drop_for(pid)
        if d is None:
            continue
        new = [p for p in my_pids if p != d] + [pid]
        gain = (strength.total(new) - base) / n
        nw = strength.weekly(new)
        gain_now = float(nw[i_now] - base_week[i_now]) if i_now is not None else 0.0
        tr = trending.get(r.get("sleeper_id") or "", {})
        if gain <= 0.05 and gain_now <= 0.3:
            continue
        why = []
        u = r.get("usage") or {}
        if u.get("snap_last2") and u.get("snap") and u["snap_last2"] > u["snap"] + 0.1:
            why.append(f"Snap share up to {u['snap_last2'] * 100:.0f}%")
        if u.get("tgt_share_last2") and u["tgt_share_last2"] >= 0.18:
            why.append(f"{u['tgt_share_last2'] * 100:.0f}% target share last 2 games")
        if u.get("carry_share_last2") and u["carry_share_last2"] >= 0.45:
            why.append(f"{u['carry_share_last2'] * 100:.0f}% of team carries last 2 games")
        if tr.get("add", 0) >= 5000:
            why.append(f"Added in {tr['add']:,} Sleeper leagues in 48 hrs")
        if r.get("pct_change") and r["pct_change"] >= 5:
            why.append(f"ESPN roster % up {r['pct_change']:.0f}")
        if gain_now > 1:
            why.append(f"+{gain_now:.1f} to your lineup this week")
        if r.get("injury") in ("IR", "O", "PUP") and gain > 0.5:
            why.append(f"Injured now ({r['injury']}): a stash for the stretch run and playoffs")
        why += [x for x in r.get("notes", []) if "matchup" in x.lower()][:1]
        bid = None
        if faab_budget:
            # Bid share grows with weekly gain and with how hot the player is league-wide.
            share = min(0.45, 0.025 * gain * 4 + min(tr.get("add", 0), 60000) / 60000 * 0.12)
            bid = int(round(max(1, share * (faab_left if faab_left is not None else faab_budget))))
        out.append({"pid": pid, "drop": d, "gain_ros": round(gain, 2), "gain_week": round(gain_now, 1),
                    "adds_48h": tr.get("add", 0), "faab": bid, "why": why})
    out.sort(key=lambda x: -(x["gain_ros"] + 0.35 * x["gain_week"]))
    stream = sorted([x for x in out if x["gain_week"] > 0.5], key=lambda x: -x["gain_week"])[:8]
    return {"adds": out[:25], "streamers": stream}


def rising(players, trending, limit=30):
    """No-league fallback: widely available players whose role is growing."""
    out = []
    for pid, r in players.items():
        if r["pos"] not in POS_GROUPS:
            continue
        own = r.get("pct_owned")
        if own is not None and own > 60:
            continue
        u = r.get("usage") or {}
        tr = trending.get(r.get("sleeper_id") or "", {})
        score = (r.get("ros_ppg") or 0) + tr.get("add", 0) / 5000
        if u.get("snap_last2") and u.get("snap"):
            score += max(0, u["snap_last2"] - u["snap"]) * 20
        if score > 6:
            out.append({"pid": pid, "score": round(score, 2), "adds_48h": tr.get("add", 0),
                        "why": [n for n in r.get("notes", []) if "share" in n.lower()][:2]})
    return sorted(out, key=lambda x: -x["score"])[:limit]


def playoff_schedule(players, sched, dvp, playoff_weeks, pids):
    """Opponents and matchup grades for each player in the fantasy playoff weeks."""
    by = {(r.team, r.week): r for r in sched.itertuples()}
    raw = {}
    for pid, r in players.items():
        if r["pos"] not in POS_GROUPS:
            continue
        games = []
        for w in playoff_weeks:
            g = by.get((r["team"], w))
            if g is None:
                games.append({"week": w, "opp": "BYE", "factor": 0.0})
            else:
                f = dvp.get(g.opp, {}).get(r["pos"], 1.0)
                games.append({"week": w, "opp": ("" if g.home else "@") + g.opp, "factor": round(f, 3)})
        raw[pid] = games
    # Grade against everyone else at the position
    out = {}
    for pos in POS_GROUPS:
        pool = [(pid, np.mean([x["factor"] for x in raw[pid]]) if raw[pid] else 1.0)
                for pid in raw if players[pid]["pos"] == pos]
        vals = np.array([v for _, v in pool]) if pool else np.array([1.0])
        for pid, v in pool:
            pct = (vals < v).mean()
            grade = "A" if pct >= 0.8 else "B" if pct >= 0.6 else "C" if pct >= 0.4 else "D" if pct >= 0.2 else "F"
            if pid in pids or players[pid].get("vorp", 0) > 0:
                out[pid] = {"games": raw[pid], "avg": round(float(v), 3), "grade": grade,
                            "points": players[pid].get("playoff_pts")}
    return out


def playoff_odds(teams, schedule, strength, reg_weeks, week, playoff_teams, sims=4000, seed=7):
    """Monte Carlo the rest of the fantasy regular season with each team's projected weekly lineup points."""
    rng = np.random.default_rng(seed)
    ids = [t["id"] for t in teams]
    ix = {t: i for i, t in enumerate(ids)}
    wins = np.array([t["wins"] + 0.5 * t["ties"] for t in teams], dtype=float)
    pf = np.array([t["points_for"] for t in teams], dtype=float)
    weekly = {t["id"]: strength.weekly(t["pids"]) for t in teams}
    games = [g for g in schedule if g["period"] and week <= g["period"] <= reg_weeks and g["home"] is not None
             and g["away"] is not None]
    if not games:
        return {}
    W = np.tile(wins, (sims, 1))
    P = np.tile(pf, (sims, 1))
    for g in games:
        wi = strength.idx.get(g["period"])
        h, a = ix.get(g["home"]), ix.get(g["away"])
        if wi is None or h is None or a is None:
            continue
        mh, ma = weekly[g["home"]][wi], weekly[g["away"]][wi]
        sh = rng.normal(mh, 22, sims)
        sa = rng.normal(ma, 22, sims)
        W[:, h] += sh > sa
        W[:, a] += sa > sh
        P[:, h] += sh
        P[:, a] += sa
    key = W * 10000 + P  # wins first, points for as the tiebreaker
    order = np.argsort(-key, axis=1)
    made = np.zeros(len(ids))
    for k in range(min(playoff_teams, len(ids))):
        np.add.at(made, order[:, k], 1)
    return {tid: round(float(made[ix[tid]] / sims), 3) for tid in ids}
