"""Turn a trade into a pitch: which psychological levers fit this manager, and the exact message to send.

Tactics are drawn from negotiation research as applied to fantasy trading (FantasyPros "Trading 101" 2026,
PFF "30 Ways to Win a Trade", The Fantasy Footballers' negotiation guides):
  - need framing: lead with the hole in THEIR lineup, not the value you want
  - box-score / recency bias: most managers judge by points scored, so cite the flattering, true stats
  - social proof: quote whichever expert ranking makes your player look best
  - loss aversion: tell them what their weak spot is costing them each week
  - anchoring + concession: open slightly in your favor, then "give" the add-on you planned all along
  - choice / decoy: two options makes "which one" the question instead of "whether"
  - urgency: deadlines, byes and injuries create a reason to act now
  - reciprocity and honesty: explain your own need; a believable reason makes a deal feel fair
"""
import datetime as dt

from ..model.projections import ordinal

PLAYBOOK = [
    {"name": "Feed what they chase", "how": "Managers repeat themselves: the positions they ask for in trades and grab "
     "on waivers show what they believe they need. Offer that position and the deal sells itself.",
     "source": "League trade and waiver history"},
    {"name": "Lead with their need", "how": "Open by naming the hole in their lineup (an injury, a bye week crunch, a weak flex). "
     "People accept trades that solve a problem they already feel.", "source": "FantasyPros Trading 101 (2026)"},
    {"name": "Sell the box score", "how": "Most managers judge players by fantasy points scored. Cite total points, last week's "
     "game and touchdowns for the player you send; let the usage data stay in your back pocket.",
     "source": "PFF, cognitive biases in trading"},
    {"name": "Anchor, then concede", "how": "Open slightly in your favor. The first number frames the whole negotiation, and "
     "the add-on you 'give up' later feels like a win for them.", "source": "Fantasy Footballers negotiation tips"},
    {"name": "Offer two options", "how": "Send one offer and mention a second. The question becomes 'which one', not 'whether'.",
     "source": "Decoy/choice framing"},
    {"name": "Use expert ranks as social proof", "how": "Quote whichever ranking (FantasyPros ROS, ESPN, trade value trend) makes "
     "your player look best. It's true and it shifts the burden of proof.", "source": "PFF"},
    {"name": "Frame the cost of waiting", "how": "Loss aversion: 'your flex has averaged 6 points' hits harder than "
     "'my guy averages 12'.", "source": "Behavioral economics"},
    {"name": "Create real urgency", "how": "Trade deadline, upcoming byes, or your player's soft matchup this week are reasons "
     "to decide now. Don't invent fake competing offers.", "source": "Fantasy Footballers (dynasty psychology)"},
    {"name": "Never insult with a lowball", "how": "Lopsided offers make people defensive and kill future deals. If there's no "
     "logical reason for them to accept, don't send it.", "source": "FantasyPros Trading 101 (2026)"},
    {"name": "Mind the roster spot", "how": "In a 2-for-1, the team getting two players has to cut someone. Name who they'd drop "
     "and why it doesn't hurt.", "source": "Fantasy Footballers trading etiquette"},
    {"name": "Be fast and friendly", "how": "Reply quickly, keep messages short, and build a reputation as the easy person to "
     "trade with. You'll be the first call next time.", "source": "PFF"},
]


def _names(pids, P):
    return " + ".join(P[p]["name"] for p in pids)


def _flattering(pid, P):
    """The two most flattering TRUE facts about a player, for the manager who judges by box scores."""
    r = P[pid]
    u = r.get("usage") or {}
    facts = []
    if r.get("pts_rank") and r["pts_rank"] <= 36:
        facts.append((3 - r["pts_rank"] / 20, f"the {ordinal(r['pts_rank'])}-most points among {r['pos']}s"))
    if u.get("last_pts") is not None and u.get("ppg") and u["last_pts"] >= max(u["ppg"] * 1.2, 12):
        facts.append((2.5, f"{u['last_pts']:.1f} points in his last game"))
    if u.get("td") and u["td"] >= 3:
        facts.append((2.2, f"{u['td']:.0f} TDs already"))
    if u.get("targets_pg") and u["targets_pg"] >= 7:
        facts.append((2.0, f"{u['targets_pg']:.1f} targets a game"))
    if u.get("carries_pg") and u["carries_pg"] >= 15:
        facts.append((2.0, f"{u['carries_pg']:.1f} carries a game"))
    if r.get("fp_ros_pos_rank") and r["fp_ros_pos_rank"] <= 30:
        facts.append((1.8, f"FantasyPros has him {r['pos']}{round(r['fp_ros_pos_rank'])} rest of season"))
    if r.get("fc_trend30") and r["fc_trend30"] > 150:
        facts.append((1.6, "his trade value is up over the last month"))
    if r.get("sos_ros") and r["sos_ros"] > 1.05:
        facts.append((1.5, "one of the easier schedules the rest of the way"))
    if u.get("ppg") and u["ppg"] >= 12:
        facts.append((1.4, f"{u['ppg']:.1f} points a game"))
    facts.sort(key=lambda x: -x[0])
    return [f for _, f in facts[:2]]


