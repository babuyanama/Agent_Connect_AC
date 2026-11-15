"""
Read-only hardware tools.

Rules this file follows:
  1. Only commands listed in ALLOWED can run. The model never writes a command.
  2. Commands run as a list (no shell), with a timeout.
  3. Every tool returns plain dicts. If something is missing, it says so
     in an "error" field instead of guessing.
"""
import json
import os
import platform
import re
import shlex
import shutil
import subprocess

# --- the allow-list -------------------------------------------------------
# name -> (command, needs_root)
ALLOWED = {
    "lscpu":       (["lscpu", "-J"], False),
    "free":        (["free", "-b"], False),
    "lsblk":       (["lsblk", "-J", "-b", "-d", "-o", "NAME,SIZE,TYPE,ROTA,TRAN,MODEL"], False),
    "lspci":       (["lspci", "-mm"], False),
    "dmi_array":   (["dmidecode", "-t", "16"], True),
    "dmi_devices": (["dmidecode", "-t", "17"], True),
}

EXTRA_PATH = ":/usr/sbin:/sbin"  # dmidecode / lspci often live here


class ToolError(Exception):
    pass


def _run(name):
    """Run one allow-listed command and return its stdout."""
    if name not in ALLOWED:
        raise ToolError(f"'{name}' is not an allowed command")
    cmd, needs_root = ALLOWED[name]

    exe = shutil.which(cmd[0], path=os.environ.get("PATH", "") + EXTRA_PATH)
    if exe is None:
        raise ToolError(f"'{cmd[0]}' is not installed on this machine")
    full = [exe] + cmd[1:]

    if needs_root and os.geteuid() != 0:
        # -n = never ask for a password (we can't type one into a subprocess)
        full = ["sudo", "-n"] + full

    try:
        res = subprocess.run(
            full, capture_output=True, text=True, timeout=10,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired:
        raise ToolError(f"'{cmd[0]}' timed out")

    if res.returncode != 0:
        if needs_root:
            raise ToolError(
                f"'{cmd[0]}' needs root. Start the assistant with sudo, or "
                "allow just this command in sudoers (see README)."
            )
        raise ToolError(f"'{cmd[0]}' failed: {res.stderr.strip()[:200]}")
    return res.stdout

def _gb(num_bytes):
    return round(num_bytes / 1024**3, 1)


def _to_mb(text):
    """'16 GB' -> 16384, '8192 MB' -> 8192, 'No Module Installed' -> None"""
    m = re.match(r"(\d+)\s*(kB|MB|GB|TB)", text or "")
    if not m:
        return None
    mult = {"kB": 1 / 1024, "MB": 1, "GB": 1024, "TB": 1024 ** 2}[m.group(2)]
    return int(int(m.group(1)) * mult)


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


# --- tools ----------------------------------------------------------------
def get_cpu():
    try:
        data = json.loads(_run("lscpu"))["lscpu"]
        f = {d["field"].rstrip(":"): d["data"] for d in data}
        return {
            "model": f.get("Model name"),
            "architecture": f.get("Architecture"),
            "logical_cpus": int(f["CPU(s)"]) if "CPU(s)" in f else None,
            "cores_per_socket": f.get("Core(s) per socket"),
            "threads_per_core": f.get("Thread(s) per core"),
            "sockets": f.get("Socket(s)"),
            "max_mhz": f.get("CPU max MHz"),
        }
    except ToolError:
        info = _read("/proc/cpuinfo")
        if not info:
            raise
        model = re.search(r"model name\s*:\s*(.+)", info)
        return {
            "model": model.group(1) if model else None,
            "logical_cpus": len(re.findall(r"^processor", info, re.M)),
            "note": "lscpu missing, used /proc/cpuinfo (less detail)",
        }


def get_memory():
    """How much RAM is installed and how much is free right now."""
    try:
        line = [l for l in _run("free").splitlines() if l.startswith("Mem:")][0]
        parts = line.split()
        total, available = int(parts[1]), int(parts[6])
    except ToolError:
        meminfo = _read("/proc/meminfo") or ""
        total = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1)) * 1024
        available = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1)) * 1024
    return {
        "total_gb": _gb(total),
        "available_gb": _gb(available),
        "note": "total is what the OS can see; it can be a bit under the installed amount",
    }


def parse_dmi_array(text):
    """Parse `dmidecode -t 16` -> list of arrays."""
    arrays = []
    for chunk in text.split("\n\n"):
        if "Physical Memory Array" not in chunk:
            continue
        f = dict(
            (k.strip(), v.strip())
            for k, v in (l.strip().split(":", 1) for l in chunk.splitlines() if ":" in l)
        )
        arrays.append({
            "max_capacity_mb": _to_mb(f.get("Maximum Capacity", "")),
            "slots": int(f["Number Of Devices"]) if f.get("Number Of Devices", "").isdigit() else None,
        })
    return arrays


