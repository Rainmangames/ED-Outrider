"""How Piper says the words its English voices get wrong (a review suggestion the author picked, 2026-10-12).

Piper reads text with espeak's English rules, which mangle the Latin of the exobiology names ("Fonticulua",
"Osseus", "Clypeus Lacrimam"). resources/pronounce.json says them in a respelling: syllables joined by "-", the
stressed one in capitals ("fon-TIK-yoo-loo-uh"). Your own data/pronounce.json goes on top (never committed): stations,
carriers, system names, your commander name, or a shipped word said differently. An entry may also be raw Piper
phonemes ("[[fɒntˈɪkjuːluːə]]").

At speaking time each word found (whole words, any case) becomes Piper's raw phonemes: every syllable read by
espeak on its own in the voice's own accent, the stress marks taken off but the capital one's, and an unstressed
"uh" sound reduced to a schwa. The voice lab's "how is it said" box tries a respelling before it is saved:
"{fon-TIK-yoo-loo-uh}" in a line is said the same way. English voices only: another language's voice reads as it did.
"""
import json
import os
import re
import tempfile
import threading
import unicodedata

from . import DATA_DIR, RESOURCES_DIR

SHIPPED = os.path.join(RESOURCES_DIR, "pronounce.json")
OWN = os.path.join(DATA_DIR, "pronounce.json")
RESPELLING = re.compile(r"^[A-Za-z]+(?:-[A-Za-z]+)*$")
RAW = re.compile(r"^\[\[[^\[\]{}]+\]\]$")
WORD = re.compile(r"^[^\W\d_][\w'’-]*$")   # a key: one word (letters first; apostrophes and dashes inside)
VOWELS = set("aeiouyɑɒæɐəɛɜɪʊʌɔɚɝœøɯɤ")
INLINE = re.compile(r"\{([A-Za-z]+(?:-[A-Za-z]+)*)\}")
BLOCK = re.compile(r"(\[\[.*?\]\])")


def valid(word, say):
    """Whether (word, say) is an entry that can be used: one word, and a respelling or raw phonemes."""
    return isinstance(word, str) and bool(WORD.match(word.strip())) and isinstance(say, str) \
        and bool(RESPELLING.match(say.strip()) or RAW.match(say.strip()))


def read(path):
    """{word: say} from one file ({"words": {...}}), bad entries left out; {} when it is missing or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    words = doc.get("words") if isinstance(doc, dict) else None
    return {w.strip(): s.strip() for w, s in (words or {}).items() if valid(w, s)} if isinstance(words, dict) else {}


class Table:
    """The shipped words with yours on top, read again when either file changes. entries(): [{word, say, source}]."""

    def __init__(self, shipped=SHIPPED, own=OWN):
        self.shipped, self.own = shipped, own
        self._stamp, self._words, self._pattern, self._lock = None, {}, None, threading.Lock()

    def _mtime(self, p):
        try:
            return os.stat(p).st_mtime_ns
        except OSError:
            return None

    def words(self):
        """{lower-case word: say}, yours winning."""
        stamp = (self._mtime(self.shipped), self._mtime(self.own))
        with self._lock:
            if stamp != self._stamp:
                merged = {w.lower(): s for w, s in read(self.shipped).items()}
                merged.update({w.lower(): s for w, s in read(self.own).items()})
                self._stamp, self._words = stamp, merged
                self._pattern = re.compile(r"(?<![\w'’-])(" + "|".join(re.escape(w) for w in sorted(merged, key=len, reverse=True))
                                           + r")(?![\w'’-])([,.;:!?](?=\s|$))?", re.IGNORECASE) if merged else None
            return self._words

    def stamp(self):
        """The files' version (changes when either is written)."""
        self.words()
        return self._stamp

    def pattern(self):
        self.words()
        return self._pattern

    def entries(self):
        shipped, own = read(self.shipped), read(self.own)
        out = {w.lower(): {"word": w, "say": s, "source": "shipped"} for w, s in shipped.items()}
        for w, s in own.items():
            out[w.lower()] = {"word": w, "say": s, "source": "yours", "shipped": shipped.get(w) or next(
                (v for k, v in shipped.items() if k.lower() == w.lower()), None)}
        return sorted(out.values(), key=lambda e: e["word"].lower())

    def save(self, word, say):
        """Your file with `word` said as `say` (None: yours removed, the shipped one, if any, applies again). False for
        an entry that cannot be used. Written to a temporary file moved into place."""
        word = (word or "").strip() if isinstance(word, str) else ""
        if say is not None and not valid(word, say):
            return False
        if not WORD.match(word):
            return False
        mine = read(self.own)
        for k in [k for k in mine if k.lower() == word.lower()]:
            del mine[k]
        if say is not None:
            mine[word] = say.strip()
        os.makedirs(os.path.dirname(self.own) or ".", exist_ok=True)
        doc = {"about": "Your own pronunciations for Outrider's Piper voice (the voice lab's How is it said box): "
                        "syllables joined by '-', the stressed one in capitals, or raw phonemes in [[ ]].",
               "words": dict(sorted(mine.items(), key=lambda kv: kv[0].lower()))}
        fd, part = tempfile.mkstemp(dir=os.path.dirname(self.own) or ".", prefix="pronounce.json.", suffix=".part")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, ensure_ascii=False, indent=1)
                f.write("\n")
            os.replace(part, self.own)
        except BaseException:
            try:
                os.remove(part)
            except OSError:
                pass
            raise
        return True


