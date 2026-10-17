"""Offline smoke test: a fake 12-team ESPN league (ESPN's JSON shapes) drafted from real FantasyPros ranks.

    python -m tests.fake_league --out /tmp/out

Exercises ESPN parsing, the trade finder, pitches, waivers and playoff odds without network access to ESPN.
"""
import os
import sys

import pandas as pd

from pipeline.sources import espn, nflverse

TEAM_ID = {v: k for k, v in espn.PRO_TEAM.items() if v}
POS_ID = {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "K": 5, "DST": 16}
OWNERS = ["Josh", "Ethan", "Mull", "Ava", "Chris", "Dev", "Sam", "Jordan", "Riley", "Casey", "Morgan", "Taylor"]
SLOTS = {"0": 1, "2": 2, "4": 2, "6": 1, "23": 2, "16": 1, "17": 1, "20": 6, "21": 1}
SLOT_ID = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "K": 17, "DST": 16}


def build_fake(season=2026, week=6):
    t = nflverse.load(season)
    fp, ids, ro = t["fpecr"], t["ids"], t["rosters"]
    ro = ro.sort_values("week").groupby("gsis_id").tail(1)
    espn_of = {}
    for r in ids.dropna(subset=["fantasypros_id", "espn_id"]).itertuples():
        espn_of[str(int(r.fantasypros_id))] = int(r.espn_id)
    ov = fp[fp.page_type == "redraft-overall"].sort_values("ecr")
    pool = []
    for r in ov.itertuples():
        eid = espn_of.get(str(int(r.id)))
        if eid and r.pos in POS_ID:
            pool.append({"id": eid, "name": r.player, "pos": r.pos, "team": r.team})
    for team in TEAM_ID:
        pool.append({"id": -16000 - TEAM_ID[team], "name": f"{team} D/ST", "pos": "DST", "team": team})
    ks = fp[fp.page_type == "redraft-k"].sort_values("ecr")
    for r in ks.itertuples():
        eid = espn_of.get(str(int(r.id)))
        if eid:
            pool.append({"id": eid, "name": r.player, "pos": "K", "team": r.team})

    def player_obj(p, on_team):
        team = {"LA": "LA", "JAC": "JAX"}.get(p["team"], p["team"])
        return {"id": p["id"], "fullName": p["name"], "defaultPositionId": POS_ID[p["pos"]],
                "proTeamId": TEAM_ID.get(team, 0), "injuryStatus": "ACTIVE",
                "ownership": {"percentOwned": 90 if on_team else 20, "percentChange": 0.5},
                "stats": []}

    # Snake draft, 15 rounds, each team needs at least 1 K and 1 DST.
    rosters = {i: [] for i in range(1, 13)}
    taken = set()
    skill = [p for p in pool if p["pos"] in ("QB", "RB", "WR", "TE")]
    for rnd in range(13):
        order = range(1, 13) if rnd % 2 == 0 else range(12, 0, -1)
        for tid in order:
            have = [p["pos"] for p in rosters[tid]]
            pick = next(p for p in skill if p["id"] not in taken
                        and not (p["pos"] == "QB" and have.count("QB") >= 2)
                        and not (p["pos"] == "TE" and have.count("TE") >= 2))
            taken.add(pick["id"])
            rosters[tid].append(pick)
    for pos in ("K", "DST"):
        avail = [p for p in pool if p["pos"] == pos and p["id"] not in taken]
        for tid in range(1, 13):
            pick = avail.pop(0)
            taken.add(pick["id"])
            rosters[tid].append(pick)

    teams, rost_teams, members = [], [], []
    for tid, plist in rosters.items():
        swid = "{ME-0000}" if tid == 1 else f"{{OWNER-{tid:04d}}}"
        members.append({"id": swid, "firstName": OWNERS[tid - 1], "lastName": "X", "displayName": OWNERS[tid - 1]})
        w = (tid * 7) % 6
        teams.append({"id": tid, "abbrev": f"T{tid}", "name": f"{OWNERS[tid - 1]}'s Team", "owners": [swid],
                      "record": {"overall": {"wins": w, "losses": 5 - w, "ties": 0, "pointsFor": 500 + 20 * w,
                                             "pointsAgainst": 520}},
                      "transactionCounter": {"trades": tid % 3, "acquisitions": 4, "acquisitionBudgetSpent": 20}})
        entries, counts = [], {}
        for p in plist:
            slot = SLOT_ID[p["pos"]]
            counts[p["pos"]] = counts.get(p["pos"], 0) + 1
            if counts[p["pos"]] > {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DST": 1}[p["pos"]]:
                slot = 20
            entries.append({"playerId": p["id"], "lineupSlotId": slot,
                            "playerPoolEntry": {"id": p["id"], "onTeamId": tid, "player": player_obj(p, True)}})
        rost_teams.append({"id": tid, "roster": {"entries": entries}})

    schedule = []
    ids_ = list(range(1, 13))
    for period in range(1, 15):
        rot = ids_[:1] + ids_[1:][period % 11:] + ids_[1:][:period % 11]
        for i in range(6):
            schedule.append({"matchupPeriodId": period, "home": {"teamId": rot[i], "totalPoints": 0},
                             "away": {"teamId": rot[11 - i], "totalPoints": 0}})
    base = {"seasonId": season, "scoringPeriodId": week, "status": {"currentMatchupPeriod": week},
            "settings": {"name": "Fake Half-PPR League",
                         "rosterSettings": {"lineupSlotCounts": SLOTS},
                         "scoringSettings": {"scoringItems": [{"statId": 53, "points": 0.5}]},
                         "scheduleSettings": {"matchupPeriodCount": 14, "playoffTeamCount": 6,
                                              "playoffMatchupPeriodLength": 1},
                         "tradeSettings": {"deadlineDate": int(pd.Timestamp("2026-11-18").timestamp() * 1000)},
                         "acquisitionSettings": {"isUsingAcquisitionBudget": True, "acquisitionBudget": 100}},
            "teams": teams, "members": members, "schedule": schedule}
    fas = [{"id": p["id"], "onTeamId": 0, "status": "FREEAGENT", "player": player_obj(p, False)}
           for p in pool if p["id"] not in taken][:400]
    return base, {"teams": rost_teams}, fas


def main():
    base, rost, fas = build_fake()

    def fake_load(self):
        return espn.parse_league(base, rost, fas, [], self.cfg)

    espn.EspnLeague.load = fake_load
    os.environ.setdefault("ESPN_LEAGUE_ID", "123")
    os.environ.setdefault("ESPN_SWID", "{ME-0000}")
    from pipeline import build
    build.main()


if __name__ == "__main__":
    sys.exit(main())
