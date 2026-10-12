"""The words for spoken alerts: speech.json, several versions of every line in each personality.

The file maps each alert to lists of lines per personality ("business", "sarcastic", "sweet", or any you
add under "styles"). A list named "<style>_profane" holds that style's swearing versions. The page picks a
line at random from every personality you ticked, not one of the last few heard; with the profanity box
ticked it first rolls whether this line comes from the swearing versions (the percentage beside the box,
50% by default) or the clean ones. It fills in the {placeholders}: each alert's own (see KEYS) plus {name}
(one of the names in "Call me", picked afresh for every {name}), {cmdr}, {ship} and {here}. Numbers are
spoken to a tenth at most ("52.0M" is "52 million"). If the file is missing or broken it falls back to
plain wording.

The file is re-read whenever it changes, so an edit takes effect without restarting Outrider.

Lines you never want to hear again go in data/speech_banned.json, or next to a speech file of your own
({alert: [line, ...]}, written by the 👎 in the page's "Spoken lines" list and the voice lab's "✂ Cut this line").
They are left out here, so the page (and its voice lab) gets the trimmed lists; a ban never empties a list (the last line of one cannot be banned, and a
list whose every line is banned by hand is used whole).
"""
import json
import os
import re

from . import DATA_DIR, RESOURCES_DIR

