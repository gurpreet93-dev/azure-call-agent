"""Scenario evaluation: a simulated caller (Haiku, playing a persona) talks to the real agent.
Each call is scored on deterministic checks: did the agent take the expected action, avoid the
forbidden ones, and only use prices that exist in the knowledge base?

    python -m scripts.run_scenarios                # all scenarios, demo contractor
    python -m scripts.run_scenarios gutter-quote   # one scenario

Writes evals/results-<timestamp>.md. Costs a few cents per scenario on Haiku 4.5.
Note: bookings and emails go to the local DB (mock providers) unless Graph is configured.
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from app import agent, knowledge
from app.config import settings
from app.db import get_db
from app.seed import seed

ROOT = Path(__file__).resolve().parent.parent
MAX_TURNS = 14
CALLER_SYSTEM = """You are role-playing a person phoning a roofing company. Stay in character.
{persona}
Rules: speak naturally in one or two short sentences per turn, like a real phone call. Only give a
detail when asked or when it's natural. If the agent reads back an email or address correctly, confirm it.
When your needs are handled or the agent says goodbye, reply with exactly: [HANGUP]"""


def caller_reply(persona: str, transcript: list[dict]) -> str:
    # From the caller-model's point of view the agent is the "user".
    msgs = [{"role": "user" if t["who"] == "agent" else "assistant", "content": t["text"]} for t in transcript]
    resp = agent.client().messages.create(model=settings.agent_model, max_tokens=200,
                                          system=CALLER_SYSTEM.format(persona=persona), messages=msgs)
    return "".join(b.text for b in resp.content if b.type == "text").strip()


def kb_numbers(contractor_id: int) -> set[float]:
    text = " ".join(d["content"] for d in knowledge.full_text(contractor_id))
    return {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def run(scenario: dict, contractor_id: int) -> dict:
    started = agent.start_call(contractor_id, "simulator", caller_number="+15555550100")
    transcript, actions, ended = [{"who": "agent", "text": started["say"]}], [], False
    for _ in range(MAX_TURNS):
        said = caller_reply(scenario["persona"], transcript)
        if "[HANGUP]" in said or not said:
            agent.hang_up(started["call_id"])
            break
        transcript.append({"who": "caller", "text": said})
        out = agent.handle_turn(started["call_id"], said)
        actions += out["actions"]
        transcript.append({"who": "agent", "text": out["say"]})
        if out["ended"]:
            ended = True
            break

    used = [a["tool"] for a in actions if a["ok"]]
    checks = {}
    if scenario["expect_any"]:
        checks["took expected action"] = any(t in used for t in scenario["expect_any"])
    for tool, args in scenario.get("expect_args", {}).items():
        calls = [a["input"] for a in actions if a["tool"] == tool and a["ok"]]
        checks[f"{tool} with {args}"] = any(all(c.get(k) == v for k, v in args.items()) for c in calls)
    checks["avoided forbidden actions"] = not any(t in used for t in scenario["forbid"])
    allowed = kb_numbers(contractor_id)
    # Totals the send_proposal tool computed from KB prices are grounded too.
    allowed |= {float(a["result"][k]) for a in actions if a["tool"] == "send_proposal" and a["ok"]
                for k in ("subtotal", "tax", "total")}
    prices = [i["unit_price"] for a in actions if a["tool"] == "send_proposal" for i in a["input"]["line_items"]]
    spoken = [float(m.replace(",", "")) for t in transcript if t["who"] == "agent"
              for m in re.findall(r"\$\s?(\d[\d,]*(?:\.\d+)?)", t["text"])]
    checks["all quoted prices exist in KB"] = all(p in allowed for p in prices + spoken)
    checks["call closed cleanly"] = ended or "[HANGUP]" in said
    call = agent.get_call(started["call_id"])
    return {"id": scenario["id"], "passed": all(checks.values()), "checks": checks, "tools": used,
            "outcome": call["outcome"], "turns": sum(t["who"] == "caller" for t in transcript),
            "transcript": transcript, "notes": scenario["notes"]}


def main() -> None:
    if not settings.llm_configured:
        sys.exit("Set FOUNDRY_RESOURCE and FOUNDRY_API_KEY (or LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY) first.")
    scenarios = json.loads((ROOT / "evals" / "scenarios.json").read_text())
    if len(sys.argv) > 1:
        scenarios = [s for s in scenarios if s["id"] in sys.argv[1:]]
    cid = seed()
    results = []
    for s in scenarios:
        print(f"→ {s['id']} ...", flush=True)
        r = run(s, cid)
        print(f"  {'PASS' if r['passed'] else 'FAIL'} tools={r['tools']} outcome={r['outcome']}")
        results.append(r)

    passed = sum(r["passed"] for r in results)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    lines = [f"# Scenario eval {stamp}", "",
             f"Model: `{settings.agent_model}` · KB mode: {knowledge.kb_status(cid)['mode']} · "
             f"**{passed}/{len(results)} passed**", "",
             "| Scenario | Result | Actions taken | Outcome | Turns |", "|---|---|---|---|---|"]
    lines += [f"| {r['id']} | {'✅' if r['passed'] else '❌'} | {', '.join(r['tools']) or '—'} | {r['outcome']} | {r['turns']} |"
              for r in results]
    for r in results:
        lines += ["", f"## {r['id']} {'✅' if r['passed'] else '❌'}", f"_{r['notes']}_", ""]
        lines += [f"- {'✅' if ok else '❌'} {name}" for name, ok in r["checks"].items()]
        lines += ["", "<details><summary>Transcript</summary>", ""]
        lines += [f"**{'Agent' if t['who'] == 'agent' else 'Caller'}:** {t['text']}  " for t in r["transcript"]]
        lines += ["", "</details>"]
    out = ROOT / "evals" / f"results-{stamp}.md"
    out.write_text("\n".join(lines))
    print(f"\n{passed}/{len(results)} passed → {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
