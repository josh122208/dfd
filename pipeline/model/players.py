"""Player identity crosswalk (ESPN / Sleeper / FantasyPros / nflverse) and usage features."""
import datetime as dt

import numpy as np
import pandas as pd

from ..util import norm_name, norm_team

SKILL = ("QB", "RB", "WR", "TE")
FANTASY_POS = SKILL + ("K",)


def _str_id(x):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    try:
        return str(int(float(x)))
    except (TypeError, ValueError):
        return str(x)


def _same_person(a, b):
    """Loose name check: same last name (after normalization), or one full name contains the other."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return True
    return na.split(" ")[-1] == nb.split(" ")[-1] or na in nb or nb in na


class Registry:
    """Master player list keyed by `pid` (the nflverse gsis_id, or DST_<TEAM> for defenses)."""

    def __init__(self, t):
        self.players = {}
        self.by_espn, self.by_sleeper, self.by_fp, self.by_pfr, self.by_name = {}, {}, {}, {}, {}
        ro = t["rosters"]
        if not ro.empty:
            ro = ro.sort_values("week").groupby("gsis_id").tail(1)
            ro = ro[ro.position.isin(FANTASY_POS + ("FB",))]
            today = dt.date.today()
            for _, r in ro.iterrows():
                age = None
                if isinstance(r.get("birth_date"), str):
                    try:
                        b = dt.date.fromisoformat(r["birth_date"][:10])
                        age = round((today - b).days / 365.25, 1)
                    except ValueError:
                        pass
                self.add(r.gsis_id, name=r.full_name, pos="RB" if r.position == "FB" else r.position,
                         team=norm_team(r.team), espn_id=_str_id(r.get("espn_id")), sleeper_id=_str_id(r.get("sleeper_id")),
                         pfr_id=r.get("pfr_id"), age=age, exp=r.get("years_exp"), headshot=r.get("headshot_url"),
                         status=r.get("status"))
        ids = t["ids"]
        if not ids.empty:
            for _, r in ids[ids.gsis_id.notna()].iterrows():
                p = self.players.get(r.gsis_id)
                if p is None:
                    continue
                fp = _str_id(r.get("fantasypros_id"))
                if fp:
                    p["fp_id"] = fp
                    self.by_fp[fp] = r.gsis_id
                for k, src in (("espn_id", "espn_id"), ("sleeper_id", "sleeper_id")):
                    if not p.get(k) and _str_id(r.get(src)):
                        p[k] = _str_id(r.get(src))
                        (self.by_espn if k == "espn_id" else self.by_sleeper)[p[k]] = r.gsis_id
        for team in ("ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LV LAC LA MIA MIN NE NO "
                     "NYG NYJ PHI PIT SF SEA TB TEN WAS").split():
            self.add(f"DST_{team}", name=f"{team} D/ST", pos="DST", team=team)

    def add(self, pid, **kw):
        p = self.players.setdefault(pid, {"pid": pid})
        p.update({k: v for k, v in kw.items() if v is not None and v == v})
        if p.get("espn_id"):
            self.by_espn[p["espn_id"]] = pid
        if p.get("sleeper_id"):
            self.by_sleeper[p["sleeper_id"]] = pid
        if p.get("pfr_id"):
            self.by_pfr[p["pfr_id"]] = pid
        if p.get("name") and p.get("pos"):
            self.by_name.setdefault((norm_name(p["name"]), p["pos"]), pid)
        return p

    def match(self, espn_id=None, sleeper_id=None, fp_id=None, name=None, pos=None, team=None):
        """Find a pid for an outside player record, creating one if it's genuinely new."""
        if pos == "DST" and team:
            return f"DST_{norm_team(team)}"
        for key, table in ((_str_id(espn_id), self.by_espn), (_str_id(sleeper_id), self.by_sleeper),
                           (_str_id(fp_id), self.by_fp)):
            if key and key in table:
                hit = table[key]
                # ID crosswalks occasionally point at the wrong person: require the last names to agree.
                if not name or _same_person(name, self.players.get(hit, {}).get("name")):
                    return hit
        if name and pos:
            pid = self.by_name.get((norm_name(name), pos))
            if pid:
                return pid
        if not name or not pos:
            return None
        pid = f"X_{norm_name(name).replace(' ', '_')}_{pos}"
        self.add(pid, name=name, pos=pos, team=norm_team(team), espn_id=_str_id(espn_id),
                 sleeper_id=_str_id(sleeper_id))
        return pid


