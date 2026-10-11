"""EDSM's journal upload (PLAN-edmc-functionality part H): what a journal line sends to the player's own EDSM account,
and what EDSM's answer means. Pure: the server (ed_outrider.py) owns the queue, the accounts and the sending.

- Every live event goes, minus EDSM's discard list (fetched from EDSM at start and every DISCARD_EVERY_S; DISCARD is
  the copy used until then, as EDSM served it on 2026-10-08). Docked always goes (EDMC keeps it too).
- Each event carries where the player was (EDSM's "transient" fields: _systemAddress, _systemName, _systemCoordinates,
  _marketId, _stationName, _shipId) from the uploads' Session, which has seen the line already. EDSM fills what is
  missing from the earlier events of the same account, so events go in order.
- Cargo, ShipLocker and Backpack without their contents get them from Cargo.json / ShipLocker.json / Backpack.json,
  only when the file's timestamp is the event's (NFS can serve an older file: then the event goes as written).
- Events wait to be sent together: a jump, a docking, a Location or a shut-down sends what waits (RELEASE), and so
  does HOLD_S after the first of them (later ones join its deadline): about one request per jump, and one every
  HOLD_S during a long stay.
- answer(): EDSM's msgnum codes (www.edsm.net/en/api-journal-v1, saved in project/research-edmc-2026-10-08/).

Research and rules: project/research-edmc-2026-10-08/edsm-inara.md.
"""
import json
import os

UPLOAD_URL = "https://www.edsm.net/api-journal-v1"
DISCARD_URL = "https://www.edsm.net/api-journal-v1/discard"
SOFTWARE = "ED Outrider"   # fromSoftware: stable for ever (EDSM's 204/205 key on it)
DISCARD_EVERY_S = 2 * 3600
HOLD_S = 300               # an event waits at most this long for others to go with it
RELEASE = frozenset({"FSDJump", "CarrierJump", "Docked", "Location", "Shutdown"})
BATCH = 200                # events per request, at most
DRY_ENV = "OUTRIDER_EDSM_DRYRUN"   # a developer's switch, not a setting: build and log the requests, send nothing

# EDSM's discard list as it served it on 2026-10-08 (GET DISCARD_URL), until the live one is fetched
DISCARD = frozenset((
    "ShutDown", "EDDItemSet", "EDDCommodityPrices", "ModuleArrived", "ShipArrived", "Coriolis", "EDShipyard", "Market",
    "Shipyard", "Outfitting", "ModuleInfo", "Status", "SquadronCreated", "SquadronStartup", "DisbandedSquadron",
    "InvitedToSquadron", "AppliedToSquadron", "JoinedSquadron", "LeftSquadron", "SharedBookmarkToSquadron",
    "CarrierStats", "CarrierTradeOrder", "CarrierFinance", "CarrierBankTransfer", "CarrierCrewServices",
    "CarrierJumpRequest", "CarrierJumpCancelled", "CarrierDepositFuel", "CarrierDockingPermission",
    "CarrierModulePack", "CarrierBuy", "CarrierNameChange", "CarrierDecommission", "ColonisationConstructionDepot",
    "ColonisationContribution", "BookDropship", "CancelDropship", "DropshipDeploy", "CollectItems", "DropItems",
    "Disembark", "Embark", "Fileheader", "Commander", "NewCommander", "ClearSavedGame", "Music", "Continued",
    "Passengers", "DockingCancelled", "DockingDenied", "DockingGranted", "DockingRequested", "DockingTimeout",
    "StartJump", "Touchdown", "Liftoff", "NavBeaconScan", "SupercruiseEntry", "SupercruiseExit", "NavRoute",
    "NavRouteClear", "PVPKill", "CrimeVictim", "UnderAttack", "ShipTargeted", "Scanned", "DataScanned",
    "DatalinkScan", "EngineerApply", "EngineerLegacyConvert", "FactionKillBond", "Bounty", "CapShipBond",
    "DatalinkVoucher", "SystemsShutdown", "EscapeInterdiction", "HeatDamage", "HeatWarning", "HullDamage",
    "ShieldState", "FuelScoop", "LaunchDrone", "AfmuRepairs", "CockpitBreached", "ReservoirReplenished",
    "CargoTransfer", "ApproachBody", "LeaveBody", "DiscoveryScan", "MaterialDiscovered", "Screenshot", "CrewAssign",
    "CrewFire", "NpcCrewRank", "ShipyardNew", "StoredModules", "MassModuleStore", "ModuleStore", "ModuleSwap",
    "SuitLoadout", "SwitchSuitLoadout", "CreateSuitLoadout", "LoadoutEquipModule", "PowerplayVote",
    "PowerplayVoucher", "PowerplayMerits", "ChangeCrewRole", "CrewLaunchFighter", "CrewMemberJoins",
    "CrewMemberQuits", "CrewMemberRoleChange", "KickCrewMember", "EndCrewSession", "LaunchFighter", "DockFighter",
    "FighterDestroyed", "FighterRebuilt", "VehicleSwitch", "LaunchSRV", "DockSRV", "SRVDestroyed", "JetConeBoost",
    "JetConeDamage", "RebootRepair", "RepairDrone", "WingAdd", "WingInvite", "WingJoin", "WingLeave", "ReceiveText",
    "SendText", "Shutdown", "SupercruiseDestinationDrop", "FSSSignalDiscovered", "AsteroidCracked",
    "ProspectedAsteroid", "ScanBaryCentre", "FSSBodySignals", "SAASignalsFound", "ScanOrganic"))
