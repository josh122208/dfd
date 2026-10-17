"""Situational adjustments to our usage model: injured teammates, trench and play-style matchups, coach talk, and
handcuff value. Each returns small, capped effects plus a plain-English note."""
import numpy as np

from . import injury

OUT_WEEKS = {"IR": 4, "PUP": 4, "SUSP": 2, "O": 1, "D": 1}
PTS_PER = {"tgt": {"WR": 1.55, "TE": 1.45, "RB": 1.3, "QB": 0.0}, "car": {"RB": 0.6, "QB": 0.7, "WR": 0.7, "TE": 0.5}}


def _ord(n):
    n = int(n)
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def vacated(S, statuses):
    """Targets and carries freed up by injured teammates, handed to the players who absorb them.

    Uses this season's games the injured player missed when there are any (what actually happened on this team),
    blended with a share-proportional split. Returns {pid: {"pts", "weeks", "notes"}}."""
    uw, usage, reg = S.get("usage_weekly"), S["usage"], S["reg"]
    if uw is None or uw.empty:
        return {}
    out = {}
    for x, (st, _) in statuses.items():
        if st not in OUT_WEEKS:
            continue
        ux = usage.get(x) or {}
        tsx, csx = ux.get("tgt_share") or 0, ux.get("carry_share") or 0
        if ux.get("games", 0) < 2 or (tsx < 0.12 and csx < 0.25):
            continue
        team = reg.players[x].get("team")
        rows = uw[uw.team == team]
        if rows.empty:
            continue
        per_week = rows.groupby("week")[["targets", "carries"]].sum().mean()
        x_weeks = set(uw[uw.pid == x].week)
        mates = [y for y in rows.pid.unique() if y != x and statuses.get(y, (None,))[0] not in OUT_WEEKS
                 and reg.players.get(y, {}).get("pos") in ("RB", "WR", "TE")]
        xpos = reg.players[x].get("pos")
        for kind, col, xs, team_pg in (("tgt", "target_share", tsx, per_week.get("targets", 0)),
                                       ("car", "carry_share", csx, per_week.get("carries", 0))):
            if xs < (0.12 if kind == "tgt" else 0.25):
                continue
            elig = [y for y in mates if kind == "tgt" or reg.players[y]["pos"] == "RB"]
            base_share = {y: (rows[(rows.pid == y) & rows.week.isin(x_weeks)][col].mean() if x_weeks else np.nan)
                          for y in elig}
            base_share = {y: (v if v == v else (usage.get(y, {}).get("tgt_share" if kind == "tgt" else "carry_share") or 0))
                          for y, v in base_share.items()}
            wt = {y: base_share[y] * (1.5 if reg.players[y]["pos"] == xpos else 1.0) for y in elig}
            tot = sum(wt.values()) or 1
            for y in elig:
                heur = 0.8 * xs * wt[y] / tot  # ~80% of the vacated work stays with current teammates
                yr = rows[rows.pid == y]
                without = yr[~yr.week.isin(x_weeks)]
                n = len(without)
                emp = (without[col].mean() - base_share[y]) if n else 0.0
                delta = (n * emp + 2 * heur) / (n + 2)
                # If he's already been absorbing the work lately, his recent games already show it.
                last = yr.sort_values("week").tail(3)
                present = float(last.week.isin(x_weeks).mean()) if len(last) else 1.0
                delta *= present
                if delta <= 0.015:
                    continue
                extra = delta * team_pg
                pts = extra * PTS_PER[kind].get(reg.players[y]["pos"], 1.0)
                if pts < 0.4:
                    continue
                o = out.setdefault(y, {"pts": 0.0, "weeks": 0, "notes": []})
                o["pts"] += pts
                o["weeks"] = max(o["weeks"], OUT_WEEKS[st])
                how = f"{n} game{'s' if n != 1 else ''} without him this year" if n else "share-based estimate"
                o["notes"].append(f"{reg.players[x]['name']} is {st}: about +{extra:.1f} "
                                  f"{'targets' if kind == 'tgt' else 'carries'} a game for him ({how}), +{pts:.1f} pts")
    for y, o in out.items():
        base = (usage.get(y) or {}).get("base") or 0
        o["pts"] = min(o["pts"], max(3.0, 0.5 * base))
    return out


