"""Injury and reinjury risk: how likely a player is to miss future games, and what that does to his value.

Model (hand-tuned, informed by NFL injury research rather than fitted):
  - each game has a base chance of an injury that costs games, by position (RBs highest);
  - that hazard rises with injury history (games missed last season / this season, weeks on the report),
    a recent soft-tissue injury (hamstrings, groins and calves recur at high rates in the weeks after return),
    and age at physically demanding positions;
  - an injury costs about D games, so k weeks ahead he's unavailable if he got hurt in any of the last
    min(k, D) games:  avail(k) = (1 - h)^min(k, D).  At the base RB rate that's ~0.7 games missed per 6,
    in line with typical NFL missed-game rates by position.
"""
BASE_HAZARD = {"QB": 0.025, "RB": 0.055, "WR": 0.040, "TE": 0.040}
# Multiplier on hazard when this body part was hurt recently, plus typical games lost per injury.
BODY = {
    "hamstring": (1.6, 2.5, "Hamstring injuries recur often in the weeks after a player returns"),
    "groin": (1.4, 2.5, "Groin strains tend to linger and recur"),
    "calf": (1.4, 2.5, "Calf strains recur and can lead to Achilles trouble"),
    "quadricep": (1.3, 2.5, "Quad strains can recur"),
    "thigh": (1.3, 2.5, "Thigh strains can recur"),
    "achilles": (1.5, 4.0, "Achilles issues are slow to fully heal"),
    "ankle": (1.25, 3.0, "Ankle sprains (especially high-ankle) often get re-aggravated"),
    "knee": (1.2, 3.0, "Knee injuries raise short-term re-injury risk"),
    "foot": (1.25, 3.0, "Foot injuries are easy to re-aggravate"),
    "toe": (1.15, 2.0, "Toe injuries (turf toe) hamper cutting and linger"),
    "concussion": (1.3, 1.5, "A recent concussion raises the chance of another"),
    "back": (1.2, 2.0, "Back injuries are prone to flare-ups"),
    "hip": (1.15, 2.0, "Hip injuries can flare up"),
    "shoulder": (1.15, 2.0, "Shoulder injuries can be re-aggravated by contact"),
    "neck": (1.15, 2.0, "Neck injuries are handled cautiously"),
    "pectoral": (1.2, 4.0, "Pectoral injuries are often long-term"),
}


def _part(text):
    if not isinstance(text, str):
        return None
    t = text.lower()
    for k in BODY:
        if k in t:
            return k
    return None


def history(t, reg, usage, sched, week):
    """Per-player injury history from last season's games, this season's games and the injury reports."""
    out = {}
    team_games = sched[sched.final].groupby("team").size().to_dict() if not sched.empty else {}
    prev = t["stats_prev"]
    prev_games = {}
    if not prev.empty:
        p = prev[prev.season_type == "REG"].copy()
        p["pts"] = p.fantasy_points.fillna(0) + 0.5 * p.receptions.fillna(0)
        g = p.groupby("player_id").agg(n=("pts", "size"), ppg=("pts", "mean"))
        prev_games = {k: (int(v.n), float(v.ppg)) for k, v in g.iterrows()}
        prev_max = int(p.groupby("team").week.nunique().max() or 17)
    else:
        prev_max = 17
    inj = t["injuries"]
    reports = {}
    if not inj.empty:
        rep = inj[(inj.report_status.isin(["Out", "Doubtful", "Questionable"]))
                  | (inj.practice_status.fillna("").str.contains("Did Not|Limited"))]
        rep = rep[~rep.report_primary_injury.fillna("").str.contains("not injury related", case=False)]
        for gid, g in rep.groupby("gsis_id"):
            g = g.sort_values("week")
            reports[gid] = {"weeks": int(g.week.nunique()),
                            "missed_reports": int((g.report_status == "Out").sum()),
                            "parts": [x for x in (_part(v) for v in g.report_primary_injury) if x],
                            "last_week": int(g.week.max()),
                            "last_part": _part(g.report_primary_injury.iloc[-1])}
    for pid, p in reg.players.items():
        if p.get("pos") not in BASE_HAZARD:
            continue
        u = usage.get(pid, {})
        h = {}
        pg = prev_games.get(pid)
        if pg and pg[1] >= 6:  # only count misses for players who were regulars
            h["missed_prev"] = max(0, prev_max - pg[0])
        tg = team_games.get(p.get("team"))
        if u.get("games") and tg and (u.get("snap") or 0) >= 0.4:
            h["missed_cur"] = max(0, tg - u["games"])
        r = reports.get(pid)
        if r:
            h.update(r)
        if h:
            out[pid] = h
    return out


def risk(r, u, hist, status, inj_detail, week):
    """Return (hazard per game, games lost per injury, level, reasons) for one projected player row."""
    pos = r["pos"]
    if pos not in BASE_HAZARD:
        return None
    h0 = BASE_HAZARD[pos]
    mult, dur, why = 1.0, 2.5, []
    mp = hist.get("missed_prev")
    if mp is not None and mp >= 4:
        mult *= 1.3 if mp < 8 else 1.5
        why.append(f"Missed {mp} games last season")
    mc = hist.get("missed_cur")
    if mc:
        if mc >= 2:
            mult *= 1.25
        why.append(f"Missed {mc} game{'s' if mc > 1 else ''} this season")
    rw = hist.get("weeks", 0)
    if rw >= 3:
        mult *= 1.2
        why.append(f"On the injury report {rw} weeks this season")
    # Most relevant body part: the current injury, else the latest one this season.
    part = _part(inj_detail) or hist.get("last_part")
    recent = status is not None or (hist.get("last_week") is not None and week - hist["last_week"] <= 4)
    if part and recent:
        m, d, note = BODY[part]
        repeat = hist.get("parts", []).count(part) >= 2
        mult *= m * (1.15 if repeat else 1.0)
        dur = d
        why.append(note + (" (and it's happened more than once this year)" if repeat else ""))
    age = r.get("age") or 0
    if pos == "RB" and age >= 28:
        mult *= 1.2
        why.append(f"Age {int(age)} running back")
    elif pos in ("WR", "TE") and age >= 31:
        mult *= 1.15
        why.append(f"Age {int(age)}")
    if pos == "RB" and (u.get("carries_pg") or 0) >= 18:
        mult *= 1.1
        why.append(f"Heavy workload ({u['carries_pg']:.0f} carries a game)")
    h = min(h0 * mult, 0.2)
    # Levels are relative to the position: every RB carries some risk; "High" means well above his peers.
    level = "High" if mult >= 1.5 else "Medium" if mult >= 1.2 else "Low"
    return h, dur, level, why


def availability(h, dur, k):
    """Expected share of a game available k weeks from now (k=0 is this week, handled by injury status)."""
    if k <= 0:
        return 1.0
    return (1 - h) ** min(k, dur)


def summarize(level, h, dur, weeks_ahead=6):
    exp_missed = sum(1 - availability(h, dur, k) for k in range(1, weeks_ahead + 1))
    return {"level": level, "hazard": round(h, 3), "exp_missed_6wk": round(exp_missed, 2)}

