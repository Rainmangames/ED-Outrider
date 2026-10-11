"""Questions to Outrider by voice (tablet plan phase 6): the tablet app hears "hey Vespa ..." (or a tap), turns the
question into text and sends it to POST /api/ask; the answer is said on the PC and shown as a caption everywhere.

First the fixed commands, matched without any AI (resources/ask.json: editable phrases): status report, fuel,
unsold, next jump, what's left here, nearest unvisited, nearest station (or carrier, Vista, repair...), hush, unhush. Their answers come from the same read-only tools
the MCP bridge serves (outrider/tools.py), called in-process. Then, only when [assistant] enabled is true, an optional
AI layer for anything else: an OpenAI-compatible chat-completions endpoint (Ollama, Venice.ai, OpenAI...) given those
same tools. It runs on the PC; the key never leaves it. Nothing is sent anywhere unless it is configured.
"""
import asyncio
import json
import os
import re
import sys

import outrider
import outrider.dock as dock
import outrider.tools as tools

ASK_FILE = os.path.join(outrider.RESOURCES_DIR, "ask.json")
TEXT_MAX = 500
# in the order questions are matched: nearest_dock before fuel, unsold, next_jump and whats_left, whose single words
# ("fuel") are in its questions too ("nearest station with fuel" asked for the fuel gauge: the sweep of 2026-10-09)
COMMANDS = ("unhush", "hush", "nearest_dock", "next_jump", "status_report", "fuel", "unsold", "whats_left", "nearest_unvisited")
# what the voice says it knows when nothing matched: one label per command, in the order said (a test checks every
# command has one, so a new command is never left out of the list)
LABELS = {"status_report": "a status report", "fuel": "fuel", "unsold": "unsold data", "next_jump": "the next jump",
          "whats_left": "what's left here", "nearest_unvisited": "the nearest unvisited system",
          "nearest_dock": "the nearest station or carrier", "hush": "hush", "unhush": "unhush"}


def known_questions():
    """'I only know a few questions so far: ...' from LABELS."""
    labels = list(LABELS.values())
    return f"I only know a few questions so far: {', '.join(labels[:-1])} and {labels[-1]}."


# "nearest station", "nearest Vista"...: the words of the question narrow the search (a whole phrase is needed in
# ask.json, never a bare "nearest": the author, 2026-10-08)
NEAREST_WORDS = {"vista": "Vista", "genomics": "Vista", "cartographics": "UC", "cartographic": "UC", "repair": "Repair",
                 "repairs": "Repair", "refuel": "Refuel", "fuel": "Refuel", "shipyard": "Shipyard", "outfitting": "Outfitting"}
WAKE = re.compile(r"^(hey|hi|ok|okay)?\s*vespa\b[\s,]*")
ASSISTANT = {"enabled": False, "base_url": "", "api_key": "", "model": "", "timeout": 20.0, "max_rounds": 4}
SYSTEM_PROMPT = ("You are Vespa, the co-pilot voice of ED Outrider, an exploration companion for the game Elite Dangerous. "
                 "Answer the commander's question in one or two short spoken sentences: no lists, no markdown, numbers "
                 "rounded the way a person says them. Use the tools for facts about their game; never invent them. "
                 "If the tools cannot answer, say so briefly.")


