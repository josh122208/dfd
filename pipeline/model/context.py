"""Team-level context: Vegas, pace, pass tendency, efficiency, QB situation, coaching, defense vs position."""
import numpy as np
import pandas as pd

from ..util import norm_team

POSITIONS = ("QB", "RB", "WR", "TE")


def current_week(games):
    """First regular-season week with a game not yet final."""
    if games.empty:
        return 1
    pending = games[games.home_score.isna()]
    return int(pending.week.min()) if not pending.empty else int(games.week.max())


def implied_totals(row):
    """(away, home) implied points. nflverse spread_line is the expected home margin."""
    tot, spr = row.get("total_line"), row.get("spread_line")
    if pd.isna(tot) or pd.isna(spr):
        return None, None
    return tot / 2 - spr / 2, tot / 2 + spr / 2


def schedule_table(games, scoreboard=None):
    """One row per team-game with opponent, home/away, kickoff and implied total (live lines override)."""
    rows = []
    for _, g in games.iterrows():
        away_imp, home_imp = implied_totals(g)
        total, spread = g.get("total_line"), g.get("spread_line")
        live = (scoreboard or {}).get((g.away_team, g.home_team))
        if live and live.get("total") is not None and live.get("spread_home") is not None:
            total, spread = live["total"], -live["spread_home"]
            away_imp, home_imp = total / 2 - spread / 2, total / 2 + spread / 2
        for team, opp, home, imp, opp_imp in ((g.home_team, g.away_team, True, home_imp, away_imp),
                                              (g.away_team, g.home_team, False, away_imp, home_imp)):
            pts = g.home_score if home else g.away_score
            opp_pts = g.away_score if home else g.home_score
            rows.append({"team": team, "opp": opp, "week": int(g.week), "home": home, "game_id": g.game_id,
                         "gameday": g.gameday, "gametime": g.gametime, "weekday": g.get("weekday"),
                         "implied": imp, "opp_implied": opp_imp, "total": total,
                         "spread": (spread if home else -spread) if pd.notna(spread) else None,
                         "pts": pts, "opp_pts": opp_pts, "final": pd.notna(pts),
                         "qb": g.home_qb_name if home else g.away_qb_name,
                         "coach": g.home_coach if home else g.away_coach,
                         "roof": g.get("roof"), "stadium": g.get("stadium"), "row": g})
    return pd.DataFrame(rows)


def team_context(t, sched, week):
    """Per-team offensive profile used for adjustments and for the 'why' notes."""
    pbp, games_all = t["pbp"], t["games_all"]
    ctx = {}
    played = sched[sched.final]
    league_ppg = played.pts.mean() if not played.empty else 22.5
    for team in sorted(set(sched.team)):
        g = played[played.team == team]
        c = {"team": team, "games": int(len(g)),
             "ppg": float(g.pts.mean()) if len(g) else None,
             "opp_ppg": float(g.opp_pts.mean()) if len(g) else None,
             "avg_implied": float(g.implied.mean()) if len(g) and g.implied.notna().any() else league_ppg}
        cur = sched[(sched.team == team) & (sched.week == week)]
        if not cur.empty:
            r = cur.iloc[0]
            c.update({"opp": r.opp, "home": bool(r.home), "implied": r.implied, "opp_implied": r.opp_implied,
                      "spread": r.spread, "total": r.total, "qb_this_week": r.qb, "coach": r.coach,
                      "kickoff": f"{r.gameday} {r.gametime}", "weekday": r.weekday})
        else:
            c.update({"opp": None, "bye": True})
        # Who has actually been taking the snaps at QB this year?
        if not pbp.empty:
            drop = pbp[(pbp.posteam == team) & (pbp.qb_dropback == 1) & pbp.passer_player_id.notna()]
            if "passer_player_name" in drop:
                counts = drop.groupby("passer_player_name").size().sort_values(ascending=False)
                c["primary_qb"] = counts.index[0] if len(counts) else None
            off = pbp[(pbp.posteam == team) & pbp.play_type.isin(["pass", "run"])]
            neutral = off[(off.wp.between(0.2, 0.8)) & (off.down <= 3) & (off.half_seconds_remaining > 120)]
            c["proe"] = float(neutral.pass_oe.mean()) if neutral.pass_oe.notna().any() else None
            c["plays_pg"] = float(len(off) / max(len(g), 1)) if len(g) else None
            c["epa_play"] = float(off.epa.mean()) if len(off) else None
            c["pass_epa"] = float(off[off.play_type == "pass"].epa.mean()) if len(off) else None
            c["rush_epa"] = float(off[off.play_type == "run"].epa.mean()) if len(off) else None
            de = pbp[(pbp.defteam == team) & pbp.play_type.isin(["pass", "run"])]
            c["def_pass_epa"] = float(de[de.play_type == "pass"].epa.mean()) if len(de) else None
            c["def_rush_epa"] = float(de[de.play_type == "run"].epa.mean()) if len(de) else None
            rz = off[off.yardline_100 <= 20]
            c["rz_trips_pg"] = float(rz.drive.nunique() / max(len(g), 1)) if len(g) and "drive" in rz else None
            # QB efficiency per passer this season (EPA per dropback)
            if "passer_player_name" in drop:
                q = drop.groupby("passer_player_name").agg(n=("epa", "size"), epa=("epa", "mean"))
                c["qb_epa"] = {k: (int(v.n), float(v.epa)) for k, v in q.iterrows()}
        # Coaching change since last season
        prev = games_all[(games_all.season == t["games"].season.max() - 1)] if not games_all.empty else pd.DataFrame()
        if not prev.empty and c.get("coach"):
            last = prev[(prev.home_team == team) | (prev.away_team == team)].sort_values("week").tail(1)
            if len(last):
                lr = last.iloc[0]
                pc = lr.home_coach if lr.home_team == team else lr.away_coach
                c["new_coach"] = bool(pc and pc != c["coach"])
        ctx[team] = c
    # League ranks for pace/PROE to phrase notes ("3rd-fastest offense")
    for key in ("proe", "plays_pg", "epa_play", "def_pass_epa", "def_rush_epa", "rz_trips_pg"):
        vals = [(k, v[key]) for k, v in ctx.items() if v.get(key) is not None and not np.isnan(v[key])]
        rev = key not in ("def_pass_epa", "def_rush_epa")  # lower EPA allowed = better defense
        for rank, (k, _) in enumerate(sorted(vals, key=lambda x: x[1], reverse=rev), 1):
            ctx[k][key + "_rank"] = rank
    return ctx


