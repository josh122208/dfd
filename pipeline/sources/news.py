"""What coaches are saying: recent player news (RotoWire blurbs via ESPN) and team headlines, reduced to coach
quotes and a simple role signal.

The signal is a keyword read of the coach's own words ("more involved", "limited", "committee", ...), not an
AI judgment: it nudges projections a few percent and the quotes are shown so you can judge for yourself.
"""
import datetime as dt
import html
import re
from concurrent.futures import ThreadPoolExecutor

from ..util import get_json, log, norm_name, safe

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


def player_news(players, coach_of, other_names=(), max_age=3 * 3600, days=21, workers=8):
    """players: {pid: {espn_id, name, team}}. Returns {pid: {quotes, signal, items}} for the last `days` days.

    ESPN's feed for one player often carries items about teammates or opponents, so every item is pooled and
    each coach sentence is credited to the player it actually names (full name, or a last name that's unique on
    his team). A sentence that names nobody ("he said...") goes to the last player named in that story; sentences
    about players we don't track are dropped rather than guessed."""
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    todo = [(pid, p) for pid, p in players.items() if p.get("espn_id")]
    # One quick probe first: if the news service is unreachable, skip instead of retrying hundreds of times.
    if not todo or safe(lambda: get_json(PLAYER_NEWS, params={"playerId": todo[0][1]["espn_id"], "limit": 1},
                                         max_age=max_age, retries=1), None, "news probe") is None:
        return {}

    def fetch(item):
        pid, p = item
        d = safe(lambda: get_json(PLAYER_NEWS, params={"playerId": p["espn_id"], "limit": 8}, max_age=max_age),
                 None, f"news {p['name']}")
        return pid, (d or {}).get("feed", []) or []

    with ThreadPoolExecutor(workers) as ex:
        feeds = list(ex.map(fetch, todo))

    # Name index: tracked players by full name and by (team, last name); untracked names only as "someone else".
    full, by_last = {}, {}
    for pid, p in players.items():
        n = norm_name(p.get("name"))
        if not n:
            continue
        full[n] = pid
        by_last.setdefault((p.get("team"), n.split(" ")[-1]), []).append(pid)
    others = {norm_name(n) for n, _ in other_names} - set(full)
    other_last = {(tm, norm_name(n).split(" ")[-1]) for n, tm in other_names if norm_name(n) not in full}
    names = sorted([n for n in full | {o: None for o in others} if len(n) > 4], key=len, reverse=True)
    name_re = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b") if names else None
    coach_last = {(c.get("coach") or "").split(" ")[-1].lower() for c in coach_of.values() if c.get("coach")}

    def mentions(sentence, team):
        """Tracked pids named in a sentence; ["?"] if only an untracked player is named; [] if nobody."""
        t = norm_name(sentence)
        found, other = [], False
        if name_re:
            for m in name_re.finditer(t):
                pid = full.get(m.group(1))
                if pid and pid not in found:
                    found.append(pid)
                elif not pid:
                    other = True
        if not found:
            words = set(t.split())
            for (tm, last), pids in by_last.items():
                if tm == team and last in words and len(pids) == 1 and len(last) > 3 and last not in coach_last:
                    found.append(pids[0])
            if not found and any((team, w) in other_last for w in words if len(w) > 3 and w not in coach_last):
                other = True
        return found or (["?"] if other else [])

    speech = re.compile(r"\b(said|says|saying|told|tells|noted|added|indicated|expects?|expected|confirmed|announced|"
                        r"mentioned|hinted|praised|called|described|explained|according to|believes?|wants?|plans?|"
                        r"stated|revealed|acknowledged|admitted|insisted|stressed|emphasized|suggested|declined to|"
                        r"wouldn't|won't commit|ruled|listed|declared|updated|relayed|optimistic|hopeful)\b", re.I)

    def is_coach(sentence, team):
        """The player's own coach (or a coach/coordinator by title) saying or judging something."""
        low = sentence.lower()
        c = (coach_of.get(team) or {}).get("coach") or ""
        own = c and (c.lower() in low or re.search(r"\b" + re.escape(c.split(" ")[-1].lower()) + r"\b", low))
        titled = re.search(r"\b(head coach|coach|coordinator|play-?caller|oc)\b", low)
        if not (own or titled):
            return False
        if re.search(r"coaching (tenure|career|staff history)|coach(es)?'s? (teams|system|scheme|offense)\b", low) and not own:
            return False
        return bool(speech.search(sentence))

    seen_items, quotes, items = set(), {}, {}
    for fetched_for, feed in feeds:
        team = players[fetched_for].get("team")
        for f in feed:
            key = f.get("id") or f.get("headline")
            if key in seen_items:
                continue
            seen_items.add(key)
            try:
                when = dt.datetime.fromisoformat(f.get("published", "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if when < cutoff:
                continue
            date = when.date().isoformat()
            head = _clean(f.get("headline") or "")
            body = _clean(f.get("story") or f.get("description") or "")
            prev = mentions(head, team)
            for pid in prev:
                if pid != "?":
                    items.setdefault(pid, []).append({"date": date, "headline": head})
            for sent in _sentences(body):
                named = mentions(sent, team)
                # "He"/"his" only refers back to the player named in the sentence right before.
                targets = named or (prev if re.search(r"\b(he|his|him)\b", sent, re.I) else [])
                prev = named
                a, b = _score(sent)
                for pid in targets:
                    if pid == "?" or not is_coach(sent, players[pid].get("team")):
                        continue
                    quotes.setdefault(pid, []).append({"date": date, "text": sent[:280], "tone": (a > b) - (a < b),
                                                       "pos": a, "neg": b})
    out = {}
    for pid in set(items) | set(quotes):
        qs, seen, uniq = sorted(quotes.get(pid, []), key=lambda q: q["date"], reverse=True), set(), []
        for q in qs:
            if q["text"] not in seen:
                seen.add(q["text"])
                uniq.append(q)
        pos, neg = sum(q["pos"] for q in uniq), sum(q["neg"] for q in uniq)
        # Net tone of the coach's recent words, damped: -1 (role shrinking/limited) .. +1 (role growing).
        signal = 0.0 if pos + neg == 0 else max(-1.0, min(1.0, (pos - neg) / (pos + neg + 1)))
        out[pid] = {"quotes": [{k: q[k] for k in ("date", "text", "tone")} for q in uniq[:4]],
                    "signal": round(signal, 2), "items": sorted(items.get(pid, []), key=lambda i: i["date"], reverse=True)[:4]}
    log.info("player news: %d items pooled; %d players with news, %d with coach quotes", len(seen_items), len(out),
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