# every line the page speaks, and the {placeholders} it fills for it. Always available: {name} (one of the
# names you asked to be called, at random: game commander names are often unpronounceable), {cmdr} (the
# commander name itself), {ship} and {here}.
KEYS = {
    "speech_on": "spoken alerts were just switched on",
    "game_start": "you loaded into the game",
    "game_exit": "you quit the game",
    "arrival_undiscovered": "you jumped into a system nobody has discovered: {system}",
    "arrival_discovered": "a system announced as new turned out to be known: {system}",
    "leaving": "jumping away with work left behind: {text} (what is left)",
    "find_body": "a valuable or special planet was scanned: {what} (its class and flags), {body}, {value}",
    "mapped_before": "a valuable planet was scanned that someone else had already mapped (said once per system, "
                     "instead of the scan's alert)",
    "find_bio": "a body with biology worth your threshold: {body}, {value}",
    "sample_clear": "far enough from the last sample to take the next: {genus}",
    "bio_tag_near": "within 100 m of a plant you tagged with the composition scanner where the next sample of the "
                    "species you are sampling would count: {genus}, {distance} (metres)",
    "codex": "a new codex entry: {entry} (the entry's name), {what} (voucher or new to the region)",
    "phenomena": "the FSS found notable stellar phenomena in a system where you have none on record (said with delight, in "
                 "every style: the co-pilot loves new things): {what} (already phrased, e.g. 'three notable stellar "
                 "phenomena, all in rings')",
    "rare_star": "a rare star resolved, by the arrival's own scan or the FSS (said with delight, in every style): {what} "
                 "(already phrased, e.g. 'a black hole at B', 'a Wolf-Rayet star, the main star')",
    "surface_site": "surface sites found on a body: Guardian or Thargoid sites (the DSS's map says them) or an unknown "
                    "planetary anomaly (the FSS says it), said with delight in every style: {what} (already phrased, e.g. "
                    "'Guardian sites on B 3')",
    "signal_poi": "the FSS found a signal worth a detour, new to this system: a Titan, a Guardian beacon, a generation ship "
                  "or a listening post (said with delight, in every style): {what} (already phrased, e.g. 'the Titan "
                  "Cocijo', 'a Guardian beacon')",
    "curious": "the system you are in shows curiosities for the first time (rare planet classes, odd orbits, ringed "
               "landables...): one line that there is something to look at on the panels, never the list",
    "ggg": "one of your own scans predicts a green gas giant (the community's seven-layer ladder model: a guess, said "
           "with delight, never a fact): {body} (the body's short name, e.g. 'B 3')",
    "fuel_low": "the game's low fuel warning: {pct}",
    "fuel_star": "arrived under 30% fuel at a star you cannot scoop: {pct}, {star}",
    "fuel_target": "targeted a star you cannot scoop with under 30% fuel: {pct}, {system}",
    "fuel_topup": "at a scoopable star, targeting onward, with the tank lowish and scoopable stars scarce locally: "
                  "{rate} (e.g. '8 of the last 20 stars were scoopable'), {jumps} (jumps of fuel left, e.g. 'about 6 jumps')",
    "hull": "hull fell below half or a quarter: {pct}",
    "heat": "heat damage",
    "interdicted": "interdicted: {by}",
    "docked_sell": "docked where you can sell a worthwhile haul: {value}, {station}",
    "undocked_unsold": "undocked with a red-level haul unsold: {value}",
    "sold": "you sold data: {sold} (what was sold), {still} (what is still aboard, may be empty)",
    "unsold_warn": "unsold data reached the amber level: {value}",
    "unsold_urgent": "unsold data reached the red level: {value}",
    "carrier_departs": "your carrier leaves in under five minutes without you: {minutes}, {carrier}",
    "carrier_arrived": "your carrier arrived: {carrier}, {system}",
    "autotarget_nothing": "the co-pilot button asked to target the next route system with none to target: {why} (e.g. "
                          "'no route is plotted', 'the route is complete')",
    "fss_done": "you finished the FSS, every body is found, and some are worth your time: {count} (how many bodies), "
                "{text} (what is worth doing, e.g. 'B 1, Earth-like world, 3.1M to map, and biology on C 2, up to 19M')",
    "fss_nothing": "you finished the FSS, every body is found, and nothing is worth staying for: {count} (how many bodies)",
    "fss_unfinished": "you closed the FSS with bodies still unresolved: {left} (how many, e.g. '3 bodies')",
    "jumponium": "a landable body just scanned carries the material your FSD injections are short of, when the FSS debrief "
                 "does not say it: {body} (e.g. 'B 4'), {material} (e.g. 'polonium'), {pct} (its share, e.g. '1.3 percent')",
    "left_body": "you left a body (back to supercruise) with exobiology unfinished: {body}, "
                 "{text} (what is unfinished, e.g. 'Stratum 2 of 3, and Tussock untouched, up to 4.1M')",
    "bio_done_more": "the third sample of a species, and more remain on this body: {species}, {value}, "
                     "{left} (what is left here, e.g. 'Bacterium and Fungoida')",
    "bio_done_last": "the third sample of the last species worth sampling on this body: {species}, {value}",
    "tank_full": "fuel scooping filled the tank: {jumps} (max-range jumps a full tank gives; may be missing)",
    "scoop_stopped": "fuel scooping stopped before the tank was full: {pct}",
    "supercharged": "the frame shift drive was supercharged by a neutron star or white dwarf cone: {mult} (e.g. '4 times')",
    "fsd_charge": "the jump line, said in the hyperspace tunnel (the game itself announces the charge): {system} (where you "
                  "are jumping to). Whether the star there is scoopable and any hazard are said after it, outside these lines",
    "body_brief": "approaching a body with biological signals: {body}, "
                  "{text} (what they could be, e.g. '3 biological signals, one of Stratum, Bacterium or Fungoida, 1M to 19M')",
    "high_g": "approaching a landable high-gravity body with a lot of unsold data aboard: {gravity} (in g), "
              "{value} (the unsold total), {rebuys} (how many rebuys that is; may be missing)",
    "arrival_brief": "a briefing on the system you just entered, after the honk: "
                     "{text} (e.g. 'Undiscovered. 14 bodies. Scoopable K star.')",
    "region": "the first jump into a new galactic region this trip, when the arrival briefing is not spoken (it opens "
              "with the region then): {region} (e.g. 'the Norma Arm'), {count} (e.g. '31 species you have logged elsewhere "
              "are new to your codex here'; may be empty, so it ends the line as its own sentence)",
    "session_recap": "you quit the game after a session of at least three jumps, in place of the plain goodbye: "
                     "{text} (e.g. '142 jumps, 3,100 light-years, 12 systems nobody had seen')",
    # the discovery streak: once per streak, when a run reaches its threshold (the page's alerts dialog)
    "streak_known": "several systems in a row were already known to others: {count} (how many in a row)",
    "streak_new": "several undiscovered systems in a row: {count} (how many in a row)",
    "welcome_back": "you loaded into the game after a break of over about two hours, in place of the plain greeting: "
                    "{text} (e.g. 'Away 3 days. 412 million aboard, unsold for 5 days. Fuel 64 percent.')",
    "ship_lost": "your ship was destroyed with data aboard and you paid the rebuy: "
                 "{text} (e.g. 'Lost 212 million: 148 million cartographics, 64 million exobiology, 31 systems.')",
    "sale_left": "a sale's pages stopped with data still unsold (Universal Cartographics sells 50 systems a page), or "
                 "completed samples still unsold after a Vista Genomics sale: {text} (e.g. 'Sold 50 systems for 14.8 million. "
                 "43 systems are still unsold, 2 million, 270 first discoveries: sell the next page.')",
}
# keys with lines in speech.json that nothing speaks yet (the shipped-lines test allows them); none now
RESERVED = ()
ALWAYS = ("name", "cmdr", "ship", "here")
PLACEHOLDER = re.compile(r"\{(\w+)\}")
# a Piper voice name (the same rule as outrider.tts.VOICE_NAME: nothing that could leave piper-voices/)
VOICE_NAME = re.compile(r"^[a-z]{2,3}_[A-Z]{2}-[A-Za-z0-9_]+-(x_low|low|medium|high)$")


