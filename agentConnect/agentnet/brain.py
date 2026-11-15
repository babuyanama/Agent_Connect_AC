import json, re
from . import llm, taxonomy

EVENT = re.compile(r"\b(diwali|dussehra|navratri|holi|eid|christmas|onam|pongal|big billion|new year)\b", re.I)


def decide(agent, post, author_name, score):
    if llm.enabled():
        res = llm.decide(agent, post, author_name)
        if res is not None:
            return res
    return mock_decide(agent, post, author_name, score)


def mock_decide(agent, post, author_name, score):
    exp = taxonomy.expand(json.loads(post["topics"] or "{}"))
    m = EVENT.search(post["text"])
    event = m.group(1).title() if m else "this season"
    for rule in json.loads(agent.get("playbook") or "[]"):
        if any(exp.get(t, 0) >= 0.25 for t in rule["when"]):
            return True, rule["reply"].format(author=author_name, event=event)
    return False, ""
