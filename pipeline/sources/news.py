"""What coaches are saying: recent player news (RotoWire blurbs via ESPN) and team headlines, reduced to coach
quotes and a simple role signal.

The signal is a keyword read of the coach's own words ("more involved", "limited", "committee", ...), not an
AI judgment: it nudges projections a few percent and the quotes are shown so you can judge for yourself.
"""
import datetime as dt
import html
import re
from concurrent.futures import ThreadPoolExecutor

from ..util import get_json, log, safe

PLAYER_NEWS = "https://site.api.espn.com/apis/fantasy/v2/games/ffl/news/players"
TEAM_NEWS = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news"
ESPN_TEAM = {"LA": "lar", "WAS": "wsh", "LV": "lv", "JAX": "jax"}

POSITIVE = [
    "more involved", "bigger role", "expanded role", "increased role", "more snaps", "more touches", "more work",
    "feature", "bell cow", "workhorse", "lead back", "the starter", "starting job", "earned", "trust", "full go",
    "no limitations", "fully cleared", "cleared to play", "ready to go", "100 percent", "get him the ball",
    "get him involved", "wants to get", "playmaker", "focal point", "impressed", "breakout", "won the job",
]
NEGATIVE = [
    "limited", "snap count", "pitch count", "ease him back", "eased back", "work him back", "rotation", "rotate",
    "committee", "timeshare", "split work", "share the load", "demoted", "benched", "bench", "inactive",
    "not ready", "setback", "day-to-day", "week-to-week", "doesn't expect", "won't play", "game-time decision",
    "frustrat", "struggl", "needs to be better", "ball security", "fumbl", "drop", "healthy scratch", "pull back",
]
COACH_WORDS = re.compile(r"\b(coach|coordinator|play-?caller|head coach|OC|told reporters|said|says|according to)\b",
                         re.I)


def _clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", text) if s.strip()]


def _score(text):
    t = text.lower()
    pos = sum(t.count(w) for w in POSITIVE)
    neg = sum(t.count(w) for w in NEGATIVE)
    return pos, neg


def coach_lines(text, coach_names):
    """Sentences that report something a coach said, preferring ones that name the coach."""
    out = []
    names = [n for n in coach_names if n]
    for s in _sentences(text):
        named = any(n.lower() in s.lower() for n in names)
        if named or (COACH_WORDS.search(s) and re.search(r"coach|coordinator", s, re.I)):
            out.append(s)
    return out


def player_news(players, coach_of, max_age=3 * 3600, days=21, workers=8):
    """players: {pid: {espn_id, name, team}}. Returns {pid: {quotes, signal, items}} for the last `days` days."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)

    def one(item):
        pid, p = item
        d = safe(lambda: get_json(PLAYER_NEWS, params={"playerId": p["espn_id"], "limit": 8}, max_age=max_age),
                 None, f"news {p['name']}")
        if not d:
            return pid, None
        coach = coach_of.get(p.get("team"), {})
        names = [coach.get("coach"), (coach.get("coach") or "").split(" ")[-1]]
        items, quotes, pos, neg = [], [], 0, 0
        for f in d.get("feed", []) or []:
            try:
                when = dt.datetime.fromisoformat(f.get("published", "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if when < cutoff:
                continue
            body = _clean(f.get("story") or f.get("description") or "")
            head = _clean(f.get("headline") or "")
            items.append({"date": when.date().isoformat(), "headline": head})
            for s in coach_lines(head + " " + body, names):
                a, b = _score(s)
                pos, neg = pos + a, neg + b
                quotes.append({"date": when.date().isoformat(), "text": s[:280], "tone": (a > b) - (a < b)})
        if not items:
            return pid, None
        # Net tone of the coach's recent words, damped: -1 (role shrinking/limited) .. +1 (role growing).
        signal = 0.0 if pos + neg == 0 else max(-1.0, min(1.0, (pos - neg) / (pos + neg + 1)))
        seen, uniq = set(), []
        for q in quotes:
            if q["text"] not in seen:
                seen.add(q["text"])
                uniq.append(q)
        return pid, {"quotes": uniq[:4], "signal": round(signal, 2), "items": items[:4]}

    todo = [(pid, p) for pid, p in players.items() if p.get("espn_id")]
    # One quick probe first: if the news service is unreachable, skip instead of retrying hundreds of times.
    if not todo or safe(lambda: get_json(PLAYER_NEWS, params={"playerId": todo[0][1]["espn_id"], "limit": 1},
                                         max_age=max_age, retries=1), None, "news probe") is None:
        return {}
    with ThreadPoolExecutor(workers) as ex:
        res = dict(ex.map(one, todo))
    out = {k: v for k, v in res.items() if v}
    log.info("player news: %d of %d players have recent items, %d with coach quotes", len(out), len(todo),
             sum(1 for v in out.values() if v["quotes"]))
    return out


def team_news(teams, coach_of, max_age=3 * 3600, days=10):
    """Latest headlines per NFL team that quote or mention the head coach."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    if safe(lambda: get_json(TEAM_NEWS, params={"limit": 1}, max_age=max_age, retries=1), None, "team news probe") is None:
        return {}

    def one(team):
        slug = ESPN_TEAM.get(team, team.lower())
        d = safe(lambda: get_json(TEAM_NEWS, params={"team": slug, "limit": 25}, max_age=max_age), None, f"team news {team}")
        if not d:
            return team, []
        coach = (coach_of.get(team) or {}).get("coach") or ""
        last = coach.split(" ")[-1] if coach else None
        out = []
        for a in d.get("articles", []) or []:
            try:
                when = dt.datetime.fromisoformat(a.get("published", "").replace("Z", "+00:00"))
            except ValueError:
                continue
            text = _clean(f"{a.get('headline', '')}. {a.get('description', '')}")
            if when >= cutoff and (last and last.lower() in text.lower() or re.search(r"\bcoach\b", text, re.I)):
                out.append({"date": when.date().isoformat(), "text": text[:260], "link": ((a.get("links") or {}).get("web") or {}).get("href")})
        return team, out[:3]

    with ThreadPoolExecutor(8) as ex:
        return {k: v for k, v in ex.map(one, sorted(teams)) if v}
