"""Rest-of-season value over replacement, market trade value and the gap between them."""
import numpy as np

FLEX_SETS = {"FLEX": {"RB", "WR", "TE"}, "RB/WR": {"RB", "WR"}, "WR/TE": {"WR", "TE"}, "OP": {"QB", "RB", "WR", "TE"}}
PLAYOFF_WEIGHT = 1.3


def starters_by_position(players, slots, teams):
    """How many players at each position start league-wide, flex spots included."""
    pool = sorted(players.values(), key=lambda r: r.get("ros_ppg") or 0, reverse=True)
    used, counts = set(), {}
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        n = teams * slots.get(pos, 0)
        picks = [r for r in pool if r["pos"] == pos][:n]
        used.update(r["pid"] for r in picks)
        counts[pos] = len(picks)
    for slot, elig in FLEX_SETS.items():
        n = teams * slots.get(slot, 0)
        picks = [r for r in pool if r["pos"] in elig and r["pid"] not in used][:n]
        for r in picks:
            used.add(r["pid"])
            counts[r["pos"]] = counts.get(r["pos"], 0) + 1
    return counts


def compute(players, S):
    slots, teams, week = S["slots"], S["teams"], S["week"]
    playoff = set(S["playoff_weeks"])
    counts = starters_by_position(players, slots, teams)
    repl = {}
    for pos, n in counts.items():
        ppg = sorted([r.get("ros_ppg") or 0 for r in players.values() if r["pos"] == pos], reverse=True)
        idx = min(len(ppg) - 1, int(round(n * 1.3))) if ppg else 0
        repl[pos] = ppg[idx] if ppg else 0
    S["replacement"] = {k: round(v, 2) for k, v in repl.items()}
    S["starter_counts"] = counts

    for r in players.values():
        rp = repl.get(r["pos"], 0)
        tot = 0.0
        for w, v in r.get("ros", []):
            if w == week and r.get("locked"):
                continue
            tot += max(v - rp, 0) * (PLAYOFF_WEIGHT if w in playoff else 1.0)
        r["vorp"] = round(tot, 1)

    # Fill market value where FantasyCalc has no number, from FantasyPros ROS rank (log-log fit).
    have = [(r["fp_ros_overall"], r["fc_value"]) for r in players.values()
            if r.get("fc_value") and r.get("fp_ros_overall")]
    fit = None
    if len(have) >= 20:
        x = np.log([a for a, _ in have])
        y = np.log([b for _, b in have])
        fit = np.polyfit(x, y, 1)
    for r in players.values():
        if not r.get("fc_value") and r.get("fp_ros_overall"):
            if fit is not None:
                r["fc_value"] = float(np.exp(np.polyval(fit, np.log(r["fp_ros_overall"]))))
            else:
                # FantasyCalc unreachable: a typical redraft value curve on FantasyPros' ROS rank.
                r["fc_value"] = 10000 * float(np.exp(-(r["fp_ros_overall"] - 1) / 90))
            r["fc_estimated"] = True

    # Put our model on the market's scale, position by position: the Nth-best RB by our projections gets the
    # Nth-highest RB market value. Keeps positional pricing (cheap QBs in 1-QB leagues, etc.) intact.
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        grp = [r for r in players.values() if r["pos"] == pos]
        market = sorted([r["fc_value"] for r in grp if r.get("fc_value")], reverse=True)
        ranked = sorted(grp, key=lambda r: (r.get("vorp", 0), r.get("ros_ppg") or 0), reverse=True)
        for i, r in enumerate(ranked):
            r["model_value"] = round(market[i], 0) if i < len(market) and (r.get("ros_ppg") or 0) > 0 else 0
    for r in players.values():
        if not r.get("fc_value"):
            r["fc_value"] = r["model_value"] * 0.9 if r["model_value"] else 0
        r["fc_value"] = round(float(r["fc_value"]), 0)
        r["edge"] = r["model_value"] - r["fc_value"]

    # Box-score perception: where he ranks at his position in total points (what most managers look at).
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        grp = [r for r in players.values() if r["pos"] == pos]
        grp.sort(key=lambda r: r.get("season_pts") or 0, reverse=True)
        for i, r in enumerate(grp, 1):
            r["pts_rank"] = i if r.get("season_pts") else None
        grp.sort(key=lambda r: r.get("ros_ppg") or 0, reverse=True)
        for i, r in enumerate(grp, 1):
            r["ros_rank"] = i
        grp.sort(key=lambda r: r.get("proj") or 0, reverse=True)
        for i, r in enumerate(grp, 1):
            r["week_rank"] = i
    return players