def qb_change(c):
    """Is a different QB starting this week than the one who's played most? Returns (flag, factor, note)."""
    cur, prim = c.get("qb_this_week"), c.get("primary_qb")
    if not cur or not prim or not isinstance(cur, str):
        return False, 1.0, None
    # pbp passer names are abbreviated ("J.Hurts"); compare on last name + first initial.
    def key(n):
        parts = n.replace(".", " ").split()
        return (parts[0][0] + parts[-1]).lower() if parts else n.lower()
    if key(cur) == key(prim):
        return False, 1.0, None
    epa = c.get("qb_epa") or {}
    prim_epa = epa.get(prim, (0, 0.05))[1]
    cur_epa = next((v[1] for k, v in epa.items() if key(k) == key(cur) and v[0] >= 40), None)
    # Each 0.1 EPA/dropback is worth roughly 6% of pass-catcher production; unknown backups get a flat haircut.
    factor = 0.88 if cur_epa is None else float(np.clip(1 + 0.6 * (cur_epa - prim_epa), 0.75, 1.1))
    return True, factor, f"{cur} starting at QB instead of {prim}"


def defense_vs_position(stats, ppr):
    """Schedule-adjusted fantasy points allowed by each defense to each position (1.0 = average)."""
    if stats.empty:
        return {}, {}
    s = stats[stats.season_type == "REG"].copy()
    s["pts"] = s.fantasy_points.fillna(0) + ppr * s.receptions.fillna(0)
    s = s[s.position.isin(POSITIONS)]
    by_game = s.groupby(["game_id", "week", "team", "opponent_team", "position"]).pts.sum().reset_index()
    # Offensive baseline: what each offense typically scores per position
    off_avg = by_game.groupby(["team", "position"]).pts.mean().rename("off_avg").reset_index()
    by_game = by_game.merge(off_avg, on=["team", "position"])
    pos_avg = by_game.groupby("position").pts.mean()
    by_game["ratio"] = by_game.pts / by_game.off_avg.clip(lower=1)
    out, raw = {}, {}
    for (defense, pos), g in by_game.groupby(["opponent_team", "position"]):
        n = len(g)
        shrunk = (g.ratio.sum() + 3 * 1.0) / (n + 3)  # regress toward average with 3 phantom games
        out.setdefault(norm_team(defense), {})[pos] = float(shrunk)
        raw.setdefault(norm_team(defense), {})[pos] = float(g.pts.mean())
    # Rank 1 = easiest matchup
    ranks = {}
    for pos in POSITIONS:
        order = sorted(out, key=lambda d: out[d].get(pos, 1), reverse=True)
        for i, d in enumerate(order, 1):
            ranks.setdefault(d, {})[pos] = i
    return out, {"ranks": ranks, "allowed": raw, "pos_avg": pos_avg.to_dict()}
