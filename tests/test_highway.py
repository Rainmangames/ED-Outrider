"""Unit tests: The Neutron Highway: plotting, following a route, the map and auto-target (fakes only).

Run all: python3 -m unittest discover tests (or scripts/verify.sh).
"""
import argparse
import json
import os
import time
import unittest
import unittest.mock

from support import (  # also puts the repository root on sys.path
    AUTHOR_BINDS, FakeGame, HWY_EXACT, _HwSession, _fake_evdev, hwy_jump, hwy_plot_exact, hwy_ts, make_controls,
    outrider_honk, types_ns, use_fake_time, user_docs,
)
import outrider.bio  # noqa: E402
import ed_outrider  # noqa: E402
import outrider.honk  # noqa: E402
import outrider.fsd  # noqa: E402
import outrider.highway  # noqa: E402




def spansh_knows(**ids):
    """A stand-in for Spansh.system_record: the systems Spansh knows (name lower-cased -> id64), on the x axis as in
    HWY_EXACT (Start at 0, End at 200)."""
    at = {100: 0.0, 105: 200.0, 201: 150.0, 202: 300.0}
    return unittest.mock.AsyncMock(side_effect=lambda n: {"name": n, "id64": ids[n.lower()], "x": at.get(ids[n.lower()], 0.0),
                                                          "y": 0.0, "z": 0.0} if n.lower() in ids else None)

