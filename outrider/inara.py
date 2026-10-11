"""Inara's commander-profile API: what a journal line sends to the player's own Inara profile, and what Inara's answer
means. Pure: the server (ed_outrider.py) owns the queue, the API keys and the sending. (Planned in
project/PLAN-inara-2026-10-11.md, the author's notes, not in the repository.)

Inara takes a commander's career, not the galaxy: travel, ranks, credits, ships, inventory, missions and the like
(https://inara.cz/elite/inara-api-docs/, saved by the author 2026-10-11). Its rules, kept here:
- one POST of a batch of events per request, at most one request every MIN_GAP_S (Inara revokes a key that floods it);
- only what changed: a snapshot (ranks, inventory, the ship) goes when it differs from what was last sent to this
  commander's profile (Sent, kept in the database), never on every line the game writes;
- the journal's own timestamps; Live game only (the hub's gates); never the fleet carrier's hold (the API has none).
- `isBeingDeveloped` while the developer tries it (DEV_ENV, never a setting); DRY_ENV builds and logs, sends nothing.

The session's own following (the ship flown, a taxi, docking that must not be logged twice) is `track`, fed every
line through outrider.uploads.TRACKERS: build() then reads what the line meant.
"""
import hashlib
import json
import os

from . import materials, uploads

URL = "https://inara.cz/inapi/v1/"
SOFTWARE = "ED Outrider"   # appName: exactly as Inara whitelisted it
HOLD_S = 300               # an event waits at most this long for others to go with it
MIN_GAP_S = 35             # between two requests (EDMC's pace; Inara's limit is about two a minute)
BATCH = 100                # events per request, at most
RATE_WAIT_S = 3600         # Inara refused for the rate: an hour (it revokes access for that long)
DRY_ENV = "OUTRIDER_INARA_DRYRUN"   # a developer's switch: build and log the requests, send nothing
DEV_ENV = "OUTRIDER_INARA_DEV"      # a developer's switch: isBeingDeveloped (Inara skips the global events)
# events that send what waits (the journal's natural moments: Inara asks for batches on jumps and docks)
RELEASE = frozenset({"FSDJump", "CarrierJump", "Docked", "Shutdown"})


def _flag(env, environ=None):
    environ = os.environ if environ is None else environ
    return str(environ.get(env, "")).strip().lower() in ("1", "true", "yes", "on")


def dry_run(environ=None):
    """Whether Inara requests are only built and logged, never sent (OUTRIDER_INARA_DRYRUN=1)."""
    return _flag(DRY_ENV, environ)


def dev_mode(environ=None):
    """Whether requests say isBeingDeveloped (OUTRIDER_INARA_DEV=1): the developer's test account and trial."""
    return _flag(DEV_ENV, environ)


# ---- the session's own following (fed every line: outrider.uploads.TRACKERS) ----

def _state(session):
    t = session.track.get("inara")
    if t is None:
        t = session.track["inara"] = {"ship": {}, "taxi": None, "suppress_dock": False, "undocked": False,
                                      "starting": False, "credits": None, "loan": None, "line": {},
                                      "materials": materials.new_state(), "cargo": None, "locker": None}
    return t


def track(session, ev):
    """Follow what Inara's events need from earlier lines; `line` says what this one meant (build and hold read it)."""
    t = _state(session)
    name = ev.get("event")
    line = t["line"] = {}
    # the inventories, as the game last said them: materials folded (the Materials tab's fold), the ship's hold and the
    # ship locker as their latest event (its contents may be in Cargo.json / ShipLocker.json: read when sent)
    materials.apply(t["materials"], ev)
    if name == "Cargo" and ev.get("Vessel", "Ship") == "Ship":
        t["cargo"] = ev
    elif name == "ShipLocker":
        t["locker"] = ev
    if name == "Fileheader" and not uploads.continued(ev):
        t.update(ship={}, taxi=None, suppress_dock=False, undocked=False, starting=False, credits=None, loan=None)
    elif name == "LoadGame":
        # a new session: the start-up lines follow (ranks, inventory, the ship...) and end with the ship's Cargo
        t.update(starting=True, suppress_dock=False, undocked=False, credits=ev.get("Credits"), loan=ev.get("Loan"),
                 taxi=None, ship=_ship(ev.get("Ship"), ev.get("ShipID"), ev.get("ShipName"), ev.get("ShipIdent")))
    elif name == "Cargo" and ev.get("Vessel", "Ship") == "Ship" and t["starting"]:
        t["starting"] = False
        line["release"] = True   # the start-up's lines go together, now
    elif name == "Loadout":
        t["ship"] = _ship(ev.get("Ship"), ev.get("ShipID"), ev.get("ShipName"), ev.get("ShipIdent"))
    elif name == "ShipyardSwap":
        t["ship"] = _ship(ev.get("ShipType"), ev.get("ShipID"))
        t["suppress_dock"] = True
    elif name == "ShipyardNew":
        t["ship"] = _ship(ev.get("ShipType"), ev.get("NewShipID"))
        t["suppress_dock"] = True
    elif name == "SetUserShipName" and ev.get("ShipID") == t["ship"].get("id"):
        t["ship"] = dict(t["ship"], name=ev.get("UserShipName"), ident=ev.get("UserShipId"))
    elif name in ("BookTaxi", "BookDropship"):
        t["taxi"] = "dropship" if name == "BookDropship" else "shuttle"
    elif name == "Location":
        if ev.get("Docked"):
            t["suppress_dock"] = True   # docked at login: the Docked the game may write next is not a new docking
    elif name == "Undocked":
        t["undocked"] = True
        t["suppress_dock"] = False   # a login's or a swap's Docked the game did not write: none is coming now
    elif name in ("SupercruiseEntry", "FSDJump", "CarrierJump"):
        t["undocked"] = False   # left (a hyperspace jump can start from normal space, with no SupercruiseEntry)
    elif name == "Docked":
        # EDMC's rules: not the docking a login or a ship swap writes, nor docking again where you undocked without
        # leaving (a hop between pads), so the flight log is not filled with docks
        if t["undocked"]:
            t["undocked"] = False
        elif t["suppress_dock"]:
            t["suppress_dock"] = False
        else:
            line["log_dock"] = True
    if name in ("Docked", "FSDJump", "Touchdown") and not ev.get("Taxi"):
        t["taxi"] = None if name == "FSDJump" else t["taxi"]