def parse_dmi_devices(text):
    """Parse `dmidecode -t 17` -> list of memory slots."""
    slots = []
    for chunk in text.split("\n\n"):
        if not any(l.strip() == "Memory Device" for l in chunk.splitlines()):
            continue
        f = dict(
            (k.strip(), v.strip())
            for k, v in (l.strip().split(":", 1) for l in chunk.splitlines() if ":" in l)
        )
        size_mb = _to_mb(f.get("Size", ""))
        slots.append({
            "slot": f.get("Locator"),
            "size_mb": size_mb,            # None = empty
            "type": f.get("Type") if size_mb else None,
            "speed": f.get("Speed") if size_mb else None,
        })
    return slots


def get_ram_slots():
    """Slots used / free and the maximum RAM the board supports."""
    arrays = parse_dmi_array(_run("dmi_array"))
    slots = parse_dmi_devices(_run("dmi_devices"))
    if not slots:
        raise ToolError("firmware did not report any memory slots")

    installed_mb = sum(s["size_mb"] or 0 for s in slots)
    used = [s for s in slots if s["size_mb"]]
    max_mb = sum(a["max_capacity_mb"] or 0 for a in arrays) or None

    out = {
        "slots_total": len(slots),
        "slots_used": len(used),
        "slots_free": len(slots) - len(used),
        "installed_gb": round(installed_mb / 1024, 1),
        "slots": [
            {"slot": s["slot"],
             "size_gb": round(s["size_mb"] / 1024, 1) if s["size_mb"] else None,
             "type": s["type"], "speed": s["speed"]}
            for s in slots
        ],
        "notes": [],
    }
    if max_mb:
        out["max_supported_gb"] = round(max_mb / 1024, 1)
        out["max_additional_gb"] = max(0, round((max_mb - installed_mb) / 1024, 1))
        out["notes"].append(
            "Max capacity comes from firmware and is sometimes wrong. "
            "The per-slot limit is not reported, so check the laptop manual."
        )
    else:
        out["notes"].append("Firmware did not report a maximum capacity.")
    return out


def get_storage():
    data = json.loads(_run("lsblk"))["blockdevices"]
    disks = []
    for d in data:
        if d.get("type") != "disk":
            continue
        rota = d.get("rota")
        is_rotational = rota in (True, 1, "1", "true")
        is_nvme = d["name"].startswith("nvme") or d.get("tran") == "nvme"
        kind = "NVMe SSD" if is_nvme else ("HDD" if is_rotational else "SSD (SATA/other)")
        disks.append({
            "name": d["name"],
            "model": (d.get("model") or "").strip() or None,
            "size_gb": _gb(int(d["size"])),
            "kind": kind,
            "connection": d.get("tran"),
        })
    return {"disks": disks}


def get_gpu():
    gpus = []
    for line in _run("lspci").splitlines():
        parts = shlex.split(line)
        # parts: [address, class, vendor, device, ...]
        if len(parts) >= 4 and any(k in parts[1] for k in ("VGA", "3D", "Display")):
            gpus.append({"type": parts[1], "vendor": parts[2], "model": parts[3]})
    if not gpus:
        raise ToolError("no GPU found in lspci output")
    return {"gpus": gpus, "note": "VRAM is not reported by lspci"}


def get_system():
    """Laptop make/model, BIOS, OS. All readable without root."""
    os_name = None
    for line in (_read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            os_name = line.split("=", 1)[1].strip('"')
    return {
        "vendor": _read("/sys/class/dmi/id/sys_vendor"),
        "product": _read("/sys/class/dmi/id/product_name"),
        "board": _read("/sys/class/dmi/id/board_name"),
        "bios_version": _read("/sys/class/dmi/id/bios_version"),
        "os": os_name,
        "kernel": platform.release(),
    }


# --- registry + schemas the LLM sees --------------------------------------
REGISTRY = {
    "get_cpu": (get_cpu, "CPU model, cores, threads, architecture, max clock."),
    "get_memory": (get_memory, "Total and currently available RAM."),
    "get_ram_slots": (get_ram_slots, "RAM slots used/free, module sizes and types, max RAM the board supports. Use this for upgrade questions."),
    "get_storage": (get_storage, "Disks with size and kind (NVMe SSD, SATA SSD, HDD)."),
    "get_gpu": (get_gpu, "Graphics cards (integrated and discrete)."),
    "get_system": (get_system, "Laptop vendor/model, BIOS version, OS and kernel."),
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": {}},
        },
    }
    for name, (_, desc) in REGISTRY.items()
]


def call_tool(name):
    """Run a tool by name. Always returns a dict, never raises."""
    if name not in REGISTRY:
        return {"error": f"unknown tool '{name}'"}
    try:
        return REGISTRY[name][0]()
    except ToolError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"could not read this info ({type(e).__name__})"}