KEEP = frozenset({"Docked"})   # sent even if the list says otherwise

# events written without their contents, and the file that has them: {event: (file, the contents' key)}
FILES = {"Cargo": ("Cargo.json", "Inventory"), "ShipLocker": ("ShipLocker.json", "Items"),
         "Backpack": ("Backpack.json", "Items")}


def dry_run(environ=None):
    """Whether EDSM requests are only built and logged, never sent: when the developer starts Outrider with
    OUTRIDER_EDSM_DRYRUN=1 (EDSM has no test endpoint: everything sent lands in the player's real account)."""
    environ = os.environ if environ is None else environ
    return str(environ.get(DRY_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def discard_list(data):
    """EDSM's answer to GET DISCARD_URL as a set of event names, or None when it is not a list of names."""
    if not isinstance(data, list) or not data or not all(isinstance(x, str) for x in data):
        return None
    return frozenset(data)


def _with_file(ev, session):
    """A Cargo / ShipLocker / Backpack event with its file's contents, when it was written without them and the file
    is the one it wrote (same timestamp); otherwise the event as it is."""
    name = ev.get("event")
    if name not in FILES or not session.dir:
        return ev
    filename, key = FILES[name]
    if ev.get(key):
        return ev
    try:
        with open(os.path.join(session.dir, filename), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return ev
    if not isinstance(data, dict) or data.get("timestamp") != ev.get("timestamp") or data.get("event") != name:
        return ev
    if name == "Cargo" and data.get("Vessel", ev.get("Vessel")) != ev.get("Vessel"):
        return ev
    return dict(ev, **{k: v for k, v in data.items() if k not in ev})


def transient(session):
    """EDSM's transient fields from where the session is (only those known)."""
    out = {}
    if session.addr is not None:
        out["_systemAddress"] = session.addr
    if session.system:
        out["_systemName"] = session.system
    if session.pos is not None:
        out["_systemCoordinates"] = list(session.pos)
    if session.market_id is not None:
        out["_marketId"] = session.market_id
    if session.station:
        out["_stationName"] = session.station
    if isinstance(session.ship_id, int) and session.ship_id >= 0:
        out["_shipId"] = session.ship_id
    return out


def build(ev, session, discard=DISCARD):
    """[(event name, the event as EDSM gets it)] for one journal line (the session has seen it), or [] when EDSM does
    not want it."""
    name = ev.get("event")
    if not name or (name in discard and name not in KEEP):
        return []
    return [(name, dict(_with_file(ev, session), **transient(session)))]


def hold(ev, session=None):
    """How long this line's event may wait for others (HOLD_S), or 0: it sends what waits, itself included."""
    return 0 if ev.get("event") in RELEASE else HOLD_S


def request(rows, account, version):
    """The POST body for rows of one commander and one game version (the caller groups them): EDSM's JSON form."""
    first = rows[0]
    return {"commanderName": account["name"], "apiKey": account["key"], "fromSoftware": SOFTWARE,
            "fromSoftwareVersion": version, "fromGameVersion": first["gameversion"] or "",
            "fromGameBuild": first["gamebuild"] or "", "message": [json.loads(r["message"]) for r in rows]}


def same_batch(rows, limit=BATCH):
    """The leading rows that may go in one request: one commander, one game version and build (EDSM's rule)."""
    out = []
    for r in rows[:limit]:
        if out and (r["cmdr"], r["gameversion"], r["gamebuild"]) != (out[0]["cmdr"], out[0]["gameversion"], out[0]["gamebuild"]):
            break
        out.append(r)
    return out


FATAL = {201: "EDSM has no commander by that name", 202: "the EDSM API key is missing",
         203: "EDSM refused the commander name or API key", 204: "EDSM does not know this software",
         205: "EDSM has blocked this software", 207: "EDSM needs the game version (Outrider's bug)"}


def answer(rows, reply):
    """EDSM's reply (decoded JSON) for these rows -> [(row id, state, status text, retry_in)] (upload_loop's results).
    100 is per event; 201-205 and 207 hold everything (waiting on the player, or on a fix); 206 (our bad JSON) and
    208 (the Legacy game) drop the batch; anything else is tried again later."""
    if not isinstance(reply, dict):
        raise ValueError("EDSM's answer is not a JSON object")
    try:
        code = int(reply.get("msgnum"))
    except (TypeError, ValueError):
        raise ValueError(f"EDSM's answer has no msgnum: {str(reply)[:200]}")
    msg = str(reply.get("msg") or "")
    if code in FATAL:
        return [(r["id"], "held", f"{code} {FATAL[code]}", None) for r in rows]
    if code in (206, 208):
        return [(r["id"], "dropped", f"{code} {msg}".strip(), None) for r in rows]
    if code != 100:
        return [(r["id"], "queued", f"{code} {msg}".strip(), 60) for r in rows]
    events = reply.get("events") if isinstance(reply.get("events"), list) else []
    out = []
    for i, r in enumerate(rows):
        e = events[i] if i < len(events) and isinstance(events[i], dict) else {}
        try:
            n = int(e.get("msgnum"))
        except (TypeError, ValueError):
            n = None
        text = f"{n} {e.get('msg') or ''}".strip() if n is not None else "100 (no answer for this event)"
        if n is None or 100 <= n <= 104 or n in (500, 501):   # stored, already stored, older, duplicate, crew; or kept
            out.append((r["id"], "sent", text, None))
        else:                                                 # 3xx, 4xx: EDSM will not take this one
            out.append((r["id"], "dropped", text, None))
    return out