def resolve_speech_file(name, root, default, resources=RESOURCES_DIR, warn=None):
    """[server] speech_file, resolved: relative to `root`, ~ expanded; `default` when unset. A relative name missing
    there but present in resources/ (a config written before the layout move, speech_file = "speech.json") is taken
    from resources/, and warn(text) says so (review F22). The server resolves it here, so it and the tests
    always speak from the same file."""
    if not name:
        return default
    raw = os.path.expanduser(str(name))
    path = os.path.join(root, raw)
    moved = os.path.join(resources, raw)
    if not os.path.isabs(raw) and not os.path.exists(path) and os.path.exists(moved):
        if warn:
            warn(f"[server] speech_file = {name!r}: not found in {root}; using {moved} (it moved to resources/). "
                 f"Change the config to say so.")
        return moved
    return path


def style_voice(styles, style):
    """(voice, speed) a personality asks for in "styles" ({"label", "voice", "speed"}), (None, None) for a plain
    label. A personality's voice wins over the one picked in the dialog; its speed multiplies yours."""
    st = (styles or {}).get((style or "").removesuffix("_profane"))
    if not isinstance(st, dict):
        return None, None
    voice = st.get("voice") if isinstance(st.get("voice"), str) and VOICE_NAME.fullmatch(st["voice"]) else None
    speed = st.get("speed") if isinstance(st.get("speed"), (int, float)) and 0.5 <= st.get("speed") <= 2 else None
    return voice, speed


def style_voices(styles):
    """The distinct voices the personalities in "styles" ask for (see style_voice)."""
    return {v for v in (style_voice(styles, k)[0] for k in (styles or {})) if v}


def fills(key):
    return set(PLACEHOLDER.findall(KEYS.get(key, ""))) | set(ALWAYS)


