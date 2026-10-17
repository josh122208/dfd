"""Settings come from environment variables (GitHub Actions secrets/variables) with sane defaults."""
import datetime as dt
import os
from dataclasses import dataclass, field


def _season_default():
    today = dt.date.today()
    # The NFL season that is "current" runs Sept through early Feb of the next year.
    return today.year if today.month >= 3 else today.year - 1


@dataclass
class Config:
    season: int = int(os.environ.get("SEASON") or _season_default())
    league_id: str = (os.environ.get("ESPN_LEAGUE_ID") or "").strip()
    espn_s2: str = (os.environ.get("ESPN_S2") or "").strip()
    swid: str = (os.environ.get("ESPN_SWID") or "").strip()
    my_team_id: str = (os.environ.get("MY_TEAM_ID") or "").strip()
    site_password: str = os.environ.get("SITE_PASSWORD") or ""
    # Used only when no ESPN league is configured.
    default_teams: int = int(os.environ.get("LEAGUE_SIZE") or 12)
    default_ppr: float = float(os.environ.get("PPR") or 0.5)
    # Players named here (comma separated) are treated as your roster when no ESPN league is set.
    manual_roster: list = field(default_factory=lambda: [
        s.strip() for s in (os.environ.get("MY_ROSTER") or "").split(",") if s.strip()])

    @property
    def cookies(self):
        if self.espn_s2 and self.swid:
            swid = self.swid if self.swid.startswith("{") else "{" + self.swid + "}"
            return {"espn_s2": self.espn_s2, "SWID": swid}
        return None
