"""Optimal lineups: this week's start/sit for a roster, and fast rest-of-season lineup strength for trade math."""
import math

import numpy as np

from ..sources.espn import SLOT_ELIGIBLE

DEDICATED = ("QB", "RB", "WR", "TE", "K", "DST")
FLEX_ORDER = ("RB/WR", "WR/TE", "FLEX", "OP")


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def win_prob(a_mean, a_sd, b_mean, b_sd):
    s = math.sqrt(max(a_sd ** 2 + b_sd ** 2, 1e-6))
    return phi((a_mean - b_mean) / s)


def slot_list(slots):
    out = []
    for s in DEDICATED + FLEX_ORDER:
        out += [s] * int(slots.get(s, 0))
    return out


def score_of(r):
    """Points a player contributes this week: actual if his game is over, else projection."""
    if r.get("locked") and r.get("actual") is not None:
        return r["actual"]
    return r.get("proj") or 0.0


def optimize_week(roster, slots):
    """roster: list of player rows (+ 'slot' = current ESPN slot). Locked players can't move."""
    order = slot_list(slots)
    assigned, used = [], set()
    open_slots = list(order)
    # Players whose games started stay where ESPN has them.
    for r in roster:
        if r.get("locked") and r.get("slot") in open_slots:
            open_slots.remove(r["slot"])
            assigned.append((r["slot"], r))
            used.add(r["pid"])
    # Without ESPN slot data (slot is None) a locked player can still be placed using his actual points.
    movable = [r for r in roster if r["pid"] not in used and (not r.get("locked") or r.get("slot") is None)
               and r.get("slot") != "IR" and r.get("injury") not in ("IR",)]
    movable.sort(key=score_of, reverse=True)
    for s in [x for x in DEDICATED + FLEX_ORDER for _ in range(open_slots.count(x))]:
        elig = SLOT_ELIGIBLE[s]
        pick = next((r for r in movable if r["pos"] in elig and r["pid"] not in used), None)
        if pick:
            used.add(pick["pid"])
            assigned.append((s, pick))
        else:
            assigned.append((s, None))
    rank = {s: i for i, s in enumerate(order)}
    assigned.sort(key=lambda x: rank.get(x[0], 99))
    bench = [r for r in roster if r["pid"] not in used]
    return assigned, bench