def check(doc):
    """Problems with a speech document, as readable strings (empty when it is fine)."""
    problems = []
    if not isinstance(doc, dict):
        return ["the file must hold one JSON object"]
    styles, lines = doc.get("styles"), doc.get("lines")
    if not isinstance(styles, dict) or not styles:
        problems.append('"styles" must map each personality to its label')
        styles = {}
    for name, st in styles.items():   # a label, or {label, voice, speed}: a Piper voice of its own (see style_voice)
        if isinstance(st, str):
            continue
        if not isinstance(st, dict):
            problems.append(f'"styles" / "{name}" must be a label or an object {{"label", "voice", "speed"}}')
            continue
        if "voice" in st and not (isinstance(st["voice"], str) and VOICE_NAME.fullmatch(st["voice"])):
            problems.append(f'"styles" / "{name}": "voice" must be a Piper voice name such as "en_US-ryan-high"')
        if "speed" in st and not (isinstance(st["speed"], (int, float)) and not isinstance(st["speed"], bool)
                                  and 0.5 <= st["speed"] <= 2):
            problems.append(f'"styles" / "{name}": "speed" must be a number from 0.5 to 2')
    if not isinstance(lines, dict):
        return problems + ['"lines" must map each alert to its versions']
    known_lists = set(styles) | {s + "_profane" for s in styles}
    for key, entry in lines.items():
        if key not in KEYS:
            problems.append(f'"{key}" is not an alert Outrider speaks (known: {", ".join(KEYS)})')
            continue
        if not isinstance(entry, dict):
            problems.append(f'"{key}" must be an object of personality lists')
            continue
        allowed = fills(key)
        for style, versions in entry.items():
            if style.startswith("_") or style in ("when",):
                continue
            if style not in known_lists:
                problems.append(f'"{key}" has a list "{style}" for a personality not under "styles"')
                continue
            if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
                problems.append(f'"{key}" / "{style}" must be a list of strings')
                continue
            for v in versions:
                bad = set(PLACEHOLDER.findall(v)) - allowed
                if bad:
                    problems.append(f'"{key}" / "{style}": {{{"}, {".join(sorted(bad))}}} is not filled for this alert in "{v}"')
    return problems


BANNED_FILE = "speech_banned.json"   # your own choice of lines: in data/ (git-ignored)


def banned_path(speech_path):
    """Where the bans for a speech file live: data/speech_banned.json for the shipped resources/speech.json,
    and next to the file for a copy of your own (so each lines file keeps its own bans)."""
    folder = os.path.dirname(os.path.abspath(speech_path))
    if os.path.normcase(folder) == os.path.normcase(os.path.abspath(RESOURCES_DIR)):
        folder = DATA_DIR
    return os.path.join(folder, BANNED_FILE)


