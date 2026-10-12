"""Unit tests: the pronunciation table (outrider/pronounce.py; a review suggestion the author picked, 2026-10-12): the
respelling's phonemes, whole words only, the files merged with yours on top, bad entries ignored, Piper given the
blocks, the API.

Run all: python3 -m unittest discover tests (or scripts/verify.sh).
"""
import json
import os
import tempfile
import types
import unittest

from support import ed_outrider  # noqa: F401  (puts the repository root on sys.path)
import outrider.pronounce as P  # noqa: E402

# a stand-in for espeak: each syllable read as itself, stressed the way espeak marks a word read alone
FAKE = {"fon": "fˈɒn", "tik": "tˈɪk", "yoo": "jˈuː", "loo": "lˈuː", "uh": "ˈʌ", "oss": "ˈɒs", "ee": "ˈiː", "uss": "ˈʌs",
        "tut": "tˈʌt", "set": "sˈɛt", "dit": "dˈɪt", "ar": "ˈɑːɹ", "ruh": "ɹˈʌ", "rut": "ɹˈʌt", "kak": "kˈæk", "toy": "tˈɔɪ"}


def fake(voice, text):
    return FAKE.get(text, text)


def table(d, shipped=None, own=None):
    paths = []
    for name, words in (("shipped.json", shipped), ("own.json", own)):
        p = os.path.join(d, name)
        if words is not None:
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"words": words}, f)
        paths.append(p)
    return P.Table(*paths)


class Respelling(unittest.TestCase):
    def setUp(self):
        P._cache.clear()

    def test_phonemes(self):
        """Every syllable read alone, the stress only on the capital one, an unstressed "uh" sound a schwa; "tuh",
        "seh", "dih" as "tut", "set", "dit" less the t; a lone "ay" its own sound; one r where two syllables meet."""
        self.assertEqual(P.to_phonemes("fon-TIK-yoo-loo-uh", "en-gb-x-rp", fake), "fɒntˈɪkjuːluːə")
        self.assertEqual(P.to_phonemes("OSS-ee-uss", "en-gb-x-rp", fake), "ˈɒsiːəs")
        self.assertEqual(P.to_phonemes("kak-TOY-tuh", "en-us", fake), "kæktˈɔɪtə")
        self.assertEqual(P.to_phonemes("set-SEH-dih", "en-us", fake), "sɛtsˈɛdɪ")
        self.assertEqual(P.to_phonemes("AY-ee", "en-us", fake), "ˈeɪiː")
        self.assertEqual(P.to_phonemes("ar-ruh-TOY", "en-us", fake), "ɑːɹətˈɔɪ")
        self.assertEqual(P.to_phonemes("[[ˈɒsiəs]]", "en-us", fake), "ˈɒsiəs")             # raw phonemes as they are
        self.assertEqual(P.to_phonemes("fon-tik", "en-us", lambda v, t: "fˈɒntɪk:" + t), "fˈɒntɪk:fontik")   # no stress said: one word

    def test_apply(self):
        with tempfile.TemporaryDirectory() as d:
            t = table(d, {"Osseus": "OSS-ee-uss", "Fonticulua": "fon-TIK-yoo-loo-uh"})
            say = lambda text, v="en-gb-x-rp": P.apply(text, v, t, fake)
            self.assertEqual(say("Osseus Fonticulua on A 2."), "[[ˈɒsiːəs]] [[fɒntˈɪkjuːluːə]] on A 2.")
            self.assertEqual(say("an osseus, then FONTICULUA."), "an [[ˈɒsiːəs,]] then [[fɒntˈɪkjuːluːə.]]")   # any case; punctuation inside
            self.assertEqual(say("Osseusx and Osseus-like"), "Osseusx and Osseus-like")                      # whole words only
            self.assertEqual(say("[[ˈeɪ]] 2 Osseus"), "[[ˈeɪ]] 2 [[ˈɒsiːəs]]")                              # a block already there kept
            self.assertEqual(say("say {OSS-ee-uss} now"), "say [[ˈɒsiːəs]] now")                           # the lab's try
            self.assertEqual(say("Osseus", "de"), "Osseus")                                                # another language
            self.assertEqual(P.apply("Osseus", "en-us", None, fake), "Osseus")                             # no table

    def test_files_merged_and_bad_entries(self):
        """Yours on top of the shipped words (any case); a bad entry (two words, digits, not a respelling) left out;
        save writes your file and takes an entry back out."""
        with tempfile.TemporaryDirectory() as d:
            t = table(d, {"Osseus": "OSS-ee-uss", "Two words": "TOO", "Tubus": "too bus", "Clypeus": 7, "Cerbrus": "ser{x}"},
                      {"osseus": "[[ˈɒsjəs]]", "Jameson": "JAYM-sun"})
            self.assertEqual(t.words(), {"osseus": "[[ˈɒsjəs]]", "jameson": "JAYM-sun"})
            self.assertEqual([(e["word"], e["source"]) for e in t.entries()], [("Jameson", "yours"), ("osseus", "yours")])
            self.assertEqual(next(e for e in t.entries() if e["word"] == "osseus")["shipped"], "OSS-ee-uss")
            self.assertFalse(t.save("Two words", "TOO"))
            self.assertFalse(t.save("Tubus", "too bus"))
            self.assertTrue(t.save("Tubus", "TOO-buss"))
            self.assertTrue(t.save("OSSEUS", None))   # yours removed: the shipped one again
            self.assertEqual(t.words(), {"osseus": "OSS-ee-uss", "jameson": "JAYM-sun", "tubus": "TOO-buss"})
            with open(os.path.join(d, "own.json"), encoding="utf-8") as f:
                self.assertEqual(json.load(f)["words"], {"Jameson": "JAYM-sun", "Tubus": "TOO-buss"})
            with open(os.path.join(d, "shipped.json"), "w") as f:
                f.write("{not json")
            self.assertEqual(t.words(), {"jameson": "JAYM-sun", "tubus": "TOO-buss"})   # a broken file: nothing from it

    def test_shipped_table(self):
        """Every shipped entry is a usable one-word respelling with one stressed syllable; with Piper here, espeak gives
        each phonemes in both accents, one stress mark each."""
        with open(P.SHIPPED, encoding="utf-8") as f:
            words = json.load(f)["words"]
        self.assertGreaterEqual(len(words), 100)
        for w, s in words.items():
            self.assertTrue(P.valid(w, s), w)
            self.assertEqual(sum(p.isupper() for p in s.split("-")), 1, w)
        import importlib.util
        if importlib.util.find_spec("piper") is None:
            self.skipTest("no Piper")
        for voice in ("en-gb-x-rp", "en-us"):
            for w, s in words.items():
                ph = P.to_phonemes(s, voice)
                self.assertTrue(ph and ph.count("ˈ") == 1 and " " not in ph, (voice, w, ph))


