"""Questions by voice (tablet plan phase 6): POST /api/ask matches the fixed phrases (resources/ask.json), answers
from the read-only tools in-process, sends the answer to every window through the co-pilot channel, and only with
[assistant] enabled asks an OpenAI-compatible endpoint: here always a fake one on a free port, never a real provider."""
import asyncio
import contextlib
import io
import json
import os
import tempfile
import time
import tomllib
import unittest
import unittest.mock

from support import ed_outrider
from test_mcp import fake_get   # the same fixture payloads as the MCP tools

import outrider.ask as ask  # noqa: E402
import outrider.auth  # noqa: E402


class Phrases(unittest.TestCase):
    def test_starter_phrases(self):
        ph = ask.load_phrases()
        self.assertEqual(set(ph), set(ask.COMMANDS))
        cases = {"Hey Vespa, status report.": "status_report", "vespa what's my fuel": "fuel", "How much am I carrying?": "unsold",
                 "next jump": "next_jump", "how many jumps left on the highway": "next_jump", "What's left here?": "whats_left",
                 "nearest unvisited system": "nearest_unvisited", "hush": "hush", "Vespa, be quiet!": "hush",
                 "unhush": "unhush", "voice back please": "unhush", "jumps left": "fuel",
                 "what's the weather like": None, "": None, "hushpuppies": None}
        for text, want in cases.items():
            self.assertEqual(ask.match(text, ph), want, text)

    def test_broken_phrase_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "ask.json")
            with open(p, "w") as f:
                f.write("{not json")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                ph = ask.load_phrases(p)
        self.assertIn("ask.json", err.getvalue())
        self.assertEqual(ask.match("status report", ph), "status_report")

    def test_fixed_answers(self):
        go = lambda c: asyncio.run(ask.fixed_answer(c, fake_get()))   # noqa: E731
        self.assertEqual(go("fuel"), "Fuel at 41 percent, about 6 jumps at max range.")
        self.assertEqual(go("unsold"), "About 900 credits unsold: 600 cartographic and 300 exobiology.")
        self.assertEqual(go("next_jump"), "Next highway stop: Neu, 400 light years, a neutron star. 42 jumps left, refuel in 3.")
        self.assertEqual(go("nearest_unvisited"), "Nearest unvisited: Near New, 7.0 light years.")
        self.assertEqual(go("whats_left"), "Still to do here: find 1 more body in the FSS; biology on A 1; map A 2.")
        self.assertEqual(go("status_report"), "Start. Fuel 41 percent, 6 jumps. 1.2 million credits unsold. Highway: next Neu, 42 jumps left.")
        self.assertEqual(asyncio.run(ask.fixed_answer("fuel", fake_get(down=True))), "Outrider isn't running (start it, then ask again)")
        # one jump is "1 jump" (the tablet's Now header said "1 jumps", and so did these)
        async def one(name, args, get, rows):
            return {"current_status": {"system": "Sol", "fuel_pct": 9, "fuel_jumps": 1},
                    "highway_route": {"route": True, "next": {"system": "Neu", "distance_ly": 40}, "jumps_left": 1}}[name]
        with unittest.mock.patch.object(ask.tools, "call", one):
            self.assertEqual(go("fuel"), "Fuel at 9 percent, about 1 jump at max range.")
            self.assertEqual(go("status_report"), "Sol. Fuel 9 percent, 1 jump. Highway: next Neu, 1 jump left.")
            self.assertEqual(go("next_jump"), "Next highway stop: Neu, 40.0 light years. 1 jump left.")
        self.assertEqual([ask.words_cr(x) for x in (0, 950, 12_400, 1_000_000, 2_350_000_000)],
                         ["0", "950", "12 thousand", "1 million", "2.4 billion"])

    def test_settings(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(ask.assistant_settings({}), ask.ASSISTANT)
            a = ask.assistant_settings({"assistant": {"enabled": True, "base_url": "http://localhost:11434/v1", "model": "qwen3",
                                                      "timeout": 30, "max_rounds": 2, "api_key": " k "}})
            bad = ask.assistant_settings({"assistant": {"enabled": "yes", "base_url": "ftp://x", "timeout": 999}})
        self.assertEqual((a["enabled"], a["model"], a["timeout"], a["max_rounds"], a["api_key"]), (True, "qwen3", 30.0, 2, "k"))
        self.assertEqual((bad["enabled"], bad["base_url"], bad["timeout"]), (False, "", 20.0))
        self.assertIn("[assistant] enabled", err.getvalue())
        import argparse
        args = argparse.Namespace(journals=None, legacy=None, host=None, port=None, radius=None, db=None)
        st = ed_outrider.settings_from({"assistant": {"enabled": True, "model": "m", "base_url": "http://h/v1"}}, args, None, ([], []))
        back = tomllib.loads(ed_outrider.config_text(st))["assistant"]
        self.assertEqual((back["enabled"], back["model"], back["base_url"], back["max_rounds"]), (True, "m", "http://h/v1", 4))


class Endpoint(unittest.TestCase):
    def setUp(self):
        self.db = ed_outrider.open_db(":memory:")
        self.addCleanup(self.db.close)
        self.state = ed_outrider.State(self.db, ed_outrider.Journals(self.db), None, 25)

    def client(self, go):
        from aiohttp.test_utils import TestClient, TestServer

        async def run():
            async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                return await go(c)
        return asyncio.run(run())

    def test_contract(self):
        async def go(c):
            out = {}
            r = await c.post("/api/ask", json={"text": "Hey Vespa, nearest unvisited?", "source": "vespa"})
            out["fixed"] = (r.status, await r.json())
            out["copilot"] = dict(self.state.copilot)
            await c.get("/api/nearby?speaker=1")   # a window that speaks (S24)
            r = await c.post("/api/ask", json={"text": "what is the meaning of life"})
            out["none"] = await r.json()
            r = await c.post("/api/ask", json={"text": "hush"})
            out["hush"] = (await r.json(), self.state.hush and self.state.hush["mode"], self.state.copilot["action"])
            await c.post("/api/ask", json={"text": "voice back"})
            out["unhush"] = self.state.hush
            out["bad"] = [(r.status, (await r.json())["code"]) for r in
                          [await c.post("/api/ask", json=b) for b in ({}, {"text": ""}, {"text": "x" * 501}, {"text": 5})]]
            r = await c.post("/api/ask", json={"text": "fuel"}, headers={"X-Outrider-App": "0.1.0"})
            out["old"] = (r.status, (await r.json())["code"])
            with unittest.mock.patch.object(outrider.auth, "is_loopback", lambda remote: False):
                self.state.password = "pw"
                r = await c.post("/api/ask", json={"text": "fuel"})
                out["lan"] = (r.status, (await r.json())["code"])
                self.state.password = ""
            return out
        out = self.client(go)
        status, body = out["fixed"]
        self.assertEqual((status, set(body), body["matched"], body["command"], body["spoken"]),
                         (200, {"answer", "spoken", "matched", "command"}, "fixed", "nearest_unvisited", False))
        self.assertEqual((out["copilot"]["action"], out["copilot"]["words"]), ("say", body["answer"]))   # every window's caption
        self.assertEqual((out["none"]["matched"], out["none"]["command"], out["none"]["spoken"]), ("none", None, True))
        self.assertIn("status report", out["none"]["answer"])
        self.assertEqual((out["hush"][0]["command"], out["hush"][1], out["hush"][2]), ("hush", "30m", "caption"))
        self.assertIsNone(out["unhush"])
        self.assertEqual(out["bad"], [(400, "bad_request")] * 4)
        self.assertEqual((out["old"], out["lan"]), ((426, "app_too_old"), (401, "signin_required")))

    def test_speaker_seen_expires(self):
        self.state.speaker_seen = time.monotonic() - 61
        self.assertFalse(self.state.speaker_present())
        self.state.speaker_seen = time.monotonic()
        self.assertTrue(self.state.speaker_present())

    # ---- the AI layer, against a fake OpenAI-compatible server ----
    def fake_ai(self, script):
        """An aiohttp app answering POST /v1/chat/completions from `script` (one answer per request, the last repeats):
        a dict (the JSON), an int (that status), or ("sleep", s). Records each request's body and headers."""
        from aiohttp import web
        seen = []

        async def chat(request):
            seen.append((await request.json(), dict(request.headers)))
            item = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(item, tuple):
                await asyncio.sleep(item[1])
                item = {"choices": [{"message": {"content": "late"}}]}
            if isinstance(item, int):
                return web.json_response({"error": "nope"}, status=item)
            return web.json_response(item)
        app = web.Application()
        app.router.add_post("/v1/chat/completions", chat)
        return app, seen

    def ai_run(self, script, text="how far is the nearest unvisited system", **cfg):
        from aiohttp.test_utils import TestClient, TestServer
        ai_app, seen = self.fake_ai(script)

        async def run():
            async with TestServer(ai_app) as ai:
                self.state.assistant = dict(ask.ASSISTANT, enabled=True, base_url=str(ai.make_url("/v1")), model="tiny",
                                            api_key="sk-test", **cfg)
                async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                    r = await c.post("/api/ask", json={"text": text})
                    return r.status, await r.json()
        status, body = asyncio.run(run())
        return status, body, seen

    def test_ai_tool_round_trip(self):
        call = {"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "nearest_unvisited", "arguments": "{}"}}]}}]}
        done = {"choices": [{"message": {"content": "Nothing unvisited is known nearby."}}]}
        status, body, seen = self.ai_run([call, done], text="tell me something interesting")
        self.assertEqual((status, body["matched"], body["answer"], body["command"]), (200, "ai", "Nothing unvisited is known nearby.", None))
        first, second = seen[0][0], seen[1][0]
        self.assertEqual((first["model"], seen[0][1].get("Authorization")), ("tiny", "Bearer sk-test"))
        self.assertIn("nearest_unvisited", {t["function"]["name"] for t in first["tools"]})
        self.assertEqual(second["messages"][-1]["role"], "tool")
        self.assertIn("nearest", json.loads(second["messages"][-1]["content"]))   # the tool ran in-process
        self.assertEqual(self.state.copilot["words"], "Nothing unvisited is known nearby.")

    def test_ai_failures(self):
        self.assertEqual(self.ai_run([401], text="sing")[:2], (502, {"error": "the AI provider refused the key (HTTP 401)", "code": "ai_error"}))
        status, body, _ = self.ai_run([("sleep", 1.0)], text="sing", timeout=0.2)
        self.assertEqual((status, body["code"]), (504, "ai_timeout"))
        loop = {"choices": [{"message": {"tool_calls": [{"id": "x", "function": {"name": "current_status", "arguments": "{}"}}]}}]}
        ran, real = [], ask.tools.call

        async def counted(*a, **k):
            ran.append(a[0])
            return await real(*a, **k)
        with unittest.mock.patch.object(ask.tools, "call", counted):
            status, body, seen = self.ai_run([loop], text="sing", max_rounds=2)
        self.assertEqual((status, body["code"], len(seen)), (502, "ai_error", 3))
        # two rounds of tools, then a request for the answer only; a tool asked for then is not run (Codex F7)
        self.assertEqual((len(ran), "tool_choice" in seen[1][0], seen[2][0].get("tool_choice")), (2, False, "none"))
        self.assertEqual(self.ai_run([{"choices": [{"message": {"content": ""}}]}], text="sing")[1]["code"], "ai_error")
        # enabled but not set up: says so (503), and a fixed phrase never goes to the AI at all
        self.state.assistant = dict(ask.ASSISTANT, enabled=True)
        out = self.client(lambda c: self._post(c, "sing"))
        self.assertEqual(out, (503, "ai_off"))
        status, body, seen = self.ai_run([401], text="fuel")
        self.assertEqual((status, body["matched"], seen), (200, "fixed", []))

    def test_ai_malformed_replies(self):
        """R4: an AI provider's answer of an unexpected shape gives 502 ai_error (or, where the meaning is plain, an
        answer), never a 500 with a traceback."""
        msg = lambda m: {"choices": [{"message": m}]}   # noqa: E731
        call = lambda args: msg({"tool_calls": [{"id": "c1", "function": {"name": "current_status", "arguments": args}}]})   # noqa: E731
        done = msg({"content": "All quiet."})
        for script in ([{"choices": ["not a dict"]}], [{"choices": [{"message": "text"}]}], [msg({"tool_calls": "x"})],
                       [msg({"tool_calls": ["not a dict"]})], [msg({"content": 5})], [{"choices": {"0": 1}}], [["a list"]]):
            with contextlib.redirect_stderr(io.StringIO()):
                status, body, _ = self.ai_run(list(script), text="sing")
            self.assertEqual((status, body.get("code")), (502, "ai_error"), script)
        # plain meanings accepted: arguments already an object, content as a list of text parts
        status, body, seen = self.ai_run([call({}), done], text="sing")
        self.assertEqual((status, body["answer"]), (200, "All quiet."))
        self.assertEqual(seen[1][0]["messages"][-1]["role"], "tool")
        status, body, _ = self.ai_run([msg({"content": [{"type": "text", "text": "All"}, {"type": "text", "text": " quiet."}]})], text="sing")
        self.assertEqual((status, body["answer"]), (200, "All quiet."))

    def test_ai_backstop(self):
        """R4: anything else going wrong in the AI layer is ai_error with the exception's name, not a 500."""
        async def broken(*a, **k):
            raise KeyError("surprise")
        with unittest.mock.patch.object(ask, "ai_answer", broken), contextlib.redirect_stderr(io.StringIO()) as err:
            status, body, _ = self.ai_run([{}], text="sing")
        self.assertIn("surprise", err.getvalue())   # the traceback is in the log
        self.assertEqual((status, body["code"]), (502, "ai_error"))
        self.assertIn("KeyError", body["error"])

    async def _post(self, c, text):
        r = await c.post("/api/ask", json={"text": text})
        return r.status, (await r.json()).get("code")


if __name__ == "__main__":
    unittest.main()


class KnownQuestions(unittest.TestCase):
    def test_every_command_is_named(self):
        """What the voice says it knows lists every command it answers: "the nearest station" was left out
        (review 2026-10-11)."""
        import outrider.ask as ask
        self.assertEqual(set(ask.LABELS), set(ask.COMMANDS))
        said = ask.known_questions()
        for label in ask.LABELS.values():
            self.assertIn(label, said)