def read_bans(path):
    """speech_banned.json as {alert: [line, ...]}; {} when it is missing or broken (a broken file bans nothing)."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict):
        return {}
    return {k: [t for t in v if isinstance(t, str)] for k, v in doc.items() if isinstance(v, list)}


def shape_ok(doc):
    """Whether a speech document can be used at all: "styles" an object of labels or objects, "lines" an object of
    alert objects whose lists hold strings only. A placeholder or an unknown alert is a problem to report, not a reason
    to refuse it; a list holding an object would break the bans and the page (Codex F7)."""
    if not (isinstance(doc, dict) and isinstance(doc.get("styles"), dict) and isinstance(doc.get("lines"), dict)):
        return False
    if not all(isinstance(v, (str, dict)) for v in doc["styles"].values()):
        return False
    for entry in doc["lines"].values():
        if not isinstance(entry, dict):
            return False
        for style, versions in entry.items():
            if not style.startswith("_") and style != "when" and not (
                    isinstance(versions, list) and all(isinstance(v, str) for v in versions)):
                return False
    return True


def _is_list(style, versions):
    """A personality's list of lines in an alert's entry (not "_comment" or "when")."""
    return isinstance(versions, list) and not style.startswith("_") and style != "when"


def apply_bans(lines, bans):
    """speech.json's "lines" without the banned lines: (lines, whole), `whole` naming each "alert/list" kept whole
    because every line in it is banned (the guard: a ban never empties a list, so an alert never goes quiet)."""
    out, whole = {}, []
    for key, entry in (lines or {}).items():
        cut = set(bans.get(key) or ())
        if not cut or not isinstance(entry, dict):
            out[key] = entry
            continue
        new = {}
        for style, versions in entry.items():
            if _is_list(style, versions):
                kept = [v for v in versions if v not in cut]
                if versions and not kept:
                    whole.append(f"{key}/{style}")
                    kept = versions
                new[style] = kept
            else:
                new[style] = versions
        out[key] = new
    return out, whole


class SpeechLines:
    """speech.json, re-read when its modification time changes. A broken edit keeps the last good copy
    in use and reports what is wrong, so a typo never silences the alerts. Its speech_banned.json (see banned_path) is
    re-read the same way, and its lines are left out of lines()."""

    def __init__(self, path):
        self.path = path
        self.doc, self.error, self.problems, self._mtime = None, None, [], None
        self.bans_path = banned_path(path)
        self.bans, self._bans_mtime, self._bans_gen = {}, None, 0

    def _refresh_bans(self):
        try:
            mtime = os.stat(self.bans_path).st_mtime_ns
        except OSError:
            mtime = None
        if mtime != self._bans_mtime:
            self._bans_mtime = mtime
            self.bans = read_bans(self.bans_path) if mtime is not None else {}

    def refresh(self):
        self._refresh_bans()
        try:
            mtime = os.stat(self.path).st_mtime_ns
        except OSError:
            self.doc, self.error, self.problems, self._mtime = None, f"{self.path} not found: plain wording is used", [], None
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            with open(self.path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            self.error = f"{os.path.basename(self.path)} could not be read ({e}); " + \
                ("the last good copy is still in use" if self.doc else "plain wording is used")
            return
        self.problems = check(doc)
        if shape_ok(doc):
            self.doc, self.error = doc, None
        else:   # the last good document stays in use, and the bans keep working on it
            self.error = "; ".join(self.problems or ["the file's shape is not a speech document"]) + \
                ("; the last good copy is still in use" if self.doc else "; plain wording is used")

    def version(self):
        self.refresh()
        # the bans are part of it: a 👎 (or the voice lab's ✂) makes every page fetch the trimmed lines
        return f"{self._mtime}-{self._bans_mtime}-{self._bans_gen}" if self.doc else None

    def info(self):
        """The payload's small summary: the page fetches the lines themselves when the version changes."""
        self.refresh()
        return {"version": self.version(), "error": self.error, "problems": self.problems[:20],
                "file": os.path.basename(self.path)}

    def banned(self):
        """The bans that still match a line in the speech file, {alert: [line, ...]} (an edited line's old ban does
        nothing, so it is not listed)."""
        lines = (self.doc or {}).get("lines") or {}
        out = {}
        for key, cut in self.bans.items():
            entry = lines.get(key)
            have = {v for st, vs in entry.items() if _is_list(st, vs) for v in vs} if isinstance(entry, dict) else set()
            hit = [t for t in cut if t in have]
            if hit:
                out[key] = hit
        return out

    def lines(self):
        self.refresh()
        trimmed, whole = apply_bans((self.doc or {}).get("lines") or {}, self.bans)
        return {"styles": (self.doc or {}).get("styles") or {}, "lines": trimmed,
                "banned": self.banned(), "banned_whole": whole,
                "version": self.version(), "error": self.error, "problems": self.problems[:20]}

    def set_ban(self, alert, template, ban=True):
        """Ban (or unban) one line of an alert: (HTTP status, answer). Only a line that is in the speech file is
        accepted, and a ban that would leave one of its lists empty is refused (409)."""
        self.refresh()
        entry = ((self.doc or {}).get("lines") or {}).get(alert)
        if not isinstance(alert, str) or not isinstance(template, str) or not isinstance(entry, dict):
            return 400, {"error": "no such alert in the speech file"}
        homes = [vs for st, vs in entry.items() if _is_list(st, vs) and template in vs]
        if not homes:
            return 400, {"error": "that line is not in the speech file"}
        cut = list(self.bans.get(alert) or [])
        if ban and template not in cut:
            for vs in homes:
                if all(v == template or v in cut for v in vs):
                    return 409, {"error": "that is the last line left in its list: an alert never goes quiet"}
            cut.append(template)
        elif not ban:
            cut = [t for t in cut if t != template]
        bans = dict(self.bans)
        if cut:
            bans[alert] = cut
        else:
            bans.pop(alert, None)
        tmp = self.bans_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.bans_path), exist_ok=True)   # data/ on a fresh copy
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(bans, f, indent=1, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, self.bans_path)
        except OSError as e:
            return 500, {"error": f"{BANNED_FILE} could not be written ({e.strerror or e})"}
        self.bans, self._bans_gen = bans, self._bans_gen + 1
        try:
            self._bans_mtime = os.stat(self.bans_path).st_mtime_ns
        except OSError:
            pass
        return 200, {"ok": True, "banned": sum(len(v) for v in self.banned().values())}


