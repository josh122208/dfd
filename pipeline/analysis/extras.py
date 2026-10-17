"""Waiver pickups, playoff schedule strength, and playoff odds."""
import numpy as np

POS_GROUPS = ("QB", "RB", "WR", "TE")


def waivers(players, free_ids, my_pids, strength, week, trending, faab_budget, faab_left, slots, me=None,
            waiver_status=None, waiver_rank=None, n_teams=12):
    """Rank free agents by how much they improve MY lineup, after dropping my least useful player."""
    base = strength.total(my_pids, me)
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
        gain = (strength.total(new, me) - base) / n
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
                    "adds_48h": tr.get("add", 0), "faab": bid, "why": why,
                    "on_waivers": (waiver_status or {}).get(pid) == "WAIVERS"})
    out.sort(key=lambda x: -(x["gain_ros"] + 0.35 * x["gain_week"]))
    # Waiver-order leagues: free agents cost nothing; a claim costs your priority, so save it for real upgrades.
    if not faab_budget:
        claims = [x for x in out if x["on_waivers"]]
        best = claims[0]["gain_ros"] if claims else 0
        pr = f"#{waiver_rank} " if waiver_rank else ""
        for x in out:
            if not x["on_waivers"]:
                x["how"] = "Add now"
            elif x["gain_ros"] >= max(1.5, 0.7 * best) or (x["gain_week"] >= 4 and x is claims[0]):
                x["how"] = f"Worth your {pr}claim"
            else:
                x["how"] = "Wait until he clears"
    stream = sorted([x for x in out if x["gain_week"] > 0.5], key=lambda x: -x["gain_week"])[:8]
    # Handcuffs: backups who'd inherit a starter's job, available now, plus insurance for your own starters.
    mine = set(my_pids)
    free = set(free_ids)
    stashes = []
    for pid in free:
        hc = players[pid].get("handcuff")
        if hc and hc["contingent_pts"] >= 1.5:
            stashes.append({"pid": pid, "for": hc["for"], "yours": hc["for"] in mine,
                            "contingent": hc["contingent_pts"], "if_out": hc["if_starter_out"]})
    for pid, r in players.items():
        hc = r.get("handcuff")
        if hc and hc["for"] in mine and pid not in mine and pid not in free:
            stashes.append({"pid": pid, "for": hc["for"], "yours": True, "contingent": hc["contingent_pts"],
                            "if_out": hc["if_starter_out"], "owner": r.get("on_team")})
    stashes.sort(key=lambda x: (-x["yours"], -x["contingent"]))
    return {"adds": out[:25], "streamers": stream, "stashes": stashes[:12]}


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


def season_sim(teams, schedule, strength, reg_weeks, week, playoff_teams, me=None, sims=5000, seed=7):
    """Monte Carlo the rest of the fantasy regular season with each team's projected weekly lineup points.

    Returns playoff odds for everyone and, for your team: projected record, wins likely needed, each remaining
    matchup with win odds, and leverage (how much winning that week moves your playoff odds)."""
    rng = np.random.default_rng(seed)
    ids = [t["id"] for t in teams]
    ix = {t: i for i, t in enumerate(ids)}
    wins = np.array([t["wins"] + 0.5 * t["ties"] for t in teams], dtype=float)
    pf = np.array([t["points_for"] for t in teams], dtype=float)
    weekly = {t["id"]: strength.weekly(t["pids"], played=True) for t in teams}
    games = [g for g in schedule if g["period"] and week <= g["period"] <= reg_weeks and g["home"] is not None
             and g["away"] is not None]
    if not games:
        return {"odds": {}}
    W = np.tile(wins, (sims, 1))
    P = np.tile(pf, (sims, 1))
    my_results, my_games = {}, []
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
        if me in (g["home"], g["away"]):
            mine_home = g["home"] == me
            won = (sh > sa) if mine_home else (sa > sh)
            my_results[g["period"]] = won
            opp = g["away"] if mine_home else g["home"]
            my_games.append({"week": g["period"], "opp": opp,
                             "me": round(float(mh if mine_home else ma), 1), "them": round(float(ma if mine_home else mh), 1),
                             "win_prob": round(float(won.mean()), 3)})
    key = W * 10000 + P  # wins first, points for as the tiebreaker
    order = np.argsort(-key, axis=1)
    made = np.zeros((sims, len(ids)), dtype=bool)
    for k in range(min(playoff_teams, len(ids))):
        made[np.arange(sims), order[:, k]] = True
    odds = {tid: round(float(made[:, ix[tid]].mean()), 3) for tid in ids}
    out = {"odds": odds}
    if me in ix:
        i = ix[me]
        last_in = W[np.arange(sims), order[:, min(playoff_teams, len(ids)) - 1]]
        need = float(np.median(last_in))
        remaining = len(my_games)
        mm = made[:, i]
        for g in my_games:
            won = my_results[g["week"]]
            p_w = mm[won].mean() if won.any() else 0.0
            p_l = mm[~won].mean() if (~won).any() else 0.0
            g["leverage"] = round(float(p_w - p_l), 3)
            g["odds_if_win"], g["odds_if_lose"] = round(float(p_w), 3), round(float(p_l), 3)
        # Strength of remaining schedule: average projected opponent points, ranked against every team's slate.
        opp_pts = {}
        for g in games:
            wi = strength.idx.get(g["period"])
            if wi is None:
                continue
            for a, b in ((g["home"], g["away"]), (g["away"], g["home"])):
                opp_pts.setdefault(a, []).append(weekly[b][wi])
        avg = {t: np.mean(v) for t, v in opp_pts.items() if v}
        rank = sorted(avg, key=lambda t: -avg[t]).index(me) + 1 if me in avg else None
        out["me"] = {"odds": odds[me], "proj_wins": round(float(W[:, i].mean()), 1),
                     "proj_losses": round(float(teams[i]["losses"] + remaining - (W[:, i].mean() - wins[i])), 1),
                     "wins_now": float(wins[i]), "losses_now": int(teams[i]["losses"]), "remaining": remaining,
                     "wins_needed": need, "need_more": max(0.0, round(need - wins[i], 1)),
                     "schedule_rank": rank, "games": sorted(my_games, key=lambda g: g["week"])}
    return out
