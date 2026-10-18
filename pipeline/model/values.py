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

    # Trade value = consensus of three views, all on FantasyCalc's scale:
    #   market  (FantasyCalc: what real managers trade players for)          45%
    #   experts (FantasyPros rest-of-season rank, mapped onto that scale)    35%
    #   model   (our rest-of-season value over replacement, same mapping)    20%
    # A single source can be stale or quirky (and FantasyCalc only covers ~200 players); the blend is steadier.
    market_all = sorted([r["fc_value"] for r in players.values() if r.get("fc_value")], reverse=True)
    if len(market_all) < 50:
        # FantasyCalc unreachable: a typical redraft value curve stands in for the market's shape.
        market_all = [10000 * float(np.exp(-i / 90)) for i in range(600)]

    def at_rank(dist, rank):
        """Value at a (fractional) 1-based rank in a descending list, extended with a gentle tail."""
        i = max(rank - 1, 0)
        if i < len(dist) - 1:
            lo = int(i)
            return dist[lo] + (dist[lo + 1] - dist[lo]) * (i - lo)
        return dist[-1] * float(np.exp(-(i - len(dist) + 1) / 60))

    by_pos_market = {}
    for r in players.values():
        if r.get("fc_value"):
            by_pos_market.setdefault(r["pos"], []).append(r["fc_value"])
    for v in by_pos_market.values():
        v.sort(reverse=True)
    for r in players.values():
        if r.get("fp_ros_overall"):
            r["fp_value"] = round(at_rank(market_all, r["fp_ros_overall"]))
        elif r.get("fp_ros_pos_rank") and len(by_pos_market.get(r["pos"], [])) >= 10:
            r["fp_value"] = round(at_rank(by_pos_market[r["pos"]], r["fp_ros_pos_rank"]))
    # Model on the market scale, position by position: the Nth-best RB by our projections gets the
    # Nth-highest RB value (keeps positional pricing such as cheap QBs in 1-QB leagues).
    for pos in ("QB", "RB", "WR", "TE", "K", "DST"):
        grp = [r for r in players.values() if r["pos"] == pos]
        dist = by_pos_market.get(pos) or sorted([r["fp_value"] for r in grp if r.get("fp_value")], reverse=True)
        ranked = sorted(grp, key=lambda r: (r.get("vorp", 0), r.get("ros_ppg") or 0), reverse=True)
        for i, r in enumerate(ranked):
            r["model_value"] = round(at_rank(dist, i + 1)) if dist and (r.get("ros_ppg") or 0) > 0 else 0
    for r in players.values():
        parts = [(0.45, r.get("fc_value")), (0.35, r.get("fp_value")), (0.20, r.get("model_value"))]
        parts = [(w, v) for w, v in parts if v]
        if not parts:
            r["value"] = 0
        elif len(parts) == 1 and r.get("model_value") and not r.get("fc_value") and not r.get("fp_value"):
            # Only our model rates him (no market, no experts): don't let it set his price alone.
            r["value"] = round(0.6 * r["model_value"])
        else:
            r["value"] = round(sum(w * v for w, v in parts) / sum(w for w, _ in parts))
        r["value_sources"] = {k: r.get(k) for k in ("fc_value", "fp_value", "model_value") if r.get(k)}
        r["edge"] = (r.get("model_value") or 0) - r["value"]

    # Perceived price: what a typical manager thinks he's worth. Half the market's view (FantasyCalc and
    # FantasyPros, no model), half what his points-per-game rank at the position would fetch (box-score bias).
    for pos in ("QB", "RB", "WR", "TE"):
        grp = [r for r in players.values() if r["pos"] == pos]
        dist = sorted([r["value"] for r in grp if r.get("value")], reverse=True)
        scored = [r for r in grp if (r.get("usage") or {}).get("games", 0) >= 3]
        scored.sort(key=lambda r: r["usage"]["ppg"], reverse=True)
        box = {r["pid"]: at_rank(dist, i + 1) for i, r in enumerate(scored)} if dist else {}
        for r in grp:
            mk = [v for v in (r.get("fc_value"), r.get("fp_value")) if v]
            market = sum(mk) / len(mk) if mk else r.get("value") or 0
            r["perceived"] = round(0.5 * market + 0.5 * box[r["pid"]]) if r["pid"] in box else round(market)

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