def ban_line(speech_path, alert, template, ban=True):
    """set_ban on the speech file at `speech_path` (a script's or a test's way to cut a line)."""
    return SpeechLines(speech_path).set_ban(alert, template, ban)


# ---- filling and tidying lines outside the page (a Python mirror of page.js's, kept in step by the tests) ----

# made-up values for trying lines out, already phrased the way the page phrases them
SAMPLES = {
    "arrival_undiscovered": {"system": "Drojau LL-O b26-3"}, "arrival_discovered": {"system": "Drojau LL-O b26-3"},
    "leaving": {"text": "A 2, a class two gas giant, plus 1.4M to map"},
    "find_body": {"what": "Water world, terraformable, undiscovered", "body": "A 3", "value": "2.3M"}, "mapped_before": {},
    "find_bio": {"body": "B 7", "value": "19.0M"}, "sample_clear": {"genus": "Stratum"}, "bio_tag_near": {"genus": "Tussock", "distance": 80},
    "codex": {"entry": "Stratum Tectonicas", "what": "new to your codex for this region"},
    "phenomena": {"what": "three notable stellar phenomena, all in rings"},
    "rare_star": {"what": "a black hole at B"},
    "surface_site": {"what": "Guardian sites on B 3"},
    "signal_poi": {"what": "a Guardian beacon"},
    "curious": {},
    "ggg": {"body": "B 3"},
    "fuel_low": {"pct": 18}, "fuel_star": {"pct": 22, "star": "white dwarf"},
    "fuel_target": {"pct": 22, "system": "Drojau LL-O b26-3"},
    "fuel_topup": {"rate": "8 of the last 20 stars were scoopable", "jumps": "about 6 jumps"}, "hull": {"pct": 42}, "interdicted": {"by": "someone"},
    "docked_sell": {"value": "114.1M", "station": "Jaques Station"}, "undocked_unsold": {"value": "260.4M"},
    "sold": {"sold": "12.6M cr cartographics and 4.1M cr exobiology", "still": ""},
    "unsold_warn": {"value": "52.0M"}, "unsold_urgent": {"value": "251.3M"},
    "carrier_departs": {"minutes": 4, "carrier": "Out Of The Blue"},
    "autotarget_nothing": {"why": "no route is plotted"},
    "carrier_arrived": {"carrier": "Out Of The Blue", "system": "Smojooe AR-E b25-8"},
    "fss_done": {"count": 14, "text": "B 1, Earth-like world, 3.1M to map, and biology on C 2, up to 19.0M"},
    "fss_nothing": {"count": 14}, "fss_unfinished": {"left": "3 bodies"},
    "jumponium": {"body": "B 4", "material": "polonium", "pct": "1.3 percent"},
    "region": {"region": "the Norma Arm", "count": "31 species you have logged elsewhere are new to your codex here"},
    "left_body": {"body": "A 3", "text": "Stratum 2 of 3, and Tussock untouched, up to 4.1M"},
    "bio_done_more": {"species": "Stratum Tectonicas", "value": "19.2M", "left": "Bacterium and Fungoida"},
    "bio_done_last": {"species": "Stratum Tectonicas", "value": "19.2M"},
    "tank_full": {"jumps": 8}, "scoop_stopped": {"pct": 64}, "supercharged": {"mult": "4 times"},
    "fsd_charge": {"system": "Drojau LL-O b26-3"},
    "body_brief": {"body": "B 7", "text": "3 biological signals, one of Stratum, Bacterium or Fungoida, 1.0M to 19.0M"},
    "high_g": {"gravity": "2.6", "value": "480.2M", "rebuys": "3.2"},
    "arrival_brief": {"text": "Undiscovered. 14 bodies. Scoopable K star."},
    "session_recap": {"text": "142 jumps, 3,100 light-years, 12 systems nobody had seen, 9 species sampled"},
    "streak_known": {"count": 10}, "streak_new": {"count": 5},
    "welcome_back": {"text": "Away 3 days. 412.0M aboard, unsold for 5 days. Fuel 64 percent. Docked at Jaques Station."},
    "ship_lost": {"text": "Lost 212.4M: 148.1M cartographics, 64.3M exobiology, 31 systems and 9 first discoveries. "
                          "The nearest lost system is Drojau LL-O b26-3, 42 light-years."},
    "sale_left": {"text": "Sold 50 systems for 14.8M. 43 systems are still unsold, 2.0M, 270 first discoveries: "
                          "sell the next page."},
}
# the voice lab's Audition (page.js AUDITION_KEYS): one personality across the alerts that matter, in a session's order
AUDITION = ("game_start", "arrival_brief", "find_body", "leaving", "fuel_low", "sold", "bio_done_last", "session_recap")
DEFAULT_NAMES = "Boss, Hefay, Sir"