def trench(S, pos, team, opp, pid, u):
    """Offensive line vs pass rush / run defense, plus blitz, play-action and stacked-box style matchups."""
    tm, lg, qb, rbs = S.get("trench", ({}, {}, {}, {}))
    o, d = tm.get(team, {}), tm.get(opp, {})
    if not o or not d or not lg:
        return 1.0, []
    f, notes = 1.0, []
    adot = (u or {}).get("adot") or 0
    if pos in ("QB", "WR", "TE") and "off_sack" in o and "def_sack" in d:
        rel = (o["off_sack"] * d["def_sack"] / lg["sack"]) / lg["sack"]
        share = 1.0 if pos == "QB" else (0.6 if adot >= 11 else 0.3)
        fp = float(np.clip(1 - 0.10 * (rel - 1), 0.93, 1.06))
        f *= 1 + (fp - 1) * share
        if pos == "QB" and abs(fp - 1) >= 0.025:
            notes.append(f"Pass protection {'mismatch' if fp < 1 else 'edge'}: {team}'s line ranks "
                         f"{_ord(o.get('off_sack_rank', 16))} in sack rate, {opp}'s pass rush "
                         f"{_ord(d.get('def_sack_rank', 16))}")
    if pos == "RB" and "off_stuff" in o and "def_stuff" in d:
        rel = (o["off_stuff"] * d["def_stuff"] / lg["stuff"]) / lg["stuff"]
        fr = float(np.clip(1 - 0.12 * (rel - 1), 0.92, 1.06))
        f *= fr
        if abs(fr - 1) >= 0.025:
            notes.append(f"Run-blocking {'mismatch' if fr < 1 else 'edge'}: {team} gets stuffed at the "
                         f"{_ord(33 - o.get('off_stuff_rank', 16))}-highest rate, {opp} stuffs runs "
                         f"{_ord(d.get('def_stuff_rank', 16))}-most")
    if pos in ("QB", "WR", "TE") and "def_blitz" in d:
        qpid = S.get("starting_qb", {}).get(team)
        edge = (qb.get(qpid) or {}).get("edge")
        if edge is not None:
            fb = 1 + float(np.clip(3 * edge * (d["def_blitz"] - lg["blitz"]) / max(lg["blitz"], 0.01), -0.04, 0.04))
            f *= 1 + (fb - 1) * (1.0 if pos == "QB" else 0.5)
            if pos == "QB" and abs(fb - 1) >= 0.02:
                notes.append(f"{opp} blitzes {d['def_blitz'] * 100:.0f}% of dropbacks ({_ord(d.get('def_blitz_rank', 16))}) "
                             f"and he's been {'better' if edge > 0 else 'worse'} against the blitz")
    if pos in ("QB", "WR", "TE") and "off_pa_rate" in o and "def_pa_epa" in d:
        fpa = 1 + float(np.clip(6 * (o["off_pa_rate"] - lg["pa"]) * (d["def_pa_epa"] - lg["pa_epa"]) * 10, -0.03, 0.03))
        f *= fpa
        if pos == "QB" and fpa >= 1.02:
            notes.append(f"Play-action heavy offense vs a defense that's been beaten by play-action")
    if pos == "RB" and "def_stacked" in d and pid in rbs:
        ratio = rbs[pid]["stacked_ratio"]
        excess = (d["def_stacked"] - lg["stacked"]) / max(lg["stacked"], 0.01)
        fs = 1 + float(np.clip((ratio - 1) * excess * 0.5, -0.03, 0.03))
        f *= fs
        if abs(fs - 1) >= 0.02:
            notes.append(f"{opp} stacks the box often ({_ord(d.get('def_stacked_rank', 16))}) and he's "
                         f"{'held up' if ratio >= 1 else 'struggled'} against 8+ defenders")
    return float(np.clip(f, 0.88, 1.12)), notes


def coach(S, pid):
    """Small nudge from the tone of the coach's recent words about the player, with the quote as a note."""
    n = (S.get("news") or {}).get(pid)
    if not n or not n.get("quotes"):
        return 1.0, None
    q = n["quotes"][0]
    tone = {1: "positive", -1: "cautionary", 0: "neutral"}[q["tone"]]
    text = q["text"] if len(q["text"]) <= 200 else q["text"][:197] + "…"
    return 1 + 0.04 * n["signal"], f"Coach talk ({q['date']}, {tone}): {text}"


def handcuffs(out, S):
    """Give backup RBs credit for the chance the starter ahead of them misses games."""
    usage, week = S["usage"], S["week"]
    by_team = {}
    for pid, r in out.items():
        if r["pos"] == "RB" and r.get("team") and usage.get(pid):
            by_team.setdefault(r["team"], []).append(pid)
    changed = []
    for team, rbs in by_team.items():
        rbs.sort(key=lambda p: -((usage[p].get("carry_share_last2") or usage[p].get("carry_share") or 0)))
        if len(rbs) < 2:
            continue
        starter, backup = rbs[0], rbs[1]
        su, bu = usage[starter], usage[backup]
        if (su.get("carry_share") or 0) < 0.4 or (bu.get("carry_share") or 0) < 0.08:
            continue
        s_row, b_row = out[starter], out[backup]
        rk = s_row.get("inj_risk")
        if not rk or s_row.get("injury") in ("IR", "PUP", "SUSP", "O"):
            continue  # a starter who's already out is handled by the vacated-work adjustment
        role = 0.75 * (su.get("base") or 0)
        added = 0.0
        for item in b_row.get("ros", []):
            w, v = item
            k = w - week
            if k < 1 or v <= 0:
                continue
            p_out = 1 - injury.availability(rk["hazard"], 2.5, k)
            gain = max(0.0, role - v) * p_out
            item[1] = round(v + gain, 2)
            added += gain
        if added > 0.3:
            b_row["handcuff"] = {"for": starter, "contingent_pts": round(added, 1), "if_starter_out": round(role, 1)}
            b_row["notes"].append(f"Handcuff to {s_row['name']}: worth about {role:.1f} pts a week if he goes down "
                                  f"(+{added:.1f} expected rest-of-season points already counted)")
            changed.append(backup)
    return changed
