"""Trade finder: who to sell, who to buy, which manager to call, and what offer they'll actually accept."""
import itertools
import math

import numpy as np

from .lineup import phi
from .persuasion import pitch

POS_GROUPS = ("QB", "RB", "WR", "TE")


def sell_high(players, mine):
    """My players whose market price is above what their usage and schedule support."""
    out = []
    for pid in mine:
        r = players.get(pid)
        if not r or r["pos"] not in POS_GROUPS or (r.get("value") or 0) < 1500:
            continue
        u = r.get("usage") or {}
        score, why = 0.0, []
        if u.get("exp_ppg") and u.get("games", 0) >= 3 and u["ppg"] - u["exp_ppg"] >= 2:
            score += (u["ppg"] - u["exp_ppg"]) / max(u["exp_ppg"], 5) * 3
            why.append(f"Scoring {u['ppg']:.1f}/game on usage worth {u['exp_ppg']:.1f}")
        if u.get("td") is not None and u.get("td_exp") is not None and u["td"] - u["td_exp"] >= 1.5:
            score += (u["td"] - u["td_exp"]) * 0.5
            why.append(f"{u['td']:.0f} TDs on {u['td_exp']:.1f} expected")
        mkt, mdl = r.get("value") or 0, r.get("model_value") or 0
        if mkt > mdl * 1.1:
            score += (mkt - mdl) / max(mkt, 1) * 3
            why.append(f"Trade value {mkt:,.0f}, but our rest-of-season model says {mdl:,.0f}")
        if u.get("last3_ppg") and u.get("ppg") and u["last3_ppg"] > u["ppg"] * 1.25 and u["games"] >= 4:
            score += 0.5
            why.append(f"Hot streak ({u['last3_ppg']:.1f}/game last 3) makes his price peak now")
        if r.get("sos_ros") and r["sos_ros"] < 0.94:
            score += 0.6
            why.append("Tough rest-of-season schedule")
        if u.get("tgt_share_last2") and u.get("tgt_share") and u["tgt_share_last2"] < u["tgt_share"] - 0.05:
            score += 0.5
            why.append("Target share trending down")
        if any("instead of" in n for n in r.get("notes", [])):
            score += 0.4
            why.append("His QB situation got worse")
        ir = r.get("inj_risk") or {}
        if ir.get("level") == "High":
            score += 0.8
            why.append(f"High injury risk (about {ir['exp_missed_6wk']:.1f} games missed expected over the next 6): "
                       + "; ".join(ir.get("why", [])[:2]))
        if score >= 1:
            out.append({"pid": pid, "score": round(score, 2), "why": why})
    return sorted(out, key=lambda x: -x["score"])


def buy_low(players, owner_of, me):
    """Players on other rosters whose usage and schedule beat their price."""
    out = []
    for pid, r in players.items():
        tid = owner_of.get(pid)
        if tid is None or tid == me or r["pos"] not in POS_GROUPS or (r.get("model_value") or 0) < 1000:
            continue
        u = r.get("usage") or {}
        score, why = 0.0, []
        if u.get("exp_ppg") and u.get("games", 0) >= 3 and u["exp_ppg"] - u["ppg"] >= 2:
            score += (u["exp_ppg"] - u["ppg"]) / max(u["exp_ppg"], 5) * 3
            why.append(f"Usage worth {u['exp_ppg']:.1f}/game but he's scored {u['ppg']:.1f}")
        if u.get("td") is not None and u.get("td_exp") is not None and u["td_exp"] - u["td"] >= 1.2:
            score += (u["td_exp"] - u["td"]) * 0.5
            why.append(f"Only {u['td']:.0f} TDs on {u['td_exp']:.1f} expected")
        mkt, mdl = r.get("value") or 0, r.get("model_value") or 0
        if mdl > mkt * 1.1:
            score += (mdl - mkt) / max(mdl, 1) * 3
            why.append(f"Our model values him at {mdl:,.0f}, his trade value is {mkt:,.0f}")
        if u.get("last_pts") is not None and u.get("ppg") and u["last_pts"] < 0.6 * u["ppg"] and u["games"] >= 3:
            score += 0.5
            why.append(f"Coming off a dud ({u['last_pts']:.1f} pts), so his manager is frustrated")
        if r.get("sos_ros") and r["sos_ros"] > 1.06:
            score += 0.6
            why.append("Soft rest-of-season schedule")
        if u.get("tgt_share_last2") and u.get("tgt_share") and u["tgt_share_last2"] > u["tgt_share"] + 0.05:
            score += 0.5
            why.append("Target share rising")
        if u.get("snap_last2") and u.get("snap") and u["snap_last2"] > u["snap"] + 0.1:
            score += 0.5
            why.append("Snap share rising")
        ir = r.get("inj_risk") or {}
        if ir.get("level") == "High":
            score -= 0.8
            why.append("Caution: high injury risk (" + "; ".join(ir.get("why", [])[:1]) + ")")
        if score >= 1:
            out.append({"pid": pid, "team": tid, "score": round(score, 2), "why": why})
    return sorted(out, key=lambda x: -x["score"])[:40]