_cache, _cache_lock = {}, threading.Lock()


def piper_phonemize(espeak_voice, text):
    """espeak's phonemes for `text` in `espeak_voice`, as Piper reads text (its own phonemizer and lock, so this
    never runs beside a synthesis's)."""
    from piper import voice as pv   # Piper itself: only with a Piper voice in use
    from piper.phonemize_espeak import EspeakPhonemizer
    with pv._ESPEAK_PHONEMIZER_LOCK:
        if pv._ESPEAK_PHONEMIZER is None:
            pv._ESPEAK_PHONEMIZER = EspeakPhonemizer()
        return "".join("".join(s) for s in pv._ESPEAK_PHONEMIZER.phonemize(espeak_voice, text)).strip()


def _stress(ph):
    for i, c in enumerate(ph):
        if unicodedata.normalize("NFD", c)[0] in VOWELS:
            return ph[:i] + "ˈ" + ph[i:]
    return "ˈ" + ph


def to_phonemes(say, espeak_voice, phonemize=piper_phonemize):
    """A respelling's raw phonemes for `espeak_voice` (cached), or a raw entry's own; None when espeak gives nothing."""
    say = say.strip()
    if RAW.match(say):
        return say[2:-2].strip()
    key = (say, espeak_voice)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    parts = say.split("-")
    loud = [p for p in parts if p.isupper()]
    if not loud or len(loud) == len(parts):   # no stress said (or all of it): espeak reads it as one word
        out = phonemize(espeak_voice, "".join(parts).lower()) or None
    else:
        out = ""
        for p in parts:
            ph = _syllable(p, espeak_voice, phonemize)
            if out and ph.lstrip("ˈ")[:1] == out[-1:] and out[-1:] not in VOWELS:   # "ar-ruh": one r, not two
                ph = ph.replace(out[-1], "", 1)
            out += ph
        out = out or None
    with _cache_lock:
        _cache[key] = out
    return out


# a syllable that is only a vowel sound: espeak would read these as a word or as letters ("ay" is "aye")
LONE_VOWELS = {"uh": "ʌ", "eh": "ɛ", "ih": "ɪ", "ay": "eɪ"}


def _syllable(p, espeak_voice, phonemize):
    """One syllable's phonemes: stressed when written in capitals; an unstressed "uh" sound a schwa."""
    low = p.lower()
    if low in LONE_VOWELS:
        ph = LONE_VOWELS[low]
    elif low[-2:] in ("uh", "eh", "ih") and len(low) > 2:   # "tuh", "seh", "dih": read as "tut", "set", "dit", less the t
        ph = re.sub(r"[ˈˌ\s]", "", phonemize(espeak_voice, low[:-1] + "t") or "")
        ph = ph[:-1] if ph.endswith("t") else ph
    else:
        ph = re.sub(r"[ˈˌ\s]", "", phonemize(espeak_voice, low) or "")
    if p.isupper():
        return _stress(ph)
    return ph.replace("ʌ", "ə").replace("ɐ", "ə")


def apply(text, espeak_voice, table, phonemize=piper_phonemize):
    """`text` with the table's words (and any {respelling}) as Piper's raw phonemes, punctuation right after one moved
    inside its block (Piper drops a comma that starts the text after a block). Raw blocks already there are left as
    they are. Only for an English voice (espeak "en..."); anything else gets the text unchanged."""
    if not str(espeak_voice or "").startswith("en"):
        return text
    pat = table.pattern() if table else None
    words = table.words() if table else {}

    def block(say, punct):
        ph = to_phonemes(say, espeak_voice, phonemize)
        return f"[[{ph}{punct or ''}]]" if ph else None

    def one(m):
        return block(words[m.group(1).lower()], m.group(2)) or m.group(0)

    def inline(m):
        return block(m.group(1), "") or m.group(1).replace("-", "")
    out = []
    for part in BLOCK.split(text):
        if not part.startswith("[["):
            part = INLINE.sub(inline, part)
            parts = BLOCK.split(part)   # the inline ones are blocks now: the table's words only between them
            part = "".join(p if p.startswith("[[") or not pat else pat.sub(one, p) for p in parts)
        out.append(part)
    return "".join(out)
