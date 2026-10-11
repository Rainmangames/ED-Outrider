"""Unit tests: Road to Riches: Spansh's route of systems with valuable bodies, plotting it, following it by the journal
(fakes only: no network, no clipboard tool).

Run all: python3 -m unittest discover tests (or scripts/verify.sh).
"""
import asyncio
import time
import unittest
import unittest.mock

from support import _HwSession, hwy_jump, hwy_ts, types_ns  # also puts the repository root on sys.path
import ed_outrider  # noqa: E402
import outrider.riches as riches  # noqa: E402


def body(name, subtype, ls, scan, mapped, **kw):
    return dict({"name": name, "type": "Planet", "subtype": subtype, "distance_to_arrival": ls,
                 "estimated_scan_value": scan, "estimated_mapping_value": mapped}, **kw)


# A synthetic answer in Spansh's Road to Riches layout (made-up systems, for the route logic); RichesRows.test_real_answer
# reads a real one (tests/fixtures/spansh_riches.json).
RESULT = [
    {"name": "Alpha Rich", "id64": 1001, "x": 0, "y": 0, "z": 0, "jumps": 0,
     "bodies": [body("Alpha Rich 1", "Earth-like world", 500, 1200000, 3000000, is_terraformable=False),
                body("Alpha Rich 2 a", "Water world", 8000, 800000, 1500000)]},
    {"name": "Beta Rich", "id64": 1002, "x": 20, "y": 0, "z": 0, "jumps": 1,
     "bodies": [body("Beta Rich 4", "High metal content world", 300, 450000, 900000, is_terraformable=True)]},
    {"name": "Gamma Rich", "id64": 1003, "x": 45, "y": 0, "z": 0, "jumps": 2,
     "bodies": [body("Gamma Rich 1", "Ammonia world", 1200, 600000, 1100000)]},
]


