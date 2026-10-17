"""nflverse / ffverse / dynastyprocess open data (all served from GitHub)."""
import pandas as pd

from ..util import get_csv, log, safe

REL = "https://github.com/nflverse/nflverse-data/releases/download"
RAW = "https://raw.githubusercontent.com"


def load(season):
    """Download every table the model uses. Missing tables come back as empty DataFrames."""
    empty = pd.DataFrame()
    t = {}
    t["games"] = safe(lambda: get_csv(f"{RAW}/nflverse/nfldata/master/data/games.csv", max_age=1800), empty, "games")
    t["stats"] = safe(lambda: get_csv(f"{REL}/stats_player/stats_player_week_{season}.csv"), empty, "stats")
    t["stats_prev"] = safe(lambda: get_csv(f"{REL}/stats_player/stats_player_week_{season - 1}.csv", max_age=7 * 86400),
                           empty, "stats_prev")
    t["snaps"] = safe(lambda: get_csv(f"{REL}/snap_counts/snap_counts_{season}.csv"), empty, "snaps")
    t["injuries"] = safe(lambda: get_csv(f"{REL}/injuries/injuries_{season}.csv", max_age=1800), empty, "injuries")
    t["pbp"] = safe(lambda: get_csv(f"{REL}/pbp/play_by_play_{season}.csv.gz"), empty, "pbp")
    t["ep"] = safe(lambda: get_csv(f"https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_{season}.csv"),
                   empty, "ffopportunity")
    t["rosters"] = safe(lambda: get_csv(f"{REL}/weekly_rosters/roster_weekly_{season}.csv"), empty, "rosters")
    t["ids"] = safe(lambda: get_csv(f"{RAW}/dynastyprocess/data/master/files/db_playerids.csv", max_age=86400), empty, "ids")
    t["fpecr"] = safe(lambda: get_csv(f"{RAW}/dynastyprocess/data/master/files/db_fpecr_latest.csv", max_age=1800),
                      empty, "fantasypros")
    t["depth"] = safe(lambda: _latest_depth(season), empty, "depth")
    t["ftn"] = safe(lambda: get_csv(f"{REL}/ftn_charting/ftn_charting_{season}.csv"), empty, "ftn charting")
    for k, v in t.items():
        log.info("nflverse %-11s %s rows", k, len(v))
    t["games_all"] = t["games"]
    if not t["games"].empty:
        t["games"] = t["games"][(t["games"].season == season) & (t["games"].game_type == "REG")].copy()
    return t


def _latest_depth(season):
    d = get_csv(f"{REL}/depth_charts/depth_charts_{season}.csv")
    if "dt" in d:
        d = d[d.dt == d.dt.max()]
    return d
