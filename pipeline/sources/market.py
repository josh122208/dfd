"""Crowd and market sources: Sleeper projections/trends, FantasyCalc trade values, FantasyPros rankings."""
import pandas as pd

from ..util import get_json, log, norm_team, safe

SLEEPER = "https://api.sleeper.app"


def sleeper_players():
    """Injury body part, practice status and depth-chart order for every NFL player (~5 MB)."""
    d = get_json(f"{SLEEPER}/v1/players/nfl", max_age=12 * 3600)
    out = {}
    for sid, p in d.items():
        if p.get("position") not in ("QB", "RB", "WR", "TE", "K", "DEF"):
            continue
        out[sid] = {
            "name": p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
            "pos": "DST" if p.get("position") == "DEF" else p.get("position"),
            "team": norm_team(p.get("team")),
            "gsis_id": (p.get("gsis_id") or "").strip() or None,
            "espn_id": p.get("espn_id"),
            "injury_status": p.get("injury_status"),
            "injury_body_part": p.get("injury_body_part"),
            "injury_notes": p.get("injury_notes"),
            "practice": p.get("practice_participation"),
            "depth_order": p.get("depth_chart_order"),
            "age": p.get("age"),
            "years_exp": p.get("years_exp"),
            "news_updated": p.get("news_updated"),
        }
    return out


def sleeper_projections(season, weeks):
    """{week: {sleeper_id: half-PPR points}} for every requested week Sleeper has published."""
    out = {}
    params = [("season_type", "regular")] + [("position[]", p) for p in ("QB", "RB", "WR", "TE", "K", "DEF")]
    for w in weeks:
        rows = safe(lambda: get_json(f"{SLEEPER}/projections/nfl/{season}/{w}", params=params, max_age=3 * 3600),
                    [], f"sleeper proj w{w}")
        wk = {}
        for r in rows or []:
            pts = (r.get("stats") or {}).get("pts_half_ppr")
            if pts is not None:
                wk[str(r.get("player_id"))] = float(pts)
        if wk:
            out[w] = wk
    log.info("sleeper projections for weeks %s", sorted(out))
    return out


def sleeper_trending():
    """Adds/drops across all Sleeper leagues in the last 48 hours: a live read on the waiver crowd."""
    res = {}
    for kind in ("add", "drop"):
        rows = safe(lambda: get_json(f"{SLEEPER}/v1/players/nfl/trending/{kind}",
                                     params={"lookback_hours": 48, "limit": 200}, max_age=3600), [], f"trend {kind}")
        for r in rows or []:
            res.setdefault(str(r["player_id"]), {})[kind] = r.get("count", 0)
    return res


def fantasycalc(teams=12, ppr=0.5):
    """Redraft trade values from FantasyCalc (built from millions of real trades)."""
    d = get_json("https://api.fantasycalc.com/values/current",
                 params={"isDynasty": "false", "numQbs": 1, "numTeams": teams, "ppr": ppr}, max_age=3 * 3600)
    rows = []
    for r in d:
        p = r.get("player") or {}
        rows.append({
            "fc_name": p.get("name"), "pos": p.get("position"), "team": norm_team(p.get("maybeTeam")),
            "sleeper_id": str(p.get("sleeperId")) if p.get("sleeperId") else None,
            "espn_id": p.get("espnId"), "mfl_id": p.get("mflId"),
            "fc_value": r.get("value"), "fc_rank": r.get("overallRank"), "fc_pos_rank": r.get("positionRank"),
            "fc_trend30": r.get("trend30Day"),
        })
    log.info("fantasycalc: %d values", len(rows))
    return pd.DataFrame(rows)


def fantasypros(fpecr):
    """Expert consensus ranks: this week (by position) and rest of season (overall + by position)."""
    if fpecr is None or fpecr.empty:
        return {}
    out = {}
    for _, r in fpecr.iterrows():
        pt = r["page_type"]
        if not (pt.startswith("weekly-") or pt.startswith("redraft-")):
            continue
        key = str(int(r["id"])) if pd.notna(r["id"]) else None
        if not key:
            continue
        d = out.setdefault(key, {"name": r["player"], "pos": r["pos"], "team": norm_team(r.get("team")),
                                 "bye": r.get("bye")})
        if pt in ("weekly-qb", "weekly-rb", "weekly-wr", "weekly-te", "weekly-k", "weekly-dst"):
            d["fp_week_rank"] = r["ecr"]
            d["fp_week_sd"] = r["sd"]
            d["fp_week_best"], d["fp_week_worst"] = r["best"], r["worst"]
        elif pt == "redraft-overall":
            d["fp_ros_overall"] = r["ecr"]
        elif pt in ("redraft-qb", "redraft-rb", "redraft-wr", "redraft-te", "redraft-k", "redraft-dst"):
            d["fp_ros_pos_rank"] = r["ecr"]
    return out