class RichesRows(unittest.TestCase):
    """riches_rows, riches_match, todo and the arrival line: pure."""

    def test_rows(self):
        rows = riches.riches_rows(RESULT)
        self.assertEqual([(r["system"], r["id64"], r["jumps"], len(r["bodies"])) for r in rows],
                         [("Alpha Rich", 1001, 0, 2), ("Beta Rich", 1002, 1, 1), ("Gamma Rich", 1003, 2, 1)])
        b = rows[0]["bodies"][0]
        self.assertEqual((b["name"], b["subtype"], b["ls"], b["scan"], b["map"], b["terraformable"]),
                         ("Alpha Rich 1", "Earth-like world", 500.0, 1200000, 3000000, 0))
        self.assertEqual(rows[1]["bodies"][0]["terraformable"], 1)
        # the answer may hold the list under "result" or "systems"
        self.assertEqual(len(riches.riches_rows({"result": RESULT})), 3)
        self.assertEqual(len(riches.riches_rows({"systems": RESULT})), 3)

    def test_real_answer(self):
        """A real answer (recorded 2026-10-06 from Colonia, trimmed): the systems and bodies as Spansh sends them, the
        body's id64 (a string) giving the game's BodyID (its top 9 bits), as body_id is everywhere else."""
        import json
        import os
        with open(os.path.join(os.path.dirname(__file__), "fixtures", "spansh_riches.json"), encoding="utf-8") as f:
            answer = json.load(f)
        rows = riches.riches_rows(answer["result"])
        self.assertEqual([r["system"] for r in rows], ["Colonia", "Eol Prou PC-K c9-4", "Eol Prou WY-R d4-388"])
        self.assertEqual((rows[1]["id64"], rows[1]["jumps"], len(rows[1]["bodies"])), (1167391498834, 1, 2))
        b = rows[1]["bodies"][0]
        self.assertEqual((b["name"], b["subtype"], b["terraformable"], b["body_id"], round(b["ls"])),
                         ("Eol Prou PC-K c9-4 A 6", "High metal content world", 1, 9, 364))
        self.assertEqual((b["scan"], b["map"]), (161983, 588537))
        self.assertEqual([x["body_id"] for x in rows[2]["bodies"]], [13, 17])   # "A 7" and "BC 3"

    def test_lenient_and_errors(self):
        rows = riches.riches_rows([
            "junk", {"id64": 5}, {"name": "  ", "id64": 6},
            {"name": "Ok", "id64": "77", "x": "bad", "jumps": "x",
             "bodies": [None, {"name": ""}, {"name": " Ok  1 ", "estimated_scan_value": "oops"},
                        {"name": "Ok 2", "distance_to_arrival": float("inf"), "estimated_scan_value": 10}]},
            {"system": "Named By System", "id64": 2 ** 70}])
        self.assertEqual([r["system"] for r in rows], ["Ok", "Named By System"])
        self.assertEqual((rows[0]["id64"], rows[0]["x"], rows[0]["jumps"]), (77, None, 0))
        self.assertEqual([(b["name"], b["scan"], b["ls"]) for b in rows[0]["bodies"]], [("Ok 1", None, None), ("Ok 2", 10, None)])
        self.assertIsNone(rows[1]["id64"])   # out of range: no id64, matched by name
        for bad in (None, {}, "x", 5, [], [{"bodies": []}], {"result": "no"}):
            with self.assertRaises(riches.RichesError):
                riches.riches_rows(bad)
        with unittest.mock.patch.object(riches, "RICHES_MAX_SYSTEMS", 2):
            with self.assertRaisesRegex(riches.RichesError, "longer than Outrider keeps"):
                riches.riches_rows(RESULT)

    def test_match(self):
        rows = riches.riches_rows(RESULT + [dict(RESULT[0], jumps=1)])   # Alpha Rich twice
        self.assertEqual(riches.riches_match(rows, 1002, "x"), 1)
        self.assertEqual(riches.riches_match(rows, None, "  gamma  RICH "), 2)
        self.assertEqual(riches.riches_match(rows, 1001, "Alpha Rich", near=0), 0)
        self.assertEqual(riches.riches_match(rows, 1001, "Alpha Rich", near=1), 3)
        self.assertEqual(riches.riches_match(rows, 1001, "Alpha Rich", near=4), 3)   # past the last: the latest before
        self.assertIsNone(riches.riches_match(rows, 999, "Nowhere"))

    def test_todo_and_value(self):
        bodies = riches.riches_rows(RESULT)[0]["bodies"]
        out = riches.todo(bodies, {"alpha rich 1"}, set(), True)
        self.assertEqual([(b["scanned"], b["mapped"], b["done"]) for b in out], [(True, False, False), (False, False, False)])
        out = riches.todo(bodies, {"alpha rich 1"}, {"alpha rich 1"}, True)
        self.assertTrue(out[0]["done"])
        out = riches.todo(bodies, {"alpha rich 1"}, set(), False)   # a route that does not count the map
        self.assertTrue(out[0]["done"])
        self.assertEqual(riches.body_value(bodies[0], True), 4200000)
        self.assertEqual(riches.body_value(bodies[0], False), 1200000)
        self.assertIsNone(riches.body_value({"name": "x", "scan": None, "map": 5}, False))
        self.assertEqual(riches.body_value({"name": "x", "scan": None, "map": 5}, True), 5)

    def test_text(self):
        rows = riches.riches_rows(RESULT)
        left = riches.todo(rows[0]["bodies"], set(), set(), True)
        self.assertEqual(riches.riches_text(rows, 0, left),
                         "Two bodies here: the best is a earth-like world, about 1.2 million credits, 500 light seconds out.")
        self.assertEqual(riches.riches_text(rows, 1, riches.todo(rows[1]["bodies"], set(), set(), True)),
                         "One body here: the best is a high metal content world, about 450 thousand credits, 300 light seconds out.")
        self.assertEqual(riches.riches_text(rows, 0, []), "Nothing left to do in Alpha Rich. Next stop: Beta Rich.")
        self.assertEqual(riches.riches_text(rows, 2, []), "Nothing left to do in Gamma Rich. Road to Riches complete.")
        self.assertEqual([riches.money(n) for n in (None, 950, 12000, 1000000, 2450000)],
                         [None, "950", "12 thousand", "1 million", "2.5 million"])


