"""Unit tests: potential green gas giants (outrider/ggg.py, the community's seven-layer ladder model; the author's
ask, 2026-10-12), and the server's alert, Here's and Nearby's marks.

Run all: python3 -m unittest discover tests (or scripts/verify.sh).
"""
import time
import types
import unittest

from support import ed_outrider  # also puts the repository root on sys.path
import outrider.ggg as G  # noqa: E402

# a Class I giant whose bottom cloud layer sits exactly on its 250 K border (capped ladder: the exact float32 path)
GREEN = {"PlanetClass": "Sudarsky class I gas giant", "SurfaceTemperature": 250.0, "MassEM": 300.0, "Radius": 7.0e7}


class Model(unittest.TestCase):
    def test_predictions(self):
        p = G.predict(GREEN)
        self.assertEqual((p["chance"], p["class"], p["rung"], p["border"]), ("likely", "class1", 1, 250))
        self.assertIsNone(G.predict(dict(GREEN, SurfaceTemperature=251.3)))                     # no layer on a border
        self.assertIsNone(G.predict(dict(GREEN, SurfaceTemperature=100.0)))                     # the nudged window
        self.assertIsNone(G.predict(dict(GREEN, PlanetClass="Sudarsky class II gas giant")))    # no rule known
        self.assertIsNone(G.predict(dict(GREEN, MassEM=None)))                                  # needs mass and radius
        self.assertIsNone(G.predict({"PlanetClass": "Icy body", "SurfaceTemperature": 250.0, "MassEM": 1.0, "Radius": 1e6}))
        q = G.predict({"PlanetClass": "Sudarsky class III gas giant", "SurfaceTemperature": 700.0, "MassEM": 500.0, "Radius": 7e7})
        self.assertEqual(q["chance"], "possible")    # a derived rule (680-900 K): "possible", not "likely"
        self.assertEqual([G.gas_class(c) for c in ("Sudarsky class IV gas giant", "Class V gas giant", "Gas giant with water based life",
                                                    "Gas giant with ammonia based life", "Water giant", "Helium rich gas giant", "Rocky body")],
                         ["class4", "class5", "water_life", "ammonia_life", "water_giant", "helium", None])

    def test_records_and_codex(self):
        """Outrider's records (yours and Spansh's keep the journal's temperature; radius in km), and the codex names."""
        rec = {"type": "Planet", "subtype": "Class I gas giant", "temperature": 250.0, "radius_km": 70000.0,
               "ed": {"PlanetClass": "Sudarsky class I gas giant", "MassEM": 300.0}}
        self.assertEqual(G.predict_record(rec)["chance"], "likely")
        self.assertIsNone(G.predict_record(dict(rec, type="Star")))
        self.assertTrue(G.confirmed(["Green Class I Gas Giant"]))
        self.assertFalse(G.confirmed(["Stratum Tectonicas - Grey", None]))


class Server(unittest.TestCase):
    def setUp(self):
        self.db = ed_outrider.open_db(":memory:")
        self.addCleanup(self.db.close)
        self.j = ed_outrider.Journals(self.db)
        self.state = ed_outrider.State(self.db, self.j, types.SimpleNamespace(cached=lambda i: (None, None)), 25)
        self.now = time.time()

    def test_alert_here_and_nearby(self):
        at = lambda s: ed_outrider.iso_ts(self.now - 60 + s)
        self.j.handle({"event": "FSDJump", "timestamp": at(0), "StarSystem": "Gree Sector AB-C d1-2", "SystemAddress": 9, "StarPos": [0, 0, 0]})
        scan = {"event": "Scan", "timestamp": at(5), "ScanType": "Detailed", "StarSystem": "Gree Sector AB-C d1-2", "SystemAddress": 9,
                "BodyID": 4, "BodyName": "Gree Sector AB-C d1-2 B 3", "DistanceFromArrivalLS": 900.0, "WasDiscovered": False, "WasMapped": False,
                **GREEN}
        self.j.handle(scan)
        said = [m for m in self.j.moments if m["kind"] == "ggg"]
        self.assertEqual([(m["body"], m["chance"], m["what"]) for m in said], [("B 3", "likely", "a class one gas giant")])
        self.j.handle(dict(scan, timestamp=at(6)))   # the same body again: no second alert
        self.assertEqual(len([m for m in self.j.moments if m["kind"] == "ggg"]), 1)
        body = next(b for b in self.state.system_detail(9)["bodies"] if b["name"] == "B 3")
        self.assertEqual((body["ggg"]["chance"], body["ggg"]["border"]), ("likely", 250))
        # Nearby's row: a system worth going to
        recs = [{"name": "Gree Sector AB-C d1-2 B 3", "body_id": 4, "type": "Planet", "temperature": 250.0, "radius_km": 70000.0,
                 "ed": {"PlanetClass": "Sudarsky class I gas giant", "MassEM": 300.0}}]
        self.assertEqual(ed_outrider.ggg_bodies(self.db, 9, "Gree Sector AB-C d1-2", recs), ([{"body": "B 3", "chance": "likely"}], 0))
        # the codex confirms it: no longer a guess
        self.db.execute("INSERT INTO codex (ts, entry_id, name, category, subcategory, region, system, system_name, body_id, is_new, new_traits, voucher) "
                        "VALUES (?, 1, 'Green Class I Gas Giant', 'Astronomical Bodies', 'Gas Giants', 18, 9, 'Gree Sector AB-C d1-2', 4, 1, 0, 0)", (at(9),))
        self.assertEqual(ed_outrider.ggg_bodies(self.db, 9, "Gree Sector AB-C d1-2", recs), ([], 1))
        body = next(b for b in self.state.system_detail(9)["bodies"] if b["name"] == "B 3")
        self.assertEqual(body["ggg"], {"confirmed": True})