uploads.TRACKERS.append(track)


def _ship(kind, game_id, name=None, ident=None):
    return {k: v for k, v in (("type", kind), ("id", game_id), ("name", name), ("ident", ident)) if v not in (None, "")}


# ---- what was last sent, per commander (kept in the database: State's meta inara_sent) ----

class Sent:
    """{commander: {what: digest}}: whether a snapshot (ranks, inventory, a ship...) differs from what was last sent
    to that commander's profile. changed() records it as sent at once (the event is queued in the same transaction);
    snapshot() / restore() for the tick's checkpoint, so a retried tick sends nothing twice."""

    def __init__(self, data=None, save=None):
        self.data = {c: dict(v) for c, v in (data or {}).items() if isinstance(v, dict)}
        self.save = save

    @staticmethod
    def digest(value):
        return hashlib.sha1(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]

    def changed(self, cmdr, what, value):
        d = self.digest(value)
        mine = self.data.setdefault(cmdr or "", {})
        if mine.get(what) == d:
            return False
        mine[what] = d
        if self.save:
            self.save(self.data)
        return True

    def forget(self, cmdr):
        """Nothing of this commander's counts as sent (dropped unsent, or a new key: maybe another Inara account): the
        next login sends every snapshot again."""
        if self.data.pop(cmdr or "", None) is not None and self.save:
            self.save(self.data)

    def snapshot(self):
        return json.loads(json.dumps(self.data))

    def restore(self, snap):
        self.data = json.loads(json.dumps(snap))


# ---- building Inara's events ----

def _event(ev, name, data):
    return (name, {"eventName": name, "eventTimestamp": ev.get("timestamp"), "eventData": data})


def _ship_fields(t, ev):
    """The ship flown (shipType, shipGameID), or a taxi's flag instead (the API's isTaxiShuttle / isTaxiDropship)."""
    if ev.get("Taxi"):
        return {"isTaxiDropship": True} if t["taxi"] == "dropship" else {"isTaxiShuttle": True}
    s = t["ship"]
    return {k: v for k, v in (("shipType", s.get("type")), ("shipGameID", s.get("id"))) if v is not None}


def _coords(ev):
    pos = ev.get("StarPos")
    return list(pos) if isinstance(pos, (list, tuple)) and len(pos) == 3 else None


def _travel(ev, session, t, sent):
    """The flight log and the current location."""
    name = ev.get("event")
    out = []
    if name == "FSDJump":
        data = {"starsystemName": ev.get("StarSystem"), "starsystemCoords": _coords(ev), "jumpDistance": ev.get("JumpDist")}
        out.append(_event(ev, "addCommanderTravelFSDJump", _clean(dict(data, **_ship_fields(t, ev)))))
    elif name == "CarrierJump":
        data = {"starsystemName": ev.get("StarSystem"), "starsystemCoords": _coords(ev), "stationName": ev.get("StationName"),
                "marketID": ev.get("MarketID")}
        out.append(_event(ev, "addCommanderTravelCarrierJump", _clean(dict(data, **_ship_fields(t, ev)))))
    elif name == "Docked" and t["line"].get("log_dock"):
        data = {"starsystemName": ev.get("StarSystem"), "stationName": ev.get("StationName"), "marketID": ev.get("MarketID")}
        out.append(_event(ev, "addCommanderTravelDock", _clean(dict(data, **_ship_fields(t, ev)))))
    elif name == "Touchdown" and ev.get("PlayerControlled", True):
        data = {"starsystemName": ev.get("StarSystem") or session.system, "starsystemBodyName": ev.get("Body"),
                "starsystemBodyCoords": _latlon(ev)}
        out.append(_event(ev, "addCommanderTravelLand", _clean(dict(data, **_ship_fields(t, ev)))))
    elif name == "DropshipDeploy":
        data = {"starsystemName": ev.get("StarSystem") or session.system, "starsystemBodyName": ev.get("Body"),
                "isTaxiDropship": True}
        out.append(_event(ev, "addCommanderTravelLand", _clean(data)))
    elif name == "Location":
        data = {"starsystemName": ev.get("StarSystem"), "starsystemCoords": _coords(ev)}
        if ev.get("Docked"):
            data.update(stationName=ev.get("StationName"), marketID=ev.get("MarketID"))
        if ev.get("Latitude") is not None and ev.get("BodyType") == "Planet":   # landed, or on foot on a body
            data.update(starsystemBodyName=ev.get("Body"), starsystemBodyCoords=_latlon(ev))
        out.append(_event(ev, "setCommanderTravelLocation", _clean(data)))
    elif name == "SupercruiseExit":
        data = {"starsystemName": ev.get("StarSystem") or session.system}
        if ev.get("BodyType") == "Planet":
            data["starsystemBodyName"] = ev.get("Body")
        out.append(_event(ev, "setCommanderTravelLocation", _clean(data)))
    elif name == "ApproachSettlement":
        data = {"starsystemName": session.system, "starsystemBodyName": ev.get("BodyName"),
                "stationName": ev.get("Name_Localised") or ev.get("Name"), "marketID": ev.get("MarketID"),
                "starsystemBodyCoords": _latlon(ev)}
        out.append(_event(ev, "setCommanderTravelLocation", _clean(data)))
    return out