class HighwayH1(unittest.TestCase):
    """Neutron Highway, batch H1 (server): fleet loadouts, Spansh plot jobs (mocked), the route, progress and detours,
    the spoken moments, the clipboard (mocked), the auto-target stub and the config. No network, no clipboard tool."""

    # a fixture exact-plotter result in Spansh's layout: neutron at the start, A and D; a refuel stop at C
    EXACT = HWY_EXACT
    # and a neutron-plotter one: waypoints with the jumps between them, no fuel
    NEUTRON = {"system_jumps": [
        {"system": "Start", "id64": 100, "x": 0, "y": 0, "z": 0, "distance_jumped": 0, "distance_left": 300, "jumps": 0,
         "neutron_star": True},
        {"system": "Waypoint", "id64": 201, "x": 150, "y": 0, "z": 0, "distance_jumped": 150, "distance_left": 150,
         "jumps": 3, "neutron_star": True},
        {"system": "Far End", "id64": 202, "x": 300, "y": 0, "z": 0, "distance_jumped": 150, "distance_left": 0,
         "jumps": 4, "neutron_star": False}], "total_jumps": 7}

    @staticmethod
    def loadout(ts, ship_id=7, name="Sample Ship", ship="krait_light", fsd="int_hyperdrive_overcharge_size5_class5",
                mods=None, unladen=410.5, r0=58.4, booster=True):
        fsd_mod = {"Slot": "FrameShiftDrive", "Item": fsd, "Health": 1.0}
        if mods:
            fsd_mod["Engineering"] = {"Modifiers": [{"Label": k, "Value": v, "OriginalValue": v / 2} for k, v in mods.items()]}
        modules = [fsd_mod] + ([{"Slot": "Slot03_Size5", "Item": "int_guardianfsdbooster_size5", "Health": 1.0}] if booster else [])
        return {"event": "Loadout", "timestamp": ts, "Ship": ship, "ShipID": ship_id, "ShipName": name, "ShipIdent": "SP-01",
                "HullHealth": 1.0, "UnladenMass": unladen, "CargoCapacity": 8, "MaxJumpRange": r0,
                "FuelCapacity": {"Main": 32.0, "Reserve": 0.63}, "Modules": modules}

    def setUp(self):
        import types
        self.db = ed_outrider.open_db(":memory:")
        self.addCleanup(self.db.close)
        self.j = ed_outrider.Journals(self.db)
        self.state = ed_outrider.State(self.db, self.j, types.SimpleNamespace(cached=lambda i: (None, None)), 25)
        self.now = time.time()

    ts = hwy_ts

    jump = hwy_jump

    plot_exact = hwy_plot_exact

    def hw_moments(self):
        return [(m["what"], m["text"]) for m in self.j.moments if m["kind"] == "highway"]

    # ---- fleet loadouts ----

    def test_fleet_loadouts_latest_per_ship(self):
        self.j.handle(self.loadout("2026-01-02T00:00:00Z"))
        self.j.handle(self.loadout("2026-01-03T00:00:00Z", ship_id=9, name="", ship="mandalay"))
        self.j.handle(self.loadout("2026-01-01T00:00:00Z", name="Old name"))   # older, read later: kept out
        self.j.handle(dict(self.loadout("2026-01-04T00:00:00Z", ship_id=11, ship="adder_taxi")))   # a shuttle: no
        fleet = self.state.fleet_list()
        self.assertEqual([(f["ship_id"], f["name"], f["type"], f["ts"][:10]) for f in fleet],
                         [(9, None, "mandalay", "2026-01-03"), (7, "Sample Ship", "krait_light", "2026-01-02")])
        f = fleet[1]["figures"]
        self.assertEqual((f["fuel_main"], f["fuel_reserve"], f["booster_ly"], f["fuel_power"], f["supercharge"], f["exact"]),
                         (32.0, 0.63, 10.5, 2.45, 4, True))
        self.assertAlmostEqual(fleet[1]["range"], ed_outrider.fleet_range(f), places=6)
        # a newer Loadout replaces the row
        self.j.handle(self.loadout("2026-01-05T00:00:00Z", name="Renamed", r0=60.0))
        self.assertEqual(self.state.fleet_ship(7)["name"], "Renamed")
        self.assertEqual(self.state.fleet_ship(7)["figures"]["max_range"], 60.0)

    def test_fleet_rebuilt_by_a_reread_and_route_kept(self):
        import shutil, tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp)
        here = os.path.dirname(os.path.abspath(__file__))
        jdir = os.path.join(tmp, "journals")
        shutil.copytree(os.path.join(here, "fixtures", "journals"), jdir)
        path = os.path.join(tmp, "x.sqlite")
        db = ed_outrider.open_db(path)
        j = ed_outrider.Journals(db)
        j.scan_dir(jdir)
        db.commit()
        rows = [tuple(r) for r in db.execute("SELECT ship_id, name, ship_type, ts FROM fleet_loadouts")]
        self.assertEqual(rows, [(7, "Sample Ship", "krait_light", "2026-09-20T19:00:05Z")])
        st = ed_outrider.State(db, j, None, 25)
        st.highway_store(ed_outrider.highway_rows("exact", self.EXACT), {"plotter": "exact"})
        db.close()
        # the parser version moves on: the journal-derived tables are rebuilt, the live-only route is not touched
        db = ed_outrider.open_db(path, rescan=True)
        self.assertEqual(db.execute("SELECT count(*) FROM fleet_loadouts").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT count(*) FROM highway_route").fetchone()[0], 6)
        self.assertIsNotNone(ed_outrider.meta_get(db, "highway"))
        j = ed_outrider.Journals(db)
        j.scan_dir(jdir)
        db.commit()
        self.assertEqual([tuple(r) for r in db.execute("SELECT ship_id, name, ship_type, ts FROM fleet_loadouts")], rows)
        db.close()
        self.assertIn("DELETE FROM fleet_loadouts", ed_outrider.RESET_JOURNAL_DATA)
        self.assertNotIn("highway", ed_outrider.RESET_JOURNAL_DATA)
        self.assertGreaterEqual(ed_outrider.PARSER_VERSION, 36)   # 35: fleet_loadouts; 36: their booster figures (F1)

    def test_fleet_figures(self):
        mul, p, b = 0.013, 2.45, 10.5
        drive = lambda mf: (mf / mul) ** (1 / p)
        # an engineered SCO 5A whose Modifiers agree with its MaxJumpRange: the Loadout's own figures are used
        r0 = 2000 / (400 + 6.1) * drive(6.1) + b
        f = ed_outrider.fleet_figures(self.loadout("2026-01-01T00:00:00Z", mods={"FSDOptimalMass": 2000, "MaxFuelPerJump": 6.1},
                                                   unladen=400, r0=r0))
        self.assertEqual((f["optimal_mass"], f["optimal_source"], f["max_fuel"], f["fuel_multiplier"]), (2000, "loadout", 6.1, 0.013))
        # stock figures that miss the Loadout's range (the fixture ship): the optimal mass that range implies, so a
        # plot made from these figures has the game's range
        f = ed_outrider.fleet_figures(self.loadout("2026-01-01T00:00:00Z"))
        self.assertEqual((f["optimal_source"], f["max_fuel"]), ("range", 5.2))
        self.assertAlmostEqual(f["optimal_mass"] / (410.5 + 5.2) * drive(5.2) + b, 58.4, places=2)
        # stock and right: kept
        r0 = 1175 / (300 + 5.2) * drive(5.2)
        f = ed_outrider.fleet_figures(self.loadout("2026-01-01T00:00:00Z", unladen=300, r0=r0, booster=False))
        self.assertEqual((f["optimal_mass"], f["optimal_source"], f["booster_ly"]), (1175, "stock", 0))
        # MaxJumpRange WITHOUT the booster's ly (review F1: seven of the author's Loadouts): the booster powered off,
        # or a Loadout written in outfitting. The engineered optimal mass stands (it used to be swapped for a smaller
        # one, so Spansh simulated ~1.5x the fuel per jump); off: no booster; on: its ly on top of the range
        r_drive = 2000 / (400 + 6.1) * drive(6.1)
        for on, boost_ly, rng in ((False, 0, r_drive), (True, b, r_drive + b)):
            ev = self.loadout("2026-01-01T00:00:00Z", mods={"FSDOptimalMass": 2000, "MaxFuelPerJump": 6.1}, unladen=400,
                              r0=r_drive)
            ev["Modules"][1]["On"] = on
            f = ed_outrider.fleet_figures(ev)
            self.assertEqual((f["optimal_mass"], f["optimal_source"], f["booster_ly"]), (2000, "loadout", boost_ly), on)
            self.assertAlmostEqual(f["max_range"], rng, places=2)
            self.assertAlmostEqual(ed_outrider.fleet_range(f, fuel=6.1), rng, places=1)
        # the Caspian's Mk II: p 2.5025 (the fuel model's), supercharge x6; a size 8 SCO: p 2.90, x4
        mk2 = ed_outrider.fleet_figures(self.loadout("2026-01-01T00:00:00Z",
                                                     fsd="int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii"))
        self.assertEqual((mk2["fuel_power"], mk2["supercharge"], mk2["max_fuel"], mk2["exact"]), (2.5025, 6, 6.8, True))
        s8 = ed_outrider.fleet_figures(self.loadout("2026-01-01T00:00:00Z", fsd="int_hyperdrive_overcharge_size8_class5"))
        self.assertEqual((s8["fuel_power"], s8["supercharge"], s8["max_fuel"], s8["fuel_multiplier"]), (2.90, 4, 20.7, 0.013))
        std = ed_outrider.fleet_figures(self.loadout("2026-01-01T00:00:00Z", fsd="int_hyperdrive_size2_class1_free"))
        self.assertEqual((std["fuel_power"], std["max_fuel"], std["fuel_multiplier"]), (2.0, 0.6, 0.011))
        # a drive no table knows: no exact plot (the neutron plotter still works from the range)
        new = ed_outrider.fleet_figures(self.loadout("2026-01-01T00:00:00Z", fsd="int_hyperdrive_overcharge_size9_class5_new"))
        self.assertFalse(new["exact"])
        self.assertIsNotNone(ed_outrider.fleet_range(new))

    # ---- Spansh's routes and plot jobs ----

    def test_highway_rows(self):
        rows = ed_outrider.highway_rows("exact", self.EXACT)
        self.assertEqual([(r["system"], r["id64"], r["neutron"], r["refuel"], r["jumps"]) for r in rows][:4],
                         [("Start", 100, 1, 0, 0), ("Neu A", 101, 1, 0, 1), ("Bridge B", 102, 0, 0, 1), ("Scoop C", 103, 0, 1, 1)])
        self.assertEqual((rows[1]["fuel_used"], rows[1]["fuel_left"], rows[1]["remaining"]), (5.1, 26.9, 150))
        n = ed_outrider.highway_rows("neutron", self.NEUTRON)
        self.assertEqual([(r["system"], r["jumps"], r["neutron"], r["fuel_used"]) for r in n],
                         [("Start", 0, 1, None), ("Waypoint", 3, 1, None), ("Far End", 4, 0, None)])
        for bad in (None, {}, {"jumps": "x"}, {"jumps": [EXACT_ROW := {"name": "Only", "id64": 1}]}):
            with self.assertRaises(ed_outrider.HighwayError):
                ed_outrider.highway_rows("exact", bad)
        self.assertEqual(EXACT_ROW["name"], "Only")

    def run_plot(self, script, timeout=5.0, poll=0.01):
        import asyncio
        sp = ed_outrider.Spansh(self.db)
        sp.session = _HwSession(script)

        async def go():
            return await sp.plot(ed_outrider.SPANSH_GENERIC_ROUTE, {"source": "Start"}, poll=poll, timeout=timeout)
        return sp.session, asyncio.run(go())

    def test_plot_job_queued_then_done(self):
        s, result = self.run_plot([(200, {"job": "abc-1", "status": "queued"}), (202, {"job": "abc-1", "status": "queued"}),
                                   (200, {"job": "abc-1", "status": "ok", "result": self.EXACT})])
        self.assertEqual(result, self.EXACT)
        self.assertEqual([c[0] for c in s.calls], [ed_outrider.SPANSH_GENERIC_ROUTE] + [ed_outrider.SPANSH_RESULTS.format(job="abc-1")] * 2)
        self.assertEqual(s.calls[0][1], {"source": "Start"})

    def test_plot_job_errors(self):
        import asyncio
        with self.assertRaisesRegex(ed_outrider.HighwayError, "Spansh: Could not find starting system"):
            self.run_plot([(400, {"error": "Could not find starting system"})])
        with self.assertRaisesRegex(ed_outrider.HighwayError, "Spansh: no route"):   # the job itself fails later
            self.run_plot([(200, {"job": "j", "status": "queued"}), (200, {"status": "error", "error": "no route"})])
        with self.assertRaisesRegex(ed_outrider.HighwayError, "HTTP 503"):
            self.run_plot([(503, ValueError("not JSON"))])
        with self.assertRaisesRegex(ed_outrider.HighwayError, "cannot be reached"):
            self.run_plot([ed_outrider.ClientError("connection refused")])
        with self.assertRaisesRegex(ed_outrider.HighwayError, "cannot be reached"):
            self.run_plot([asyncio.TimeoutError()])
        t = time.monotonic()
        with self.assertRaisesRegex(ed_outrider.HighwayError, "had not finished the route after 0.1 s"):
            s, _ = self.run_plot([(200, {"job": "slow", "status": "queued"})], timeout=0.1, poll=0.03)
        self.assertLess(time.monotonic() - t, 2)
        sp = ed_outrider.Spansh(self.db)   # never started (offline): a clear error, no request
        with self.assertRaisesRegex(ed_outrider.HighwayError, "cannot be reached"):
            asyncio.run(sp.plot(ed_outrider.SPANSH_ROUTE, {}))

    def test_endpoints(self):
        import asyncio
        from aiohttp.test_utils import TestClient, TestServer
        self.j.handle(self.loadout("2026-01-02T00:00:00Z"))
        self.j.handle({"event": "Cargo", "timestamp": "2026-01-02T00:00:01Z", "Vessel": "Ship", "Count": 3})
        self.jump(-100, 100, "Start", 0)
        sp = ed_outrider.Spansh(self.db)
        sp.session = _HwSession([(200, {"job": "e1", "status": "queued"}),
                                 (200, {"job": "e1", "status": "ok", "result": self.EXACT})])
        sp.system_names = unittest.mock.AsyncMock(return_value=["Sol", "Solati"])
        sp.system_record = spansh_knows(start=100, end=105, **{"far end": 202})
        self.state.spansh = sp
        cb = types_ns(enabled=True, tool="wl-copy", copied=[])
        cb.copy = lambda text: cb.copied.append(text) or True
        cb.info = lambda: {"enabled": True, "available": True, "tool": "wl-copy", "why": None, "last": None}
        self.state.clipboard = cb

        async def go():
            out = {}
            with unittest.mock.patch.object(ed_outrider, "HIGHWAY_POLL_S", 0.01):
                async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                    r = await c.get("/api/highway")
                    out["empty"] = await r.json()
                    r = await c.post("/api/highway/plot", json={"to": "End", "injections": True})
                    out["started"] = (r.status, await r.json())
                    r = await c.post("/api/highway/plot", json={"to": "End"})
                    out["busy"] = r.status
                    await self.state.highway_task
                    await asyncio.sleep(0.05)   # the clipboard copy runs on the executor
                    r = await c.get("/api/highway")
                    out["view"] = await r.json()
                    r = await c.get("/api/nearby")
                    out["payload"] = (await r.json())["highway"]
                    for body in ({"plotter": "x", "to": "End"}, {"to": ""}, {"plotter": "exact", "to": "End", "ship_id": 99},
                                 {"plotter": "neutron", "to": "End", "ship_id": None}, {"plotter": "neutron", "to": "End", "range": "far"},
                                 {"plotter": "neutron", "to": "End", "range": 50, "supercharge_multiplier": 5}, [1, 2]):
                        r = await c.post("/api/highway/plot", json=body)
                        out.setdefault("bad", []).append(r.status)
                    r = await c.get("/api/highway/systems?q=Sol")
                    out["suggest"] = await r.json()
                    r = await c.get("/api/highway/systems?q=S")
                    out["short"] = r.status
                    r = await c.get("/api/highway", headers={"Sec-Fetch-Site": "cross-site"})
                    out["cross"] = r.status
                    r = await c.post("/api/highway/clear", headers={"Origin": "http://evil.example"})
                    out["evil"] = r.status
                    sp.session = _HwSession([(200, {"job": "n1", "status": "ok", "result": self.NEUTRON})])
                    r = await c.post("/api/highway/plot", json={"plotter": "neutron", "to": "Far End", "range": 50.5})
                    await self.state.highway_task
                    out["neutron_params"] = sp.session.calls[0]
                    r = await c.post("/api/highway/clear")
                    out["cleared"] = r.status
                    r = await c.get("/api/highway")
                    out["after"] = (await r.json())["route"]
            return out
        out = asyncio.run(go())
        self.assertIsNone(out["empty"]["route"])
        self.assertEqual([f["ship_id"] for f in out["empty"]["fleet"]], [7])
        self.assertEqual(out["started"][0], 202)
        self.assertEqual(out["busy"], 409)
        v = out["view"]
        self.assertEqual(v["plotting"]["state"], "done")
        r = v["route"]
        self.assertEqual((r["from"], r["to"], r["count"], r["at"], r["furthest"], r["plotter"], r["total_ly"]),
                         ("Start", "End", 6, 0, 0, "exact", 200))
        self.assertEqual([x["system"] for x in r["ahead"]], ["Neu A", "Bridge B", "Scoop C", "Neu D", "End"])
        self.assertEqual([(x["i"], x["system"], x["neutron"], x["fuel_left"]) for x in r["done"]], [(0, "Start", True, 32.0)])
        self.assertEqual(r["neutrons"], [0, 1, 4])
        self.assertEqual(r["options"], {"cargo": 3, "injections": True, "exclude_secondary": False, "supercharged": False,
                                        "no_neutrons": False})
        self.assertEqual(cb.copied, ["Neu A", "Waypoint"])   # each new plot from where you are: its first hop is copied
        p = out["payload"]
        self.assertEqual((p["next"]["name"], p["next"]["neutron"], p["next"]["distance"], p["index"], p["total"],
                          p["refuel_in"], p["boost_here"], p["off_route"], p["jumps_left"]),
                         ("Neu A", True, 50.0, 1, 5, 3, True, False, 5))
        self.assertEqual(out["bad"], [400] * 7)
        self.assertEqual(out["suggest"], {"q": "Sol", "values": ["Sol", "Solati"]})
        self.assertEqual((out["short"], out["cross"], out["evil"]), (400, 403, 403))
        self.assertEqual(out["neutron_params"], (ed_outrider.SPANSH_ROUTE, {"from": "Start", "to": "Far End", "range": 50.5,
                                                                            "efficiency": 60, "supercharge_multiplier": 4}))
        self.assertEqual((out["cleared"], out["after"]), (200, None))

    def test_exact_plot_params(self):
        # the exact plotter's request carries the ship's figures under Spansh's names (as Auto_Neutron's request has them)
        import asyncio
        self.j.handle(self.loadout("2026-01-02T00:00:00Z", fsd="int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii"))
        sp = ed_outrider.Spansh(self.db)
        sp.session = _HwSession([(200, {"job": "e", "status": "ok", "result": self.EXACT})])
        sp.system_record = spansh_knows(start=100, end=105)
        self.state.spansh = sp

        async def go():
            out, status = self.state.highway_start_plot({"from": "Start", "to": "End", "cargo": 4, "exclude_secondary": True})
            await self.state.highway_task
            return status
        self.assertEqual(asyncio.run(go()), 202)
        url, params = sp.session.calls[0]
        f = self.state.fleet_ship(7)["figures"]
        self.assertEqual(url, ed_outrider.SPANSH_GENERIC_ROUTE)
        self.assertEqual(params, {"source": 100, "destination": 105, "is_supercharged": 0, "use_supercharge": 1,
                                  "use_injections": 0, "exclude_secondary": 1, "fuel_power": 2.5025,
                                  "fuel_multiplier": f["fuel_multiplier"], "optimal_mass": f["optimal_mass"],
                                  "supercharge_multiplier": 6, "base_mass": round(410.5 + 0.63, 3), "tank_size": 32.0,
                                  "internal_tank_size": 0.63, "max_fuel_per_jump": 6.8, "range_boost": 10.5, "cargo": 4})
        # a failed plot keeps the route you had and says why
        sp.session = _HwSession([(400, {"error": "Could not find system End"})])

        async def fail():
            self.state.highway_start_plot({"from": "Start", "to": "End"})
            await self.state.highway_task
        asyncio.run(fail())
        self.assertEqual(self.state.highway_plotting["state"], "failed")
        self.assertEqual(self.state.highway_plotting["error"], "Spansh: Could not find system End")
        self.assertEqual(self.state.highway_view()["route"]["count"], 6)

    def test_exact_plot_without_neutrons(self):
        """no_neutrons: Spansh's exact plotter is told not to use the neutron supercharge (regular jumps only), and the
        route remembers it was asked for."""
        import asyncio
        self.j.handle(self.loadout("2026-01-02T00:00:00Z", fsd="int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii"))
        sp = ed_outrider.Spansh(self.db)
        sp.session = _HwSession([(200, {"job": "e", "status": "ok", "result": self.EXACT})])
        sp.system_record = spansh_knows(start=100, end=105)
        self.state.spansh = sp

        async def go():
            out, status = self.state.highway_start_plot({"from": "Start", "to": "End", "no_neutrons": True})
            await self.state.highway_task
            return status
        self.assertEqual(asyncio.run(go()), 202)
        params = sp.session.calls[0][1]
        self.assertEqual((params["use_supercharge"], params["is_supercharged"]), (0, 0))
        self.assertIs(self.state.highway_view()["route"]["options"]["no_neutrons"], True)

    def test_exact_plot_sends_id64s(self):
        """Spansh's exact plotter answers "Unable to find route" to system names; it takes id64s (found in game
        2026-10-03, the neutron plotter still takes names). Both ends are looked up in Spansh's search (the exact name,
        any case): a system known here is not necessarily one Spansh knows."""
        import asyncio
        self.j.handle(self.loadout("2026-01-02T00:00:00Z", fsd="int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii"))
        self.jump(-100, 100, "Start", 0)
        sp = ed_outrider.Spansh(self.db)
        self.state.spansh = sp
        search = ed_outrider.SPANSH_SYSTEM_SEARCH
        here = (200, {"count": 1, "results": [{"id64": 100, "name": "Start", "x": 0, "y": 0, "z": 0}]})
        found = (200, {"count": 2, "results": [{"id64": 1, "name": "Thailoea AA-A h0 X", "x": 0, "y": 0, "z": 0},
                                               {"id64": 18207037532889, "name": "Thailoea AA-A H0", "x": 0, "y": 0, "z": 0}]})
        none = (200, {"count": 0, "results": []})
        ok = (200, {"job": "e", "status": "ok", "result": self.EXACT})

        def plot(body, script):
            sp.session = _HwSession(script)

            async def go():
                self.state.highway_start_plot(dict({"to": "End"}, **body))
                await self.state.highway_task
            asyncio.run(go())
            p = self.state.highway_plotting
            return p["state"], p["error"], sp.session.calls
        # here to a system Spansh's search knows: both looked up, then the plot by id64
        state, err, calls = plot({"to": "thailoea aa-a h0"}, [here, found, ok])
        self.assertEqual((state, err), ("done", None))
        self.assertEqual(calls[:2], [(search, {"q": "Start"}), (search, {"q": "thailoea aa-a h0"})])
        self.assertEqual((calls[2][0], calls[2][1]["source"], calls[2][1]["destination"]),
                         (ed_outrider.SPANSH_GENERIC_ROUTE, 100, 18207037532889))
        # nobody knows the destination; a typed start nobody knows (Spansh or here) says so
        self.assertEqual(plot({"to": "Nowhere"}, [here, none])[:2], ("failed", "Spansh knows no system called Nowhere"))
        state, err, calls = plot({"from": "Brand New", "to": "Start"}, [none, here])
        self.assertEqual((state, err), ("failed", ed_outrider.highway_not_yet("Brand New")))
        # Spansh's own answer to the plot stands
        state, err, _ = plot({"to": "Start"}, [here, here, (400, {"error": "Unable to find route"})])
        self.assertEqual(err, "Spansh: Unable to find route")
        # the neutron plotter keeps sending names
        far = (200, {"results": [{"id64": 202, "name": "Far End", "x": 300, "y": 0, "z": 0}]})
        state, err, calls = plot({"plotter": "neutron", "to": "Far End", "range": 50},
                                 [here, far, (200, {"job": "n", "status": "ok", "result": self.NEUTRON})])
        self.assertEqual((calls[2][1]["from"], calls[2][1]["to"], len(calls)), ("Start", "Far End", 3))
        # offline (Spansh never started): a clear error before any request
        with self.assertRaisesRegex(ed_outrider.HighwayError, "cannot be reached"):
            asyncio.run(ed_outrider.Spansh(self.db).system_id64("Nowhere"))

    # ---- progress, detours, the moments ----

    def test_nearest_is_the_closest_route_system_passed_or_not(self):
        """Off the route, the nearest route system is the CLOSEST one, passed or not (the author's rule): getting back
        on the highway is the fastest way on. The same side trip twice gives the same answer (review F7: the cache
        used to keep a stale "not yet passed" answer)."""
        self.plot_exact()
        self.jump(1, 101, "Neu A", 50)
        self.jump(2, 999, "Side Trip", 60)
        p = self.state.highway_summary()
        self.assertEqual((p["nearest"]["name"], p["nearest"]["distance"]), ("Neu A", 10.0))
        for s, (id64, name, x) in enumerate(((102, "Bridge B", 80), (103, "Scoop C", 100), (104, "Neu D", 150)), 3):
            self.jump(s, id64, name, x)
        self.jump(6, 999, "Side Trip", 60)   # the same side trip, now far past Neu A: still the closest route system
        p = self.state.highway_summary()
        self.assertEqual((p["off_route"], p["furthest"], p["nearest"]["name"], p["nearest"]["distance"]),
                         (True, 4, "Neu A", 10.0))
        self.jump(7, 997, "New Side Trip", 55)   # a new system by Neu A, long passed: Neu A, not Neu D 95 ly ahead
        p = self.state.highway_summary()
        self.assertEqual((p["nearest"]["name"], p["nearest"]["distance"], p["nearest"]["index"]), ("Neu A", 5.0, 1))
        self.jump(8, 998, "Other Side", 145)   # next to Neu D: that one
        p = self.state.highway_summary()
        self.assertEqual((p["nearest"]["name"], p["nearest"]["distance"], p["nearest"]["index"]), ("Neu D", 5.0, 4))

    def test_a_jump_read_after_the_plot_still_counts(self):
        """A jump the game wrote while the plot finished (in the same second, or just before) is read by the next
        tick: it still moves the route (review F8: it was dropped against the plot's wall-clock time)."""
        self.plot_exact()   # at Start, arrived 100 s ago
        self.jump(-0.5, 101, "Neu A", 50)   # written half a second before the route was stored, read now
        p = self.state.highway_summary()
        self.assertEqual((p["at"], p["next"]["name"]), (1, "Bridge B"))
        self.assertEqual(self.hw_moments()[-1][0], "next")

    def test_plotted_in_hyperspace_the_arrival_at_the_start_joins(self):
        """Plotted while jumping to the route's start (review F8, second case): the arrival, read after the route
        was stored, joins the route there."""
        self.jump(-100, 990, "Before", -30)
        rows = ed_outrider.highway_rows("exact", self.EXACT)
        self.state.highway_store(rows, {"plotter": "exact", "options": {}, "ship": None})
        self.jump(-0.3, 100, "Start", 0)
        hw = ed_outrider.meta_get(self.db, "highway")
        self.assertEqual((hw["at"], hw["furthest"]), (0, 0))
        self.assertIsNotNone(hw["arrival_ts"])
        # a line older than the position the route was plotted at (a re-read) still never moves it
        self.jump(-300, 104, "Neu D", 150)
        self.assertEqual(ed_outrider.meta_get(self.db, "highway")["at"], 0)

    def test_a_respawn_off_the_route_is_a_detour(self):
        """Died and rebought at a station far back (Died, Resurrect, Location there): the Highway says you are off the
        route and where the nearest route system is (review F10: it said you were still at Neu A). A relog where you
        already were changes nothing."""
        self.plot_exact()
        self.jump(1, 101, "Neu A", 50)
        self.j.handle({"event": "Died", "timestamp": self.ts(2)})
        self.j.handle({"event": "Resurrect", "timestamp": self.ts(3), "Option": "rebuy", "Cost": 1000, "Bankrupt": False})
        self.j.handle({"event": "Location", "timestamp": self.ts(4), "StarSystem": "Far Station", "SystemAddress": 777,
                       "StarPos": [500, 0, 0], "Docked": True})
        p = self.state.highway_summary()
        self.assertEqual((p["at"], p["off_route"], p["nearest"]["name"]), (None, True, "End"))
        self.assertEqual(self.hw_moments()[-1][0], "off_route")
        # back in a route system and logging out and in there: you are at it, once
        self.jump(5, 104, "Neu D", 150)
        n = len(self.hw_moments())
        self.j.handle({"event": "Location", "timestamp": self.ts(6), "StarSystem": "Neu D", "SystemAddress": 104,
                       "StarPos": [150, 0, 0]})
        self.assertEqual((self.state.highway_summary()["at"], len(self.hw_moments())), (4, n))

    def test_progress_detour_resume_complete(self):
        hw = self.plot_exact()
        self.assertEqual((hw["at"], hw["furthest"]), (0, 0))
        self.jump(1, 101, "Neu A", 50)
        self.assertEqual(self.hw_moments()[-1], ("next", "Next Neutron Highway Stop: Bridge B, with two jumps left to refuel. "
                                                         "Boost your FSD to continue."))
        self.jump(2, 999, "Elsewhere", 85)
        self.jump(3, 998, "Elsewhere Two", 86)   # still off: said once
        self.assertEqual(self.hw_moments()[-1], ("off_route", "Off route: detour."))
        self.assertEqual(len(self.hw_moments()), 2)
        p = self.state.highway_summary()
        self.assertEqual((p["off_route"], p["at"], p["furthest"], p["nearest"]["name"], p["nearest"]["distance"], p["index"]),
                         (True, None, 1, "Bridge B", 6.0, 2))
        # back at any route system (not a neutron one: a bridging jump counts)
        self.jump(4, 102, "Bridge B", 80, kind="CarrierJump")
        self.assertEqual(self.hw_moments()[-1], ("back", "Back on the highway. Next Neutron Highway Stop: Scoop C, "
                                                         "with one jump left to refuel."))
        self.jump(5, 103, "Scoop C", 100)
        self.assertEqual(self.hw_moments()[-1], ("next", "Refuel here before continuing. Next Neutron Highway Stop: Neu D."))
        # back along the route: the position moves back, the furthest point stays
        self.jump(6, 101, "Neu A", 50)
        p = self.state.highway_summary()
        self.assertEqual((p["at"], p["furthest"], p["next"]["name"], p["refuel_in"]), (1, 3, "Bridge B", 2))
        self.jump(7, 105, "End", 200)
        self.assertEqual(self.hw_moments()[-1], ("complete", "Highway complete."))
        n = len(self.hw_moments())
        self.jump(8, 997, "After", 250)   # finished: no detour, the route stays until cleared
        self.assertEqual(len(self.hw_moments()), n)
        p = self.state.highway_summary()
        self.assertEqual((p["complete"], p["off_route"], p["next"], p["furthest"]), (True, False, None, 5))
        self.state.highway_clear()
        self.assertIsNone(self.state.highway_summary())
        self.jump(9, 101, "Neu A", 50)
        self.assertEqual(len(self.hw_moments()), n)

    def test_moments_only_live_and_never_on_a_reread(self):
        self.plot_exact(start=-3000)
        # an arrival older than the plot (a late legacy folder): nothing moves
        self.jump("2020-01-01T00:00:00Z", 103, "Scoop C", 100)
        self.assertEqual(ed_outrider.meta_get(self.db, "highway")["at"], 0)
        # a catch-up after the plot but not just now (the server was down): progress, no words
        hw = ed_outrider.meta_get(self.db, "highway")
        hw["created_ts"] = self.ts(-3600)
        ed_outrider.meta_set(self.db, "highway", hw)
        self.jump(-1800, 101, "Neu A", 50)
        self.jump(-1700, 999, "Elsewhere", 60)
        hw = ed_outrider.meta_get(self.db, "highway")
        self.assertEqual((hw["at"], hw["furthest"], bool(hw["off_route"]), self.hw_moments()), (None, 1, True, []))
        self.jump(1, 102, "Bridge B", 80)
        self.assertEqual([w for w, _ in self.hw_moments()], ["back"])
        # a journal re-read replays those arrivals: the route's progress and the moments stay as they were
        before = ed_outrider.meta_get(self.db, "highway")
        self.db.executescript(ed_outrider.RESET_JOURNAL_DATA)
        self.j.reload()
        for s, i, name, x in ((-1800, 101, "Neu A", 50), (-1700, 999, "Elsewhere", 60), (1, 102, "Bridge B", 80)):
            self.jump(s, i, name, x)
        self.assertEqual(ed_outrider.meta_get(self.db, "highway"), before)
        self.assertEqual(len(self.hw_moments()), 1)

    def test_failed_tick_does_not_repeat_the_moment(self):
        self.plot_exact()
        cp = self.j.checkpoint()
        self.jump(1, 101, "Neu A", 50)
        self.assertEqual(len(self.hw_moments()), 1)
        self.db.rollback()   # the tick failed: rolled back and reloaded, its lines read again
        self.j.reload()
        self.j.restore(cp)
        self.jump(1, 101, "Neu A", 50)
        self.assertEqual(len(self.hw_moments()), 1)
        self.assertEqual(ed_outrider.meta_get(self.db, "highway")["at"], 1)

    def test_summary_before_joining(self):
        # plotted from elsewhere (you are not at its start yet): the next stop is the start, with nothing done, nothing
        # flown, no refuel counted from a row you are not at (found with a real Spansh plot: ly_left read 0)
        self.jump(-100, 555, "Far Away", -40)
        self.state.highway_store(ed_outrider.highway_rows("exact", self.EXACT), {"plotter": "exact"})
        p = self.state.highway_summary()
        self.assertEqual((p["at"], p["index"], p["next"]["name"], p["next"]["distance"], p["ly_left"], p["refuel_in"],
                          p["jumps_left"], p["off_route"]), (None, 0, "Start", 40.0, None, None, 5, False))
        self.jump(1, 999, "Elsewhere", -30)   # not joined yet: flying to its start is no detour
        self.assertEqual((self.hw_moments(), self.state.highway_summary()["off_route"]), ([], False))
        self.jump(2, 100, "Start", 0)
        self.assertEqual(self.hw_moments()[-1][0], "next")
        self.assertEqual(self.state.highway_summary()["ly_left"], 200)

    def test_highway_text(self):
        rows = ed_outrider.highway_rows("exact", self.EXACT)
        self.assertEqual(ed_outrider.highway_text(rows, 0),
                         "Next Neutron Highway Stop: Neu A, with three jumps left to refuel. Boost your FSD to continue.")
        self.assertEqual(ed_outrider.highway_text(rows, 3), "Refuel here before continuing. Next Neutron Highway Stop: Neu D.")
        self.assertEqual(ed_outrider.highway_text(rows, 4), "Next Neutron Highway Stop: End. Boost your FSD to continue.")
        far = [dict(r, refuel=0) for r in rows]
        far[-1]["refuel"] = 1
        far = far[:1] + [dict(far[1], id64=i) for i in range(7)] + far[1:]   # the refuel stop is 11 jumps away
        self.assertNotIn("refuel", ed_outrider.highway_text(far, 0))
        self.assertEqual(ed_outrider.highway_match(rows, None, "neu d"), 4)   # by name when there is no id64
        twice = rows + [dict(rows[1])]
        self.assertEqual((ed_outrider.highway_match(twice, 101, "", 0), ed_outrider.highway_match(twice, 101, "", 3)), (1, 6))

    # ---- the desktop clipboard (never a real tool) ----

    def test_clipboard(self):
        ran = []

        def run(argv, **kw):
            ran.append((argv, kw))
            return types_ns(returncode=0)
        which = lambda name: "/usr/bin/" + name
        cb = ed_outrider.Clipboard(True, which=which, run=run, env={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0"})
        self.assertEqual((cb.tool, cb.argv), ("wl-copy", ["wl-copy"]))
        self.assertTrue(cb.copy("Neu A"))
        argv, kw = ran[0]
        self.assertEqual((argv, kw["input"], kw.get("shell", False), kw["timeout"]), (["wl-copy"], b"Neu A", False, 5))
        x11 = ed_outrider.Clipboard(True, which=lambda n: n == "xclip" and "/usr/bin/xclip", run=run, env={"DISPLAY": ":0"})
        self.assertEqual(x11.argv, ["xclip", "-selection", "clipboard"])
        none = ed_outrider.Clipboard(True, which=which, run=run, env={})
        self.assertEqual((none.tool, none.info()["available"]), (None, False))
        self.assertIn("wl-copy", none.info()["why"])
        self.assertFalse(none.copy("X"))
        off = ed_outrider.Clipboard(False, which=which, run=run, env={"DISPLAY": ":0"})
        self.assertFalse(off.copy("X"))
        self.assertEqual(len(ran), 1)
        bad = ed_outrider.Clipboard(True, which=which, run=lambda *a, **k: types_ns(returncode=1), env={"DISPLAY": ":0"})
        self.assertFalse(bad.copy("X"))
        self.assertEqual(bad.info()["last"]["error"], "xclip failed")

        def boom(*a, **k):
            raise OSError("no such file")
        self.assertFalse(ed_outrider.Clipboard(True, which=which, run=boom, env={"DISPLAY": ":0"}).copy("X"))

    def test_clipboard_on_live_arrivals_only(self):
        copied = []
        self.state.clipboard = types_ns(enabled=True, tool="xclip", copy=lambda t: copied.append(t) or True)
        self.plot_exact()
        self.state.highway_copy_next()   # the plot's own copy is the page's job (force); nothing new here
        self.assertEqual(copied, [])
        self.jump(1, 101, "Neu A", 50)
        self.state.highway_copy_next()
        self.state.highway_copy_next()   # once per arrival
        self.assertEqual(copied, ["Bridge B"])
        self.jump(2, 999, "Elsewhere", 60)   # off route: nothing to copy
        self.state.highway_copy_next()
        self.jump(3, 105, "End", 200)        # the end: nothing next
        self.state.highway_copy_next()
        self.assertEqual(copied, ["Bridge B"])
        # an arrival read late (catch-up): not copied
        self.state.highway_clear()
        self.plot_exact(start=-3000)
        hw = ed_outrider.meta_get(self.db, "highway")
        hw["created_ts"] = self.ts(-3600)
        ed_outrider.meta_set(self.db, "highway", hw)
        self.jump(-1800, 101, "Neu A", 50)
        self.state.highway_copy_next()
        self.assertEqual(copied, ["Bridge B"])
        # switched off in the config
        self.jump(4, 102, "Bridge B", 80)
        self.state.clipboard.enabled = False
        self.state.highway_copy_next()
        self.assertEqual(copied, ["Bridge B"])

    # ---- too much fuel for the next jump; conservative range ----

    # the author's Caspian Explorer (its real Loadout): the SCO Mk II (p 2.5025, multiplier 0.011, x6), an engineered
    # optimal mass, a size 5 Guardian booster, a 160 t tank and a 1.14 t reservoir: the game's MaxJumpRange 82.97 ly
    CASPIAN = {"unladen": 1295.875, "max_range": 82.97, "booster_ly": 10.5, "max_fuel": 6.8, "fuel_power": 2.5025,
               "fuel_multiplier": 0.011, "optimal_mass": 7238.5, "fuel_main": 160.0, "fuel_reserve": 1.14}

    def caspian_loadout(self, ts, ship_id=39):
        ev = self.loadout(ts, ship_id=ship_id, name="Wanderer II", ship="explorer_nx",
                          fsd="int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii",
                          mods={"FSDOptimalMass": 7238.5, "MaxFuelPerJump": 6.8}, unladen=1295.875, r0=82.97)
        ev["FuelCapacity"] = {"Main": 160.0, "Reserve": 1.14}
        return ev

    def test_max_fuel_for_a_jump_caspian(self):
        c, m = self.CASPIAN, outrider.fsd.fleet_model(self.CASPIAN)
        self.assertAlmostEqual(ed_outrider.fsd_range(m, c["unladen"] + c["max_fuel"]), 82.97, places=6)   # the game's figure
        full = ed_outrider.fleet_range(c)
        self.assertAlmostEqual(full, 75.3, delta=0.06)   # a full tank: 75.3 x 6 = 452 ly
        # a 487.9 ly neutron jump (81.3 ly x 6): in range only with about 36 t aboard (the reservoir counted as mass)
        need = ed_outrider.max_fuel_for_jump(m, 487.9, other=1.14, mult=6, cap=160)
        self.assertAlmostEqual(need, 36.07, delta=0.02)
        self.assertLess(need, 50)
        self.assertTrue(outrider.fsd.jump_in_reach(m, 487.9, need, 1.14, 6))
        self.assertFalse(outrider.fsd.jump_in_reach(m, 487.9, need + 0.01, 1.14, 6))
        self.assertFalse(outrider.fsd.jump_in_reach(m, 487.9, 160, 1.14, 6))
        # Spansh's own formula from the exact plotter's inputs agrees: optimal mass / mass x (max fuel / mult)^(1/p) + booster
        drive = (c["max_fuel"] / c["fuel_multiplier"]) ** (1 / c["fuel_power"])
        self.assertAlmostEqual((c["optimal_mass"] / (c["unladen"] + 1.14 + need) * drive + 10.5) * 6, 487.9, delta=0.1)
        # cargo weighs like fuel; no cap: the same answer
        self.assertAlmostEqual(ed_outrider.max_fuel_for_jump(m, 487.9, other=11.14, mult=6), need - 10, delta=0.01)
        self.assertAlmostEqual(ed_outrider.max_fuel_for_jump(m, 487.9, other=1.14, mult=6), need, places=6)
        # a full tank's jump: any fuel the tank holds will do; past the best range whatever the fuel: None
        self.assertEqual(ed_outrider.max_fuel_for_jump(m, 451, other=1.14, mult=6, cap=160), 160.0)
        self.assertEqual(ed_outrider.max_fuel_for_jump(m, 75.0, cap=160), 160.0)
        self.assertIsNone(ed_outrider.max_fuel_for_jump(m, 500, other=1.14, mult=6, cap=160))
        # below one max jump's fuel the fuel itself limits the jump: a 5 t tank cannot pay for the longest jump
        self.assertIsNone(ed_outrider.max_fuel_for_jump(m, 80, cap=5))
        self.assertEqual(ed_outrider.max_fuel_for_jump(m, 60, cap=5), 5.0)

    def heavy_route(self, rows_at, plotter="exact", ship_id=39, start=-100):
        """A route through the Caspian's long neutron jump, you at its start (arrived `start` s ago, plotted now):
        Start (neutron) -487.9 ly-> Neu Far (not neutron) -40 ly-> Plain -60 ly-> End."""
        self.j.handle(self.caspian_loadout(self.ts(-1000), ship_id=39))
        self.jump(start, 300, "Start", 0)
        rows = [dict(system=n, id64=i, x=x, y=0.0, z=0.0, distance=d, fuel_used=None, fuel_left=None, neutron=nu, refuel=0,
                     jumps=jm, remaining=627.9 - x) for n, i, x, d, nu, jm in rows_at]
        return self.state.highway_store(rows, {"plotter": plotter, "options": {},
                                               "ship": {"ship_id": ship_id, "name": "Wanderer II", "type": "explorer_nx"}})

    ROUTE_HEAVY = [("Start", 300, 0.0, 0, 1, 0), ("Neu Far", 301, 487.9, 487.9, 0, 1), ("Plain", 302, 527.9, 40.0, 0, 1),
                   ("End", 303, 587.9, 60.0, 0, 1)]

    def fuel(self, main, reservoir=1.14, cargo=0, live=True):
        self.j.status_json = {"fuel_main": main, "fuel_reservoir": reservoir, "cargo": cargo, "live": live, "ts": self.ts(0)}

    def heavy_moments(self):
        return [m["text"] for m in self.j.moments if m["kind"] == "highway" and m["what"] == "heavy"]

    def test_heavy_warning(self):
        self.heavy_route(self.ROUTE_HEAVY)
        self.fuel(140)
        t = self.now
        self.assertTrue(self.state.highway_heavy_check(t))   # a plot made where you are: looked at at once
        h = self.state.highway_summary()["heavy"]
        self.assertEqual((h["have_t"], h["distance"], h["boost"], h["next"]), (140, 487.9, 6, "Neu Far"))
        self.assertAlmostEqual(h["need_t"], 36.0, delta=0.11)
        self.assertEqual(self.heavy_moments(), ["Too much fuel for the next jump. It needs about 36 tons aboard; you have 140."])
        self.assertEqual(self.state.highway_view()["route"]["summary"]["heavy"], h)   # the Highway header has it too
        # the fuel changes: looked at again, at most every HIGHWAY_HEAVY_EVERY_S, said once per system
        self.fuel(120)
        self.assertFalse(self.state.highway_heavy_check(t + 1))
        self.assertEqual(self.state.highway_summary()["heavy"]["have_t"], 140)
        self.assertTrue(self.state.highway_heavy_check(t + 1 + ed_outrider.HIGHWAY_HEAVY_EVERY_S))
        self.assertEqual(self.state.highway_summary()["heavy"]["have_t"], 120)
        t += 10
        self.fuel(36.5)   # within HIGHWAY_HEAVY_SLACK of the limit: no warning (the plan sits right at it)
        self.assertTrue(self.state.highway_heavy_check(t))
        self.assertIsNone(self.state.highway_summary()["heavy"])
        t += 10
        self.fuel(150)    # scooped again: the warning is back, but it is not said twice in one system
        self.state.highway_heavy_check(t)
        self.assertEqual(self.state.highway_summary()["heavy"]["have_t"], 150)
        self.assertEqual(len(self.heavy_moments()), 1)
        # a jet-cone charge held at a plain system multiplies that jump too; with none, a normal jump within range
        # needs no warning at a full tank (Neu Far -> Plain is 40 ly)
        self.jump(t - self.now + 1, 301, "Neu Far", 487.9)
        self.assertTrue(self.state.highway_heavy_check(t + 1))   # left: cleared
        self.assertIsNone(self.state.highway_summary()["heavy"])
        self.fuel(160)
        self.state.highway_heavy_check(t + 20)
        self.assertIsNone(self.state.highway_summary()["heavy"])
        self.assertEqual(len(self.heavy_moments()), 1)

    def test_heavy_only_live_for_this_ship(self):
        # an arrival read late (catch-up): nothing looked at in that system
        self.heavy_route(self.ROUTE_HEAVY)
        hw = ed_outrider.meta_get(self.db, "highway")
        hw["created_ts"] = self.ts(-3600)
        ed_outrider.meta_set(self.db, "highway", hw)
        self.fuel(140)
        self.state.highway_heavy_check(self.now)
        self.assertIsNone(self.state.highway_summary()["heavy"])
        self.assertEqual(self.heavy_moments(), [])
        # the game not running (Status.json stale): no warning
        self.state.highway_clear()
        self.heavy_route(self.ROUTE_HEAVY)
        self.fuel(140, live=False)
        self.state.highway_heavy_check(self.now)
        self.assertIsNone(self.state.highway_summary()["heavy"])
        # plotted for another ship (or from a typed range: no ship): skipped
        for sid in (40, None):
            self.state.highway_clear()
            self.heavy_route(self.ROUTE_HEAVY, ship_id=sid)
            self.fuel(140)
            self.state.highway_heavy_check(self.now)
            self.assertIsNone(self.state.highway_summary()["heavy"])
        self.assertEqual(self.heavy_moments(), [])
        # off the route: nothing to say about the next jump
        self.state.highway_clear()
        self.heavy_route(self.ROUTE_HEAVY)
        self.jump(1, 999, "Elsewhere", 5)
        self.fuel(140)
        self.state.highway_heavy_check(self.now + 2)
        self.assertIsNone(self.state.highway_summary()["heavy"])

    def test_heavy_neutron_plotter(self):
        # the neutron plotter has no fuel figures: the range check works when the next waypoint is one jump away
        self.heavy_route(self.ROUTE_HEAVY, plotter="neutron")
        self.fuel(140)
        self.state.highway_heavy_check(self.now)
        self.assertEqual(self.state.highway_summary()["heavy"]["next"], "Neu Far")
        self.assertEqual(len(self.heavy_moments()), 1)
        # several jumps to the next waypoint: the first one's length is not known, so nothing is said
        self.state.highway_clear()
        far = [r if r[0] != "Neu Far" else r[:5] + (3,) for r in self.ROUTE_HEAVY]
        self.heavy_route(far, plotter="neutron")
        self.fuel(140)
        self.state.highway_heavy_check(self.now)
        self.assertIsNone(self.state.highway_summary()["heavy"])
        self.assertEqual(len(self.heavy_moments()), 1)

    def test_heavy_in_the_tick(self):
        # the tick looks after the commit: a live arrival at the neutron start with a full tank warns once
        import contextlib
        self.heavy_route(self.ROUTE_HEAVY)
        self.fuel(160)
        with contextlib.ExitStack() as stack:
            stack.enter_context(unittest.mock.patch.object(ed_outrider, "LIVE_DIRS", []))
            for name in ("watch_status", "maybe_refresh", "apply_own_changes", "maybe_classify_target", "maybe_unsold",
                         "maybe_sale_left", "maybe_locate_carrier", "maybe_find_sellers", "maybe_backup_on_quit"):
                stack.enter_context(unittest.mock.patch.object(self.state, name, lambda *a: None))
            self.state.tick({})
            self.state.tick({})
        self.assertEqual(self.heavy_moments(), ["Too much fuel for the next jump. It needs about 36 tons aboard; you have 160."])

    def test_plot_from_a_system_spansh_does_not_know(self):
        """A fresh discovery Spansh does not know yet (where you are): the route is plotted from the Spansh system near
        it that is nearest the destination, out of what Spansh already sent about the neighbourhood, and starts with
        the jump there. The page says so."""
        import asyncio
        self.j.handle(self.loadout("2026-01-02T00:00:00Z", fsd="int_hyperdrive_overcharge_size8_class5_overchargebooster_mkii"))
        self.jump(-100, 900, "Fresh", -20)
        self.state.bases = {100: ("spansh", {"name": "Start", "x": 0.0, "y": 0.0, "z": 0.0}),
                            555: ("spansh", {"name": "Behind", "x": -35.0, "y": 0.0, "z": 0.0}),   # nearer, the wrong way
                            556: ("edsm", {"name": "Edsm Only", "x": -21.0, "y": 0.0, "z": 0.0})}  # not Spansh's
        sp = ed_outrider.Spansh(self.db)
        sp.system_record = spansh_knows(start=100, end=105)
        sp.session = _HwSession([(200, {"job": "e", "status": "ok", "result": self.EXACT})])
        self.state.spansh = sp

        async def go():
            self.state.highway_start_plot({"to": "End"})
            await self.state.highway_task
        asyncio.run(go())
        p = self.state.highway_plotting
        self.assertEqual(p["state"], "done", p["error"])
        self.assertEqual(sp.session.calls[0][1]["source"], 100)                     # plotted from Start
        route = self.state.highway_view()["route"]
        self.assertEqual((route["from"], route["to"], route["count"], route["at"]), ("Fresh", "End", 7, 0))
        self.assertIn("Spansh doesn't know Fresh yet: the route starts with a 20.0 ly jump to Start", route["stand_in"])
        self.assertEqual(p["note"], route["stand_in"])
        rows = self.state.highway_state()[1]
        self.assertEqual([(r["system"], r["distance"], r["jumps"], r["fuel_used"]) for r in rows[:3]],
                         [("Fresh", None, 0, None), ("Start", 20.0, 1, None), ("Neu A", 50, 1, 5.1)])
        self.assertEqual(rows[0]["remaining"], 220)

        # a destination Spansh does not know (visited before): plotted to the Spansh system nearest it, then the jump
        self.jump(-90, 950, "Deep", 230)
        self.jump(-80, 100, "Start", 0)
        sp.session = _HwSession([(200, {"count": 1, "results": [{"name": "End", "id64": 105, "x": 200, "y": 0, "z": 0}]}),
                                 (200, {"job": "e", "status": "ok", "result": self.EXACT})])

        async def go2():
            self.state.highway_start_plot({"to": "Deep"})
            await self.state.highway_task
        asyncio.run(go2())
        p = self.state.highway_plotting
        self.assertEqual(p["state"], "done", p["error"])
        self.assertEqual(sp.session.calls[0][:2], ("POST", ed_outrider.SPANSH_SEARCH))   # what Spansh knows around Deep
        rows = self.state.highway_state()[1]
        self.assertEqual([(r["system"], r["distance"]) for r in rows[-2:]], [("End", 50), ("Deep", 30.0)])
        self.assertEqual(rows[0]["remaining"], 230)
        self.assertIn("the route ends with a 30.0 ly jump from End", p["note"])

    def test_splice_route_neutron_legs(self):
        rows = ed_outrider.highway_rows("neutron", self.NEUTRON)
        out = outrider.highway.splice_route(rows, "neutron", 50, start={"system": "S", "id64": 1, "x": -120, "y": 0, "z": 0})
        self.assertEqual([(r["system"], r["jumps"], r["distance"]) for r in out[:2]], [("S", 0, None), ("Start", 3, 120.0)])
        self.assertIsNone(outrider.highway.stand_in({"x": 0, "y": 0, "z": 0}, None, [], 50))

    def test_conservative_plot_params(self):
        import asyncio
        self.j.handle(self.caspian_loadout("2026-01-02T00:00:00Z"))
        self.j.handle({"event": "Cargo", "timestamp": "2026-01-02T00:00:01Z", "Vessel": "Ship", "Count": 0})
        sp = ed_outrider.Spansh(self.db)
        sp.system_record = spansh_knows(start=100, end=105)
        self.state.spansh = sp

        def plot(body, result=None):
            sp.session = _HwSession([(200, {"job": "c", "status": "ok", "result": result or self.EXACT})])

            async def go():
                out, status = self.state.highway_start_plot(dict({"from": "Start", "to": "End"}, **body))
                if status == 202:
                    await self.state.highway_task
                return status, out
            status, out = asyncio.run(go())
            return status, out, (sp.session.calls[0][1] if sp.session.calls else None)
        f = self.state.fleet_ship(39)["figures"]
        # exact: the optimal mass scaled so the normal full-tank range is 5 ly shorter; the booster untouched
        status, _, params = plot({"conservative": True, "conservative_ly": 5})
        self.assertEqual(status, 202)
        self.assertEqual(params["range_boost"], 10.5)
        self.assertLess(params["optimal_mass"], f["optimal_mass"])
        full = ed_outrider.fsd_range(outrider.fsd.fleet_model(f), f["unladen"] + f["fuel_main"])
        short = ed_outrider.fsd_range(dict(outrider.fsd.fleet_model(f), r0=10.5 + (f["max_range"] - 10.5) * params["optimal_mass"] / f["optimal_mass"]),
                                      f["unladen"] + f["fuel_main"])
        self.assertAlmostEqual(short, full - 5, delta=0.05)
        drive = (f["max_fuel"] / f["fuel_multiplier"]) ** (1 / f["fuel_power"])   # and by Spansh's own formula
        spansh = lambda opt: opt / (params["base_mass"] + params["tank_size"]) * drive + params["range_boost"]
        self.assertAlmostEqual(spansh(f["optimal_mass"]) - spansh(params["optimal_mass"]), 5, delta=0.05)
        self.assertAlmostEqual(full, 75.34, delta=0.01)
        o = ed_outrider.meta_get(self.db, "highway")["options"]
        self.assertEqual((o["conservative_ly"], o["range_full"], o["range"]), (5.0, round(full, 2), round(full - 5, 2)))
        self.assertEqual(self.state.highway_view()["route"]["options"]["conservative_ly"], 5.0)
        # off (the default): the ship's own figures, no margin recorded
        status, _, params = plot({})
        self.assertEqual(params["optimal_mass"], f["optimal_mass"])
        self.assertNotIn("conservative_ly", ed_outrider.meta_get(self.db, "highway")["options"])
        # [highway] conservative = true starts it on; the request's own tick wins
        self.state.highway_cfg.update(conservative=True, conservative_ly=3.0)
        _, _, params = plot({})
        self.assertAlmostEqual(spansh(f["optimal_mass"]) - spansh(params["optimal_mass"]), 3, delta=0.05)
        _, _, params = plot({"conservative": False})
        self.assertEqual(params["optimal_mass"], f["optimal_mass"])
        self.state.highway_cfg.update(conservative=False, conservative_ly=5.0)
        self.assertEqual(self.state.highway_view()["defaults"], {"efficiency": 60, "conservative": False, "conservative_ly": 5.0})
        # neutron: the range less the margin, never under half of it
        _, _, params = plot({"plotter": "neutron", "range": 50, "conservative": True, "conservative_ly": 5}, self.NEUTRON)
        self.assertEqual((params["range"], params["supercharge_multiplier"]), (45.0, 6))
        o = ed_outrider.meta_get(self.db, "highway")["options"]
        self.assertEqual((o["range"], o["range_full"], o["conservative_ly"]), (45.0, 50.0, 5.0))
        _, _, params = plot({"plotter": "neutron", "conservative": True, "conservative_ly": 5}, self.NEUTRON)
        self.assertAlmostEqual(params["range"], round(ed_outrider.fleet_range(f) - 5, 2), places=6)   # the ship's range less 5
        _, _, params = plot({"plotter": "neutron", "range": 8, "conservative": True, "conservative_ly": 6}, self.NEUTRON)
        self.assertEqual(params["range"], 4.0)
        # the ship's Guardian booster floor (Codex F5): 12 ly with its 10.5 ly booster and a 10 ly margin plots 11.25 ly
        # (the drive's 1.5 ly halved, the booster kept), not 6.0; a typed range with no ship has no booster to keep
        _, _, params = plot({"plotter": "neutron", "range": 12, "conservative": True, "conservative_ly": 10}, self.NEUTRON)
        self.assertEqual(params["range"], 11.25)
        _, _, params = plot({"plotter": "neutron", "range": 12, "ship_id": None, "conservative": True, "conservative_ly": 10},
                            self.NEUTRON)
        self.assertEqual(params["range"], 6.0)
        self.assertEqual(ed_outrider.conservative_range(8, 6, 10.5), 4.0)   # a booster longer than the range: none
        self.assertLessEqual(ed_outrider.conservative_range(12, 0.5, 10.5), 12)
        _, _, params = plot({"plotter": "neutron", "range": 50, "conservative": False}, self.NEUTRON)
        self.assertEqual(params["range"], 50)
        # refused: a margin out of range, a tick that is not a boolean
        for bad in ({"conservative": True, "conservative_ly": 0}, {"conservative": True, "conservative_ly": 51},
                    {"conservative": "yes"}, {"conservative": True, "conservative_ly": "far"}):
            status, out, params = plot(bad)
            self.assertEqual((status, params), (400, None), bad)

    # ---- config ----

    def test_config_keys(self):
        import contextlib, io, tomllib
        args = argparse.Namespace(journals=None, legacy=None, host=None, port=None, radius=None, db=None)
        st = ed_outrider.settings_from({}, args, None, ([], []))
        bg = {"background_image": "", "background_extent": [-45000.0, 45000.0, -20000.0, 70000.0], "background_opacity": 0.6,
              "conservative": False, "conservative_ly": 5.0, "autotarget_entry": "type", "autotarget_map_wait": 5.0,
              "autotarget_search_wait": 2.0, "autotarget_key_delay": 0.05, "autotarget_keys": {},
              "autotarget_plot": ["hold CamZoomOut 0.2", "hold UI_Select 1"],
              "autotarget_search": ["hold CamYawRight 0.3", "press UI_Up", "press UI_Select"],
              "autotarget_submit": ["wait 1.5", "press Enter", "wait 0.5", "press Enter"], "autotarget_dry_run": False}
        self.assertEqual(st["highway"], dict({"clipboard": True, "autotarget": False, "autotarget_delay": 5.0, "efficiency": 60}, **bg))
        back = tomllib.loads(ed_outrider.config_text(st))["highway"]
        self.assertEqual(back, dict({"clipboard": True, "autotarget": False, "autotarget_delay": 5, "efficiency": 60}, **bg))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            st = ed_outrider.settings_from({"highway": {"clipboard": False, "autotarget": "yes", "autotarget_delay": 500,
                                                        "efficiency": 0, "conservative": True, "conservative_ly": 99}},
                                           args, None, ([], []))
        self.assertEqual(st["highway"], dict(dict({"clipboard": False, "autotarget": False, "autotarget_delay": 60.0, "efficiency": 1}, **bg),
                                             conservative=True, conservative_ly=50.0))
        self.assertIn("[highway] autotarget", err.getvalue())
        back = tomllib.loads(ed_outrider.config_text(st))["highway"]
        self.assertEqual((back["clipboard"], back["conservative"], back["conservative_ly"]), (False, True, 50))
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "ed_outrider.toml.example"), encoding="utf-8") as f:
            example = f.read()
        readme = user_docs()
        for key in ("clipboard", "autotarget", "autotarget_delay", "efficiency", "conservative", "conservative_ly",
                    "autotarget_entry", "autotarget_map_wait", "autotarget_search_wait", "autotarget_key_delay",
                    "autotarget_keys", "autotarget_plot", "autotarget_dry_run"):
            self.assertIn(f"# {key} = ", example[example.index("[highway]"):])
            self.assertIn(f"`{key}`", readme[readme.index("| `[highway]`"):].split("\n")[0])


class HighwayMap(unittest.TestCase):
    """The Highway map's background: the region layer (GET /api/regions, from the shipped bio_rules.json, read only)
    and the optional background image (GET /api/highway/background: the configured file only, image types only)."""

    def setUp(self):
        import tempfile
        import types
        self.db = ed_outrider.open_db(":memory:")
        self.addCleanup(self.db.close)
        self.j = ed_outrider.Journals(self.db)
        self.state = ed_outrider.State(self.db, self.j, types.SimpleNamespace(cached=lambda i: (None, None)), 25)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.tmp)
        self.png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 40   # only the signature matters to the server

    def write(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def get(self, url, headers=None, **params):
        import asyncio
        from aiohttp.test_utils import TestClient, TestServer

        async def go():
            async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                r = await c.get(url, params=params, headers=headers or {})
                return r.status, r.headers.copy(), await r.read()
        return asyncio.run(go())

    @staticmethod
    def cell_of(layer, x, z):
        """A position's region read from the layer as the page does (hwyRegionAt): column and row by truncation."""
        c, r = int((x - layer["origin"][0]) / layer["cell"]), int((z - layer["origin"][1]) / layer["cell"])
        if c < 0 or r < 0 or r >= layer["size"]:
            return 0
        x0 = 0
        for length, v in layer["rows"][r]:
            if c < x0 + length:
                return v
            x0 += length
        return 0

    def test_region_layer_encoding(self):
        L = outrider.bio.region_layer()
        R = outrider.bio.load_rules()
        self.assertEqual(L["rows"], R["region_grid"])   # the shipped runs as they are: [length, region] per row
        self.assertEqual((L["size"], L["origin"]), (2048, [-49985, -24105]))
        self.assertAlmostEqual(L["cell"], 4096 / 83)
        self.assertTrue(all(sum(n for n, _ in row) == L["size"] for row in L["rows"]))
        self.assertEqual(len(L["names"]), 43)
        self.assertIsNone(L["names"][0])
        self.assertEqual(L["source"]["licence"], "MIT")
        # the landmarks land in their regions, read the page's way and the server's
        for (x, y, z), name in [((0, 0, 0), "Inner Orion Spur"), ((25.21875, -20.90625, 25899.96875), "Galactic Centre"),
                                ((-9530.5, -910.28125, 19808.125), "Inner Scutum-Centaurus Arm"),
                                ((-1111.5625, -134.21875, 65269.75), "The Abyss")]:
            n = self.cell_of(L, x, z)
            self.assertEqual(L["names"][n], name)
            self.assertEqual(outrider.bio.region_number(x, y, z), n)
        # outside the grid: nothing
        self.assertEqual(self.cell_of(L, -60000, 0), 0)
        # one label per region, each inside its own region (a curved arm's centroid can fall outside it)
        self.assertEqual(sorted(lb["n"] for lb in L["labels"]), list(range(1, 43)))
        for lb in L["labels"]:
            self.assertEqual(self.cell_of(L, lb["x"], lb["z"]), lb["n"], lb["name"])
            self.assertEqual(lb["name"], L["names"][lb["n"]])
            self.assertGreater(lb["cells"], 0)

    def test_regions_endpoint(self):
        st, h, body = self.get("/api/regions", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(st, 200)
        self.assertEqual(h.get("Content-Encoding"), "gzip")   # 185 KB of runs: compressed on the wire
        d = json.loads(body)
        self.assertEqual(d, json.loads(json.dumps(outrider.bio.region_layer())))
        etag = h["ETag"]
        self.assertEqual(h["Cache-Control"], "no-cache")
        st, h2, body = self.get("/api/regions", headers={"If-None-Match": etag})
        self.assertEqual((st, body, h2["ETag"]), (304, b"", etag))   # the page has it: not sent again
        self.assertEqual(self.get("/api/regions", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        with unittest.mock.patch.object(outrider.bio, "load_rules", return_value=None):
            st, _, body = self.get("/api/regions")
        self.assertEqual(st, 404)
        self.assertIn("region map", json.loads(body)["error"])

    def test_background_image_route(self):
        cfg = self.state.highway_cfg
        # none configured
        st, _, body = self.get("/api/highway/background")
        self.assertEqual(st, 404)
        self.assertFalse(self.state.highway_view()["background"]["image"])
        # the configured file, whatever the request asks for
        path = self.write("galaxy.png", self.png)
        self.write("secret.png", b"\x89PNG\r\n\x1a\nsecret")
        cfg["background_image"] = path
        for params in ({}, {"path": "/etc/passwd"}, {"path": os.path.join(self.tmp, "secret.png")}, {"v": "../../x"}):
            st, h, body = self.get("/api/highway/background", **params)
            self.assertEqual((st, body), (200, self.png), params)
            self.assertEqual(h["Content-Type"], "image/png")
            self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.get("/api/highway/background/../secret.png")[0], 404)
        bg = self.state.highway_view()["background"]
        self.assertEqual((bg["image"], bg["name"], bg["extent"], bg["why"]), (True, "galaxy.png", ed_outrider.HIGHWAY_BG_EXTENT, None))
        self.assertTrue(bg["v"].endswith(f"-{len(self.png)}"))
        # another site cannot read it
        self.assertEqual(self.get("/api/highway/background", headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        # not an image by its extension or its content, missing, a folder, empty: never served
        bad = {"text": self.write("notes.txt", b"hello"), "toml": self.write("ed_outrider.toml", b"[server]"),
               "svg": self.write("map.svg", b"<svg onload='x'/>"), "fake": self.write("fake.png", b"#!/bin/sh\n"),
               "webp": self.write("fake.webp", b"RIFF\x00\x00\x00\x00WAVE"), "empty": self.write("empty.jpg", b""),
               "missing": os.path.join(self.tmp, "gone.png"), "folder": os.path.join(self.tmp, "dir.png")}
        os.mkdir(bad["folder"])
        for what, p in bad.items():
            cfg["background_image"] = p
            st, h, body = self.get("/api/highway/background")
            self.assertEqual(st, 404, what)
            self.assertNotIn(b"hello", body)
            bg = self.state.highway_view()["background"]
            self.assertFalse(bg["image"], what)
            self.assertTrue(bg["why"], what)
        # the other types by their first bytes
        for name, data, ctype in [("a.jpg", b"\xff\xd8\xff\xe0rest", "image/jpeg"), ("a.gif", b"GIF89a....", "image/gif"),
                                  ("a.webp", b"RIFF\x10\x00\x00\x00WEBPVP8 ", "image/webp")]:
            cfg["background_image"] = self.write(name, data)
            st, h, body = self.get("/api/highway/background")
            self.assertEqual((st, h["Content-Type"], body), (200, ctype, data))

    def test_background_config(self):
        import contextlib, io, tomllib
        args = argparse.Namespace(journals=None, legacy=None, host=None, port=None, radius=None, db=None)
        st = ed_outrider.settings_from({"highway": {"background_image": "pics/galaxy.png", "background_extent": [-40000, 40000, -20000, 70000],
                                                    "background_opacity": 0.4}}, args, None, ([], []))
        hw = st["highway"]
        self.assertEqual(hw["background_image"], os.path.join(ed_outrider.SCRIPT_DIR, "pics/galaxy.png"))   # relative to the repo
        self.assertEqual((hw["background_extent"], hw["background_opacity"]), ([-40000.0, 40000.0, -20000.0, 70000.0], 0.4))
        back = tomllib.loads(ed_outrider.config_text(st))["highway"]
        self.assertEqual((back["background_image"], back["background_extent"], back["background_opacity"]),
                         ("pics/galaxy.png", [-40000, 40000, -20000, 70000], 0.4))
        self.assertEqual(ed_outrider.settings_from({"highway": {"background_image": "/abs/g.JPG"}}, args, None, ([], []))
                         ["highway"]["background_image"], "/abs/g.JPG")
        # a file that is not an image type, a bad extent and an opacity out of range: reported, the defaults used
        for cfg, key, want in [({"background_image": "ed_outrider.toml"}, "background_image", ""),
                               ({"background_image": "data/ed_outrider.sqlite"}, "background_image", ""),
                               ({"background_image": "x.svg"}, "background_image", ""),
                               ({"background_image": 3}, "background_image", ""),
                               ({"background_extent": [1, 2, 3]}, "background_extent", ed_outrider.HIGHWAY_BG_EXTENT),
                               ({"background_extent": [5, 1, 0, 9]}, "background_extent", ed_outrider.HIGHWAY_BG_EXTENT),
                               ({"background_extent": [0, 1, True, 9]}, "background_extent", ed_outrider.HIGHWAY_BG_EXTENT),
                               ({"background_extent": "all"}, "background_extent", ed_outrider.HIGHWAY_BG_EXTENT)]:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                got = ed_outrider.settings_from({"highway": cfg}, args, None, ([], []))["highway"][key]
            self.assertEqual(got, want, cfg)
            self.assertIn(f"[highway] {key}", err.getvalue())
        self.assertEqual(ed_outrider.settings_from({"highway": {"background_opacity": 7}}, args, None, ([], []))["highway"]["background_opacity"], 1.0)
        self.assertEqual(ed_outrider.settings_from({"highway": {"background_opacity": 0}}, args, None, ([], []))["highway"]["background_opacity"], 0.05)
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(here, "ed_outrider.toml.example"), encoding="utf-8") as f:
            example = f.read()
        readme = user_docs()
        for key in ("background_image", "background_extent", "background_opacity"):
            self.assertIn(f"# {key} = ", example[example.index("[highway]"):])
            self.assertIn(f"`{key}`", readme[readme.index("| `[highway]`"):].split("\n")[0])


class HighwayAutoTarget(unittest.TestCase):
    """The Neutron Highway's auto-target (outrider/target.py): the steps from the author's bindings, the typing keymap,
    the guards, the runner against a simulated game on a FAKE virtual keyboard (never a uinput device, never a real
    key), the lock with auto honk, the trigger, the "test now" endpoint and the toggle."""

    def setUp(self):
        import tempfile
        import types
        import outrider.honk
        import outrider.target
        import contextlib
        import io
        self.T = outrider.target
        quiet = contextlib.redirect_stdout(io.StringIO())   # the server's "highway auto-target: ..." lines
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.db = ed_outrider.open_db(":memory:")
        self.addCleanup(self.db.close)
        self.j = ed_outrider.Journals(self.db)
        self.state = ed_outrider.State(self.db, self.j, types.SimpleNamespace(cached=lambda i: (None, None)), 25)
        self.now = time.time()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.journals, self.binds = make_controls(self, tmp.name, "HCS X56 Attempt 1\n" * 4)
        with open(os.path.join(self.binds, "HCS X56 Attempt 1.4.2.binds"), "w") as f:
            f.write(AUTHOR_BINDS)
        outrider.honk._bindings_cache.clear()
        # quick timings for tests (the defaults are for the game)
        for k, v in (("TAP_S", 0.002), ("VERIFY_WAIT", 0.4), ("POLL_S", 0.01), ("LOCK_WAIT", 2.0)):
            p = unittest.mock.patch.object(outrider.target, k, v)
            p.start()
            self.addCleanup(p.stop)
        self.evdev = _fake_evdev()
        self.status = {"live": True, "gui_focus": 0, "flags": 1 << 4, "flags2": 0, "destination": None}
        self.game = FakeGame(self.evdev, self.status, {"Bridge B": 102, "Col 285 Sector AB-C d13-5": 555})
        self.honker = outrider.honk.Honker("auto", 1.0, [self.journals])
        self.honker.evdev, self.honker.ui = self.evdev, self.game   # never open(): there is no UInput here at all
        self.honker.owners = {"target"}
        self.here = 101
        self.cfg = {"map_wait": 0.4, "search_wait": 0.02, "key_delay": 0.0, "plot": ["hold CamZoomOut 0.05", "hold UI_Select 0.55"],
                    "submit": ["wait 0.2", "press Enter", "wait 0.05", "press Enter"]}
        self.copied = []
        self.targeter = outrider.target.Targeter(self.honker, [self.journals], self.cfg, log=lambda line: None,
                                                 copy=lambda t: self.copied.append(t) or setattr(self.game, "clipboard", t) or True)

    def fake_time(self):
        """Run the targeter and the game on a FakeClock: the run takes no real time."""
        self.clock = use_fake_time(self.targeter, self.game)
        return self.clock

    def run_target(self, name="Bridge B", id64=102, **kw):
        return self.targeter.run(name, id64, lambda: self.status, lambda: self.here, **kw)

    def released(self):
        """Every key pressed was let go, in the end."""
        down = set()
        for n, v in self.game.writes:
            (down.add if v else down.discard)(n)
        return not down

    # ---- the steps, the keys, the keymap ----

    def test_steps_from_the_authors_bindings(self):
        steps, missing = self.targeter.plan()
        self.assertEqual(missing, [])
        got = [(s["phase"], s["do"], s.get("names") or s.get("value") or s.get("secs")) for s in steps]
        self.assertEqual(got, [
            (1, "press", ["KEY_LEFTALT", "KEY_RIGHTALT", "KEY_T"]), (1, "focus", 6),
            # the yaw first: the map opens on the panel it last showed, and a camera move hands focus back to the map
            (2, "hold", ["KEY_LEFTALT", "KEY_RIGHTSHIFT", "KEY_X"]), (2, "press", ["KEY_W"]), (2, "press", ["KEY_SPACE"]),
            (3, "type", None), (4, "wait", 0.2), (4, "press", ["KEY_ENTER"]), (4, "wait", 0.05), (4, "press", ["KEY_ENTER"]),
            (4, "wait", 0.02),
            (5, "hold", ["KEY_RIGHTALT", "KEY_COMPOSE", "KEY_2"]), (5, "hold", ["KEY_SPACE"]),
            (6, "press", ["KEY_LEFTALT", "KEY_RIGHTALT", "KEY_T"]), (6, "focus", None),   # GuiFocus 0
            (7, "verify", None)])
        self.assertEqual(steps[14]["value"], 0)
        words = self.targeter.describe(steps)
        self.assertEqual(words[0], "1 open the galaxy map: press Left Alt + Right Alt + T (secondary binding of Galaxy Map Open "
                                   "in HCS X56 Attempt 1)")
        self.assertIn("hold Space (secondary binding of UI Select in HCS X56 Attempt 1) for 0.55 s", words[12])
        # the default plot step and the other reads: UI_Back Backspace, CycleNextPanel Alt+Alt+W, the arrows WASD
        # (zoom, not yaw: a yaw there can swing the camera onto a neighbouring star and plot to it, 2026-10-03)
        self.assertEqual(self.T.build_steps()[11], {"do": "hold", "keys": "CamZoomOut", "secs": 0.2, "phase": 5, "label": "plot the route"})
        self.assertEqual(self.T.build_steps()[12], {"do": "hold", "keys": "UI_Select", "secs": 1.0, "phase": 5, "label": "plot the route"})
        # step 2 is configurable too ([highway] autotarget_search): the older layout's UI_Right, then select
        self.assertEqual([x.get("keys") for x in self.T.build_steps({"search": ["press UI_Right", "press UI_Select"]}) if x["phase"] == 2],
                         ["UI_Right", "UI_Select"])
        b = self.targeter.bindings()
        self.assertEqual({k: v[0] for k, v in b.items() if v[0]}, {
            "GalaxyMapOpen": ["KEY_LEFTALT", "KEY_RIGHTALT", "KEY_T"], "UI_Right": ["KEY_D"], "UI_Left": ["KEY_A"],
            "UI_Up": ["KEY_W"], "UI_Down": ["KEY_S"], "UI_Select": ["KEY_SPACE"], "UI_Back": ["KEY_BACKSPACE"],
            "CycleNextPanel": ["KEY_LEFTALT", "KEY_RIGHTALT", "KEY_W"], "CamYawRight": ["KEY_LEFTALT", "KEY_RIGHTSHIFT", "KEY_X"],
            "CamZoomOut": ["KEY_RIGHTALT", "KEY_COMPOSE", "KEY_2"]})
        # Primary Fire still reads the same (the reader is shared with auto honk)
        self.assertEqual(outrider_honk().primary_fire_binding([self.journals])[0], ["KEY_LEFTALT", "KEY_RIGHTALT", "KEY_K"])
        # overrides, paste entry, and a step list of your own
        self.targeter.configure({"keys": {"GalaxyMapOpen": "KEY_LEFTALT+KEY_RIGHTALT+KEY_T", "Enter": "KEY_KPENTER"},
                                 "entry": "paste", "plot": ["press UI_Up", "wait 0.1", "hold KEY_SPACE 1"]})
        steps, missing = self.targeter.plan()
        self.assertEqual(missing, [])
        self.assertEqual([s.get("names") for s in steps if s["phase"] in (3, 4, 5)],
                         [["KEY_LEFTCTRL", "KEY_V"], None, ["KEY_KPENTER"], None, ["KEY_KPENTER"], None, ["KEY_W"], None, ["KEY_SPACE"]])
        self.assertIn("(autotarget_keys)", steps[0]["what"])

    def test_missing_bindings_stop_it(self):
        with open(os.path.join(self.binds, "HCS X56 Attempt 1.4.2.binds"), "w") as f:
            f.write(AUTHOR_BINDS.replace('<Secondary Device="Keyboard" Key="Key_Space" />', '<Secondary Device="{NoDevice}" Key="" />'))
        os.utime(os.path.join(self.binds, "HCS X56 Attempt 1.4.2.binds"), (time.time() + 5, time.time() + 5))
        steps, missing = self.targeter.plan()
        self.assertEqual([m[0] for m in missing], ["UI_Select"])
        self.assertIn("UI Select has no keyboard binding in HCS X56 Attempt 1", missing[0][1])
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"]), (False, 0))
        self.assertIn("no keyboard binding for UI_Select", r["why"])
        self.assertEqual(self.game.writes, [])   # nothing pressed
        # an override fills the gap
        self.targeter.configure({"keys": {"UI_Select": "KEY_SPACE"}})
        self.assertEqual(self.targeter.plan()[1], [])

    def test_typing_keymap(self):
        import re
        T = self.T
        # every printable ASCII character, and nothing else
        self.assertEqual(sorted(T.US_KEYMAP), [chr(c) for c in range(32, 127)])
        for name in ("Col 285 Sector AB-C d13-5", "Barnard's Star", "BD+47 2112", "Gliese 581.1", "Sagittarius A*",
                     "2MASS J05405172-0226489", "Hen 2-23", "LP 98-132", "Wolf 359", "Kappa-1 Ceti (B)", "HIP 12345",
                     "Swoiwns XX-K d8-12", "Eol Prou RS-T d3-94", "V886 Centauri", "Hesperine", "Talvik Reach", "Ossia"):
            self.assertEqual(T.untypeable(name), [], name)
            self.assertEqual(len(T.keystrokes(name)), len(name))
        self.assertEqual(T.keystrokes("Ab-'+.*( )"), [("KEY_A", True), ("KEY_B", False), ("KEY_MINUS", False),
                                                      ("KEY_APOSTROPHE", False), ("KEY_EQUAL", True), ("KEY_DOT", False),
                                                      ("KEY_8", True), ("KEY_9", True), ("KEY_SPACE", False), ("KEY_0", True)])
        self.assertEqual(T.untypeable("Pégase Ünï"), ["é", "Ü", "ï"])
        with self.assertRaises(ValueError):
            T.keystrokes("Pégase")
        # the fixture journals' and route's systems are all typeable
        here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "journals")
        names = set()
        for fn in os.listdir(here):
            with open(os.path.join(here, fn), encoding="utf-8") as f:
                for line in f:
                    for m in re.finditer(r'"StarSystem": ?"([^"]*)"', line):
                        names.add(m.group(1))
        self.assertTrue(names)
        self.assertEqual([n for n in names if T.untypeable(n)], [])

    def test_parse_step(self):
        P = self.T.parse_step
        self.assertEqual(P("press UI_Select"), {"do": "press", "keys": "UI_Select"})
        self.assertEqual(P(" hold  UI_Select 1 "), {"do": "hold", "keys": "UI_Select", "secs": 1.0})
        self.assertEqual(P("wait 0.5"), {"do": "wait", "secs": 0.5})
        for bad in ("", "jump", "hold UI_Select", "hold UI_Select 99", "wait soon", "press"):
            with self.assertRaises(ValueError):
                P(bad)

    # ---- the guards ----

    def test_guards(self):
        G = self.T.guard
        ok = {"live": True, "gui_focus": 0, "flags": 1 << 4, "flags2": 0, "destination": None}
        self.assertIsNone(G(ok, 102))
        cases = [({"live": False}, "live"), ({"flags": 1 << 0}, "place"), ({"flags": 1 << 1}, "place"),
                 ({"flags": 1 << 26}, "place"), ({"flags2": 1}, "place"), ({"flags": 1 << 22}, "danger"),
                 ({"flags": 1 << 23}, "danger"), ({"flags": 1 << 17}, "jump"), ({"flags": 1 << 30}, "jump"),
                 ({"gui_focus": 6}, "focus"), ({"gui_focus": 2}, "focus"),
                 ({"destination": {"System": 102, "Name": "Bridge B"}}, "already")]
        for change, code in cases:
            self.assertEqual(G(dict(ok, **change), 102)[0], code, change)
        self.assertEqual(G(dict(ok, gui_focus=6), 102)[1], "the galaxy map is open (the cockpit must have focus)")
        self.assertIsNone(G(dict(ok, destination={"System": 999}), 102))
        self.assertEqual(G(None, 102)[0], "live")
        # the runner refuses up front, pressing nothing
        self.status["gui_focus"] = 7
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["code"], self.game.writes), (False, 0, "focus", []))

    # ---- running against the simulated game ----

    def test_success_types_the_name_and_verifies(self):
        self.fake_time()
        r = self.run_target("Col 285 Sector AB-C d13-5", 555)
        self.assertTrue(r["ok"], r)
        self.assertEqual((self.status["gui_focus"], self.status["destination"]["System"]), (0, 555))
        self.assertTrue(self.released())
        pressed = [n for n, v in self.game.writes if v]
        self.assertEqual(pressed[:8], ["KEY_LEFTALT", "KEY_RIGHTALT", "KEY_T",            # the map
                                       "KEY_LEFTALT", "KEY_RIGHTSHIFT", "KEY_X",          # the yaw: focus to the map
                                       "KEY_W", "KEY_SPACE"])                             # W lights the search box
        self.assertEqual(pressed.count("KEY_LEFTSHIFT"), 5)   # the capitals C, S, A, B, C
        self.assertEqual(self.game.found, ("Col 285 Sector AB-C d13-5", 555))
        self.assertEqual(len(r["log"]), 16)   # each step done

    def test_enter_waits_for_the_suggestion(self):
        self.fake_time()
        # an Enter straight after the name selects nothing (the search lists its suggestion a moment later), so
        # nothing is plotted and the check fails; the default submit step waits first
        self.targeter.configure({"submit": ["press Enter"]})
        r = self.run_target()
        self.assertFalse(r["ok"], r)
        self.assertIsNone(self.game.found)
        self.assertIsNone(self.status.get("destination"))
        self.assertEqual(self.T.DEFAULT_SUBMIT, ("wait 1.5", "press Enter", "wait 0.5", "press Enter"))
        self.targeter.configure({"submit": ["wait 0.2", "press Enter"]})
        self.assertTrue(self.run_target()["ok"])

    def test_paste_entry(self):
        self.fake_time()
        self.targeter.configure({"entry": "paste"})
        r = self.run_target()
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.copied, ["Bridge B"])
        self.assertNotIn("KEY_B", [n for n, _v in self.game.writes])   # pasted, not typed
        # typing a name the keymap cannot type falls back to pasting it
        self.targeter.configure({"entry": "type"})
        self.game.systems["Pégase"] = 777
        self.status.update(destination=None)
        r = self.run_target("Pégase", 777)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.copied, ["Bridge B", "Pégase"])
        # with no clipboard tool it says so
        self.targeter.copy = None
        self.status.update(destination=None)
        r = self.run_target("Pégase", 777)
        self.assertEqual((r["ok"], r["phase"]), (False, 3))
        self.assertIn("cannot type 'é'", r["why"])

    def test_map_never_opens(self):
        self.fake_time()
        self.game.ignore_open = True
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 1, "the galaxy map did not open"))
        # it never pressed the map key a second time (that would open it late)
        self.assertEqual([n for n, v in self.game.writes if v].count("KEY_T"), 1)
        self.assertTrue(self.released())

    def test_wrong_focus_mid_way(self):
        self.fake_time()
        def panel(game):
            if len(game.text) == 3:
                game.status["gui_focus"] = 2   # the external panel came up while typing
        self.game.on_type = panel
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"]), (False, 3))
        self.assertEqual(r["why"], "the external panel is open (unexpected)")
        self.assertEqual(self.status["gui_focus"], 2)   # not Outrider's map any more: left alone
        self.assertEqual([n for n, v in self.game.writes if v].count("KEY_T"), 1)
        self.assertTrue(self.released())

    def test_system_changed_closes_the_map_it_opened(self):
        self.fake_time()
        def jumped(game):
            if len(game.text) == 2:
                self.here = 999
        self.game.on_type = jumped
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 3, "the system changed"))
        self.assertEqual(self.status["gui_focus"], 0)   # closed again
        self.assertIn("closed the galaxy map it had opened", r["log"][-1])
        self.assertTrue(self.released())

    def test_abort_while_the_map_closes_does_not_reopen_it(self):
        """Danger comes while the map is closing (its close key went down, Status.json still says galaxy map): the run
        stops without pressing the map key again, which would reopen it (the sweep of 2026-10-09)."""
        self.fake_time()
        self.game.ignore_close = True                    # the close lags: GuiFocus stays 6 for now
        real_key = self.game.key

        def key(name):
            real_key(name)
            if name == "KEY_T" and [n for n, v in self.game.writes if v].count("KEY_T") == 2:   # the close key
                self.status["flags"] |= self.T.FLAG_IN_DANGER
        self.game.key = key
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 6, "danger"))
        self.assertEqual([n for n, v in self.game.writes if v].count("KEY_T"), 2)   # open, close: not a third
        self.assertTrue(self.released())

    def test_map_never_closes_times_out(self):
        self.fake_time()
        self.game.ignore_close = True
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 6, "the galaxy map did not close"))

    def test_jump_charging_mid_way(self):
        self.fake_time()
        def charge(game):
            if len(game.text) == 4:
                game.status["flags"] |= 1 << 17
        self.game.on_type = charge
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 3, "an FSD jump started"))

    def test_jump_charging_after_the_plot_is_no_failure(self):
        """The FSD charges as the map closes, before steps 6 and 7 looked: the target is set, so the run succeeded (it said
        "failed" before)."""
        self.fake_time()
        self.game.on_close = lambda g: self.status.update(flags=self.status["flags"] | 1 << 17)
        r = self.run_target()
        self.assertTrue(r["ok"], r)
        self.assertIn("an FSD jump started, the target was set: done", r["log"][-1])
        self.assertTrue(self.released())
        # the jump went and you arrived at the target (its Destination gone with it): done too
        self.status.update(flags=1 << 4, destination=None)
        self.here = 101
        self.game.on_close = lambda g: (setattr(self, "here", 102), self.status.update(destination=None))
        r = self.run_target()
        self.assertTrue(r["ok"], r)

    def test_jump_charging_after_the_plot_without_a_target_still_fails(self):
        self.fake_time()
        self.game.no_plot = True
        self.game.on_close = lambda g: self.status.update(flags=self.status["flags"] | 1 << 17)
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 6, "an FSD jump started"))
        # and a jump somewhere else is still "the system changed"
        self.status.update(flags=1 << 4, destination=None)
        self.game.on_close = lambda g: setattr(self, "here", 999)
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 6, "the system changed"))

    def test_destination_verify(self):
        self.fake_time()
        self.game.no_plot = True   # the plot keys did nothing: no target
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 7, "no system was targeted"))
        self.game.no_plot, self.game.plot_to = False, 4242   # targeted something else
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 7, "targeted the wrong system: Bridge B"))
        self.assertEqual((r["code"], r["wrong"]), ("wrong", "Bridge B"))
        # the target shows up late (Status.json lags): 0.2 s after the map closes, inside VERIFY_WAIT (0.4 s here)
        self.game.plot_to, self.game.no_plot = None, True
        self.status["destination"] = None
        self.game.on_close = lambda g: self.clock.at(0.2, lambda: self.status.update(destination={"System": 102, "Name": "Bridge B"}))
        r = self.run_target()
        self.assertTrue(r["ok"], r)
        # and later than VERIFY_WAIT is a failure (nothing waits for ever)
        self.status["destination"] = None
        self.game.on_close = lambda g: self.clock.at(0.6, lambda: self.status.update(destination={"System": 102, "Name": "Bridge B"}))
        r = self.run_target()
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 7, "no system was targeted"))

    def test_dry_run_presses_nothing(self):
        self.fake_time()
        r = self.run_target(dry_run=True)
        self.assertEqual((r["ok"], r["dry_run"], self.game.writes), (True, True, []))
        self.assertTrue(r["log"][0].startswith("would press Left Alt + Right Alt + T"))
        self.assertEqual(len(r["log"]), 16)

    # ---- review batch 4: cancelling, the checks under the lock, multi-hop plots, the wrong system ----

    def test_run_token_stops_it_mid_way(self):   # CX-F1, F12: auto-target switched off while auto honk keeps the device open
        import threading
        self.fake_time()
        cancel = threading.Event()
        self.game.on_type = lambda g: len(g.text) == 3 and cancel.set()
        r = self.run_target(cancel=cancel)
        self.assertEqual((r["ok"], r["phase"], r["why"]), (False, 3, "stopped"))
        self.assertTrue(self.released())
        self.assertEqual(self.game.text, "Bri")                 # nothing typed after the switch-off
        self.assertEqual(self.status["gui_focus"], 0)           # the map it opened was closed on the way out
        self.assertIsNone(self.status["destination"])
        self.assertIsNone(self.targeter.run_cancel)             # the token belongs to that run only
        self.assertTrue(self.run_target()["ok"])                # the next run is not stopped by it

    def test_close_tap_held_when_stopped(self):   # F13: the closing Alt+Alt+T is a whole tap, even on the way out
        self.fake_time()
        times = []
        write = self.game.write
        self.game.write = lambda t, c, v: (times.append((self.game.names[c], v, self.clock())), write(t, c, v))[1]
        self.game.on_type = lambda g: len(g.text) == 2 and self.targeter.cancel.set()   # shutdown mid-way
        with unittest.mock.patch.object(self.T, "TAP_S", 0.05):
            r = self.run_target()
        self.assertEqual(r["why"], "stopped")
        t_down = [t for n, v, t in times if n == "KEY_T" and v == 1][-1]
        t_up = [t for n, v, t in times if n == "KEY_T" and v == 0][-1]
        self.assertGreaterEqual(t_up - t_down, 0.049)   # held, not cut to nothing
        self.assertEqual(self.status["gui_focus"], 0)

    def test_checks_again_under_the_lock(self):   # CX-F3: what changed while it waited for the keyboard
        import threading
        cases = [("system", lambda: setattr(self, "here", 999), "the system changed while it waited for the keyboard"),
                 ("docked", lambda: self.status.update(flags=1 << 4 | 1 << 0), None),
                 ("landed", lambda: self.status.update(flags=1 << 4 | 1 << 1), None),
                 ("panel", lambda: self.status.update(gui_focus=2), None),
                 ("cancel", None, "stopped")]
        for what, change, why in cases:
            self.here, self.status["flags"], self.status["gui_focus"] = 101, 1 << 4, 0
            self.game.writes.clear()
            cancel = threading.Event()
            self.honker.lock.acquire()

            def later(change=change, cancel=cancel):
                (change or cancel.set)()
                self.honker.lock.release()
            threading.Timer(0.1, later).start()
            r = self.run_target(cancel=cancel, origin=101)
            self.assertEqual((r["ok"], r["phase"], self.game.writes), (False, 0, []), what)
            if why:
                self.assertEqual(r["why"], why, what)
            else:
                self.assertEqual(r["label"], "check before the first key", what)
        # origin: a run decided at 101 that only gets going after a jump does not target from the new system
        self.here, self.status["flags"], self.status["gui_focus"] = 202, 1 << 4, 0
        r = self.run_target(origin=101)
        self.assertEqual((r["ok"], r["why"], self.game.writes), (False, "the system changed while it waited for the keyboard", []))

    def test_already_comes_first(self):   # F11: already targeted is not an error, whatever else is going on
        G = self.T.guard
        self.assertEqual(G({"live": True, "gui_focus": 6, "flags": 1 << 4, "destination": {"System": 102}}, 102)[0], "already")
        self.assertEqual(G({"live": True, "gui_focus": 0, "flags": 1 << 4 | 1 << 17, "destination": {"System": 102}}, 102)[0],
                         "already")
        # a route the game plotted that ends at the next system (its first hop is the target): already done too (F2)
        self.assertEqual(G({"live": True, "gui_focus": 0, "flags": 1 << 4, "destination": {"System": 7}}, 102, lambda: 102)[0],
                         "already")
        self.assertIsNone(G({"live": True, "gui_focus": 0, "flags": 1 << 4, "destination": {"System": 7}}, 102, lambda: 8))

    def test_multi_hop_plot_counts(self):   # F2: a waypoint beyond plain jump range plots a route whose first hop differs
        self.fake_time()
        self.game.plot_to = 4242   # the destination is the route's first hop
        plotted = lambda: 102 if self.status.get("destination") else None   # NavRoute.json follows the plot
        r = self.run_target(route_end=plotted)
        self.assertTrue(r["ok"], r)
        self.status["destination"] = None
        r = self.run_target(route_end=lambda: 4243)   # a route to somewhere else: the wrong system, by name
        self.assertEqual((r["ok"], r["code"], r["wrong"]), (False, "wrong", "Bridge B"))

    def test_success_logs_nothing(self):   # Q6: a success is the caller's one line; a failure prints its steps
        self.fake_time()
        lines = []
        self.targeter.log = lines.append
        self.assertTrue(self.run_target()["ok"])
        self.assertEqual(lines, [])
        self.game.no_plot, self.status["destination"] = True, None
        r = self.run_target()
        self.assertFalse(r["ok"])
        self.assertEqual(len(lines), len(r["log"]))
        self.assertTrue(lines[-1].startswith("auto-target: stopped at step 7"), lines[-1])

    def test_lock_with_honk(self):
        import threading
        # auto honk holds the keyboard: the sequence waits for it, then runs; keys never interleave
        self.honker.lock.acquire()
        threading.Timer(0.3, self.honker.lock.release).start()
        start = time.time()
        r = self.run_target()
        self.assertTrue(r["ok"], r)
        self.assertGreaterEqual(time.time() - start, 0.29)
        # it stays busy: give up after LOCK_WAIT, pressing nothing
        self.game.writes.clear()
        self.honker.lock.acquire()
        try:
            with unittest.mock.patch.object(self.T, "LOCK_WAIT", 0.1):
                self.status["destination"] = None
                r = self.run_target()
        finally:
            self.honker.lock.release()
        self.assertEqual((r["ok"], r["phase"], self.game.writes), (False, 0, []))
        self.assertIn("busy", r["why"])
        # and auto honk's press waits for a running sequence (the same lock)
        self.status["destination"] = None
        th = threading.Thread(target=self.run_target)
        th.start()
        time.sleep(0.05)
        self.assertFalse(self.honker.lock.acquire(blocking=False))
        th.join(5)
        self.assertTrue(self.honker.lock.acquire(blocking=False))
        self.honker.lock.release()

    def test_device_owners(self):
        # auto honk off while auto-target wants the keyboard: it stays open; the last one out closes it
        h = self.honker
        h.status = "off"
        self.assertTrue(h.open())   # auto honk on while auto-target already has the keyboard: no new device, its status
        self.assertEqual((h.owners, h.status, self.game.closed),
                         ({"honk", "target"}, "ready: holds Left Alt + Right Alt + K (secondary binding of Primary Fire in HCS X56 Attempt 1) for 1 s", False))
        h.close()             # auto honk off
        self.assertTrue(h.ready and not self.game.closed)
        h.close("target")     # auto-target off too
        self.assertTrue(self.game.closed and h.ui is None)

    # ---- the trigger, the honk first, the spoken results ----

    def wire(self):
        """The State with the simulated game as its Status.json and the targeter on the fake keyboard, at Neu A."""
        self.state.honker, self.state.targeter = self.honker, self.targeter
        hwy_plot_exact(self)
        hwy_jump(self, 1, 101, "Neu A", 50)
        self.j.status_json = self.status
        self.state.highway_cfg.update(autotarget=True, autotarget_delay=0.15)

    ts = hwy_ts
    jump = hwy_jump
    EXACT = HWY_EXACT

    def moments(self):
        return [(m["ok"], m["text"]) for m in self.j.moments if m["kind"] == "autotarget"]

    def test_trigger_after_the_delay_once(self):
        import asyncio
        self.wire()

        async def go():
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(2), "BoostValue": 4})
            first, second = self.state.maybe_autotarget(), self.state.maybe_autotarget()   # one per supercharge
            await asyncio.sleep(0.08)
            early = (list(self.game.writes), self.state.autotarget_last)
            await self.state.autotarget_task
            return first, second, early
        first, second, early = asyncio.run(go())
        self.assertEqual((first, second, early), (True, False, ([], None)))   # nothing before the delay
        last = self.state.autotarget_last
        self.assertEqual((last["system"], last["done"]), ("Bridge B", True))
        self.assertEqual(self.moments(), [(True, "Successfully targeted neutron jump target Bridge B")])
        self.assertEqual(self.status["destination"]["System"], 102)
        # a failure says so (the plot keys did nothing)
        self.game.no_plot, self.status["destination"] = True, None

        async def again():
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(3), "BoostValue": 4})
            self.assertTrue(self.state.maybe_autotarget())
            await self.state.autotarget_task
        asyncio.run(again())
        self.assertEqual(self.moments()[-1], (False, "Failed to target neutron jump target Bridge B"))
        last = self.state.autotarget_last
        self.assertEqual((last["done"], last["phase"], last["why"]), (False, 7, "no system was targeted"))
        info = self.state.autotarget_info()
        self.assertEqual((info["status"], info["missing"], len(info["steps"])), ("ready", [], 16))
        with unittest.mock.patch.object(outrider.honk, "EXPERIMENTAL", " (experimental on Windows)"):   # as on Windows
            self.assertEqual(self.state.autotarget_info()["status"], "ready (experimental on Windows)")

    def test_trigger_guards(self):
        import asyncio
        self.wire()

        async def none():
            out = []
            self.state.highway_cfg["autotarget"] = False   # off (the default)
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(20), "BoostValue": 4})
            out.append(self.state.maybe_autotarget())
            self.state.highway_cfg["autotarget"] = True
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(21), "BoostValue": 4})   # an old one (catch-up)
            out.append(self.state.maybe_autotarget(now=self.now + 21 + ed_outrider.HIGHWAY_LIVE_S + 5))
            self.jump(22, 999, "Elsewhere", 60)   # off the route
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(23), "BoostValue": 4})
            out.append(self.state.maybe_autotarget())
            return out
        self.assertEqual(asyncio.run(none()), [False, False, False])
        self.assertIs(ed_outrider.HIGHWAY["autotarget"], False)
        # jumped before the delay ran out: nothing pressed, nothing said
        self.jump(30, 102, "Bridge B", 80)
        self.status["destination"] = None

        async def moved():
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(31), "BoostValue": 4})
            self.assertTrue(self.state.maybe_autotarget())
            await asyncio.sleep(0.05)
            self.jump(32, 103, "Scoop C", 100)
            await self.state.autotarget_task
        asyncio.run(moved())
        self.assertEqual((self.state.autotarget_last["why"], self.game.writes, self.moments()), ("you had jumped", [], []))
        # the next system already the target: no keys, nothing said
        self.status["destination"] = {"System": 104, "Name": "Neu D"}

        async def already():
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(33), "BoostValue": 4})
            self.assertTrue(self.state.maybe_autotarget())
            await self.state.autotarget_task
        asyncio.run(already())
        self.assertEqual((self.state.autotarget_last["why"], self.state.autotarget_last["done"], self.game.writes, self.moments()),
                         ("already the target", True, [], []))
        # a panel open at the time: refused, said as a failure
        self.status.update(destination=None, gui_focus=2)
        asyncio.run(self._boost_and_wait(34))
        self.assertEqual(self.moments(), [(False, "Failed to target neutron jump target Neu D")])
        self.assertIn("the external panel is open", self.state.autotarget_last["why"])
        self.assertEqual(self.game.writes, [])

    async def _boost_and_wait(self, s):
        self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(s), "BoostValue": 4})
        self.assertTrue(self.state.maybe_autotarget())
        await self.state.autotarget_task

    def test_switch_off_stops_a_run_under_way(self):   # CX-F1, F12: the device stays open for auto honk, the run stops
        import asyncio
        self.wire()
        self.honker.owners = {"target", "honk"}

        async def go():
            loop = asyncio.get_running_loop()
            self.game.on_type = lambda g: len(g.text) == 3 and loop.call_soon_threadsafe(self.state.set_autotarget, False)
            await self._boost_and_wait(2)
        asyncio.run(go())
        self.assertIsNotNone(self.honker.ui)                     # auto honk still has its keyboard
        self.assertIsNone(self.status["destination"])            # nothing plotted after the switch-off
        self.assertTrue(self.released())
        self.assertEqual(self.status["gui_focus"], 0)
        self.assertEqual((self.state.autotarget_last["done"], self.state.autotarget_last["why"]), (False, "stopped"))
        self.assertEqual(self.moments(), [])                     # stopped on purpose: nothing said

    def test_switch_off_with_auto_honk_off_closes_the_map(self):
        """Auto honk off (auto-target the keyboard's only owner): switching auto-target off mid-run still closes the map
        the run opened, then the device (the Fable sweep, 2026-10-09: it left the game in the galaxy map)."""
        import asyncio
        self.wire()
        self.honker.owners = {"target"}

        async def go():
            loop = asyncio.get_running_loop()
            self.game.on_type = lambda g: len(g.text) == 3 and loop.call_soon_threadsafe(self.state.set_autotarget, False)
            await self._boost_and_wait(2)
        asyncio.run(go())
        self.assertEqual(self.status["gui_focus"], 0)                # the map closed again
        self.assertTrue(self.released())
        self.assertIsNone(self.honker.ui)                          # and the keyboard, nobody wants it now
        self.assertEqual((self.state.autotarget_last["done"], self.state.autotarget_last["why"]), (False, "stopped"))

    def test_switch_off_while_waiting_for_auto_honk(self):
        import asyncio
        self.wire()
        self.state._honk_running = {"id64": 101}

        async def go():
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(2), "BoostValue": 4})
            self.assertTrue(self.state.maybe_autotarget())
            await asyncio.sleep(0.4)
            self.state.set_autotarget(False)
            self.state._honk_running = None   # the honk ends after the switch-off
            await self.state.autotarget_task
        asyncio.run(go())
        self.assertEqual((self.game.writes, self.state.autotarget_last, self.moments()), ([], None, []))

    def test_route_cleared_or_replaced_stops_it(self):   # CX-F2
        import asyncio
        self.wire()

        async def cleared():
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(2), "BoostValue": 4})
            self.assertTrue(self.state.maybe_autotarget())
            await asyncio.sleep(0.05)
            self.state.highway_clear()   # during the countdown
            await self.state.autotarget_task
        asyncio.run(cleared())
        self.assertEqual((self.game.writes, self.state.autotarget_last, self.moments()), ([], None, []))
        # replaced mid-run: the run for the old route stops between two keys
        hw = hwy_plot_exact(self)
        hwy_jump(self, 3, 101, "Neu A", 50)
        rows = ed_outrider.highway_rows("exact", self.EXACT)

        async def replaced():
            loop = asyncio.get_running_loop()
            self.game.on_type = lambda g: len(g.text) == 2 and loop.call_soon_threadsafe(
                self.state.highway_store, rows, {"plotter": "exact", "options": {}, "ship": None})
            await self._boost_and_wait(4)
        asyncio.run(replaced())
        self.assertNotEqual((ed_outrider.meta_get(self.state.db, "highway") or {}).get("id"), hw["id"])
        self.assertIsNone(self.status["destination"])
        self.assertEqual((self.state.autotarget_last["why"], self.moments()), ("stopped", []))

    def test_wrong_system_said_by_name(self):   # Q3
        import asyncio
        self.wire()
        self.game.no_plot = True
        self.game.on_close = lambda g: self.status.update(destination={"System": 555, "Name": "Col 285 Sector AB-C d13-5"})
        asyncio.run(self._boost_and_wait(2))
        self.assertEqual(self.moments(), [(False, "Targeted the wrong system: Col 285 Sector AB-C d13-5. Check before you jump.")])

    def test_multi_hop_plot_via_navroute(self):   # F2: the game's NavRoute.json ends at the next system
        import asyncio
        self.wire()
        self.game.plot_to = 4242   # the first hop of a plotted route

        def closed(g):
            self.j.navroute_end = 102
        self.game.on_close = closed
        asyncio.run(self._boost_and_wait(2))
        self.assertEqual(self.moments(), [(True, "Successfully targeted neutron jump target Bridge B")])

    def test_navroute_end_follows_the_route(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            hop = lambda n, a: {"StarSystem": n, "SystemAddress": a, "StarPos": [a, 0, 0], "StarClass": "K"}
            with open(os.path.join(d, "NavRoute.json"), "w") as f:
                json.dump({"timestamp": self.ts(5), "event": "NavRoute", "Route": [hop("Here", 101), hop("Hop", 4242),
                                                                                    hop("Bridge B", 102)]}, f)
            self.j.read_navroute(d)
        self.assertEqual(self.j.navroute_end, 102)
        self.j.reload()   # a rolled-back tick or a restart: from the database
        self.assertEqual(self.j.navroute_end, 102)
        self.j.handle({"event": "NavRouteClear", "timestamp": self.ts(6)})
        self.assertIsNone(self.j.navroute_end)

    def test_honk_goes_first(self):
        import asyncio
        self.wire()
        self.state._honk_running = {"id64": 101}   # an auto honk is due on this arrival

        async def go():
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(2), "BoostValue": 4})
            self.assertTrue(self.state.maybe_autotarget())
            await asyncio.sleep(0.4)
            waiting = list(self.game.writes)
            self.state._honk_running = None   # the honk is done
            await self.state.autotarget_task
            return waiting
        self.assertEqual(asyncio.run(go()), [])
        self.assertEqual(self.moments(), [(True, "Successfully targeted neutron jump target Bridge B")])
        # a honk that never ends: auto-target gives up, pressing nothing
        self.state._honk_running, self.status["destination"] = {"id64": 101}, None
        self.game.writes.clear()
        with unittest.mock.patch.object(ed_outrider, "AUTOTARGET_HONK_WAIT", 0.2):
            asyncio.run(self._boost_and_wait(5))
        self.assertEqual((self.state.autotarget_last["why"], self.game.writes), ("auto honk was still running", []))

    # ---- the endpoints ----

    def test_test_endpoint_and_toggle(self):
        import asyncio
        from aiohttp.test_utils import TestClient, TestServer
        self.state.autotarget_test_countdown = 0.05

        async def go():
            out = {}
            async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                post = lambda path, **kw: c.post(path, **kw)
                r = await post("/api/highway/autotarget/test")   # not started (no targeter)
                out["none"] = (r.status, await r.json())
                self.state.honker, self.state.targeter = self.honker, self.targeter
                r = await post("/api/highway/autotarget/test")
                out["noroute"] = (r.status, await r.json())
                hwy_plot_exact(self)
                hwy_jump(self, 1, 101, "Neu A", 50)
                # "test now" needs no route: the nearest Nearby system within 90% of the range you have now
                self.state.range_now = lambda: 40.0
                self.state.systems = {101: {"id64": 101, "name": "Neu A", "distance": 0},
                                      555: {"id64": 555, "name": "Col 285 Sector AB-C d13-5", "distance": 39},   # over 36
                                      102: {"id64": 102, "name": "Bridge B", "distance": 30}}
                self.j.status_json = self.status
                self.status["gui_focus"] = 6
                r = await post("/api/highway/autotarget/test")
                out["focus"] = (r.status, await r.json())
                self.status["gui_focus"] = 0
                os.rename(os.path.join(self.binds, "HCS X56 Attempt 1.4.2.binds"), os.path.join(self.binds, "gone.xml"))
                r = await post("/api/highway/autotarget/test")
                out["binds"] = (r.status, await r.json())
                os.rename(os.path.join(self.binds, "gone.xml"), os.path.join(self.binds, "HCS X56 Attempt 1.4.2.binds"))
                r = await post("/api/highway/autotarget/test", headers={"Origin": "http://evil.example"})
                out["guard"] = r.status
                r = await post("/api/highway/autotarget/test")
                out["started"] = (r.status, await r.json())
                r = await post("/api/highway/autotarget/test")
                out["busy"] = r.status
                await self.state.autotarget_test_task
                out["test"] = dict(self.state.autotarget_test)
                # the toggle and the delay (remembered), and their refusals
                r = await post("/api/highway/autotarget", json={"enabled": True, "delay": 3})
                out["toggle"] = (r.status, (await r.json())["enabled"])
                out["bad"] = [(await post("/api/highway/autotarget", json=b)).status
                              for b in ({"enabled": "yes"}, {"delay": 61}, {"delay": True}, [1])]
                r = await c.get("/api/nearby")
                out["payload"] = (await r.json())["autotarget"]
            return out
        out = asyncio.run(go())
        self.assertEqual(out["none"], (400, {"error": "not started"}))
        self.assertEqual(out["noroute"][0], 400)   # no position, no Nearby list yet: refused, saying why
        self.assertEqual(out["focus"], (400, {"error": "the galaxy map is open (the cockpit must have focus)"}))
        self.assertEqual(out["binds"][0], 400)
        self.assertIn("no keyboard binding for GalaxyMapOpen", out["binds"][1]["error"])
        self.assertEqual(out["guard"], 403)
        self.assertEqual(out["started"], (200, {"system": "Bridge B", "in": 0.05, "seq": 1, "dry_run": False, "kind": "test"}))
        self.assertEqual(out["busy"], 409)
        self.assertEqual((out["test"]["state"], out["test"]["system"]), ("done", "Bridge B"))
        self.assertEqual(self.moments(), [(True, "Successfully targeted neutron jump target Bridge B")])
        self.assertEqual(self.honker.owners, {"target"})   # the test's hold on the keyboard was let go
        self.assertEqual(out["toggle"], (200, True))
        self.assertEqual(out["bad"], [400, 400, 400, 400])
        self.assertEqual(ed_outrider.meta_get(self.db, "autotarget"), {"enabled": True, "delay": 3.0})
        self.assertEqual((out["payload"]["enabled"], out["payload"]["delay"], out["payload"]["last"]["test"]), (True, 3.0, True))

    def test_target_next_endpoint(self):   # review Q4: Target next / Retry, whether or not auto-target is on
        import asyncio
        from aiohttp.test_utils import TestClient, TestServer
        self.state.honker, self.state.targeter = self.honker, self.targeter
        self.j.status_json = self.status
        self.assertFalse(self.state.highway_cfg["autotarget"])

        async def go():
            out = {}
            async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                async def post(body=None, **kw):
                    r = await c.post("/api/highway/target", **({"json": body} if body is not None else {}), **kw)
                    return r.status, await r.json()
                out["noroute"] = await post()
                hwy_plot_exact(self)
                hwy_jump(self, 1, 101, "Neu A", 50)
                out["bad"] = [(await post(b))[0] for b in ({"countdown": 11}, {"countdown": True}, {"countdown": "5"})]
                out["badjson"] = (await c.post("/api/highway/target", data="[1]",
                                               headers={"Content-Type": "application/json"})).status
                out["guard"] = (await c.post("/api/highway/target", headers={"Origin": "http://evil.example"})).status
                out["started"] = await post({"countdown": 0})
                out["busy"] = (await post())[0]
                await self.state.autotarget_test_task
                out["last"] = dict(self.state.autotarget_last)
                # off the route: the closest route system, passed or not (Neu A, 10 ly away, over Bridge B)
                self.status["destination"] = None
                hwy_jump(self, 2, 900, "Detour", 60)
                self.here = 900
                self.game.systems["Neu A"] = 101
                out["near"] = await post({"countdown": 0})
                await self.state.autotarget_test_task
                out["near_last"] = dict(self.state.autotarget_last)
            return out
        out = asyncio.run(go())
        self.assertEqual(out["noroute"], (400, {"error": "no route is plotted"}))
        self.assertEqual((out["bad"], out["badjson"], out["guard"]), ([400, 400, 400], 400, 403))
        self.assertEqual(out["started"], (200, {"system": "Bridge B", "in": 0, "seq": 1, "dry_run": False, "kind": "next"}))
        self.assertEqual(out["busy"], 409)
        last = out["last"]
        self.assertEqual((last["done"], last["kind"], last["test"], last["index"], last["system"]), (True, "next", False, 2, "Bridge B"))
        self.assertEqual(self.moments()[0], (True, "Successfully targeted neutron jump target Bridge B"))
        self.assertEqual(self.honker.owners, {"target"})   # the run's hold on the keyboard was let go
        self.assertEqual(out["near"][1]["system"], "Neu A")
        self.assertEqual((out["near_last"]["done"], out["near_last"]["index"]), (True, 1))

    def test_target_next_waits_out_the_arrivals_danger_flag(self):
        """The game sets in-danger on every jump until some 16-26 s after the arrival (logged in game 2026-10-09):
        Target next pressed then waits for it to clear and runs, where it refused "you are in danger". Interdicted,
        or still in danger AUTOTARGET_DANGER_WAIT s after the arrival, it still refuses."""
        import asyncio
        self.state.honker, self.state.targeter = self.honker, self.targeter
        self.j.status_json = self.status
        T = self.T
        hwy_plot_exact(self)
        self.now = time.time()
        hwy_jump(self, -5, 101, "Neu A", 50)   # arrived 5 s ago
        self.here = 101
        self.status["flags"] |= T.FLAG_IN_DANGER

        async def go():
            out = {}
            body, status = self.state.start_autotarget_run("next", countdown=0)
            out["started"] = (status, body.get("system"), body.get("error"))
            await asyncio.sleep(0.3)
            out["waiting"] = (self.state.autotarget_running, [n for n, v in self.game.writes if v])
            self.status["flags"] &= ~T.FLAG_IN_DANGER   # the flag clears: the run goes
            await self.state.autotarget_test_task
            out["last"] = dict(self.state.autotarget_last)
            # interdicted: no waiting
            self.status.update(flags=self.status["flags"] | T.FLAG_IN_DANGER | T.FLAG_INTERDICTED, destination=None)
            out["interdicted"] = self.state.start_autotarget_run("next", countdown=0)
            # an arrival over AUTOTARGET_DANGER_WAIT s ago: in danger is a real one, refused
            self.status["flags"] &= ~T.FLAG_INTERDICTED
            self.j.jump_arrival = dict(self.j.jump_arrival, ts=ed_outrider.iso_ts(time.time() - ed_outrider.AUTOTARGET_DANGER_WAIT - 5))
            out["old"] = self.state.start_autotarget_run("next", countdown=0)
            return out
        out = asyncio.run(go())
        self.assertEqual(out["started"], (200, "Bridge B", None))
        self.assertEqual(out["waiting"], (None, []))   # nothing pressed while the flag was on
        self.assertEqual((out["last"]["done"], out["last"]["system"]), (True, "Bridge B"))
        self.assertEqual(out["interdicted"], ({"error": "you are in danger"}, 400))
        self.assertEqual(out["old"], ({"error": "you are in danger"}, 400))
        # it said it was waiting, once, with how long it may wait (the 60 s from the arrival, 5 s ago)
        said = [m for m in self.j.moments if m["kind"] == "autotarget" and m.get("what") == "waiting"]
        self.assertEqual(len(said), 1)
        self.assertIn(said[0]["secs"], (54, 55))
        self.assertEqual(said[0]["text"], "Not targeting due to danger. I will keep trying until you are out of danger, "
                                          f"for up to {said[0]['secs']} seconds.")

    def test_danger_wait_after_supercruise_entry_and_a_restart(self):
        """The flag comes on for entering supercruise too (lifting off a planet 15 min after the arrival, logged in game
        2026-10-09): that starts the window as an arrival does. And after a restart (the arrival not read again: no
        jump_arrival) the restored position's arrival time stands in (review 2026-10-10 #5)."""
        self.j.status_json = self.status
        self.now = time.time()
        hwy_jump(self, -900, 101, "Neu A", 50)   # arrived 15 minutes ago
        self.status["flags"] |= self.T.FLAG_IN_DANGER
        self.assertIsNone(self.state.arrival_danger_until())   # a real danger, as far as it knows
        self.j.handle({"event": "SupercruiseEntry", "timestamp": self.ts(-3), "StarSystem": "Neu A", "SystemAddress": 101})
        self.assertAlmostEqual(self.state.arrival_danger_until(), self.now - 3 + ed_outrider.AUTOTARGET_DANGER_WAIT, delta=1)
        # an entry in another system (before a jump) counts for nothing here
        self.j.supercruise_entry = {"id64": 999, "ts": self.ts(-3)}
        self.assertIsNone(self.state.arrival_danger_until())
        # restarted 5 s after an arrival: jump_arrival is gone, pos (from meta) has the arrival's ts
        self.j.supercruise_entry, self.j.jump_arrival = None, None
        self.j.pos = dict(self.j.pos, ts=self.ts(-5))
        self.assertIsNotNone(self.state.arrival_danger_until())

    def test_copilot_button_targets_next(self):
        """The co-pilot button's layout (the author, 2026-10-08): in the ship a single press targets the next route
        system after a short wait: the survey / trade route's next first, else the Highway's; with neither, the
        "nothing to target" line. A double press is the status report, a hold the hush; elsewhere a single press does
        nothing."""
        import asyncio
        import outrider.riches as riches
        self.state.honker, self.state.targeter = self.honker, self.targeter
        self.status["flags"] |= ed_outrider.FLAG_IN_MAIN_SHIP
        self.j.status_json = self.status
        self.state.COPILOT_TARGET_DELAY_S = 0.01
        moments = lambda: [m for m in self.j.moments if m["kind"] == "autotarget"]

        async def press():
            self.state.copilot_gesture("status")
            if self.state.autotarget_test_task:
                await self.state.autotarget_test_task
        asyncio.run(press())
        self.assertEqual([(m["what"], m["why"]) for m in moments()], [("nothing", "no route is plotted")])
        self.assertEqual(self.state.copilot["seq"], 0)   # no status report instead
        hwy_plot_exact(self)
        hwy_jump(self, 1, 101, "Neu A", 50)
        asyncio.run(press())
        self.assertEqual((self.state.autotarget_last["system"], self.state.autotarget_last["done"]), ("Bridge B", True))
        # a survey route as well: its next system first
        self.status["destination"] = None
        rows = riches.riches_rows([{"name": "Neu A", "id64": 101, "x": 50, "y": 0, "z": 0, "jumps": 0, "bodies": []},
                                   {"name": "Col 285 Sector AB-C d13-5", "id64": 555, "x": 60, "y": 0, "z": 0, "jumps": 1, "bodies": []}])
        self.state.riches_store(rows, {"options": {}})
        asyncio.run(press())
        self.assertEqual(self.state.autotarget_last["system"], "Col 285 Sector AB-C d13-5")
        # the survey route done: the Highway's next again
        rc = ed_outrider.meta_get(self.db, "riches")
        ed_outrider.meta_set(self.db, "riches", dict(rc, done_ts="2026-10-08T00:00:00Z"))
        self.status["destination"] = None
        asyncio.run(press())
        self.assertEqual(self.state.autotarget_last["system"], "Bridge B")
        # a press while a tap's targeting counts down (a double press slower than double_ms) cancels it before any key,
        # and that press's own tap is the status report it was meant to be, not a second target
        self.state.COPILOT_TARGET_DELAY_S = 0.3
        self.status["destination"] = None
        writes, seq = len(self.game.writes), self.state.copilot["seq"]

        async def slow_double():
            self.state.copilot_gesture("status")
            await asyncio.sleep(0.05)
            self.state.copilot_press()
            self.state.copilot_gesture("status")
            await self.state.autotarget_test_task
        asyncio.run(slow_double())
        self.assertEqual(len(self.game.writes), writes)   # nothing pressed
        self.assertEqual((self.state.copilot["seq"], self.state.copilot["action"]), (seq + 1, "status"))
        self.assertEqual(self.state.autotarget_test["state"], "failed")
        self.state.copilot_press()   # nothing counting down: a press changes nothing
        self.assertFalse(self.state._copilot_cancelled)
        # the same while the run waits out the arrival's danger flag, keys not pressed yet (the Fable review of
        # 2026-10-10, #4): it said "auto-target is already running" and then pressed the keys anyway
        self.state.COPILOT_TARGET_DELAY_S = 0.01
        self.status["destination"] = None
        self.status["flags"] |= self.T.FLAG_IN_DANGER
        self.j.jump_arrival = {"id64": self.j.pos["id64"], "ts": ed_outrider.iso_ts(time.time() - 3)}
        writes, seq, n = len(self.game.writes), self.state.copilot["seq"], len(moments())

        async def press_in_the_wait():
            self.state.copilot_gesture("status")
            await asyncio.sleep(0.2)                     # past the countdown: waiting for the flag, no key yet
            waiting = self.state.autotarget_test["state"]
            self.state.copilot_press()
            self.state.copilot_gesture("status")
            await self.state.autotarget_test_task
            return waiting
        self.assertEqual(asyncio.run(press_in_the_wait()), "running")
        self.assertEqual(len(self.game.writes), writes)   # nothing pressed
        self.assertEqual((self.state.copilot["seq"], self.state.copilot["action"]), (seq + 1, "status"))
        self.assertEqual(self.state.autotarget_test["state"], "failed")
        self.assertFalse([m for m in moments()[n:] if (m.get("text") or "").startswith("Not targeting: auto-target is already running")])
        self.status["flags"] &= ~self.T.FLAG_IN_DANGER
        # double: the status report; hold: the hush
        self.state.copilot_gesture("again")
        self.assertEqual(self.state.copilot["action"], "status")
        self.state.copilot_gesture("hush")
        self.assertIsNotNone(self.state.hush)
        # on foot: a single press does nothing
        self.status["flags"], self.status["flags2"] = 0, 1
        n = len(moments())
        asyncio.run(press())
        self.assertEqual(len(moments()), n)

    def test_target_next_targets(self):
        T = self.state.autotarget_target
        self.assertEqual(T(manual=True), (None, "no route is plotted"))
        hwy_plot_exact(self)   # at the start (Start, index 0): the next is Neu A
        self.assertEqual(T(manual=True)[0]["name"], "Neu A")
        hwy_jump(self, 1, 101, "Neu A", 50)
        tgt = T(manual=True)[0]
        self.assertEqual((tgt["name"], tgt["index"], tgt["here"]), ("Bridge B", 2, 101))
        hwy_jump(self, 2, 900, "Detour", 85)   # off the route, 5 ly past Bridge B: Bridge B, the closest
        self.assertEqual(T(), (None, "you are not at a system on the route"))   # the automatic run never does this
        self.assertEqual(T(manual=True)[0]["name"], "Bridge B")
        last = len(self.EXACT["jumps"]) - 1
        end = self.EXACT["jumps"][last]
        hwy_jump(self, 3, end["id64"], end["name"], end["x"])
        self.assertIn(T(manual=True)[1], ("you are at the end of the route", "the highway is complete"))

    def test_aim_any_route_system(self):
        """🎯 on any system of a route (the author, 2026-10-07): the Highway's or the survey route's (Road to Riches /
        Exomastery), checked (a row of the route plotted now, not where you are, with an id64), through the same run as
        Target next; the request's route and index validated; a survey route cleared stops a run aimed at it."""
        import asyncio
        import outrider.riches as riches
        from aiohttp.test_utils import TestClient, TestServer
        self.state.honker, self.state.targeter = self.honker, self.targeter
        self.j.status_json = self.status
        R = self.state.route_target
        self.assertEqual(R("highway", 1), (None, "no route is plotted"))
        self.assertEqual(R("moon", 1)[1], "route must be highway or survey")
        hwy_plot_exact(self)
        hwy_jump(self, 1, 101, "Neu A", 50)
        self.assertEqual(R("highway", 99)[1], "the route has no system 99")
        self.assertEqual(R("highway", True)[1], "index must be a whole number")
        here = self.state.highway_state()[0]["at"]
        self.assertEqual(R("highway", here)[1], "you are in Neu A already")
        self.assertEqual((R("highway", 2)[0]["name"], R("highway", 2)[0]["index"]), ("Bridge B", 2))
        self.assertEqual(R("highway", 0)[0]["name"], self.state.highway_state()[1][0]["system"])   # back to a passed one too
        # a survey route: its rows, by index
        rows = riches.riches_rows([{"name": "Neu A", "id64": 101, "x": 50, "y": 0, "z": 0, "jumps": 0, "bodies": []},
                                   {"name": "Bridge B", "id64": self.game.systems["Bridge B"], "x": 70, "y": 0, "z": 0, "jumps": 1,
                                    "bodies": []}])
        self.state.riches_store(rows, {"options": {}})
        self.assertEqual((R("survey", 1)[0]["name"], R("survey", 1)[0]["route"]), ("Bridge B", self.state.riches_state()[0]["id"]))

        async def go():
            out = {}
            async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                async def post(body):
                    r = await c.post("/api/highway/target", json=body)
                    return r.status, await r.json()
                out["bad"] = [(await post(b))[0] for b in ({"route": "moon", "index": 1}, {"route": "survey", "index": "1"},
                                                            {"route": "survey"}, {"index": 1})]
                out["started"] = await post({"route": "survey", "index": 1, "countdown": 0})
                await self.state.autotarget_test_task
                out["last"] = dict(self.state.autotarget_last)
            return out
        out = asyncio.run(go())
        self.assertEqual(out["bad"], [400, 400, 400, 400])
        self.assertEqual((out["started"][0], out["started"][1]["system"]), (200, "Bridge B"))
        self.assertEqual((out["last"]["done"], out["last"]["system"]), (True, "Bridge B"))
        # a run aimed at the survey route stops when that route is cleared (the Highway's clear would not touch it)
        self.state.autotarget_test_countdown = 0.2
        self.status["destination"] = None

        async def stop():
            body, status = self.state.start_autotarget_run("next", aim=("survey", 1))
            self.assertEqual(status, 200, body)
            await asyncio.sleep(0.05)
            self.state.riches_clear()
            await self.state.autotarget_test_task
        writes = len(self.game.writes)
        asyncio.run(stop())
        self.assertEqual(len(self.game.writes), writes)
        self.assertEqual((self.state.autotarget_test["state"], self.state.autotarget_test["why"]), ("failed", "stopped"))

    def test_aim_at_your_carrier(self):
        """🎯 beside your fleet carrier (the author, 2026-10-11): its system, through the same run as a route's 🎯; not
        with no carrier, in its system already, or decommissioned."""
        import asyncio
        from aiohttp.test_utils import TestClient, TestServer
        self.state.honker, self.state.targeter = self.honker, self.targeter
        self.j.status_json = self.status
        C = self.state.carrier_target
        self.assertEqual(C(), (None, "no fleet carrier in your journals"))
        hwy_jump(self, 1, 101, "Neu A", 50)
        self.j.carrier = {"id": 3700000001, "id64": 101, "system": "Neu A"}
        self.assertEqual(C()[1], "your carrier is in this system (Neu A)")
        self.j.carrier = {"id": 3700000001, "id64": self.game.systems["Bridge B"], "system": "Bridge B"}
        self.assertEqual((C()[0]["name"], C()[0]["here"]), ("Bridge B", 101))
        self.assertEqual(self.state.route_target("carrier", None), C())

        async def go():
            async with TestClient(TestServer(ed_outrider.make_app(self.state))) as c:
                r = await c.post("/api/highway/target", json={"route": "carrier", "countdown": 0})
                started = (r.status, await r.json())
                await self.state.autotarget_test_task
                return started, dict(self.state.autotarget_last)
        started, last = asyncio.run(go())
        self.assertEqual((started[0], started[1]["system"]), (200, "Bridge B"))
        self.assertEqual((last["done"], last["system"]), (True, "Bridge B"))

    def test_supercharge_during_a_manual_run_starts_no_second(self):
        """Review 2026-10-08 #1: a supercharge while Target next (or 🎯, or the co-pilot's tap) is counting down or
        pressing keys started the automatic run as well, two galaxy-map sequences one behind the other, the second
        overwriting the first's cancel token. One sequence at a time, whichever kind came first."""
        import asyncio
        self.wire()
        self.state.autotarget_test_countdown = 0.15

        async def go():
            body, status = self.state.start_autotarget_run("next")
            self.assertEqual(status, 200, body)
            self.j.handle({"event": "JetConeBoost", "timestamp": self.ts(2), "BoostValue": 4})
            second = self.state.maybe_autotarget()
            await self.state.autotarget_test_task
            return second
        self.assertFalse(asyncio.run(go()))
        self.assertIsNone(self.state.autotarget_task)
        self.assertEqual(self.moments(), [(True, "Successfully targeted neutron jump target Bridge B")])

    def test_run_tokens_are_per_run(self):
        """Each run's own cancel token belongs to its thread: one run starting never hides another's."""
        import threading
        mine, other = threading.Event(), threading.Event()
        self.targeter.run_cancel = mine
        seen = []
        t = threading.Thread(target=lambda: (setattr(self.targeter, "run_cancel", other), seen.append(self.targeter.run_cancel)))
        t.start()
        t.join()
        self.assertIs(seen[0], other)
        self.assertIs(self.targeter.run_cancel, mine)   # this thread's run still sees its own token
        mine.set()
        self.assertTrue(self.targeter.cancelled())
        self.targeter.run_cancel = None

    def test_target_next_stops_with_the_route(self):   # the Batch 4 lifecycle: a cleared route stops it too
        import asyncio
        self.wire()
        self.state.highway_cfg["autotarget"] = False
        self.state.autotarget_test_countdown = 0.2

        async def go():
            body, status = self.state.start_autotarget_run("next")
            self.assertEqual(status, 200, body)
            await asyncio.sleep(0.05)
            self.state.highway_clear()
            await self.state.autotarget_test_task
        asyncio.run(go())
        self.assertEqual((self.game.writes, self.moments()), ([], []))
        self.assertEqual((self.state.autotarget_test["state"], self.state.autotarget_test["why"]), ("failed", "stopped"))

    def test_config_round_trip_and_refusals(self):
        import contextlib, io, tomllib
        args = argparse.Namespace(journals=None, legacy=None, host=None, port=None, radius=None, db=None)
        cfg = {"highway": {"autotarget_entry": "paste", "autotarget_map_wait": 8, "autotarget_search_wait": 3.5,
                           "autotarget_key_delay": 0.1, "autotarget_keys": {"GalaxyMapOpen": "alt+KEY_RIGHTALT+t", "Enter": "KEY_KPENTER"},
                           "autotarget_plot": ["press UI_Up", "hold UI_Select 1.5"], "autotarget_dry_run": True}}
        st = ed_outrider.settings_from(cfg, args, None, ([], []))["highway"]
        want = {"autotarget_entry": "paste", "autotarget_map_wait": 8.0, "autotarget_search_wait": 3.5, "autotarget_key_delay": 0.1,
                "autotarget_keys": {"GalaxyMapOpen": "KEY_LEFTALT+KEY_RIGHTALT+KEY_T", "Enter": "KEY_KPENTER"},
                "autotarget_plot": ["press UI_Up", "hold UI_Select 1.5"], "autotarget_dry_run": True}
        self.assertEqual({k: st[k] for k in want}, want)
        full = ed_outrider.settings_from(cfg, args, None, ([], []))
        back = tomllib.loads(ed_outrider.config_text(full))["highway"]
        self.assertEqual({k: back[k] for k in want}, want)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            st = ed_outrider.settings_from({"highway": {"autotarget_entry": "clipboard", "autotarget_keys": {"Fire": "KEY_A"},
                                                        "autotarget_plot": ["jump now"], "autotarget_map_wait": 500,
                                                        "autotarget_dry_run": "no"}}, args, None, ([], []))["highway"]
        self.assertEqual((st["autotarget_entry"], st["autotarget_keys"], st["autotarget_plot"], st["autotarget_map_wait"],
                          st["autotarget_dry_run"]), ("type", {}, ["hold CamZoomOut 0.2", "hold UI_Select 1"], 30.0, False))
        for k in ("autotarget_entry", "autotarget_keys", "autotarget_plot", "autotarget_dry_run"):
            self.assertIn(f"[highway] {k}", err.getvalue())
        # the State hands them to the Targeter without the prefix
        self.state.highway_cfg = dict(full["highway"])
        self.assertEqual(self.state.autotarget_cfg()["entry"], "paste")