def weekly_usage(t, reg, ppr):
    """One row per player-game with points, expected points and opportunity shares."""
    s = t["stats"]
    if s.empty:
        return pd.DataFrame()
    # Kickers are left out: nflverse doesn't score them, so their "points" here would all be zero.
    s = s[(s.season_type == "REG") & s.position.isin(SKILL)].copy()
    s["pts"] = s.fantasy_points.fillna(0) + ppr * s.receptions.fillna(0)
    team_car = s.groupby(["team", "week"]).carries.sum().rename("team_carries")
    s = s.join(team_car, on=["team", "week"])
    s["carry_share"] = s.carries / s.team_carries.replace(0, np.nan)
    cols = ["player_id", "week", "team", "opponent_team", "position", "pts", "fantasy_points", "targets", "receptions",
            "receiving_yards", "receiving_tds", "receiving_air_yards", "carries", "rushing_yards", "rushing_tds",
            "attempts", "completions", "passing_yards", "passing_tds", "passing_interceptions", "passing_epa",
            "target_share", "air_yards_share", "wopr", "carry_share"]
    u = s[[c for c in cols if c in s]].rename(columns={"player_id": "pid", "opponent_team": "opp"})

    ep = t["ep"]
    if not ep.empty:
        e = ep[["player_id", "week", "total_fantasy_points_exp", "receptions_exp", "total_touchdown",
                "total_touchdown_exp"]].copy()
        # ffopportunity scores full PPR; shift expected points to this league's reception value.
        e["exp_pts"] = e.total_fantasy_points_exp - (1 - ppr) * e.receptions_exp.fillna(0)
        e = e.rename(columns={"player_id": "pid"}).drop(columns=["total_fantasy_points_exp", "receptions_exp"])
        u = u.merge(e, on=["pid", "week"], how="left")

    sn = t["snaps"]
    if not sn.empty:
        sn = sn[sn.game_type == "REG"][["pfr_player_id", "week", "offense_pct"]].copy()
        sn["pid"] = sn.pfr_player_id.map(reg.by_pfr)
        u = u.merge(sn.dropna(subset=["pid"])[["pid", "week", "offense_pct"]].drop_duplicates(["pid", "week"]),
                    on=["pid", "week"], how="left")

    pbp = t["pbp"]
    if not pbp.empty:
        rz = pbp[(pbp.yardline_100 <= 20) & pbp.play_type.isin(["pass", "run"])]
        tg = rz.groupby(["receiver_player_id", "week"]).size().rename("rz_targets")
        ca = rz.groupby(["rusher_player_id", "week"]).size().rename("rz_carries")
        gl = rz[rz.yardline_100 <= 5].groupby(["rusher_player_id", "week"]).size().rename("gl_carries")
        for ser in (tg, ca, gl):
            ser.index.names = ["pid", "week"]
            u = u.join(ser, on=["pid", "week"])
        for c in ("rz_targets", "rz_carries", "gl_carries"):
            u[c] = u[c].fillna(0)
    return u


def summarize(u, prev, ppr):
    """Per-player season/recent aggregates plus a recency-weighted per-game base rate."""
    out = {}
    prev_ppg = {}
    if prev is not None and not prev.empty:
        p = prev[(prev.season_type == "REG")].copy()
        p["pts"] = p.fantasy_points.fillna(0) + ppr * p.receptions.fillna(0)
        g = p.groupby("player_id").agg(n=("pts", "size"), ppg=("pts", "mean"), sd=("pts", "std"))
        prev_ppg = {k: (int(v.n), float(v.ppg), float(v.sd) if v.sd == v.sd else None) for k, v in g.iterrows()}
    if u.empty:
        return out, prev_ppg
    for pid, g in u.sort_values("week").groupby("pid"):
        g = g.copy()
        full = g[(g.offense_pct.isna()) | (g.offense_pct >= 0.25)] if "offense_pct" in g else g
        if full.empty:
            full = g
        k = np.arange(len(full))[::-1]
        w = 0.82 ** k
        act = float(np.average(full.pts, weights=w))
        exp = float(np.average(full.exp_pts.fillna(full.pts), weights=w)) if "exp_pts" in full else act
        base = 0.55 * exp + 0.45 * act
        n = len(full)
        pr = prev_ppg.get(pid)
        if pr and pr[0] >= 4:
            base = (n * base + 2.5 * pr[1]) / (n + 2.5)
        last2 = g.tail(2)

        def m(col, frame=g):
            return float(frame[col].mean()) if col in frame and frame[col].notna().any() else None

        out[pid] = {
            "games": int(len(g)), "base": base,
            "ppg": float(g.pts.mean()), "exp_ppg": m("exp_pts"),
            "last3_ppg": float(g.tail(3).pts.mean()), "last_pts": float(g.pts.iloc[-1]), "last_week": int(g.week.iloc[-1]),
            "weekly": [[int(a), round(float(b), 1)] for a, b in zip(g.week, g.pts)],
            "snap": m("offense_pct"), "snap_last2": m("offense_pct", last2),
            "tgt_share": m("target_share"), "tgt_share_last2": m("target_share", last2),
            "carry_share": m("carry_share"), "carry_share_last2": m("carry_share", last2),
            "wopr": m("wopr"), "targets_pg": m("targets"), "carries_pg": m("carries"),
            "rz_opps_pg": float((g.get("rz_targets", 0) + g.get("rz_carries", 0)).mean()) if "rz_targets" in g else None,
            "gl_carries": float(g.gl_carries.sum()) if "gl_carries" in g else None,
            "adot": float(g.receiving_air_yards.sum() / g.targets.sum()) if g.targets.sum() > 0 else None,
            "td": float(g.total_touchdown.sum()) if "total_touchdown" in g and g.total_touchdown.notna().any() else None,
            "td_exp": float(g.total_touchdown_exp.sum()) if "total_touchdown_exp" in g and g.total_touchdown_exp.notna().any() else None,
            "sd": float(full.pts.std()) if n >= 3 else None,
            "prev_ppg": pr[1] if pr else None, "prev_sd": pr[2] if pr else None,
            "pass_yds_pg": m("passing_yards"), "rush_yds_pg": m("rushing_yards"), "rec_yds_pg": m("receiving_yards"),
            "rec_pg": m("receptions"),
        }
    return out, prev_ppg