def lineup_report(team, rows, slots, opp_rows=None):
    """Recommended lineup, the moves to make vs ESPN, close calls with win odds, and matchup odds."""
    assigned, bench = optimize_week(rows, slots)
    starters = [r for _, r in assigned if r]
    total = sum(score_of(r) for r in starters)
    sd = math.sqrt(sum((0 if r.get("locked") else (r.get("sd") or 0)) ** 2 for r in starters))
    current = {r["pid"] for r in rows if r.get("slot") not in (None, "BE", "IR")}
    rec = {r["pid"] for r in starters}
    by = {r["pid"]: r for r in rows}
    moves = []
    ins = [by[p] for p in rec - current]
    outs = [by[p] for p in current - rec]
    ins.sort(key=score_of, reverse=True)
    outs.sort(key=score_of)
    for a, b in zip(ins, outs + [None] * len(ins)):
        moves.append({"start": a["pid"], "sit": b["pid"] if b else None,
                      "gain": round(score_of(a) - (score_of(b) if b else 0), 1)})
    for b in outs[len(ins):]:
        moves.append({"start": None, "sit": b["pid"], "gain": 0})

    matchup = None
    if opp_rows:
        oa, _ = optimize_week(opp_rows, slots)
        os_ = [r for _, r in oa if r]
        ot = sum(score_of(r) for r in os_)
        osd = math.sqrt(sum((0 if r.get("locked") else (r.get("sd") or 0)) ** 2 for r in os_))
        matchup = {"me": round(total, 1), "opp": round(ot, 1), "win_prob": round(win_prob(total, sd, ot, osd), 3)}

    wp = matchup["win_prob"] if matchup else 0.5
    close = []
    for slot, r in assigned:
        if not r or r.get("locked"):
            continue
        for b in bench:
            if b.get("locked") or b["pos"] not in SLOT_ELIGIBLE[slot] or b.get("injury") in ("O", "IR", "SUSP", "PUP"):
                continue
            diff = score_of(r) - score_of(b)
            if 0 <= diff <= 2.5:
                p = win_prob(score_of(r), r.get("sd") or 1, score_of(b), b.get("sd") or 1)
                tip = None
                # Underdogs should chase ceiling; favorites should protect the floor.
                if wp < 0.4 and (b.get("ceil") or 0) > (r.get("ceil") or 0) + 1.5:
                    tip = f"You're the underdog this week: {b['name']} has the higher ceiling ({b['ceil']} vs {r['ceil']}), worth considering"
                elif wp > 0.65 and (b.get("floor") or 0) > (r.get("floor") or 0) + 1.5:
                    tip = f"You're favored: {b['name']} has the safer floor ({b['floor']} vs {r['floor']})"
                close.append({"slot": slot, "start": r["pid"], "over": b["pid"], "prob": round(p, 3), "tip": tip})

    # Late-swap flexibility: keep the latest-kickoff player in the FLEX so you can pivot on injury news.
    tips, tipped = [], set()
    flex = [(s, r) for s, r in assigned if s in ("FLEX", "OP", "RB/WR", "WR/TE") and r and not r.get("locked")]
    for s, fr in flex:
        for s2, r2 in assigned:
            if r2 and s2 in ("RB", "WR", "TE") and r2["pos"] in SLOT_ELIGIBLE[s] and not r2.get("locked") \
                    and r2["pid"] not in tipped and (r2.get("kickoff") or "") > (fr.get("kickoff") or ""):
                tipped.add(r2["pid"])
                tips.append(f"Put {r2['name']} (later kickoff) in your {s} and {fr['name']} at {s2}: same points, but you "
                            f"keep the option to swap in a bench player if {r2['name']} is a surprise inactive.")
                break
    alerts = []
    for r in starters:
        if r.get("injury") in ("O", "IR", "SUSP", "PUP", "D") or r.get("bye"):
            alerts.append(f"{r['name']} is {'on bye' if r.get('bye') else r['injury']}: no healthy replacement on your bench at {r['pos']}")
    for r in rows:
        if r["pid"] in current and r["pid"] not in rec and (r.get("bye") or r.get("injury") in ("O", "IR", "SUSP", "PUP")):
            alerts.append(f"ESPN currently starts {r['name']} ({'bye' if r.get('bye') else r['injury']}). Fix this.")

    return {"team": team, "lineup": [{"slot": s, "pid": r["pid"] if r else None} for s, r in assigned],
            "bench": [r["pid"] for r in sorted(bench, key=score_of, reverse=True)],
            "total": round(total, 1), "sd": round(sd, 1), "moves": moves, "close": close,
            "tips": tips, "alerts": list(dict.fromkeys(alerts)), "matchup": matchup}


class Strength:
    """Rest-of-season optimal-lineup points for any roster, vectorized across weeks."""

    def __init__(self, players, slots, weeks, week, playoff_weeks, playoff_weight=1.3):
        self.slots = slots
        self.weeks = weeks
        self.idx = {w: i for i, w in enumerate(weeks)}
        wts = np.array([playoff_weight if w in playoff_weeks else 1.0 for w in weeks])
        self.players = players
        self.vec = {}
        for pid, r in players.items():
            v = np.zeros(len(weeks))
            for w, pts in r.get("ros", []):
                if w in self.idx:
                    v[self.idx[w]] = pts
            if r.get("locked") and week in self.idx:
                v[self.idx[week]] = 0.0  # already played: a trade can't change this week's points
            self.vec[pid] = v
        self.wts = wts

    def weekly(self, pids):
        n = len(self.weeks)
        by_pos = {}
        for p in pids:
            r = self.players.get(p)
            if r:
                by_pos.setdefault(r["pos"], []).append(self.vec[p])
        total = np.zeros(n)
        left = {}
        for pos in DEDICATED:
            k = int(self.slots.get(pos, 0))
            m = np.sort(np.array(by_pos.get(pos, [])).reshape(-1, n), axis=0)[::-1]
            take = m[:k]
            total += take.sum(axis=0) if len(take) else 0
            left[pos] = m[k:]
        for s in FLEX_ORDER:
            k = int(self.slots.get(s, 0))
            if not k:
                continue
            elig = SLOT_ELIGIBLE[s]
            pool = [left[p] for p in elig if p in left and len(left[p])]
            if not pool:
                continue
            m = np.sort(np.vstack(pool), axis=0)[::-1]
            total += m[:k].sum(axis=0)
            # Approximation for leagues with several flex types: remove taken values from the pool.
            rest = m[k:]
            for p in elig:
                left[p] = np.empty((0, n))
            left[next(iter(sorted(elig)))] = rest
        return total

    def total(self, pids):
        return float((self.weekly(pids) * self.wts).sum())