def team_profiles(teams, players, strength, me, slots, week, sched_byes):
    """What each manager needs, has extra of, is worried about, and how they behave."""
    starters_ppg = {pos: [] for pos in POS_GROUPS}
    lineups = {}
    for t in teams:
        rows = [players[p] for p in t["pids"] if p in players]
        lineups[t["id"]] = rows
        for pos in POS_GROUPS:
            k = int(slots.get(pos, 0)) or 1
            top = sorted([r.get("ros_ppg") or 0 for r in rows if r["pos"] == pos], reverse=True)[:k]
            starters_ppg[pos].append(np.mean(top) if top else 0)
    league_avg = {p: np.mean(v) for p, v in starters_ppg.items()}
    league_sd = {p: (np.std(v) or 1) for p, v in starters_ppg.items()}
    by_wins = sorted(teams, key=lambda t: (t["wins"], t["points_for"]), reverse=True)
    standing = {t["id"]: i + 1 for i, t in enumerate(by_wins)}
    prof = {}
    for t in teams:
        rows = lineups[t["id"]]
        z, depth = {}, {}
        for pos in POS_GROUPS:
            k = int(slots.get(pos, 0)) or 1
            vals = sorted([r.get("ros_ppg") or 0 for r in rows if r["pos"] == pos], reverse=True)
            top = vals[:k]
            z[pos] = round(float(((np.mean(top) if top else 0) - league_avg[pos]) / league_sd[pos]), 2)
            depth[pos] = len(vals)
        need = min(z, key=z.get)
        surplus = max(POS_GROUPS, key=lambda p: depth[p] - (int(slots.get(p, 0)) or 1) * 1.5 + z[p])
        hurt = [r["pid"] for r in rows if r.get("injury") in ("O", "IR", "PUP", "SUSP") and (r.get("value") or 0) > 2000]
        upcoming_byes = []
        for w in (week, week + 1, week + 2):
            n = sum(1 for r in rows if (r["team"], w) in sched_byes and (r.get("ros_ppg") or 0) >= 8)
            if n >= 2:
                upcoming_byes.append({"week": w, "starters_out": n})
        games = t["wins"] + t["losses"] + t["ties"]
        mood = "contender" if standing[t["id"]] <= 3 else (
            "desperate" if games >= 4 and t["losses"] - t["wins"] >= 2 else "bubble")
        prof[t["id"]] = {
            "id": t["id"], "name": t["name"], "owner": t.get("owner_first") or t.get("owner") or t["name"],
            "record": f"{t['wins']}-{t['losses']}" + (f"-{t['ties']}" if t["ties"] else ""), "standing": standing[t["id"]],
            "points_for": round(t["points_for"], 1), "z": z, "need": need, "surplus": surplus, "depth": depth,
            "injured_starters": hurt, "byes": upcoming_byes, "mood": mood, "trades": t.get("trades", 0),
            "strength": round(strength.total(t["pids"]) / max(len(strength.weeks), 1), 1),
            "is_me": t["id"] == me,
        }
    return prof