def load_phrases(path=ASK_FILE):
    """{command: [phrases]} from resources/ask.json, in its order; a missing or broken file falls back to the
    command names themselves (reported on stderr)."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        cmds = doc.get("commands") if isinstance(doc, dict) else None   # a list or a string: broken too, not a crash
        if not isinstance(cmds, dict):
            raise ValueError("no \"commands\" object")
        out = {}
        for name, phrases in cmds.items():
            if name in COMMANDS and isinstance(phrases, list):
                out[name] = [p for p in (norm(x) for x in phrases if isinstance(x, str)) if p]
        return out
    except (OSError, ValueError) as e:
        print(f"ask: {path}: {e}; using the command names as phrases", file=sys.stderr)
        return {c: [c.replace("_", " ")] for c in COMMANDS}


def norm(text):
    """Lower case, apostrophes dropped, other punctuation as spaces, single spaces."""
    t = str(text or "").lower().replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", t).split())


def match(text, phrases):
    """The fixed command a question is (the first in `phrases`' order with a phrase as whole words in it), or None."""
    t = WAKE.sub("", norm(text)).strip()
    padded = f" {t} "
    for name, ps in phrases.items():
        if any(f" {p} " in padded for p in ps):
            return name
    return None


# ---- the fixed answers, from the read-only tools ----

def words_cr(n):
    """Credits as said aloud: 1234567 -> "1.2 million"."""
    n = n or 0
    if n >= 1e9:
        return f"{n / 1e9:.1f} billion".replace(".0 ", " ")
    if n >= 1e6:
        return f"{n / 1e6:.1f} million".replace(".0 ", " ")
    if n >= 1e3:
        return f"{round(n / 1e3)} thousand"
    return str(int(n))


def ly(x):
    return f"{x:,.0f}" if x is not None and x >= 100 else f"{x:.1f}" if x is not None else "?"


def jumps(n):
    """'1 jump', '6 jumps'."""
    return f"{n} jump{'' if n == 1 else 's'}"


def nearest_query(text):
    """What a "nearest ..." question asks for: (services, "station" | "carrier" | None)."""
    words = norm(text).split()
    need = []
    for w in words:
        s = NEAREST_WORDS.get(w)
        if s and s not in need:
            need.append(s)
    if "uc" in words and "UC" not in need:
        need.append("UC")
    kind = "station" if any(w in ("station", "stations", "starport", "outpost") for w in words) else \
        "carrier" if any(w in ("carrier", "carriers") for w in words) else None
    return need, kind


async def fixed_answer(command, get, rows=10, text=""):
    """The words for a fixed command (hush and unhush are the caller's: they change state, the tools cannot)."""
    if command == "fuel":
        s = await tools.call("current_status", {}, get, rows)
        if s.get("error"):
            return s["error"]
        if s.get("fuel_pct") is None:
            return "I have no fuel reading yet."
        return f"Fuel at {s['fuel_pct']} percent" + (f", about {jumps(s['fuel_jumps'])} at max range." if s.get("fuel_jumps") is not None else ".")
    if command == "unsold":
        u = await tools.call("unsold_data", {}, get, rows)
        if u.get("error"):
            return u["error"]
        c, b = (u.get("cartographic") or {}).get("estimated_payout") or 0, (u.get("exobiology") or {}).get("estimated_value") or 0
        if not (u.get("total") or c or b):
            return "Nothing unsold aboard."
        parts = [f"{words_cr(c)} cartographic" if c else "", f"{words_cr(b)} exobiology" if b else ""]
        return f"About {words_cr(u.get('total') or c + b)} credits unsold: {' and '.join(p for p in parts if p)}."
    if command == "next_jump":
        h = await tools.call("highway_route", {}, get, rows)
        if h.get("error"):
            return h["error"]
        if not h.get("route", True) or not h.get("next"):
            return "No highway route plotted." if not h.get("complete") else "The highway route is complete."
        nx = h["next"]
        out = f"Next highway stop: {nx['system']}, {ly(nx.get('distance_ly'))} light years" + (", a neutron star" if nx.get("neutron") else "") + "."
        if h.get("off_route"):
            out = f"You are off the route; the nearest route system is {h.get('nearest_route_system')}. " + out
        if h.get("jumps_left") is not None:
            out += f" {jumps(h['jumps_left'])} left"
            out += f", refuel in {h['refuel_in_jumps']}." if h.get("refuel_in_jumps") else "."
        return out
    if command == "nearest_dock":
        need, kind = nearest_query(text)
        d = await tools.call("nearest_dock", {"need": need, "kind": kind or "any"}, get, rows)
        if d.get("error"):
            return d["error"]
        said = dock.spoken({"rows": [dict(p, ly=p["distance_ly"], here=not p["distance_ly"], warn=p["warnings"],
                                          station_type=p.get("station_type"), own=bool(p.get("yours")))
                                     for p in d.get("places") or []]}, need, kind)
        if any("spansh" in str(e).lower() for e in d.get("errors") or []):
            return "Spansh could not be reached, so stations are missing. " + said
        return said
    if command == "nearest_unvisited":
        n = await tools.call("nearest_unvisited", {}, get, rows)
        if n.get("error"):
            return n["error"]
        x = n.get("nearest")
        if not x:
            return n.get("note") or "No known unvisited system nearby."
        return f"Nearest unvisited: {x['name']}, {ly(x.get('distance_ly'))} light years" + (", scoopable." if x.get("scoopable") else ".")
    if command == "whats_left":
        s = await tools.call("this_system", {}, get, rows)
        if s.get("error"):
            return s["error"]
        t = s.get("to_do") or {}
        bits = []
        if t.get("honked") is False:
            bits.append("honk the system")
        if t.get("bodies_to_find"):
            bits.append(f"find {t['bodies_to_find']} more bod{'y' if t['bodies_to_find'] == 1 else 'ies'} in the FSS")
        if t.get("bio_to_sample"):
            bits.append("biology on " + ", ".join(t["bio_to_sample"][:3]))
        if t.get("worth_mapping"):
            bits.append("map " + ", ".join(x["body"] for x in t["worth_mapping"][:3]))
        return ("Still to do here: " + "; ".join(bits) + ".") if bits else "Nothing worth staying for here."
    if command == "status_report":
        s = await tools.call("current_status", {}, get, rows)
        if s.get("error"):
            return s["error"]
        out = [f"{s.get('system') or 'Position unknown'}."]
        if s.get("fuel_pct") is not None:
            out.append(f"Fuel {s['fuel_pct']} percent" + (f", {jumps(s['fuel_jumps'])}." if s.get("fuel_jumps") is not None else "."))
        if s.get("unsold"):
            out.append(f"{words_cr(s['unsold'])} credits unsold.")
        h = await tools.call("highway_route", {}, get, rows)
        if h.get("next"):
            out.append(f"Highway: next {h['next']['system']}, {jumps(h.get('jumps_left'))} left.")
        return " ".join(out)
    raise ValueError(command)


# ---- the optional AI layer ----

class AIError(Exception):
    """The AI provider failed: code "ai_timeout", "ai_error" or "ai_off" (not configured), and words for a person."""

    def __init__(self, code, why):
        super().__init__(why)
        self.code, self.why = code, why


def assistant_settings(cfg):
    """[assistant] from a parsed config (wrong values reported on stderr, defaults kept): enabled, base_url, api_key,
    model, timeout (s), max_rounds (tool rounds)."""
    a = cfg.get("assistant") if isinstance(cfg.get("assistant"), dict) else {}
    out = dict(ASSISTANT)
    for k in ("base_url", "api_key", "model"):
        v = a.get(k, out[k])
        if isinstance(v, str):
            out[k] = v.strip()
        else:
            print(f"config: [assistant] {k} must be text; ignored", file=sys.stderr)
    if isinstance(a.get("enabled"), bool):
        out["enabled"] = a["enabled"]
    elif "enabled" in a:
        print("config: [assistant] enabled must be true or false; using false", file=sys.stderr)
    for k, lo, hi in (("timeout", 2, 120), ("max_rounds", 1, 8)):
        v = a.get(k, out[k])
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            print(f"config: [assistant] {k} must be {lo} to {hi}; using {ASSISTANT[k]}", file=sys.stderr)
        else:
            out[k] = float(v) if k == "timeout" else int(v)
    if out["base_url"] and not out["base_url"].startswith(("http://", "https://")):
        print("config: [assistant] base_url must be an http(s) address; the AI layer stays off", file=sys.stderr)
        out["base_url"] = ""
    return out


MALFORMED = "the AI provider's answer was malformed"


def message_text(content):
    """A message's words: a string, or a list of text parts ({"type": "text", "text": ...}) joined. AIError else."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and all(isinstance(p, dict) for p in content):
        return "".join(p["text"] for p in content if isinstance(p.get("text"), str))
    raise AIError("ai_error", MALFORMED)


def call_arguments(fn):
    """A tool call's arguments: a JSON object in a string (OpenAI), or already an object (some providers)."""
    args = fn.get("arguments")
    if isinstance(args, dict):
        return args
    try:
        args = json.loads(args or "{}")
    except (TypeError, ValueError):
        return {}
    return args if isinstance(args, dict) else {}


def openai_tools():
    """The registry as OpenAI-style function tools (the same tools the MCP bridge serves)."""
    return [{"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["inputSchema"]}}
            for t in tools.listing()]


async def ai_answer(text, cfg, get, session, rows=10):
    """Ask the configured chat-completions endpoint, running its tool calls through the read-only registry, until it
    answers in words (at most max_rounds tool rounds). Raises AIError."""
    if not cfg.get("base_url") or not cfg.get("model"):
        raise AIError("ai_off", "the AI layer needs [assistant] base_url and model")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if cfg.get("api_key"):
        headers["Authorization"] = f"Bearer {cfg['api_key']}"
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": text}]
    import aiohttp

    async def round_trip():
        for n in range(cfg["max_rounds"] + 1):
            last = n == cfg["max_rounds"]   # the rounds of tools are used up: this request is for the answer
            body = {"model": cfg["model"], "messages": messages, "tools": openai_tools()}
            if last:
                body["tool_choice"] = "none"
            try:
                async with session.post(url, json=body, headers=headers) as r:
                    if r.status in (401, 403):
                        raise AIError("ai_error", f"the AI provider refused the key (HTTP {r.status})")
                    if r.status >= 400:
                        raise AIError("ai_error", f"the AI provider answered HTTP {r.status}")
                    d = await r.json(content_type=None)
            except aiohttp.ClientError as e:
                raise AIError("ai_error", f"the AI provider cannot be reached ({type(e).__name__})") from e
            except ValueError as e:
                raise AIError("ai_error", "the AI provider's answer was not JSON") from e
            try:
                msg = d["choices"][0]["message"]
            except (KeyError, IndexError, TypeError):
                raise AIError("ai_error", "the AI provider's answer had no message") from None
            # every step's shape checked (review R4): a provider's odd answer is ai_error, never a crash
            if not isinstance(msg, dict):
                raise AIError("ai_error", MALFORMED)
            calls = msg.get("tool_calls") or []
            if not isinstance(calls, list) or not all(isinstance(c, dict) and isinstance(c.get("function") or {}, dict) for c in calls):
                raise AIError("ai_error", MALFORMED)
            content = message_text(msg.get("content"))
            if not calls:
                words = content.strip()
                if not words:
                    raise AIError("ai_error", "the AI gave no answer")
                return words
            if last:   # asked for more tools anyway (a provider that ignores tool_choice): none run (Codex F7)
                break
            messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            for c in calls:
                fn = c.get("function") or {}
                out = await tools.call(str(fn.get("name")), call_arguments(fn), get, rows)
                messages.append({"role": "tool", "tool_call_id": c.get("id"), "name": fn.get("name"),
                                 "content": json.dumps(out, separators=(",", ":"))})
        raise AIError("ai_error", f"the AI was still calling tools after {cfg['max_rounds']} rounds")

    try:
        return await asyncio.wait_for(round_trip(), cfg["timeout"])
    except asyncio.TimeoutError:
        raise AIError("ai_timeout", f"the AI took longer than {cfg['timeout']:g} s") from None
