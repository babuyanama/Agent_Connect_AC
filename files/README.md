# Hardware Q&A Assistant

A command-line chatbot that answers questions about your own Linux laptop.
It runs a **local model through Ollama**, so nothing leaves your machine.

## Run it

```bash
# 1. install Ollama (https://ollama.com) and pull a model that supports tools
ollama pull qwen2.5:7b

# 2. run the assistant (Python 3.9+, no pip packages needed)
python -m hwassist -v
```

Try: `How much RAM do I have?`, `Can I add more RAM, and how much?`, `Is my SSD NVMe?`, then a follow-up like `and the GPU?`.
Use `/raw get_ram_slots` to see the raw data a tool returns.

Tests: `pip install pytest && pytest`

## How it works

```
question -> LLM picks tools -> we run them (read-only) -> JSON -> LLM writes the answer
```

| File | Job |
|---|---|
| `hwassist/tools.py` | Allow-listed commands, parsers, tool registry |
| `hwassist/llm.py` | Calls the local Ollama model |
| `hwassist/chat.py` | Chat loop, system prompt, conversation history |
| `tests/test_parsers.py` | Parser and safety tests using sample output |

## Design choices

**Tool calling, not guessing.** The model only sees six tools (`get_cpu`, `get_memory`, `get_ram_slots`, `get_storage`, `get_gpu`, `get_system`). It can't see raw commands.

**Safety.**
- The model picks a tool *name* only. Tools take no arguments, so there is nothing to inject.
- Commands live in a fixed `ALLOWED` dict, run as a list (no shell) with a 10s timeout.
- Everything is read-only. An unknown tool name is rejected.

**Permissions.** `dmidecode` needs root. The assistant never asks for a password. It tries `sudo -n` and, if that fails, returns a clear error that the model relays. Two ways to fix it:
1. run `sudo python -m hwassist`, or
2. allow just those two commands (edit with `sudo visudo -f /etc/sudoers.d/hwassist`):
   ```
   youruser ALL=(root) NOPASSWD: /usr/sbin/dmidecode -t 16, /usr/sbin/dmidecode -t 17
   ```
Everything else (CPU, RAM total, disks, GPU, laptop model) works without root.

**Accuracy.**
- Temperature 0 and a system prompt that says: no tool data, no answer.
- Maths is done in code (`max_additional_gb`), not by the model.
- Failures come back as `{"error": ...}`, and the model is told to explain instead of guess.
- Tools add caveats (firmware max RAM can be wrong; per-slot limit unknown; lspci has no VRAM).

**Portability.**
- Missing commands are detected with `shutil.which` (also checks `/usr/sbin`).
- `lscpu` and `free` fall back to `/proc`. Laptop info comes from `/sys/class/dmi/id`.
- `LC_ALL=C` keeps command output in English so parsers are stable.
- Parsers handle `lsblk` differences (`rota` as bool or string).

**Privacy.** Local model, no network calls except to `localhost:11434`.

## Known limits / next steps
- Not covered yet: battery, Wi-Fi, USB, temperatures, display.
- Only tested with sample outputs; real laptops will have odd firmware, so add fixtures as you meet them.
- Multi-GPU (hybrid laptops) shows both GPUs but not which one is active.
- Could add a non-LLM keyword router as a fallback if Ollama isn't installed.
