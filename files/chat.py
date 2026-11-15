
import argparse
import json
import urllib.error

from .llm import chat
from .tools import REGISTRY, TOOL_SCHEMAS, call_tool

SYSTEM_PROMPT = """You answer questions about the hardware of the laptop you run on.

Rules:
- Never state a spec from memory. Call a tool first, then answer from its result.
- If a tool result has an "error", tell the user what is missing and how to fix it.
Do not guess a value.
- If no tool covers the question, say "I can't check that."
- Do the maths from the tool result only. Use "max_additional_gb" for upgrade questions.
- Mention caveats from the tool's "notes" when they matter.
- Reuse earlier tool results for follow-ups unless the user asks for fresh data.
- Keep answers short and in plain language."""

MAX_TOOL_ROUNDS = 5


def answer(messages, model, verbose=False):
    """Run one user turn. `messages` is the whole conversation (so follow-ups work)."""
    for _ in range(MAX_TOOL_ROUNDS):
        reply = chat(messages, TOOL_SCHEMAS, model)
        messages.append(reply)

        calls = reply.get("tool_calls") or []
        if not calls:
            return reply.get("content", "").strip()

        for c in calls:
            name = c["function"]["name"]
            result = call_tool(name)       # arguments are ignored on purpose
            if verbose:
                print(f"  [tool] {name} -> {json.dumps(result)[:150]}")
            messages.append({"role": "tool", "tool_name": name,
                            "content": json.dumps(result)})
    return "Sorry, I got stuck gathering the data. Try asking more specifically."


def main():
    p = argparse.ArgumentParser(description="Ask questions about your laptop's hardware.")
    p.add_argument("--model", default="qwen2.5:7b", help="Ollama model (needs tool support)")
    p.add_argument("-v", "--verbose", action="store_true", help="show tool calls")
    args = p.parse_args()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("Hardware assistant. Ask a question, /raw <tool> to see raw data, /quit to exit.")
    print("Tools:", ", ".join(REGISTRY), "\n")

    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q in ("/quit", "/exit"):
            break
        if q.startswith("/raw"):
            name = q.partition(" ")[2].strip()
            print(json.dumps(call_tool(name), indent=2))
            continue

        messages.append({"role": "user", "content": q})
        try:
            print("bot>", answer(messages, args.model, args.verbose), "\n")
        except urllib.error.URLError:
            print("bot> Can't reach Ollama. Is it running? (`ollama serve`)\n")
            messages.pop()

        if len(messages) > 41:
            messages[1:] = messages[-40:]


if __name__ == "__main__":
    main()