def pitch(e, P, prof, teams, me, S):
    tid = e["team"]
    pr, mine = prof[tid], prof[me]
    give, get = e["give"], e["get"]
    first = pr["owner"].split(" ")[0] if pr["owner"] else "there"
    tactics, lines, talking, objections = [], [], [], []

    # 1. Need hook
    hook = None
    give_pos = {P[p]["pos"] for p in give}
    # Priority: an injury at a position we're sending > their weakest position > a bye-week crunch.
    hurt = next((P[h] for h in pr["injured_starters"] if P[h]["pos"] in give_pos), None)
    if hurt:
        hook = f"Saw {hurt['name']} is {'out' if hurt.get('injury') == 'O' else 'banged up'}. Sorry, that's rough."
        tactics.append("Lead with their need")
    if not hook and pr["need"] in give_pos:
        rows = sorted([P[p] for p in teams[tid]["pids"] if p in P and P[p]["pos"] == pr["need"]],
                      key=lambda r: -(r.get("ros_ppg") or 0))
        k = int(S["slots"].get(pr["need"], 1)) or 1
        weakest = rows[k - 1] if len(rows) >= k else None
        if weakest and weakest.get("usage", {}).get("ppg") is not None:
            hook = (f"Looks like {pr['need']} has been a tough spot for you. {weakest['name']} is averaging "
                    f"{weakest['usage']['ppg']:.1f} a game.")
        else:
            hook = f"Looks like {pr['need']} has been a tough spot for you this year."
        tactics += ["Lead with their need", "Frame the cost of waiting"]
    if not hook and pr["byes"]:
        b = pr["byes"][0]
        hook = f"Noticed you've got {b['starters_out']} starters on bye in Week {b['week']}."
        tactics.append("Create real urgency")
    if hook:
        lines.append(hook)

    tend = pr.get("tendency") or {}
    if tend.get("chases") and tend["chases"] in give_pos and not hook:
        lines.append(f"Saw you've been hunting for {tend['chases']} help.")
        tactics.append("Feed what they chase")
    # 2. The offer
    lines.append(f"Would you do {_names(give, P)} for {_names(get, P)}?")

    # 3. Box-score framing of what they receive
    for pid in give:
        facts = _flattering(pid, P)
        if facts:
            lines.append(f"{P[pid]['name']} has {' and '.join(facts)}.")
            tactics.append("Sell the box score")
            break

    # 4. Fit in their lineup
    if e["their_gain"] > 0.3:
        lines.append(f"By my math he slots right into your starting lineup, about +{e['their_gain']:.1f} points a week for you.")
        tactics.append("Lead with their need")

    # 5. Honest reason (reciprocity)
    get_pos = sorted({P[p]["pos"] for p in get})
    if mine["need"] in get_pos:
        lines.append(f"I'm thin at {mine['need']} and have depth where you need it, so it works for both of us.")
    else:
        lines.append("Trying to balance my roster a bit, and this seems like it helps us both.")
    tactics.append("Reciprocity")

    # 6. Choice
    alt = next((s for s in e.get("ladder") or [] if s["label"] == "Walk-away price"), None)
    if alt and alt["give"] != give:
        extra = [p for p in alt["give"] if p not in give]
        talking.append(f"If they counter, add {_names(extra, P)} (your walk-away price). Present it as a concession.")
        tactics.append("Anchor, then concede")

    # 7. Urgency
    deadline = S.get("trade_deadline")
    if deadline:
        try:
            d = dt.datetime.fromtimestamp(int(deadline) / 1000, dt.timezone.utc).date()
            days = (d - dt.date.today()).days
            if 0 <= days <= 21:
                lines.append(f"Trade deadline's {d.strftime('%b %-d')}, so happy to get something done this week.")
                tactics.append("Create real urgency")
        except (TypeError, ValueError, OSError):
            pass
    lines.append("No pressure either way. Let me know!")

    # Talking points backed by data (for when they push back)
    for pid in get:
        r = P[pid]
        u = r.get("usage") or {}
        ir = r.get("inj_risk") or {}
        if ir.get("level") == "High":
            talking.append(f"Know the risk: {r['name']} is high injury risk ({'; '.join(ir.get('why', [])[:2])}). "
                           "Make sure your bench can cover if he misses time.")
        if u.get("exp_ppg") and u.get("ppg") and u["exp_ppg"] > u["ppg"] + 1.5:
            talking.append(f"Don't mention it, but {r['name']}'s usage is worth {u['exp_ppg']:.1f}/game vs {u['ppg']:.1f} "
                           "scored. That's the edge you're buying.")
    for pid in give:
        r = P[pid]
        u = r.get("usage") or {}
        if u.get("td") is not None and u.get("td_exp") is not None and u["td"] - u["td_exp"] >= 1.5:
            objections.append({"if": f"\"{r['name']} is just TD-lucky\"",
                               "say": f"Point to volume: {u.get('targets_pg') or u.get('carries_pg') or 0:.1f} "
                                      f"{'targets' if u.get('targets_pg') else 'carries'} a game, and he's the guy near the goal line."})
        ir = r.get("inj_risk") or {}
        if ir.get("level") in ("Medium", "High") and ir.get("why"):
            objections.append({"if": f"\"{r['name']} is always hurt\"",
                               "say": "He's playing now and the price reflects the risk. Offer a small add-on rather than "
                                      "arguing the injury history."})
        if r.get("injury") in ("Q", "D"):
            objections.append({"if": f"\"Isn't {r['name']} hurt?\"",
                               "say": "Acknowledge it, then offer to wait until his status is clear, or add a small piece for the risk."})
    objections.append({"if": "\"I need more\"", "say": "Ask what specifically, then counter with your walk-away step. "
                                                       "Never jump straight to it."})
    objections.append({"if": f"\"I don't want to give up {P[get[0]]['name']}\"",
                       "say": "Ask which of your players they'd want instead. Their answer tells you what they value."})

    timing = _timing(give, get, P)
    profile_notes = []
    if pr["mood"] == "desperate":
        profile_notes.append(f"{pr['record']} and chasing a playoff spot: they want points now, not 'upside'.")
    elif pr["mood"] == "contender":
        profile_notes.append(f"{pr['record']} contender: pitch it as the piece that wins a title.")
    if tend.get("traits"):
        profile_notes.extend(tend["traits"])
    elif not pr["trades"]:
        profile_notes.append("Hasn't traded yet this season: keep it simple and clearly fair.")
    else:
        profile_notes.append(f"Has made {pr['trades']} trade(s): open to dealing.")

    return {"message": f"Hey {first}! " + " ".join(lines), "tactics": list(dict.fromkeys(tactics)),
            "talking_points": talking, "objections": objections, "timing": timing, "profile": profile_notes}


def _timing(give, get, P):
    tips = []
    for pid in give:
        r = P[pid]
        if r.get("locked") and r.get("actual") is not None and r.get("proj") and r["actual"] > (r.get("proj") or 0) * 1.3:
            tips.append(f"Send now: {r['name']}'s {r['actual']} points this week are fresh in everyone's mind.")
        elif not r.get("locked") and r.get("notes") and any("Soft matchup" in n for n in r["notes"]):
            tips.append(f"Option: wait until after {r['name']}'s soft matchup this week. A big game raises his price, "
                        "but a dud lowers it.")
    for pid in get:
        r = P[pid]
        if not r.get("locked") and r.get("notes") and any("Tough matchup" in n for n in r["notes"]):
            tips.append(f"{r['name']} has a tough matchup this week. If he struggles, he gets cheaper. Consider waiting.")
    if not tips:
        tips.append("Tuesday or Wednesday, after waivers run and before injury reports, is when managers react to last week.")
    return tips