def _latlon(ev):
    lat, lon = ev.get("Latitude"), ev.get("Longitude")
    return [lat, lon] if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) else None


def _clean(data):
    """Inara's event data without the fields not known (None): it sets what it is given."""
    return {k: v for k, v in data.items() if v is not None}


# the journal's rank names -> Inara's (Rank, Progress, Promotion): the Pilots' Federation's six and the two navies
RANKS = {"Combat": "combat", "Trade": "trade", "Explore": "explore", "Soldier": "soldier", "Exobiologist": "exobiologist",
         "CQC": "cqc", "Federation": "federation", "Empire": "empire"}


def _ranks(ev, session, t, sent):
    """Every rank and its progress (all of them: the author, 2026-10-11), engineers and Powerplay; only what changed
    since this commander's profile last got it."""
    name, cmdr = ev.get("event"), session.cmdr
    out = []
    if name in ("Rank", "Progress", "Promotion"):
        items = []
        for jn, inn in RANKS.items():
            v = ev.get(jn)
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if name == "Progress":
                if sent.changed(cmdr, f"progress:{inn}", v):
                    items.append({"rankName": inn, "rankProgress": round(v / 100, 4)})
            else:
                fresh = sent.changed(cmdr, f"rank:{inn}", v)
                if name == "Promotion":   # a new rank starts at 0 %
                    sent.changed(cmdr, f"progress:{inn}", 0)
                    items.append({"rankName": inn, "rankValue": v, "rankProgress": 0})
                elif fresh:
                    items.append({"rankName": inn, "rankValue": v})
        if items:
            out.append(_event(ev, "setCommanderRankPilot", items))
    elif name == "EngineerProgress":
        rows = ev.get("Engineers") if isinstance(ev.get("Engineers"), list) else [ev] if ev.get("Engineer") else []
        items = []
        for e in rows:
            if not isinstance(e, dict) or not e.get("Engineer"):
                continue
            item = _clean({"engineerName": e["Engineer"], "rankStage": e.get("Progress"),
                           "rankValue": e.get("Rank") if isinstance(e.get("Rank"), int) and e.get("Rank") > 0 else None})
            if len(item) > 1 and sent.changed(cmdr, f"engineer:{e['Engineer']}", item):
                items.append(item)
        if items:
            out.append(_event(ev, "setCommanderRankEngineer", items))
    elif name == "Powerplay" and ev.get("Power"):   # at login: the power, rank and merits
        item = _clean({"powerName": ev["Power"], "rankValue": ev.get("Rank"), "meritsValue": ev.get("Merits")})
        if sent.changed(cmdr, "power", item):
            out.append(_event(ev, "setCommanderRankPower", item))
    elif name in ("PowerplayRank", "PowerplayJoin", "PowerplayDefect", "PowerplayLeave"):
        power = ev.get("ToPower") if name == "PowerplayDefect" else ev.get("Power")
        rank = -1 if name == "PowerplayLeave" else ev.get("Rank") if name == "PowerplayRank" else 1
        if power:
            item = {"powerName": power, "rankValue": rank}
            sent.changed(cmdr, "power", item)
            out.append(_event(ev, "setCommanderRankPower", item))
    return out


def _credits(ev, session, t, sent):
    """Credits once per session, as the game says them at login (LoadGame's Credits and Loan, Statistics' wealth as
    the assets): Inara asks for figures we are sure of, never one sent on every change. And the game's statistics,
    which Statistics carries whole."""
    if ev.get("event") != "Statistics":
        return []
    out = []
    bank = ev.get("Bank_Account") if isinstance(ev.get("Bank_Account"), dict) else {}
    if isinstance(t.get("credits"), int):
        item = _clean({"commanderCredits": t["credits"], "commanderLoan": t.get("loan") or 0,
                       "commanderAssets": bank.get("Current_Wealth")})
        if sent.changed(session.cmdr, "credits", item):
            out.append(_event(ev, "setCommanderCredits", item))
    stats = {k: v for k, v in ev.items() if k not in ("timestamp", "event")}
    if stats and sent.changed(session.cmdr, "statistics", stats):
        out.append(_event(ev, "setCommanderGameStatistics", stats))
    return out