class TradeFinder:
    def __init__(self, S, players, strength, teams, me, profiles):
        self.S, self.P, self.st, self.me = S, players, strength, me
        self.teams = {t["id"]: t for t in teams}
        self.prof = profiles
        self.nweeks = max(len(strength.weeks), 1)

    def _value(self, pid):
        return self.P[pid].get("value") or 0

    def _after(self, roster, give, get, size):
        """Roster after the swap; if it's over the limit, drop the least valuable non-starters."""
        new = [p for p in roster if p not in give] + list(get)
        drops = []
        while len(new) > size:
            def only_one(p):
                pos = self.P[p]["pos"]
                return pos in ("K", "DST") and sum(1 for x in new if self.P[x]["pos"] == pos) <= 1
            worst = min((p for p in new if p not in get and not only_one(p)),
                        key=lambda p: (self.P[p].get("vorp", 0), self._value(p)))
            new.remove(worst)
            drops.append(worst)
        return new, drops

    def evaluate(self, tid, give, get):
        me_t, them = self.teams[self.me], self.teams[tid]
        my_new, my_drops = self._after(me_t["pids"], give, get, len(me_t["pids"]))
        th_new, th_drops = self._after(them["pids"], get, give, len(them["pids"]))
        my_gain = (self.st.total(my_new, self.me) - self.base[self.me]) / self.nweeks
        th_gain = (self.st.total(th_new, tid) - self.base[tid]) / self.nweeks
        give_v = sum(self._value(p) for p in give)
        get_v = sum(self._value(p) for p in get)
        # The side taking more bodies pays for it with whoever they cut.
        their_in = give_v - sum(self._value(p) for p in th_drops)
        fair = their_in / max(get_v, 1)
        give_ppg = sum((self.P[p].get("usage") or {}).get("ppg") or 0 for p in give)
        get_ppg = sum((self.P[p].get("usage") or {}).get("ppg") or 0 for p in get)
        perception = (give_ppg + 1) / (get_ppg + 1)
        pr = self.prof[tid]
        # Managers mostly judge by trade value and box scores, but a deal that visibly weakens their lineup
        # gets noticed. Coefficients are hand-tuned, not fitted: treat p_accept as a ranking signal.
        z = 4 * (fair - 1) + 0.8 * (min(perception, 1.6) - 1) + 0.5 * th_gain + 0.1
        tend = pr.get("tendency") or {}
        if pr["trades"] or tend.get("sent", 0) >= 3 or tend.get("accepted"):
            z += 0.25
        if pr["mood"] == "desperate":
            z += 0.2
        if tend.get("chases") and tend["chases"] in {self.P[p]["pos"] for p in give}:
            z += 0.3  # giving them what they keep chasing
        if tend.get("picky"):
            z -= 0.3
        if tend.get("dormant"):
            z -= 0.3
        p_accept = 1 / (1 + math.exp(-z))
        return {"team": tid, "give": list(give), "get": list(get), "my_gain": round(my_gain, 2),
                "their_gain": round(th_gain, 2), "fair": round(fair, 3), "perception": round(perception, 2),
                "p_accept": round(p_accept, 3), "give_value": round(give_v), "get_value": round(get_v),
                "my_drops": my_drops, "their_drops": th_drops, "score": round(my_gain * p_accept, 3)}

    def _my_candidates(self, sell_ids):
        mine = [p for p in self.teams[self.me]["pids"] if p in self.P and self.P[p]["pos"] in POS_GROUPS]
        mine.sort(key=self._value, reverse=True)
        return list(dict.fromkeys(mine[:10] + [p for p in sell_ids if p in mine]))

    def _combos(self, tid, my_c):
        """1-for-1, 2-for-1, 1-for-2 and 2-for-2 packages with one manager, pre-filtered to sane value ratios."""
        theirs = [p for p in self.teams[tid]["pids"] if p in self.P and self.P[p]["pos"] in POS_GROUPS]
        theirs.sort(key=self._value, reverse=True)
        th_c = theirs[:10]
        combos = [((a,), (b,)) for a in my_c for b in th_c]
        combos += [(pair, (b,)) for pair in itertools.combinations(my_c, 2) for b in th_c]
        combos += [((a,), pair) for a in my_c for pair in itertools.combinations(th_c, 2)]
        combos += [(p1, p2) for p1 in itertools.combinations(my_c[:6], 2) for p2 in itertools.combinations(th_c[:6], 2)]
        for give, get in combos:
            gv = sum(self._value(p) for p in give)
            tv = sum(self._value(p) for p in get)
            if tv > 0 and 0.8 <= gv / tv <= 1.45:
                yield give, get

    def run(self, sell_ids, limit=12):
        self.base = {tid: self.st.total(t["pids"], tid) for tid, t in self.teams.items()}
        my_c = self._my_candidates(sell_ids)
        results = []
        for tid, t in self.teams.items():
            if tid == self.me:
                continue
            for give, get in self._combos(tid, my_c):
                ev = self.evaluate(tid, give, get)
                if ev["my_gain"] >= 0.4 and ev["fair"] >= 0.93 and ev["p_accept"] >= 0.3 and ev["their_gain"] >= -1.5:
                    results.append(ev)
        results.sort(key=lambda e: -e["score"])
        # Keep variety: no more than two offers per manager, and no player in more than three offers.
        picked, per_team, per_player = [], {}, {}
        for e in results:
            if per_team.get(e["team"], 0) >= 2:
                continue
            if any(per_player.get(p, 0) >= 3 for p in e["give"] + e["get"]):
                continue
            picked.append(e)
            per_team[e["team"]] = per_team.get(e["team"], 0) + 1
            for p in e["give"] + e["get"]:
                per_player[p] = per_player.get(p, 0) + 1
            if len(picked) >= limit:
                break
        for e in picked:
            e["ladder"] = self.ladder(e)
            e["pitch"] = pitch(e, self.P, self.prof, self.teams, self.me, self.S)
        # Two offers to the same manager: mention the second in the first message (choice framing).
        seen = {}
        for e in picked:
            if e["team"] in seen:
                first = seen[e["team"]]
                names = lambda ps: " + ".join(self.P[p]["name"] for p in ps)  # noqa: E731
                msg = first["pitch"]["message"]
                alt = f"If you'd rather, I'd also do {names(e['give'])} for {names(e['get'])}."
                first["pitch"]["message"] = msg.replace(" No pressure", f" {alt} No pressure")
                first["pitch"]["tactics"].append("Offer two options")
            else:
                seen[e["team"]] = e
        return picked

    def run_matchups(self, games, general, n_weeks=4, per_opp=3):
        """Trades aimed at a specific head-to-head week: with that opponent (send them players who are on bye,
        hurt or in bad matchups that week while improving their season) and with anyone (boost your lineup that
        week). Every deal must still be fair by value, help their season-long lineup, and not hurt yours."""
        sd = 22 * 2 ** 0.5
        me_t = self.teams[self.me]
        my_c = self._my_candidates([])
        out = []
        # Trades take a day or two to clear review, so aim at next week onward.
        upcoming = [g for g in games if g["week"] > self.S["week"] and g["week"] in self.st.idx
                    and g["opp"] in self.teams][:n_weeks]
        my_weekly = self.st.weekly(me_t["pids"], played=True)
        for g in upcoming:
            tid, w = g["opp"], g["week"]
            wi = self.st.idx[w]
            th_t = self.teams[tid]
            th_weekly = self.st.weekly(th_t["pids"], played=True)
            p0 = phi((my_weekly[wi] - th_weekly[wi]) / sd)
            lev = g.get("leverage") or 0
            cands = []
            for give, get in self._combos(tid, my_c):
                ev = self.evaluate(tid, give, get)
                if ev["fair"] < 0.93 or ev["their_gain"] < -0.5 or ev["my_gain"] < -0.3 or ev["p_accept"] < 0.35:
                    continue
                my_new, _ = self._after(me_t["pids"], give, get, len(me_t["pids"]))
                th_new, _ = self._after(th_t["pids"], get, give, len(th_t["pids"]))
                m1 = self.st.weekly(my_new, played=True)[wi]
                t1 = self.st.weekly(th_new, played=True)[wi]
                p1 = phi((m1 - t1) / sd)
                if p1 - p0 < 0.03:
                    continue
                ev["h2h"] = {"week": w, "opp": tid, "p_before": round(p0, 3), "p_after": round(p1, 3),
                             "my_week": [round(float(my_weekly[wi]), 1), round(float(m1), 1)],
                             "their_week": [round(float(th_weekly[wi]), 1), round(float(t1), 1)],
                             "leverage": lev, "why": self._h2h_why(give, get, w)}
                ev["h2h"]["why"].insert(0, f"Week {w} swing: your lineup {m1 - my_weekly[wi]:+.1f}, theirs "
                                           f"{t1 - th_weekly[wi]:+.1f} projected points.")
                ev["score"] = round((ev["my_gain"] + 30 * (p1 - p0) * (1 + 2 * lev)) * ev["p_accept"], 3)
                cands.append(ev)
            cands.sort(key=lambda e: -e["score"])
            picked, used = [], set()
            for e in cands:
                key = frozenset(e["give"]) | frozenset(e["get"])
                if any(p in used for p in e["get"]) or key in used:
                    continue
                picked.append(e)
                used.update(e["get"])
                used.add(key)
                if len(picked) >= per_opp:
                    break
            for e in picked:
                e["ladder"] = self.ladder(e)
                e["pitch"] = pitch(e, self.P, self.prof, self.teams, self.me, self.S)
                h = e["h2h"]
                e["pitch"]["talking_points"].insert(0, (
                    f"You play {self.prof[tid]['owner']} in Week {w}: this moves your win odds that week from "
                    f"{h['p_before']:.0%} to {h['p_after']:.0%}. " + " ".join(h["why"][:2])))
                e["pitch"]["objections"].insert(0, {
                    "if": f"\"Isn't this just so you beat me in Week {w}?\"",
                    "say": f"Be straight: it helps you the rest of the way (+{e['their_gain']:.1f} pts a week for your "
                           "lineup by my math) and it helps me too. That's why it's a fair trade."})
            # Best general offers (any manager) for this week, if they also lift your lineup that week.
            boosts = []
            for e in general:
                if e["team"] == tid:
                    continue
                my_new, _ = self._after(me_t["pids"], e["give"], e["get"], len(me_t["pids"]))
                m1 = self.st.weekly(my_new, played=True)[wi]
                p1 = phi((m1 - th_weekly[wi]) / sd)
                if p1 - p0 >= 0.03:
                    boosts.append({"trade": general.index(e), "p_after": round(p1, 3), "my_week": round(float(m1), 1)})
            boosts.sort(key=lambda b: -b["p_after"])
            out.append({"week": w, "opp": tid, "p_before": round(p0, 3), "leverage": lev,
                        "my_week": round(float(my_weekly[wi]), 1), "their_week": round(float(th_weekly[wi]), 1),
                        "trades": picked, "boosts": boosts[:3]})
        return out

    def _h2h_why(self, give, get, week):
        """Plain-English reasons the trade swings that specific week."""
        why = []

        def wk(pid):
            return next((v for w, v in self.P[pid].get("ros", []) if w == week), 0.0)
        for p in give:  # what they receive
            r = self.P[p]
            if r.get("bye_week") == week:
                why.append(f"{r['name']} is on bye in Week {week}, so he scores 0 for them that week.")
            elif wk(p) < 0.5:
                why.append(f"{r['name']} isn't projected to play in Week {week}.")
            elif wk(p) < 0.6 * (r.get("ros_ppg") or 0):
                why.append(f"{r['name']} projects only {wk(p):.1f} that week (tough matchup or injury).")
        for p in get:  # what they lose
            r = self.P[p]
            if wk(p) >= 1.15 * (r.get("ros_ppg") or 99) or wk(p) >= 15:
                why.append(f"{r['name']} projects {wk(p):.1f} that week, and he'll be on your side.")
        return why

    def ladder(self, e):
        """Opening ask, the fair deal, and the most you should give before walking away."""
        tid = e["team"]
        steps = []
        them = self.teams[tid]
        # Opener: also ask for one of their cheap bench pieces if the deal still looks fair to them.
        extras = sorted([p for p in them["pids"] if p in self.P and p not in e["get"] and self._value(p) > 0
                         and self.P[p]["pos"] in POS_GROUPS], key=self._value)
        opener = None
        for x in extras[:8]:
            ev = self.evaluate(tid, e["give"], e["get"] + [x])
            if 0.88 <= ev["fair"] < e["fair"] - 0.02 and ev["my_gain"] >= e["my_gain"]:
                opener = ev
                break
        if opener:
            steps.append({"label": "Open with", "give": opener["give"], "get": opener["get"], "fair": opener["fair"],
                          "my_gain": opener["my_gain"], "note": "Anchors the talks in your favor. Expect a counter."})
        steps.append({"label": "Fair deal", "give": e["give"], "get": e["get"], "fair": e["fair"], "my_gain": e["my_gain"],
                      "note": "Where this should land. Fair by trade-value charts, better for you by projections."})
        mine = self.teams[self.me]["pids"]
        adds = sorted([p for p in mine if p in self.P and p not in e["give"] and self._value(p) > 0
                       and self.P[p]["pos"] in POS_GROUPS], key=self._value)
        for x in (adds if e["fair"] < 1.05 else []):
            ev = self.evaluate(tid, e["give"] + [x], e["get"])
            if ev["my_gain"] > 0.2 and ev["fair"] > e["fair"] + 0.04:
                steps.append({"label": "Walk-away price", "give": ev["give"], "get": ev["get"], "fair": ev["fair"],
                              "my_gain": ev["my_gain"],
                              "note": "Add this only after they counter. Past this, the trade stops helping you."})
                break
        return steps