def names_list(text):
    return [x.strip() for x in str(text or "").split(",") if x.strip()]


def fill(text, values, names=DEFAULT_NAMES, rng=None):
    """A line with its {placeholders} filled: every {name} is its own random pick from `names`."""
    import random
    rng = rng or random
    pool = names_list(names) or ["Commander"]

    def one(m):
        k = m.group(1)
        if k == "name":
            return rng.choice(pool)
        v = values.get(k)
        return "" if v is None else str(v)
    return PLACEHOLDER.sub(one, text)


_PROC_NAME = re.compile(r"\b([A-Z])([A-Z])-([A-Z]) ([a-h])(\d+)(?:-(\d+))?\b")
_EMOJI = re.compile("[☀-➿\U0001f300-\U0001faff⭐✨⚠️]")


def spoken_text(text):
    """Text as the page hands it to the voice: markup and emoji out, 2.3M as "2.3 million", cr as credits."""
    t = re.sub(r"<[^>]+>", "", str(text))
    t = _EMOJI.sub("", t)
    # numbers to a tenth at most, and "52.0" as "52" (12.64B is "12.6 billion", 52.0M "52 million")
    # (fixed-point, not :g, which goes to "1.23457e+06" from a million and drops the tenths from 100000)
    t = re.sub(r"(?<![\d.])\d+\.\d+(?![\d.])", lambda m: f"{float(m.group()):.1f}".removesuffix(".0"), t)
    t = re.sub(r"(\d+(?:\.\d+)?)M\b", r"\1 million", t)
    t = re.sub(r"(\d+(?:\.\d+)?)k\b", r"\1 thousand", t)
    t = re.sub(r"(\d+(?:\.\d+)?)B\b", r"\1 billion", t)
    t = re.sub(r"\bcr\b", "credits", t)
    t = re.sub(r"\s·\s", ", ", t)
    # a procedural system name's sector suffix letter by letter ("Drojau LL-O b26-3" -> "Drojau L L O, b 26 3"),
    # as the page's spokenText does; a carrier id (K7F-3XZ) or a hand-named system does not match
    t = _PROC_NAME.sub(lambda m: f"{m[1]} {m[2]} {m[3]}, {m[4]} {m[5]}" + (f" {m[6]}" if m[6] else ""), t)
    return re.sub(r"\s+", " ", t).strip()
