"""Game-time forecasts from Open-Meteo (free, no key) for outdoor stadiums."""
import datetime as dt
from zoneinfo import ZoneInfo

from ..util import get_json, safe

# Home stadium coordinates. Teams with domes or retractable roofs are in INDOOR.
STADIUMS = {
    "ARI": (33.5276, -112.2626), "ATL": (33.7554, -84.4009), "BAL": (39.2780, -76.6227), "BUF": (42.7738, -78.7870),
    "CAR": (35.2258, -80.8528), "CHI": (41.8623, -87.6167), "CIN": (39.0955, -84.5161), "CLE": (41.5061, -81.6995),
    "DAL": (32.7473, -97.0945), "DEN": (39.7439, -105.0201), "DET": (42.3400, -83.0456), "GB": (44.5013, -88.0622),
    "HOU": (29.6847, -95.4107), "IND": (39.7601, -86.1639), "JAX": (30.3239, -81.6373), "KC": (39.0489, -94.4839),
    "LV": (36.0909, -115.1833), "LAC": (33.9535, -118.3392), "LA": (33.9535, -118.3392), "MIA": (25.9580, -80.2389),
    "MIN": (44.9737, -93.2577), "NE": (42.0909, -71.2643), "NO": (29.9511, -90.0812), "NYG": (40.8135, -74.0745),
    "NYJ": (40.8135, -74.0745), "PHI": (39.9008, -75.1675), "PIT": (40.4468, -80.0158), "SF": (37.4030, -121.9700),
    "SEA": (47.5952, -122.3316), "TB": (27.9759, -82.5033), "TEN": (36.1665, -86.7713), "WAS": (38.9077, -76.8645),
}
_DOWN = False
INDOOR = {"ARI", "ATL", "DAL", "DET", "HOU", "IND", "LV", "LA", "LAC", "MIN", "NO"}
# International and neutral sites by stadium name fragment.
NEUTRAL = {"wembley": (51.5560, -0.2796, False), "tottenham": (51.6043, -0.0664, False),
           "stade de france": (48.9245, 2.3602, False), "allianz": (48.2188, 11.6247, False),
           "deutsche bank park": (50.0686, 8.6455, False), "olympiastadion": (52.5147, 13.2395, False),
           "bernabeu": (40.4531, -3.6883, True), "maracan": (-22.9122, -43.2302, False),
           "arena corinthians": (-23.5453, -46.4742, False), "melbourne": (-37.8200, 144.9834, False),
           "azteca": (19.3029, -99.1505, False), "croke": (53.3607, -6.2512, False)}


def game_weather(game):
    """Forecast at kickoff for one nflverse games.csv row. None for domes or games beyond the forecast range."""
    roof = str(game.get("roof") or "").lower()
    stadium = str(game.get("stadium") or "").lower()
    loc = None
    for frag, (lat, lon, closed) in NEUTRAL.items():
        if frag in stadium:
            loc, indoor = (lat, lon), closed
            break
    else:
        home = game.get("home_team")
        loc, indoor = STADIUMS.get(home), home in INDOOR
    if roof in ("dome", "closed") or indoor or loc is None:
        return {"indoor": True}
    try:
        kick = dt.datetime.fromisoformat(f"{game['gameday']}T{game['gametime']}").replace(tzinfo=ZoneInfo("America/New_York"))
    except Exception:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    if not (now - dt.timedelta(hours=4) <= kick <= now + dt.timedelta(days=7)):
        return None
    global _DOWN
    if _DOWN:
        return None
    d = safe(lambda: get_json("https://api.open-meteo.com/v1/forecast", params={
        "latitude": loc[0], "longitude": loc[1], "hourly": "temperature_2m,wind_speed_10m,wind_gusts_10m,precipitation,snowfall",
        "wind_speed_unit": "mph", "temperature_unit": "fahrenheit", "precipitation_unit": "inch",
        "timezone": "UTC", "forecast_days": 8}, max_age=3 * 3600), None, "weather")
    if not d:
        _DOWN = True  # don't wait on a dead service once per game
        return None
    h = d["hourly"]
    k = kick.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:00")
    if k not in h["time"]:
        return None
    i = h["time"].index(k)
    win = slice(i, min(i + 3, len(h["time"])))  # three hours of game time
    return {
        "indoor": False,
        "temp": round(sum(h["temperature_2m"][win]) / len(h["temperature_2m"][win])),
        "wind": round(max(h["wind_speed_10m"][win])),
        "gust": round(max(h["wind_gusts_10m"][win])),
        "precip": round(sum(h["precipitation"][win]), 2),
        "snow": round(sum(h["snowfall"][win]), 2),
    }


def impact(w, pos):
    """Multiplier on a player's projection from weather. Wind hurts passing and kicking most."""
    if not w or w.get("indoor"):
        return 1.0, None
    wind, precip, snow, temp = w.get("wind", 0), w.get("precip", 0), w.get("snow", 0), w.get("temp", 60)
    m, notes = 1.0, []
    if wind >= 15:
        hit = {"QB": 0.06, "WR": 0.06, "TE": 0.04, "K": 0.10, "RB": -0.01}.get(pos, 0)
        hit *= 1.0 if wind < 20 else 1.8
        m -= hit
        notes.append(f"{wind} mph wind")
    if precip >= 0.15 or snow >= 0.3:
        m -= {"QB": 0.04, "WR": 0.04, "TE": 0.03, "K": 0.05, "RB": -0.01}.get(pos, 0)
        notes.append("snow" if snow >= 0.3 else "rain")
    if temp <= 20:
        m -= {"QB": 0.02, "WR": 0.02, "TE": 0.02, "K": 0.04}.get(pos, 0)
        notes.append(f"{temp}°F")
    return m, (", ".join(notes) if notes else None)