def manager_tendencies(tx, teams, reg, faab_budget=None):
    """Read every manager's trade and waiver history: who sends offers, who declines, what they chase."""
    if not tx:
        return {}  # no history loaded (e.g. browser-synced league): don't label anyone
    pos_of = {str(p["espn_id"]): p.get("pos") for p in reg.players.values() if p.get("espn_id")}
    T = {t["id"]: {"sent": 0, "received": 0, "declined": 0, "accepted": 0, "adds": 0, "drops": 0, "bids": [],
                   "wants": {}} for t in teams}
    seen = set()
    for t in tx or []:
        if t.get("id") in seen:
            continue
        seen.add(t.get("id"))
        typ, st, team, items = t.get("type"), t.get("status"), t.get("team"), t.get("items") or []
        if team not in T:
            continue
        if typ == "TRADE_PROPOSAL":
            T[team]["sent"] += 1
            partners = {i.get("from") for i in items} | {i.get("to") for i in items}
            for p in partners - {team, None, -1}:
                if p in T:
                    T[p]["received"] += 1
            for i in items:
                if i.get("to") == team:  # what they asked for
                    pos = pos_of.get(str(i.get("player")))
                    if pos:
                        T[team]["wants"][pos] = T[team]["wants"].get(pos, 0) + 1
        elif typ == "TRADE_DECLINE":
            T[team]["declined"] += 1
        elif typ in ("TRADE_ACCEPT", "TRADE_UPHOLD") and st == "EXECUTED":
            T[team]["accepted"] += 1
        elif typ in ("WAIVER", "FREEAGENT") and st == "EXECUTED":
            for i in items:
                if i.get("type") == "ADD":
                    T[team]["adds"] += 1
                    pos = pos_of.get(str(i.get("player")))
                    if pos in ("QB", "RB", "WR", "TE"):
                        T[team]["wants"][pos] = T[team]["wants"].get(pos, 0) + 1
                elif i.get("type") == "DROP":
                    T[team]["drops"] += 1
            if typ == "WAIVER" and t.get("bid"):
                T[team]["bids"].append(t["bid"])
    adds = sorted(v["adds"] for v in T.values())
    med_adds = adds[len(adds) // 2] if adds else 0
    for v in T.values():
        traits, w = [], v["wants"]
        tot = sum(w.values())
        top = max(w, key=w.get) if w else None
        v["chases"] = top if top and w[top] >= 3 and w[top] / tot >= 0.4 else None
        if v["sent"] >= 3:
            traits.append(f"Sends a lot of trade offers ({v['sent']}): open to dealing")
        if v["accepted"]:
            traits.append(f"Has accepted {v['accepted']} trade{'s' if v['accepted'] > 1 else ''}")
        if v["declined"] >= 2 and not v["accepted"]:
            traits.append(f"Has declined {v['declined']} offers and accepted none: only a clearly good deal works")
            v["picky"] = True
        if v["chases"]:
            traits.append(f"Keeps chasing {v['chases']}s (trade asks and pickups)")
        if v["adds"] >= max(6, 1.5 * med_adds):
            traits.append(f"Very active on waivers ({v['adds']} pickups): reacts fast to news")
        if v["bids"] and faab_budget and max(v["bids"]) >= 0.25 * faab_budget:
            traits.append(f"Spends big FAAB (max ${max(v['bids'])})")
        if v["sent"] == 0 and v["adds"] <= 2 and not v["accepted"] and not v["declined"]:
            traits.append("Barely active: message them directly so the offer doesn't sit")
            v["dormant"] = True
        v["traits"] = traits
        v["max_bid"] = max(v["bids"]) if v["bids"] else 0
        del v["bids"]
    return T