def _module(m):
    """A Loadout module as Inara's loadout item (its names), engineering included."""
    item = _clean({"slotName": m.get("Slot"), "itemName": m.get("Item"), "itemValue": m.get("Value"),
                   "itemHealth": m.get("Health"), "isOn": m.get("On"), "itemPriority": m.get("Priority"),
                   "itemAmmoClip": m.get("AmmoInClip"), "itemAmmoHopper": m.get("AmmoInHopper")})
    eng = m.get("Engineering")
    if isinstance(eng, dict):
        mods = [_clean({"name": x.get("Label"), "value": x.get("Value", x.get("ValueStr")),
                        "originalValue": x.get("OriginalValue"),
                        "lessIsGood": bool(x["LessIsGood"]) if "LessIsGood" in x else None})
                for x in eng.get("Modifiers") or [] if isinstance(x, dict)]
        item["engineering"] = _clean({"blueprintName": eng.get("BlueprintName"), "blueprintLevel": eng.get("Level"),
                                      "blueprintQuality": eng.get("Quality"), "experimentalEffect": eng.get("ExperimentalEffect"),
                                      "modifiers": mods or None})
    return item


def _ship_now(ev, session, t, sent):
    """The ship flown (Loadout: at login, after outfitting, a swap): its properties, marked current, and its loadout;
    each only when it changed since this commander's profile last got it (Loadout is written often)."""
    if ev.get("event") != "Loadout" or ev.get("ShipID") is None:
        return []
    cmdr, sid = session.cmdr, ev["ShipID"]
    name = (ev.get("ShipName") or "").strip() or None   # the game writes " " for no name
    ship = _clean({"shipType": ev.get("Ship"), "shipGameID": sid, "shipName": name, "shipIdent": ev.get("ShipIdent") or None,
                   "isCurrentShip": True, "shipHullValue": ev.get("HullValue"), "shipModulesValue": ev.get("ModulesValue"),
                   "shipRebuyCost": ev.get("Rebuy"), "shipMaxJumpRange": ev.get("MaxJumpRange"),
                   "shipCargoCapacity": ev.get("CargoCapacity"), "isHot": ev.get("Hot")})
    out = []
    current = sent.changed(cmdr, "current", sid)     # another ship than last sent: it is the current one again
    if sent.changed(cmdr, f"ship:{sid}", ship) or current:
        out.append(_event(ev, "setCommanderShip", ship))
    loadout = [_module(m) for m in ev.get("Modules") or [] if isinstance(m, dict) and m.get("Slot") and m.get("Item")]
    if loadout and sent.changed(cmdr, f"loadout:{sid}", loadout):
        out.append(_event(ev, "setCommanderShipLoadout", {"shipType": ev.get("Ship"), "shipGameID": sid, "shipLoadout": loadout}))
    return out


def _reputation(ev, session, t, sent):
    """Reputation with the superpowers (Reputation, at login) and with the factions of each system you arrive in
    (FSDJump, Location, CarrierJump: MyReputation); the journal's -100..100 as Inara's -1..1, changed ones only."""
    name, cmdr = ev.get("event"), session.cmdr
    out = []
    if name == "Reputation":
        items = [{"majorfactionName": k.lower(), "majorfactionReputation": round(v / 100, 4)}
                 for k, v in ev.items() if k not in ("timestamp", "event") and isinstance(v, (int, float))
                 and not isinstance(v, bool) and sent.changed(cmdr, f"major:{k.lower()}", v)]
        if items:
            out.append(_event(ev, "setCommanderReputationMajorFaction", items))
    elif name in ("FSDJump", "Location", "CarrierJump"):
        items = [{"minorfactionName": f["Name"], "minorfactionReputation": round(f["MyReputation"] / 100, 4)}
                 for f in ev.get("Factions") or [] if isinstance(f, dict) and f.get("Name")
                 and isinstance(f.get("MyReputation"), (int, float)) and sent.changed(cmdr, f"minor:{f['Name']}", f["MyReputation"])]
        if items:
            out.append(_event(ev, "setCommanderReputationMinorFaction", items))
    return out


LOCKER = ("Items", "Components", "Data", "Consumables")   # the ship locker's kinds (Odyssey), as Inara's itemType