class Server(unittest.TestCase):
    def test_piper_gets_the_blocks(self):
        import outrider.tts
        sent = []

        def synth(text, wf, syn_config=None):
            sent.append(text)
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(8000); wf.writeframes(b"\0\0")
        with tempfile.TemporaryDirectory() as d:
            t = table(d, {"Osseus": "[[ˈɒsiəs]]"})
            keep, outrider.tts.PRONOUNCE = outrider.tts.PRONOUNCE, t
            try:
                sp = outrider.tts.Speaker("en_GB-main-low", None, voices_dir=d)
                sp._voice = types.SimpleNamespace(synthesize_wav=synth, config=types.SimpleNamespace(espeak_voice="en-gb-x-rp"))
                self.assertTrue(sp.say("Osseus on A 2 a, done"))
                t.save("Osseus", "[[ˈɒsjəs]]")
                self.assertTrue(sp.say("Osseus on A 2 a, done"))   # said again, not from the cache
            finally:
                outrider.tts.PRONOUNCE = keep
        self.assertEqual(sent, ["[[ˈɒsiəs]] on [[ˈeɪ]] 2 [[ˈeɪ,]] done", "[[ˈɒsjəs]] on [[ˈeɪ]] 2 [[ˈeɪ,]] done"])

    def test_api(self):
        import asyncio
        from aiohttp.test_utils import TestClient, TestServer
        db = ed_outrider.open_db(":memory:")
        self.addCleanup(db.close)
        state = ed_outrider.State(db, ed_outrider.Journals(db), types.SimpleNamespace(cached=lambda i: (None, None)), 25)
        with tempfile.TemporaryDirectory() as d:
            t = table(d, {"Osseus": "OSS-ee-uss"})
            import outrider.tts
            keep, outrider.tts.PRONOUNCE = outrider.tts.PRONOUNCE, t
            try:
                async def go():
                    async with TestClient(TestServer(ed_outrider.make_app(state))) as c:
                        out = {"list": (await (await c.get("/api/pronounce")).json())["words"]}
                        r = await c.post("/api/pronounce", json={"word": "Jameson", "say": "JAYM-sun"})
                        out["saved"] = [(e["word"], e["source"]) for e in (await r.json())["words"]]
                        out["bad"] = [(await c.post("/api/pronounce", json=b)).status for b in
                                      ({"word": "two words", "say": "TOO"}, {"word": "Tubus", "say": "too bus"}, {"say": "TOO"}, {"word": "Tubus"})]
                        r = await c.post("/api/pronounce", json={"word": "Jameson", "say": None})
                        out["removed"] = [e["word"] for e in (await r.json())["words"]]
                        return out
                out = asyncio.run(go())
            finally:
                outrider.tts.PRONOUNCE = keep
        self.assertEqual(out["list"], [{"word": "Osseus", "say": "OSS-ee-uss", "source": "shipped"}])
        self.assertEqual(out["saved"], [("Jameson", "yours"), ("Osseus", "shipped")])
        self.assertEqual(out["bad"], [400, 400, 400, 400])
        self.assertEqual(out["removed"], ["Osseus"])


if __name__ == "__main__":
    unittest.main()