class RichesRoute(unittest.TestCase):
    """The route in the database, progress by the journal, the plot and the endpoints (fakes only)."""

    def setUp(self):
        import types
        self.db = ed_outrider.open_db(":memory:")
        self.addCleanup(self.db.close)
        self.j = ed_outrider.Journals(self.db)
        self.state = ed_outrider.State(self.db, self.j, types.SimpleNamespace(cached=lambda i: (None, None)), 25)
        self.now = time.time()

    ts = hwy_ts
    jump = hwy_jump

    def store(self, start=-100, mapping=True):
        self.jump(start, 1001, "Alpha Rich", 0)
        return self.state.riches_store(riches.riches_rows(RESULT), {"options": {"use_mapping_value": mapping}})

    def moments(self):
        return [(m["what"], m["text"]) for m in self.j.moments if m["kind"] == "riches"]

    def scan(self, s, system, addr, body_id, name):
        self.j.handle({"event": "Scan", "timestamp": self.ts(s), "StarSystem": system, "SystemAddress": addr,
                       "BodyID": body_id, "BodyName": name, "WasDiscovered": False, "WasMapped": False,
                       "ScanType": "Detailed", "DistanceFromArrivalLS": 500.0, "PlanetClass": "Water world",
                       "MassEM": 1.0, "TerraformState": ""})

    def mapped(self, s, addr, body_id, name):
        self.j.handle({"event": "SAAScanComplete", "timestamp": self.ts(s), "SystemAddress": addr, "BodyID": body_id,
                       "BodyName": name, "ProbesUsed": 6, "EfficiencyTarget": 8})

    def test_store_and_state(self):
        rc = self.store()
        rows = self.j.riches_route(rc)
        self.assertEqual([(r["system"], len(r["bodies"])) for r in rows], [("Alpha Rich", 2), ("Beta Rich", 1), ("Gamma Rich", 1)])
        self.assertEqual((rc["at"], rc["furthest"], rc["done_ts"], rc["options"]), (0, 0, None, {"use_mapping_value": True}))
        meta, rows = self.state.riches_state()
        self.assertEqual(self.state.riches_next(meta, rows), 1)
        self.assertEqual(rows[0]["bodies"][1]["name"], "Alpha Rich 2 a")
        # a new plot replaces the old route entirely
        self.state.riches_store(riches.riches_rows(RESULT[1:]), {"options": {}})
        self.assertEqual(self.db.execute("SELECT count(*) FROM riches_route").fetchone()[0], 2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM riches_bodies").fetchone()[0], 2)
        self.state.riches_clear()
        self.assertEqual((self.state.riches_state(), self.db.execute("SELECT count(*) FROM riches_bodies").fetchone()[0]),
                         ((None, []), 0))

    def test_arrival_progress_and_done(self):
        self.store()
        self.jump(-90, 1002, "Beta Rich", 20)
        what = self.moments()
        self.assertEqual(what[0][0], "next")
        self.assertIn("One body here", what[0][1])
        meta = ed_outrider.meta_get(self.db, "riches")
        self.assertEqual((meta["at"], meta["furthest"]), (1, 1))
        # the one body there: scanned is not enough while the route counts the map; mapped finishes the system
        self.scan(-80, "Beta Rich", 1002, 4, "Beta Rich 4")
        self.assertEqual(len(self.moments()), 1)
        self.mapped(-70, 1002, 4, "Beta Rich 4")
        self.assertEqual(self.moments()[1], ("done", "Everything here is done. Next stop: Gamma Rich."))
        self.mapped(-60, 1002, 4, "Beta Rich 4")   # a remap: nothing more said
        self.assertEqual(len(self.moments()), 2)
        # the last system: arriving says what is there, doing it ends the route
        self.jump(-50, 1003, "Gamma Rich", 45)
        self.assertEqual(self.moments()[2][0], "next")
        self.scan(-40, "Gamma Rich", 1003, 1, "Gamma Rich 1")
        self.mapped(-30, 1003, 1, "Gamma Rich 1")
        self.assertEqual(self.moments()[3], ("complete", "Everything here is done. Road to Riches complete."))
        self.assertTrue(ed_outrider.meta_get(self.db, "riches")["done_ts"])
        self.jump(-20, 1001, "Alpha Rich", 0)   # after the end: quiet
        self.assertEqual(len(self.moments()), 4)

    def test_scan_only_route(self):
        self.store(mapping=False)   # use_mapping_value off: a scan finishes a body
        self.scan(-90, "Alpha Rich", 1001, 1, "Alpha Rich 1")
        self.scan(-80, "Alpha Rich", 1001, 2, "Alpha Rich 2 a")
        self.assertEqual(self.moments(), [("done", "Everything here is done. Next stop: Beta Rich.")])

    def test_detour_and_back(self):
        self.store()
        self.jump(-90, 555, "Somewhere Else", 99)
        self.assertEqual(self.moments(), [("off_route", "Off route: detour.")])
        self.jump(-80, 555, "Somewhere Else", 99)   # a relog / the same system again: said once
        self.jump(-70, 1002, "Beta Rich", 20)
        self.assertEqual([w for w, _ in self.moments()], ["off_route", "back"])
        self.assertTrue(self.moments()[1][1].startswith("Back on the route. One body here"))
        self.assertIsNone(ed_outrider.meta_get(self.db, "riches")["off_route"])

    def test_catch_up_and_reread_say_nothing(self):
        self.store()
        # a jump written before the route was plotted, and one long ago (a journal being caught up on): no moments
        self.j.handle({"event": "FSDJump", "timestamp": self.ts(-500), "StarSystem": "Beta Rich", "SystemAddress": 1002,
                       "StarPos": [20, 0, 0]})
        self.assertEqual((self.moments(), ed_outrider.meta_get(self.db, "riches")["at"]), ([], 0))
        # progress read from an old journal is marked but never spoken
        self.jump(-90000, 1002, "Beta Rich", 20)
        old = ed_outrider.iso_ts(time.time() - 3 * 86400)
        rc = ed_outrider.meta_get(self.db, "riches")
        rc.update(at=1, furthest=1, since_ts=None, created_ts="2000-01-01T00:00:00Z")
        ed_outrider.meta_set(self.db, "riches", rc)
        self.j.handle({"event": "Scan", "timestamp": old, "StarSystem": "Beta Rich", "SystemAddress": 1002, "BodyID": 4,
                       "BodyName": "Beta Rich 4", "ScanType": "Detailed", "DistanceFromArrivalLS": 300.0,
                       "PlanetClass": "Water world", "MassEM": 1.0, "TerraformState": ""})
        self.j.handle({"event": "SAAScanComplete", "timestamp": old, "SystemAddress": 1002, "BodyID": 4,
                       "BodyName": "Beta Rich 4", "ProbesUsed": 6, "EfficiencyTarget": 8})
        self.assertEqual(self.moments(), [])
        self.assertEqual(ed_outrider.meta_get(self.db, "riches")["said_done"], 1)

    def test_route_survives_a_journal_reread(self):
        self.assertNotIn("riches", ed_outrider.RESET_JOURNAL_DATA)
        self.assertNotIn("riches_route", ed_outrider.RESET_JOURNAL_DATA)
        self.assertNotIn("riches_bodies", ed_outrider.RESET_JOURNAL_DATA)

    def test_view_marks_bodies_from_the_journal(self):
        self.store()
        self.scan(-90, "Alpha Rich", 1001, 1, "Alpha Rich 1")
        self.mapped(-80, 1001, 1, "Alpha Rich 1")
        v = self.state.riches_view()
        r = v["route"]
        self.assertEqual((r["from"], r["to"], r["count"], r["at"], r["next"], r["first"]), ("Alpha Rich", "Gamma Rich", 3, 0, 1, 0))
        first = r["systems"][0]
        self.assertEqual([(b["name"], b["scanned"], b["mapped"], b["done"]) for b in first["bodies"]],
                         [("Alpha Rich 1", True, True, True), ("Alpha Rich 2 a", False, False, False)])
        self.assertEqual((first["left"], first["value"], first["value_left"], first["id"]), (1, 6500000, 2300000, "1001"))
        self.assertEqual([s["system"] for s in r["systems"]], ["Alpha Rich", "Beta Rich", "Gamma Rich"])
        self.assertEqual(v["defaults"]["radius"], 25)

    def test_copy_next(self):
        cb = types_ns(enabled=True, tool="wl-copy", copied=[])
        cb.copy = lambda text: cb.copied.append(text) or True
        self.state.clipboard = cb
        self.store()
        self.assertTrue(self.state.riches_copy_next(force=True))
        self.jump(-10, 1002, "Beta Rich", 20)
        self.assertTrue(self.state.riches_copy_next())
        self.assertFalse(self.state.riches_copy_next())   # once per arrival
        self.assertEqual(cb.copied, ["Beta Rich", "Gamma Rich"])

    def test_survey_summary(self):
        """The line under the tiles for a survey route (the payload's `survey`): the next system, where you are on it,
        what is left where you are; None without a route. The Highway's summary carries its plot time too (which of
        the two the line shows)."""
        self.assertIsNone(self.state.survey_summary())
        self.store()
        s = self.state.survey_summary()
        self.assertEqual((s["kind"], s["at"], s["index"], s["total"], s["complete"], s["off_route"]), ("riches", 0, 1, 2, False, False))
        self.assertEqual((s["next"]["name"], s["next"]["jumps"]), ("Beta Rich", 1))
        self.assertEqual(s["left_here"], 2)   # Alpha Rich's two bodies, neither scanned
        self.jump(-10, 1002, "Beta Rich", 20)
        s = self.state.survey_summary()
        self.assertEqual((s["at"], s["index"], s["next"]["name"], s["left_here"]), (1, 2, "Gamma Rich", 1))
        self.assertEqual(self.state.payload()["survey"]["at"], 1)   # in the page's payload

    def test_two_routes_one_clipboard(self):
        """A Highway route and a Road to Riches route at once: only the one plotted last copies its next system (one
        copy per jump), and the tab's map gets the whole Riches route's points."""
        cb = types_ns(enabled=True, tool="wl-copy", copied=[])
        cb.copy = lambda text: cb.copied.append(text) or True
        cb.info = lambda: {"enabled": True, "available": True}
        self.state.clipboard = cb
        self.assertIsNone(self.state.route_newest())
        self.store()
        self.assertEqual(self.state.route_newest(), "riches")
        self.assertEqual(self.state.riches_view()["route"]["points"], [[0, 0], [20, 0], [45, 0]])
        # a Highway route plotted after it takes the clipboard over
        ed_outrider.meta_set(self.db, "highway", {"id": "h", "created_ts": "2999-01-01T00:00:00Z", "at": 0})
        self.assertEqual(self.state.route_newest(), "highway")
        self.jump(-10, 1002, "Beta Rich", 20)
        self.assertFalse(self.state.riches_copy_next())
        self.assertEqual(cb.copied, [])
        # cleared: Road to Riches has it again
        ed_outrider.meta_set(self.db, "highway", None)
        self.assertEqual(self.state.route_newest(), "riches")
        # finished but left on screen: the other route has the clipboard (it went to neither)
        ed_outrider.meta_set(self.db, "highway", {"id": "h", "created_ts": "2000-01-01T00:00:00Z", "at": 0})
        rc = ed_outrider.meta_get(self.db, "riches")
        ed_outrider.meta_set(self.db, "riches", dict(rc, done_ts="2026-01-01T00:00:00Z"))
        self.assertEqual(self.state.route_newest(), "highway")

    # ---- the plot ----

    def spansh(self, script, knows=None):
        """Spansh answering `script`; its name search (State.highway_ends) knows every system, else only `knows`."""
        sp = ed_outrider.Spansh(self.db)
        sp.session = _HwSession(script)
        sp.system_record = unittest.mock.AsyncMock(side_effect=lambda n: {"name": n, "id64": 1, "x": 0.0, "y": 0.0, "z": 0.0}
                                                   if knows is None or n in knows else None)
        self.state.spansh = sp
        return sp

    def test_plot_from_a_system_spansh_does_not_know(self):
        """A fresh discovery Spansh does not know yet: the survey is plotted from a Spansh system near it (out of what
        Spansh sent about the neighbourhood) and starts with the jump there, nothing to survey in yours."""
        self.jump(-100, 900, "Fresh", -60)
        self.state.bases = {1001: ("spansh", {"name": "Alpha Rich", "x": 0.0, "y": 0.0, "z": 0.0})}
        sp = self.spansh([(200, {"job": "r1", "status": "ok", "result": RESULT})], knows=())

        async def go():
            with unittest.mock.patch.object(ed_outrider, "HIGHWAY_POLL_S", 0.01):
                self.state.riches_start_plot({"range": 42.5})
                await self.state.riches_task
        asyncio.run(go())
        p = self.state.riches_plotting
        self.assertEqual(p["state"], "done", p["error"])
        # nothing in range held: Spansh is asked around Fresh first (it knows nothing nearer here), then the plot
        self.assertEqual([c[:2] for c in sp.session.calls][:2], [("POST", ed_outrider.SPANSH_SEARCH), ("POST", ed_outrider.SPANSH_RICHES)])
        self.assertEqual(sp.session.calls[1][2]["from"], "Alpha Rich")
        self.assertIn("Spansh doesn't know Fresh yet: the route starts with a 60.0 ly jump to Alpha Rich", p["note"])
        self.assertIn("longer than this ship's 42.5 ly range", p["note"])
        meta, rows = self.state.riches_state()
        self.assertEqual([(r["system"], r["jumps"]) for r in rows[:2]], [("Fresh", 0), ("Alpha Rich", 2)])
        self.assertEqual((meta["at"], meta["stand_in"]), (0, p["note"]))
        self.assertEqual(riches.splice_survey(riches.riches_rows(RESULT), 42.5), riches.riches_rows(RESULT))   # nothing to do

    def test_plot_posts_form_fields_and_stores(self):
        self.jump(-100, 1001, "Alpha Rich", 0)
        sp = self.spansh([(200, {"job": "r1", "status": "queued"}), (200, {"job": "r1", "status": "ok", "result": RESULT})])

        async def go():
            with unittest.mock.patch.object(ed_outrider, "HIGHWAY_POLL_S", 0.01):
                out = self.state.riches_start_plot({"range": 42.5, "radius": 30, "min_value": 250000, "loop": True})
                await self.state.riches_task
            return out
        out = asyncio.run(go())
        self.assertEqual(out[1], 202)
        self.assertEqual(self.state.riches_plotting["state"], "done")
        method, url, fields = sp.session.calls[0]
        self.assertEqual((method, url), ("POST", ed_outrider.SPANSH_RICHES))
        # form fields are text on the wire
        self.assertEqual(fields, {"range": "42.5", "radius": "30.0", "max_results": "25", "max_distance": "50000",
                                  "min_value": "250000", "use_mapping_value": "1", "avoid_thargoids": "1", "loop": "1",
                                  "from": "Alpha Rich"})
        self.assertEqual(sp.session.calls[1][0], ed_outrider.SPANSH_RESULTS.format(job="r1"))   # the results are a GET
        meta, rows = self.state.riches_state()
        self.assertEqual((len(rows), meta["at"], meta["options"]["min_value"], meta["options"]["loop"]), (3, 0, 250000, True))

    def test_plot_failures_are_said(self):
        self.jump(-100, 1001, "Alpha Rich", 0)
        for script, why in (([(400, {"error": "Could not find starting system"})], "Spansh: Could not find starting system"),
                            ([(200, {"job": "e", "status": "ok", "result": []})], "found no route"),
                            ([(200, {"job": "e", "status": "ok", "result": {"unknown": 1}})], "shape Outrider does not know"),
                            ([ed_outrider.ClientError("refused")], "cannot be reached")):
            self.spansh(script)

            async def go():
                self.state.riches_start_plot({"range": 40})
                await self.state.riches_task
            asyncio.run(go())
            self.assertEqual(self.state.riches_plotting["state"], "failed")
            self.assertIn(why, self.state.riches_plotting["error"])
            self.assertEqual(self.state.riches_state(), (None, []))

    def test_plot_validation(self):
        self.jump(-100, 1001, "Alpha Rich", 0)

        async def go():
            return [self.state.riches_start_plot(b)[1] for b in (
                {}, {"range": "far"}, {"range": 0}, {"range": 40, "radius": 5000}, {"range": 40, "max_results": 0},
                {"range": 40, "loop": "yes"}, {"range": 40, "use_mapping_value": 1}, {"range": 40, "min_value": -1},
                {"from": "x" * 500, "range": 40}, {"range": True})]
        self.assertEqual(asyncio.run(go()), [400] * 10)
        self.assertIsNone(self.state.riches_task)

    def test_plot_range_from_the_ship_and_busy(self):
        self.j.handle({"event": "Loadout", "timestamp": "2026-01-02T00:00:00Z", "Ship": "krait_light", "ShipID": 7,
                       "ShipName": "S", "ShipIdent": "S-1", "HullHealth": 1.0, "UnladenMass": 410.5, "CargoCapacity": 8,
                       "MaxJumpRange": 58.4, "FuelCapacity": {"Main": 32.0, "Reserve": 0.63},
                       "Modules": [{"Slot": "FrameShiftDrive", "Item": "int_hyperdrive_overcharge_size5_class5", "Health": 1.0}]})
        self.jump(-100, 1001, "Alpha Rich", 0)
        sp = self.spansh([(200, {"job": "slow", "status": "queued"})])

        async def go():
            with unittest.mock.patch.object(ed_outrider, "HIGHWAY_POLL_S", 0.01):
                first = self.state.riches_start_plot({})
                second = self.state.riches_start_plot({})
                await asyncio.sleep(0.05)
                self.state.riches_clear()   # cancels the job that is still waiting
                await asyncio.sleep(0.02)
            return first, second
        first, second = asyncio.run(go())
        self.assertEqual((first[1], second[1]), (202, 409))
        self.assertAlmostEqual(float(sp.session.calls[0][2]["range"]), ed_outrider.fleet_range(
            self.state.fleet_ship(7)["figures"]), places=1)
        self.assertEqual(self.state.riches_plotting["error"], "cancelled")

    def test_endpoints(self):
        from aiohttp.test_utils import TestClient, TestServer
        self.jump(-100, 1001, "Alpha Rich", 0)
        self.spansh([(200, {"job": "e1", "status": "ok", "result": RESULT})])

        async def go():
            out = {}
            async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                r = await c.get("/api/riches")
                out["empty"] = await r.json()
                r = await c.post("/api/riches/plot", json={"range": 40})
                out["started"] = (r.status, await r.json())
                await self.state.riches_task
                r = await c.get("/api/riches")
                out["view"] = await r.json()
                r = await c.post("/api/riches/plot", json=[1])
                out["list"] = r.status
                r = await c.get("/api/riches", headers={"Sec-Fetch-Site": "cross-site"})
                out["cross"] = r.status
                r = await c.post("/api/riches/clear", headers={"Origin": "http://evil.example"})
                out["evil"] = r.status
                r = await c.post("/api/riches/clear")
                out["cleared"] = r.status
                r = await c.get("/api/riches")
                out["after"] = (await r.json())["route"]
            return out
        out = asyncio.run(go())
        self.assertIsNone(out["empty"]["route"])
        self.assertEqual(out["started"][0], 202)
        self.assertEqual(out["view"]["plotting"]["state"], "done")
        self.assertEqual(out["view"]["route"]["count"], 3)
        self.assertEqual((out["list"], out["cross"], out["evil"], out["cleared"], out["after"]), (400, 403, 403, 200, None))


if __name__ == "__main__":
    unittest.main()


def _exo_answer():
    import json
    import os
    with open(os.path.join(os.path.dirname(__file__), "fixtures", "spansh_exo.json"), encoding="utf-8") as f:
        return json.load(f)


class ExoRoute(unittest.TestCase):
    """Expressway to Exomastery in the survey slot (the author, 2026-10-07): Spansh's answer with each body's species,
    progress from the journal's samples (own_organic, by BodyID and species), its own lines and alert (`exo`), and the
    plot through its own endpoint. A real answer: tests/fixtures/spansh_exo.json (from Colonia, 2026-10-07)."""
    setUp = RichesRoute.setUp
    spansh = RichesRoute.spansh
    ts = hwy_ts
    jump = hwy_jump

    def store_exo(self):
        rows = riches.riches_rows(_exo_answer()["result"])
        self.jump(-100, rows[0]["id64"], rows[0]["system"], 0)
        self.state.riches_store(rows, {"options": {}, "kind": "exo"})
        return rows

    def analyse(self, s, addr, body_id, species):
        self.j.handle({"event": "ScanOrganic", "timestamp": self.ts(s), "ScanType": "Analyse", "SystemAddress": addr,
                       "Body": body_id, "Genus": "$Codex_Ent_X;", "Genus_Localised": species.split()[0],
                       "Species": "$Codex_Ent_" + species.replace(" ", "_") + ";", "Species_Localised": species})

    def test_rows_with_species(self):
        rows = riches.riches_rows(_exo_answer()["result"])
        b = rows[1]["bodies"][0]
        self.assertEqual((b["name"], b["body_id"], len(b["species"])), ("Eol Prou PX-T d3-813 ABC 2 e", 28, 7))
        self.assertEqual(b["species"][0], {"genus": "Frutexa", "species": "Frutexa Flammasis", "value": 10326000, "count": 5})
        self.assertEqual([x["value"] for x in b["species"]], sorted((x["value"] for x in b["species"]), reverse=True))
        self.assertEqual(riches._species([None, {"subtype": ""}, {"subtype": "Tubus Rosarium", "value": "x"}]),
                         [{"genus": None, "species": "Tubus Rosarium", "value": None, "count": None}])

    def test_arrival_progress_and_done(self):
        rows = self.store_exo()
        sys1 = rows[1]
        self.jump(-10, sys1["id64"], sys1["system"], 20)
        what, text = self.moments_exo()[-1]
        self.assertEqual(what, "next")
        self.assertTrue(text.startswith("14 species on two bodies here: the best, Frutexa Flammasis on ABC 2 e, about 10.3 million"), text)
        s = self.state.survey_summary()
        self.assertEqual((s["kind"], s["at"], s["left_here"]), ("exo", 1, 14))
        out = self.state.riches_view()["route"]
        self.assertEqual(out["kind"], "exo")
        row = out["systems"][1]
        self.assertEqual((row["left"], len(row["bodies"]), len(row["bodies"][0]["species"])), (14, 2, 7))
        # every species sampled on both bodies: the last says so, once, with the next stop
        n = 0
        for b in sys1["bodies"]:
            for x in b["species"]:
                n += 1
                self.analyse(-5 + n * 0.01, sys1["id64"], b["body_id"], x["species"])
        self.assertEqual(self.state.survey_summary()["left_here"], 0)
        self.assertEqual(self.moments_exo()[-1], ("done", f"Every species here is sampled. Next stop: {rows[2]['system']}."))
        self.assertEqual(len([m for m in self.moments_exo() if m[0] == "done"]), 1)
        self.assertEqual(self.state.riches_view()["route"]["systems"][1]["left"], 0)

    def moments_exo(self):
        return [(m["what"], m["text"]) for m in self.j.moments if m["kind"] == "exo"]

    def test_plot_exo(self):
        self.jump(-100, 1001, "Alpha Rich", 0)
        sp = self.spansh([(200, {"job": "e1", "status": "queued"}), (200, {"job": "e1", "status": "ok", "result": _exo_answer()["result"]})])
        self.assertEqual(self.state.riches_start_plot({"kind": "moon"})[1], 400)

        async def go():
            with unittest.mock.patch.object(ed_outrider, "HIGHWAY_POLL_S", 0.01):
                out = self.state.riches_start_plot({"kind": "exo", "range": 50, "radius": 25, "min_value": 1000000})
                await self.state.riches_task
            return out
        out = asyncio.run(go())
        self.assertEqual(out[1], 202)
        method, url, fields = sp.session.calls[0]
        self.assertEqual((method, url), ("POST", ed_outrider.SPANSH_EXO))
        self.assertNotIn("use_mapping_value", fields)   # Exomastery has none
        meta, rows = self.state.riches_state()
        self.assertEqual((meta["kind"], len(rows), len(rows[1]["bodies"][0]["species"])), ("exo", 3, 7))
        # one slot: a Road to Riches plot replaces it
        self.state.riches_store(riches.riches_rows(RESULT), {"options": {}})
        meta, rows = self.state.riches_state()
        self.assertEqual((meta.get("kind"), rows[0]["system"], self.db.execute("SELECT COUNT(*) FROM riches_species").fetchone()[0]),
                         (None, "Alpha Rich", 0))