def _with_file(ev, session, filename, keys):
    """The event, or its file's contents when it was written without them and the file is the one it wrote (the same
    timestamp: a later write means a later event, which would be the one kept)."""
    if ev is None or any(k in ev for k in keys) or not session.dir:
        return ev
    try:
        with open(os.path.join(session.dir, filename), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ev
    if isinstance(data, dict) and data.get("timestamp") == ev.get("timestamp") and data.get("event") == ev.get("event"):
        return dict(ev, **{k: v for k, v in data.items() if k not in ev})
    return ev


def _inventory(ev, session, t, sent):
    """The inventories as whole snapshots (Inara replaces them whole): materials, the ship's hold (never the SRV's,
    never the fleet carrier's: the API has none) and the ship locker. Taken at the moments a batch goes (a jump, a
    docking, the end of the login's lines, a shut-down), and only when it changed: never on every pickup."""
    if hold(ev, session) != 0:
        return []
    cmdr, out = session.cmdr, []
    m = t["materials"]
    if m.get("snapshot_ts"):
        counts = {k: v for k, v in sorted(m["counts"].items()) if v > 0}
        if sent.changed(cmdr, "materials", counts):
            out.append(_event(ev, "setCommanderInventoryMaterials", [{"itemName": k, "itemCount": v} for k, v in counts.items()]))
    cargo = _with_file(t["cargo"], session, "Cargo.json", ("Inventory",))
    if cargo is not None and isinstance(cargo.get("Inventory"), list):
        items = [_clean({"itemName": c.get("Name"), "itemCount": c.get("Count"), "isStolen": bool(c.get("Stolen")) or None,
                         "missionGameID": c.get("MissionID")}) for c in cargo["Inventory"] if isinstance(c, dict) and c.get("Name")]
        if sent.changed(cmdr, "cargo", items):
            out.append(_event(ev, "setCommanderInventoryCargo", items))
    locker = _with_file(t["locker"], session, "ShipLocker.json", LOCKER)
    if locker is not None and any(isinstance(locker.get(k), list) for k in LOCKER):
        items = [{"itemName": x["Name"], "itemCount": x.get("Count", 1), "itemType": kind}
                 for kind in LOCKER for x in locker.get(kind) or [] if isinstance(x, dict) and x.get("Name")]
        if sent.changed(cmdr, "locker", items):
            out.append(_event(ev, "resetCommanderInventory", [{"itemType": k} for k in LOCKER]))   # empty kinds too
            if items:
                out.append(_event(ev, "setCommanderInventory", items))
    return out


def _fleet(ev, session, t, sent):
    """The fleet: a ship bought (the old one stored here or sold), sold (also on a rebuy), swapped (the old one stored
    here; the new one is marked current by the Loadout that follows), sent elsewhere, renamed; the whole fleet's
    places when the shipyard lists it (StoredShips, changed ships only); the stored modules (StoredModules, whole)."""
    name, cmdr = ev.get("event"), session.cmdr
    here = _clean({"starsystemName": session.system, "stationName": session.station, "marketID": ev.get("MarketID") or session.market_id})
    out = []
    if name in ("ShipyardBuy", "ShipyardSwap", "ShipyardSell", "SellShipOnRebuy"):
        if ev.get("StoreShipID") is not None:
            out.append(_event(ev, "setCommanderShip", dict({"shipType": ev.get("StoreOldShip"), "shipGameID": ev["StoreShipID"]}, **here)))
        sold = ev.get("SellShipID", ev.get("SellShipId"))   # SellShipOnRebuy writes "SellShipId"
        if sold is not None:
            out.append(_event(ev, "delCommanderShip", {"shipType": ev.get("SellOldShip") or ev.get("ShipType"), "shipGameID": sold}))
    elif name == "ShipyardNew" and ev.get("NewShipID") is not None:
        out.append(_event(ev, "addCommanderShip", {"shipType": ev.get("ShipType"), "shipGameID": ev["NewShipID"]}))
    elif name == "ShipyardTransfer" and ev.get("ShipID") is not None:
        out.append(_event(ev, "setCommanderShipTransfer", _clean(dict({"shipType": ev.get("ShipType"), "shipGameID": ev["ShipID"],
                                                                      "transferTime": ev.get("TransferTime")}, **here))))
    elif name == "SetUserShipName" and ev.get("ShipID") is not None:
        out.append(_event(ev, "setCommanderShip", _clean({"shipType": ev.get("Ship"), "shipGameID": ev["ShipID"],
                                                          "shipName": (ev.get("UserShipName") or "").strip() or None,
                                                          "shipIdent": ev.get("UserShipId") or None})))
    elif name == "StoredShips":
        at = _clean({"starsystemName": ev.get("StarSystem"), "stationName": ev.get("StationName"), "marketID": ev.get("MarketID")})
        ships = [dict(_clean({"shipType": x.get("ShipType"), "shipGameID": x.get("ShipID"), "shipName": (x.get("Name") or "").strip() or None,
                              "isHot": x.get("Hot")}), **at) for x in ev.get("ShipsHere") or [] if isinstance(x, dict)]
        ships += [_clean({"shipType": x.get("ShipType"), "shipGameID": x.get("ShipID"), "shipName": (x.get("Name") or "").strip() or None,
                          "isHot": x.get("Hot"), "starsystemName": x.get("StarSystem"), "marketID": x.get("ShipMarketID")})
                  for x in ev.get("ShipsRemote") or [] if isinstance(x, dict)]   # in transit: no place yet
        for ship in ships:
            if ship.get("shipGameID") is not None and sent.changed(cmdr, f"stored:{ship['shipGameID']}", ship):
                out.append(_event(ev, "setCommanderShip", ship))
    elif name == "StoredModules":
        items = sorted((x for x in ev.get("Items") or [] if isinstance(x, dict) and x.get("Name")), key=lambda x: x.get("StorageSlot") or 0)
        mods = []
        for x in items:
            m = _clean({"itemName": x["Name"], "itemValue": x.get("BuyPrice"), "isHot": x.get("Hot"),
                        "starsystemName": x.get("StarSystem"), "marketID": x.get("MarketID")})
            if x.get("EngineerModifications"):
                m["engineering"] = _clean({"blueprintName": x["EngineerModifications"], "blueprintLevel": x.get("Level"),
                                           "blueprintQuality": x.get("Quality")})
            mods.append(m)
        if sent.changed(cmdr, "stored_modules", mods):
            out.append(_event(ev, "setCommanderStorageModules", mods))
    return out


def _tier(text):
    """'Tier 4' -> 4 (the journal says it in words), else None."""
    try:
        return int(str(text).split()[-1])
    except (TypeError, ValueError, IndexError):
        return None


def _goals(ev, session, t, sent):
    """Community goals (any kind: exploration and biology ones too): per goal you take part in, the goal (Inara keeps
    every goal's page from what the apps send; it ignores this in development mode), then your progress; each only when
    its figures changed (the game writes CommunityGoal often while you are signed up)."""
    if ev.get("event") != "CommunityGoal":
        return []
    cmdr, out = session.cmdr, []
    for g in ev.get("CurrentGoals") or []:
        if not isinstance(g, dict) or g.get("CGID") is None:
            continue
        top = g.get("TopTier") if isinstance(g.get("TopTier"), dict) else {}
        goal = _clean({"communitygoalGameID": g["CGID"], "communitygoalName": g.get("Title"), "starsystemName": g.get("SystemName"),
                       "stationName": g.get("MarketName"), "goalExpiry": g.get("Expiry"), "tierReached": _tier(g.get("TierReached")),
                       "tierMax": _tier(top.get("Name")), "topRankSize": g.get("TopRankSize"), "isCompleted": g.get("IsComplete"),
                       "contributorsNum": g.get("NumContributors"), "contributionsTotal": g.get("CurrentTotal"),
                       "completionBonus": top.get("Bonus") or None})
        mine = _clean({"communitygoalGameID": g["CGID"], "contribution": g.get("PlayerContribution"),
                       "percentileBand": g.get("PlayerPercentileBand"), "percentileBandReward": g.get("Bonus"),
                       "isTopRank": g.get("PlayerInTopRank")})
        if sent.changed(cmdr, f"goal:{g['CGID']}", goal):
            out.append(_event(ev, "setCommunityGoal", goal))
        if sent.changed(cmdr, f"goal_mine:{g['CGID']}", mine):
            out.append(_event(ev, "setCommanderCommunityGoalProgress", mine))
    return out


MISSION_FIELDS = (("missionExpiry", "Expiry"), ("starsystemNameTarget", "DestinationSystem"),
                  ("stationNameTarget", "DestinationStation"), ("minorfactionNameTarget", "TargetFaction"),
                  ("commodityName", "Commodity"), ("commodityCount", "Count"), ("targetName", "Target"),
                  ("targetType", "TargetType"), ("killCount", "KillCount"), ("passengerType", "PassengerType"),
                  ("passengerCount", "PassengerCount"), ("passengerIsVIP", "PassengerVIPs"),
                  ("passengerIsWanted", "PassengerWanted"))


def _items(rows):
    return [{"itemName": x["Name"], "itemCount": x.get("Count", 1)} for x in rows or [] if isinstance(x, dict) and x.get("Name")]


def _missions(ev, session, t, sent):
    """The mission log: accepted (where, for whom, what), completed (rewards, permits, the factions' gains), failed,
    abandoned; and a permit a mission awarded (Inara grants the rank permits itself)."""
    name = ev.get("event")
    if ev.get("MissionID") is None or name not in ("MissionAccepted", "MissionCompleted", "MissionFailed", "MissionAbandoned"):
        return []
    mid = ev["MissionID"]
    if name == "MissionAccepted":
        data = {"missionName": ev.get("Name"), "missionGameID": mid, "influenceGain": ev.get("Influence"),
                "reputationGain": ev.get("Reputation"), "starsystemNameOrigin": session.system,
                "stationNameOrigin": session.station, "minorfactionNameOrigin": ev.get("Faction")}
        data.update({k: ev[j] for k, j in MISSION_FIELDS if j in ev})
        return [_event(ev, "addCommanderMission", _clean(data))]
    if name in ("MissionFailed", "MissionAbandoned"):
        return [_event(ev, "setCommanderMission" + name[len("Mission"):], {"missionGameID": mid})]
    out = [_event(ev, "addCommanderPermit", {"starsystemName": x}) for x in ev.get("PermitsAwarded") or [] if isinstance(x, str) and x]
    data = {"missionGameID": mid, "donationCredits": ev.get("Donation"), "rewardCredits": ev.get("Reward")}
    if ev.get("PermitsAwarded"):
        data["rewardPermits"] = [{"starsystemName": x} for x in ev["PermitsAwarded"] if isinstance(x, str)]
    if ev.get("CommodityReward"):
        data["rewardCommodities"] = _items(ev["CommodityReward"])
    if ev.get("MaterialsReward"):
        data["rewardMaterials"] = _items(ev["MaterialsReward"])
    effects = []
    for f in ev.get("FactionEffects") or []:
        if not isinstance(f, dict) or not f.get("Faction"):
            continue
        gains = [i.get("Influence") for i in f.get("Influence") or [] if isinstance(i, dict) and i.get("Influence")]
        effects.append(_clean({"minorfactionName": f["Faction"], "influenceGain": max(gains, key=len) if gains else None,
                               "reputationGain": f.get("Reputation") or None}))
    if effects:
        data["minorfactionEffects"] = effects
    return out + [_event(ev, "setCommanderMissionCompleted", _clean(data))]


def _opponent(ev, *keys):
    """Who it was: the first of the event's names given (its readable form when the game wrote a code: an NPC's
    "$LUASC_..._SpecOps_A;" is "Spec Ops Wing Alpha"), else a Thargoid, else None."""
    for k in keys:
        if ev.get(k):
            return ev.get(k + "_Localised") or ev[k]
    return "Thargoid" if ev.get("IsThargoid") or ev.get("isThargoid") else None


def _combat(ev, session, t, sent):
    """The combat log: deaths (the killer, or a wing's names), interdictions both ways and escapes, PvP kills (Inara
    records only those). An event with no opponent named is not sent (Inara needs one)."""
    name, system = ev.get("event"), session.system
    data = None
    if name == "Died":
        killers = [k.get("Name") for k in ev.get("Killers") or [] if isinstance(k, dict) and k.get("Name")]
        data = {"starsystemName": system, "wingOpponentNames": killers} if killers else \
            {"starsystemName": system, "opponentName": _opponent(ev, "KillerName", "KillerShip")}
        kind = "addCommanderCombatDeath"
    elif name == "Interdicted":
        data = {"starsystemName": system, "opponentName": _opponent(ev, "Interdictor", "Faction", "Power"),
                "isPlayer": ev.get("IsPlayer"), "isSubmit": ev.get("Submitted")}
        kind = "addCommanderCombatInterdicted"
    elif name == "Interdiction":
        data = {"starsystemName": system, "opponentName": _opponent(ev, "Interdicted", "Faction", "Power"),
                "isPlayer": ev.get("IsPlayer"), "isSuccess": ev.get("Success")}
        kind = "addCommanderCombatInterdiction"
    elif name == "EscapeInterdiction":
        data = {"starsystemName": system, "opponentName": _opponent(ev, "Interdictor", "Faction", "Power"), "isPlayer": ev.get("IsPlayer")}
        kind = "addCommanderCombatInterdictionEscape"
    elif name == "PVPKill":
        data = {"starsystemName": system, "opponentName": ev.get("Victim")}
        kind = "addCommanderCombatKill"
    if not data or not (data.get("opponentName") or data.get("wingOpponentNames")):
        return []
    return [_event(ev, kind, _clean(data))]


def _friends(ev, session, t, sent):
    """Friends: a game friend added (or seen online) is one on Inara (which sends them a friend request there, as the
    game's does); declined or lost, removed. Each friend once: Online comes at every login."""
    if ev.get("event") != "Friends" or not ev.get("Name"):
        return []
    who, status = ev["Name"], ev.get("Status")
    if status in ("Added", "Online"):
        if sent.changed(session.cmdr, f"friend:{who}", True):
            return [_event(ev, "addCommanderFriend", {"commanderName": who, "gamePlatform": "pc"})]
    elif status in ("Declined", "Lost"):
        sent.changed(session.cmdr, f"friend:{who}", False)
        return [_event(ev, "delCommanderFriend", {"commanderName": who, "gamePlatform": "pc"})]
    return []


def _suit_modules(rows):
    return [_clean({"slotName": x.get("SlotName"), "itemName": x.get("ModuleName"), "itemClass": x.get("Class"),
                    "itemGameID": x.get("SuitModuleID"),
                    "engineering": [{"blueprintName": m} for m in x.get("WeaponMods") or []] or None})
            for x in rows or [] if isinstance(x, dict)]


def _suits(ev, session, t, sent):
    """Odyssey suit loadouts: the whole loadout when created or worn (SuitLoadout comes at every disembark: changed
    ones only), a module equipped, renamed, deleted."""
    name, cmdr = ev.get("event"), session.cmdr
    lid = ev.get("LoadoutID")
    if lid is None:
        return []
    base = _clean({"loadoutGameID": lid, "loadoutName": ev.get("LoadoutName"), "suitGameID": ev.get("SuitID"), "suitType": ev.get("SuitName")})
    if name in ("SuitLoadout", "CreateSuitLoadout"):
        data = dict(base, suitMods=ev.get("SuitMods") or [], suitLoadout=_suit_modules(ev.get("Modules")))
        if sent.changed(cmdr, f"suit:{lid}", data):
            return [_event(ev, "setCommanderSuitLoadout", data)]
    elif name == "LoadoutEquipModule":
        sent.changed(cmdr, f"suit:{lid}", None)   # changed: the next SuitLoadout goes whole
        return [_event(ev, "updateCommanderSuitLoadout", dict(base, suitLoadout=_suit_modules([ev])))]
    elif name == "LoadoutRemoveModule":
        sent.changed(cmdr, f"suit:{lid}", None)
    elif name == "RenameSuitLoadout":
        sent.changed(cmdr, f"suit:{lid}", None)
        return [_event(ev, "updateCommanderSuitLoadout", base)]
    elif name == "DeleteSuitLoadout":
        sent.changed(cmdr, f"suit:{lid}", None)
        return [_event(ev, "delCommanderSuitLoadout", {"loadoutGameID": lid})]
    return []


BUILDERS = (_travel, _ranks, _credits, _ship_now, _reputation, _inventory, _fleet, _goals, _missions, _combat, _friends, _suits)
# whole snapshots whose empty list means something (an emptied hold clears Inara's): sent even with no items
EMPTY_OK = frozenset({"setCommanderInventoryCargo", "setCommanderInventoryMaterials", "setCommanderStorageModules"})


def build(ev, session, sent=None):
    """[(event name, Inara event)] for one journal line (the session, and track(), have seen it already). sent: the
    Sent record of what each commander's profile already has (snapshots go only when they differ)."""
    t = _state(session)
    sent = sent if sent is not None else Sent()
    out = []
    for fn in BUILDERS:   # each: (ev, session, the tracker's state, sent) -> [(name, event)]
        out += fn(ev, session, t, sent)
    # who the event is for: the request's header needs the commander's Frontier ID, kept with each row
    return [(n, dict(m, _fid=session.fid)) for n, m in out if m["eventData"] or n in EMPTY_OK]


def hold(ev, session=None):
    """How long this line's events may wait for others (HOLD_S), or 0: they send what waits, themselves included. A
    jump, a docking, the end of the start-up lines (the ship's first Cargo after LoadGame) or a shut-down."""
    if ev.get("event") in RELEASE:
        return 0
    if session is not None and _state(session)["line"].get("release"):
        return 0
    return HOLD_S


# ---- the request and the answer ----

def same_batch(rows, limit=BATCH):
    """The leading rows that may go in one request: one commander (the API key is the commander's)."""
    out = []
    for r in rows[:limit]:
        if out and r["cmdr"] != out[0]["cmdr"]:
            break
        out.append(r)
    return out


def request(rows, key, version, developing=False):
    """The POST body for rows of one commander (same_batch): the header with the commander's own API key, then the
    events in the order they were queued, each with its row id as eventCustomID (the answer is paired by it)."""
    fid = next((json.loads(r["message"]).get("_fid") for r in reversed(rows) if json.loads(r["message"]).get("_fid")), None)
    header = {"appName": SOFTWARE, "appVersion": version, "isBeingDeveloped": bool(developing), "APIkey": key,
              "commanderName": rows[0]["cmdr"]}
    if fid:
        header["commanderFrontierID"] = str(fid)
    events = []
    for r in rows:
        m = json.loads(r["message"])
        events.append({"eventName": m["eventName"], "eventTimestamp": m["eventTimestamp"], "eventData": m["eventData"],
                       "eventCustomID": r["id"]})
    return {"header": header, "events": events}


# words in a refusal of the whole batch that mean "too many requests": wait an hour, the key is not wrong
RATE_WORDS = ("slow down", "too many", "too much", "revoked", "temporarily")


def answer(rows, reply):
    """Inara's reply (decoded JSON) for these rows -> [(row id, state, status text, retry_in)] (upload_loop's results).
    The header's 400 refuses the whole batch: a rate refusal waits an hour, anything else (a wrong key, an app Inara has
    not let in yet) holds the service with Inara's words until the player acts. Per event: 200, 202 and 204 are taken
    (204: formally fine, nothing to change); 400 drops that event with its words."""
    if not isinstance(reply, dict) or not isinstance(reply.get("header"), dict):
        raise ValueError(f"Inara's answer has no header: {str(reply)[:200]}")
    head = reply["header"]
    try:
        code = int(head.get("eventStatus"))
    except (TypeError, ValueError):
        raise ValueError(f"Inara's answer has no eventStatus: {str(head)[:200]}")
    text = str(head.get("eventStatusText") or "")
    if code >= 400:
        if any(w in text.lower() for w in RATE_WORDS):
            return [(r["id"], "queued", f"{code} {text}".strip(), RATE_WAIT_S) for r in rows]
        return [(r["id"], "held", f"Inara refused: {text or code}", None) for r in rows]
    events = reply.get("events") if isinstance(reply.get("events"), list) else []
    by_id = {e.get("eventCustomID"): e for e in events if isinstance(e, dict) and e.get("eventCustomID") is not None}
    out = []
    for i, r in enumerate(rows):
        e = by_id.get(r["id"]) or (events[i] if i < len(events) and isinstance(events[i], dict) else {})
        try:
            n = int(e.get("eventStatus"))
        except (TypeError, ValueError):
            n = None
        said = f"{n} {e.get('eventStatusText') or ''}".strip() if n is not None else "200 (no answer for this event)"
        out.append((r["id"], "dropped" if n is not None and n >= 400 else "sent", said, None))
    return out


def key_ok(key):
    """A plausible Inara API key (inara.cz/elite/cmdr-settings-api): letters and digits, nothing that would break the header."""
    key = str(key or "").strip()
    return 16 <= len(key) <= 128 and key.isascii() and key.replace("-", "").replace("_", "").isalnum()
