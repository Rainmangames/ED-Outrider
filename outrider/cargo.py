"""Cargo: your ship's hold and your fleet carrier's, folded from the journal. Pure: no I/O, no database.

The ship's hold is exact: the game writes a `Cargo` snapshot (with `Inventory` at login, in `Cargo.json` after every
change), and `ship_apply` folds the events between snapshots into it, keeping what you paid for each commodity (the
game's own method: average cost; a purchase reweights it, a sale or transfer leaves it).

The carrier's hold is not written anywhere whole. Its `Market.json` lists only commodities with an order: a sell
order's Stock is the holding (confirmed), a buy order shows only what it still wants. So `carrier_fold` replays your
carrier's journal events (transfers, your own trades there, orders set and cancelled), the market snapshots read at
your carrier and the counts you typed in (Recount), in time order:

- a sell order's Stock confirms the line (the author's rule, 2026-10-07: no partial-order special cases);
- your own journaled moves change any line exactly (transfers, buying from and selling to your carrier);
- other players' trades are written nowhere in your journal: a buy order's filled part is worked out at the next
  market snapshot as what was ordered minus what it still wants;
- every open sell order is listed with its stock: one the market does not list sold out (or ended);
- a sell order of n t means at least n t there: a tracked line takes that fresh count;
- everything else is tracked ("seen") or what you entered ("entered"), checked against CarrierStats' total; when the
  carrier holds less than is tracked, the lines with no news for SEEN_STALE_DAYS are dropped (old history goes wrong
  where others traded with you unjournaled).

This is the only way to follow a carrier's cargo without signing in to Frontier's servers (their companion API),
which Outrider never does.

Commodity ids are the journal's lowercase symbols (`tritium`, `methanolmonohydratecrystals`): `cid()` turns the
`$platinum_name;` form into them. Display names come from the journal's `_Localised` fields where it gives them.
"""
import calendar
import math
import re
import time

LARGE, MEDIUM, SMALL = "L", "M", "S"
# Loadout's Ship (lowercase) -> the landing pad it needs. Unknown ships (a new one) give None: no pad filter.
SHIP_PAD = {
    **dict.fromkeys(("anaconda", "belugaliner", "cutter", "federation_corvette", "type7", "type9", "type9_military",
                     "panthermkii", "empire_trader", "orca", "explorer_nx"), LARGE),
    **dict.fromkeys(("python", "python_nx", "krait_mkii", "krait_light", "mandalay", "corsair", "typex", "typex_2",
                     "typex_3", "federation_gunship", "federation_dropship", "federation_dropship_mkii", "ferdelance",
                     "mamba", "asp", "asp_scout", "independant_trader", "type6", "type8", "lakonminer"), MEDIUM),
    **dict.fromkeys(("adder", "eagle", "hauler", "sidewinder", "viper", "viper_mkiv", "cobramkiii", "cobramkiv",
                     "cobramkv", "diamondback", "diamondbackxl", "vulture", "empire_courier", "empire_eagle", "dolphin"),
                    SMALL),
}

MOVES_KEPT = 5            # each carrier line keeps its last few moves (the page's hover text)
CARRIER_JUMP_LY = 500     # a fleet carrier's longest jump
CARRIER_DEPOT_MAX = 1000  # the tritium depot (the carrier's tank) holds this much
# At a market read that finds the carrier holding less than Outrider tracks, a tracked ("seen") line with nothing newer
# than this many days is dropped: unjournaled trades (other players buying from an old sell order) leave old lines
# wrong, and the total check shows any real one that went with them (Recount puts it back)
SEEN_STALE_DAYS = 30


def cid(name):
    """The journal's commodity id: `$platinum_name;` and `Platinum` -> `platinum`."""
    s = str(name or "").strip().lower()
    if s.startswith("$"):
        s = s[1:]
    if s.endswith("_name;"):
        s = s[:-6]
    return s


def norm(name):
    """A name reduced to letters and digits, to match a journal id against Spansh's commodity names."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def learn(names, ev):
    """Keep the display names an event gives (Type_Localised, Name_Localised, Commodity_Localised). True if new."""
    new = False
    for key, loc in (("Type", "Type_Localised"), ("Name", "Name_Localised"), ("Commodity", "Commodity_Localised")):
        i, n = cid(ev.get(key)), ev.get(loc)
        if i and isinstance(n, str) and n.strip() and names.get(i) != n.strip():
            names[i] = n.strip()
            new = True
    return new


def display(i, names, spansh=None):
    """A commodity's name: the journal's own, else Spansh's (matched letters-only), else the id title-cased."""
    if names.get(i):
        return names[i]
    if spansh and norm(i) in spansh:
        return spansh[norm(i)]
    return i.replace("_", " ").title()


def _n(v):
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else 0


# ---- the ship ----

def new_ship():
    """lines: id -> {count, priced (the tons the average covers), avg (cr/t), lots (purchases), stolen, mission}."""
    return {"lines": {}, "count": None, "snap_ts": "", "ts": ""}


def _ship_add(st, i, n, price=None, stolen=0, count=True):
    if not i or n <= 0:
        return
    if not count and i not in st["lines"]:
        return   # already in a snapshot that does not list it: nothing to price (it made a 0 t line)
    line = st["lines"].setdefault(i, {"count": 0, "priced": 0, "avg": None, "lots": 0, "stolen": 0, "mission": 0})
    if count:
        line["count"] += n
        line["stolen"] += stolen
    if price:   # average cost: a purchase reweights the average over the priced tons
        priced = min(line["priced"], line["count"])
        add = min(n, max(0, line["count"] - priced)) if not count else n
        if add > 0:
            line["avg"] = ((line["avg"] or 0) * priced + price * add) / (priced + add)
            line["priced"] = priced + add
            line["lots"] += 1


def _ship_remove(st, i, n):
    line = st["lines"].get(i)
    if not line or n <= 0:
        return
    line["count"] -= n
    if line["count"] <= 0:
        del st["lines"][i]
        return
    line["priced"] = min(line["priced"], line["count"])   # a sale leaves the average (the priced tons go last)
    line["stolen"] = min(line["stolen"], line["count"])
    if not line["priced"]:
        line["avg"], line["lots"] = None, 0


def ship_snapshot(st, inventory, count, ts):
    """A whole hold (Cargo's Inventory, or Cargo.json): the lines it lists, each keeping what it cost so far."""
    old, lines = st["lines"], {}
    for it in inventory or []:
        if not isinstance(it, dict):
            continue
        i, n = cid(it.get("Name")), _n(it.get("Count"))
        if not i or not n:
            continue
        line = lines.setdefault(i, {"count": 0, "priced": 0, "avg": None, "lots": 0, "stolen": 0, "mission": 0})
        line["count"] += n
        line["stolen"] += _n(it.get("Stolen"))
        if it.get("MissionID"):
            line["mission"] += n
    for i, line in lines.items():
        o = old.get(i)
        if o and o.get("avg"):
            line["priced"], line["avg"], line["lots"] = min(o["priced"], line["count"]), o["avg"], o["lots"]
            if not line["priced"]:
                line["avg"], line["lots"] = None, 0
    st["lines"] = lines
    st["count"] = count if isinstance(count, (int, float)) else sum(x["count"] for x in lines.values())
    st["snap_ts"] = st["ts"] = max(st["ts"] or "", ts or "")


def ship_apply(st, ev):
    """Fold one journal event into the ship's hold. True if it changed anything.

    A change the latest snapshot already holds (a purchase in the same second as the Cargo.json read after it) only
    updates the price paid, never the count twice."""
    name, ts = ev.get("event"), ev.get("timestamp") or ""
    if name == "Cargo":
        if ev.get("Vessel", "Ship") != "Ship":
            return False
        if isinstance(ev.get("Inventory"), list):
            ship_snapshot(st, ev["Inventory"], ev.get("Count"), ts)
        elif isinstance(ev.get("Count"), (int, float)):
            st["count"] = ev["Count"]
        return True
    counted = ts > (st.get("snap_ts") or "")
    if name == "MarketBuy":
        _ship_add(st, cid(ev.get("Type")), _n(ev.get("Count")), price=ev.get("BuyPrice"), count=counted)
    elif name == "MarketSell":
        i = cid(ev.get("Type"))
        line = st["lines"].get(i)
        if line and _n(ev.get("AvgPricePaid")) and line["priced"]:
            line["avg"] = ev["AvgPricePaid"]   # the game's own average: it knows better
        if counted:
            _ship_remove(st, i, _n(ev.get("Count")))
    elif not counted:
        return False
    elif name == "CargoTransfer":
        for t in ev.get("Transfers") or []:
            if isinstance(t, dict):
                if t.get("Direction") == "toship":
                    _ship_add(st, cid(t.get("Type")), _n(t.get("Count")))
                else:   # tocarrier, tosrv
                    _ship_remove(st, cid(t.get("Type")), _n(t.get("Count")))
    elif name == "CarrierDepositFuel":   # from the ship's hold into the carrier's depot
        _ship_remove(st, "tritium", _n(ev.get("Amount")))
    elif name == "EjectCargo":
        _ship_remove(st, cid(ev.get("Type")), _n(ev.get("Count")))
    elif name == "CollectCargo":
        _ship_add(st, cid(ev.get("Type")), 1, stolen=1 if ev.get("Stolen") else 0)
    elif name == "MiningRefined":
        _ship_add(st, cid(ev.get("Type")), 1)
    else:
        return False
    st["ts"] = max(st["ts"] or "", ts)
    return True


def avg_text(line):
    """'Avg 45,210 cr/t (2 lots)', '45,210 cr/t' for one purchase, '... on 40 of 64 t' when part is unpriced."""
    if not line.get("avg") or not line.get("priced"):
        return ""
    price = f"{round(line['avg']):,} cr/t"
    text = f"Avg {price}" if line["lots"] > 1 else price
    if line["priced"] < line["count"]:
        return f"{text} on {line['priced']:,} of {line['count']:,} t"
    return f"{text} ({line['lots']} lots)" if line["lots"] > 1 else text


# ---- the carrier ----

# What a carrier line's moves are called (the page's hover text)
MOVE_WORDS = {"to_carrier": "moved from your ship", "to_ship": "moved to your ship", "bought": "you bought it there",
              "sold": "you sold it there", "others": "sold to your carrier by other players"}


def carrier_fold(carrier_id, events, markets=(), counts=()):
    """Your carrier's hold from its history, in time order.

    events: [(ts, event dict)] (CarrierStats, CarrierTradeOrder, CargoTransfer with "_at" the market you were docked
    at, MarketBuy, MarketSell); markets: [(ts, items)] your carrier's Market.json snapshots; counts: [(ts, id, count,
    name)] your Recount entries. Only what concerns `carrier_id` counts.

    Returns {lines: id -> {count, state: confirmed | seen | entered, ts (that state's time), moves: [[ts, delta,
    why]]}, orders: id -> {kind: sell | buy, amount, price}, stats: {cargo, used, depot, ts} (the latest CarrierStats),
    after_stats (the tons your own journaled moves added since it: its total now is cargo + after_stats), market_ts,
    names (learned from the events)}."""
    st = {"lines": {}, "orders": {}, "stats": None, "after_stats": 0, "market_ts": None, "names": {}}
    if carrier_id is None:
        return st
    feed = [(ts, 0, "ev", ev) for ts, ev in events] + [(ts, 1, "market", items) for ts, items in markets] + \
           [(ts, 2, "count", (i, n, name)) for ts, i, n, name in counts]
    for ts, _, kind, x in sorted(feed, key=lambda f: (f[0] or "", f[1])):
        if kind == "ev":
            learn(st["names"], x)
            _carrier_event(st, carrier_id, x, ts)
        elif kind == "market":
            _carrier_market(st, x, ts)
        else:
            i, n, name = x
            if name:
                st["names"].setdefault(i, name)
            if n > 0:
                st["lines"][i] = {"count": n, "state": "entered", "ts": ts, "moves": []}
            else:
                st["lines"].pop(i, None)
    return st


def _move(st, i, delta, why, ts):
    if not i or not delta:
        return
    line = st["lines"].get(i)
    if line is None:
        if delta < 0:
            return   # moved out of a line never tracked: nothing to take it from (the total check shows any gap)
        line = st["lines"][i] = {"count": 0, "state": "seen", "ts": ts, "moves": []}
    line["count"] += delta
    line["moves"] = (line["moves"] + [[ts, delta, why]])[-MOVES_KEPT:]
    if line["state"] == "seen":
        line["ts"] = ts   # last seen: when it last changed
    if line["count"] <= 0:
        del st["lines"][i]


def _carrier_event(st, carrier_id, ev, ts):
    name = ev.get("event")
    if name == "CarrierStats":
        if ev.get("CarrierID") != carrier_id:
            return
        su = ev.get("SpaceUsage") or {}
        # space a buy order reserves is held, not filled: it weighs nothing (the game's JumpRangeCurr agrees)
        used = (su.get("TotalCapacity") or 0) - (su.get("FreeSpace") or 0) - (su.get("CargoSpaceReserved") or 0) \
            if su.get("TotalCapacity") else None
        st["stats"] = {"cargo": su.get("Cargo"), "used": used, "depot": ev.get("FuelLevel"), "ts": ts}
        st["after_stats"] = 0
    elif name == "CarrierTradeOrder":
        if ev.get("CarrierID") != carrier_id or ev.get("BlackMarket"):
            return
        i = cid(ev.get("Commodity"))
        if ev.get("CancelTrade"):
            st["orders"].pop(i, None)
            line = st["lines"].get(i)
            if line and line["state"] == "confirmed":
                line["state"] = "seen"   # kept from the market's last count, which no longer confirms it
        elif _n(ev.get("SaleOrder")):
            n = _n(ev["SaleOrder"])
            st["orders"][i] = {"kind": "sell", "amount": n, "price": ev.get("Price")}
            # the game sells only what the carrier holds: an order of n t means at least n t there, and is newer news
            # than the tracked line (seen, until the market confirms it); a line confirmed or entered keeps its count
            line = st["lines"].get(i)
            if line is None or line["state"] == "seen":
                st["lines"][i] = {"count": n, "state": "seen", "ts": ts, "moves": (line or {}).get("moves", [])}
        elif _n(ev.get("PurchaseOrder")):
            st["orders"][i] = {"kind": "buy", "amount": _n(ev["PurchaseOrder"]), "price": ev.get("Price")}
    elif name == "CargoTransfer":
        if ev.get("_at") != carrier_id:
            return
        for t in ev.get("Transfers") or []:
            if not isinstance(t, dict):
                continue
            i, n = cid(t.get("Type")), _n(t.get("Count"))
            if t.get("Direction") == "tocarrier":
                _move(st, i, n, "to_carrier", ts)
                st["after_stats"] += n
            elif t.get("Direction") == "toship":
                _move(st, i, -n, "to_ship", ts)
                st["after_stats"] -= n
    elif name in ("MarketBuy", "MarketSell"):
        if ev.get("MarketID") != carrier_id:
            return
        i, n = cid(ev.get("Type")), _n(ev.get("Count"))
        buy = name == "MarketBuy"
        _move(st, i, -n if buy else n, "bought" if buy else "sold", ts)
        st["after_stats"] += -n if buy else n
        order = st["orders"].get(i)
        if order and order["kind"] == ("sell" if buy else "buy"):
            order["amount"] = max(0, order["amount"] - n)


def _carrier_market(st, items, ts):
    """A Market.json at your carrier: sell orders confirm their lines; a buy order's filled part is added."""
    sells, buys = {}, {}
    for it in items or []:
        if not isinstance(it, dict):
            continue
        i = cid(it.get("Name"))
        if not i:
            continue
        if isinstance(it.get("Name_Localised"), str) and it["Name_Localised"].strip():
            st["names"][i] = it["Name_Localised"].strip()
        if _n(it.get("Stock")):
            sells[i] = it
        elif _n(it.get("Demand")) and _n(it.get("SellPrice")):
            buys[i] = it
    for i, it in sells.items():
        # the market confirms the count; the line's recent moves (the hover text) are kept (review 2026-10-08 #10)
        st["lines"][i] = {"count": _n(it["Stock"]), "state": "confirmed", "ts": ts,
                          "moves": (st["lines"].get(i) or {}).get("moves", [])}
        st["orders"][i] = {"kind": "sell", "amount": _n(it["Stock"]), "price": it.get("BuyPrice")}
    for i in list(st["orders"]):
        # every open sell order is listed with its stock: one the market does not list sold out, or ended
        if st["orders"][i]["kind"] == "sell" and i not in sells:
            st["lines"].pop(i, None)
            del st["orders"][i]
    for i, order in list(st["orders"].items()):
        if order["kind"] != "buy":
            continue
        want = _n((buys.get(i) or {}).get("Demand"))
        if order["amount"] > want:   # other players sold you the difference (your own sales took theirs off already)
            _move(st, i, order["amount"] - want, "others", ts)
            # ...and on top of the last CarrierStats' total only when that is older than the market read before this
            # one: it cannot hold this fill then. Opening the market writes a CarrierStats just before Market.json
            # (JOURNAL_REFERENCE), and that one does hold it: added again it made a false gap (the Fable sweep,
            # 2026-10-09, correcting the night's first fix, which assumed the stats were always older)
            stats_ts = (st.get("stats") or {}).get("ts")
            if stats_ts and st.get("market_ts") and stats_ts < st["market_ts"]:
                st["after_stats"] += order["amount"] - want
        if want:
            order["amount"] = want
        else:
            del st["orders"][i]   # filled
    for i, it in buys.items():
        st["orders"].setdefault(i, {"kind": "buy", "amount": _n(it["Demand"]), "price": it.get("SellPrice")})
    reported = carrier_reported(st)
    if reported is not None and carrier_total(st) > reported:
        cutoff = _iso(_secs(ts) - SEEN_STALE_DAYS * 86400)
        for i, line in list(st["lines"].items()):
            if line["state"] == "seen" and (line["ts"] or "") < cutoff:
                del st["lines"][i]
    st["market_ts"] = ts


def _secs(ts):
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return 0


def _iso(secs):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(secs))


def carrier_total(st):
    return sum(x["count"] for x in st["lines"].values())


def carrier_reported(st):
    """The carrier's own total now: CarrierStats' Cargo plus your journaled moves since (None before any)."""
    stats = st.get("stats") or {}
    return stats["cargo"] + st.get("after_stats", 0) if isinstance(stats.get("cargo"), int) else None


def move_text(move):
    ts, delta, why = move
    return f"{'+' if delta > 0 else '−'}{abs(delta):,} t {MOVE_WORDS.get(why, why)} ({ts[:10]})"


# ---- the carrier's tritium ----

def carrier_burn(ly, used, depot):
    """Tritium one carrier jump burns. Checked against the author's 27 recorded jumps (2026-10-07): exact on 26, 1 t
    off on the other. used: the carrier's capacity in use (TotalCapacity - FreeSpace - CargoSpaceReserved: its cargo
    included, a buy order's reserved space not, as the game's own JumpRangeCurr shows); depot: the tritium in its tank,
    which weighs too (without it the formula reads 1 to 2 t low)."""
    return round(5 + ly * (25000 + used + depot) / 200000)


def carrier_jumps(depot, hold, used, ly=CARRIER_JUMP_LY):
    """How many jumps of `ly` all the carrier's tritium gives: its depot, topped up from `hold` (the tritium in its
    cargo, part of `used`) whenever the next jump needs more. Each burn lightens the next jump."""
    if not isinstance(depot, (int, float)) or not isinstance(used, (int, float)):
        return None
    depot, hold, used, n = int(depot), int(hold or 0), int(used), 0
    while n < 100000:
        burn = carrier_burn(ly, used, depot)
        if depot < burn:
            top = min(CARRIER_DEPOT_MAX - depot, hold)
            depot, hold, used = depot + top, hold - top, used - top   # cargo into the tank: the mass stays
            if depot < burn:
                break
        depot -= burn
        n += 1
    return n


def jumps_estimate(ly, laden_range):
    """A lower bound on the jumps to cover ly with this laden range (a straight line, as Plot Route says)."""
    if not ly or not laden_range or laden_range <= 0:
        return None
    return max(1, math.ceil(ly / laden_range))


# ---- the Sell / Buy lookup (Spansh's station search, read only) ----

# Spansh's station types without the fleet carriers ("Drake-Class Carrier"): stale carrier orders from years ago top
# every price list otherwise. Checked against /api/stations/field_values/type (2026-10-07).
CARRIER_TYPE = "Drake-Class Carrier"
STATION_TYPES = ("Asteroid base", "Coriolis Starport", "Dodec Starport", "Mega ship", "Ocellus Starport",
                 "Orbis Starport", "Outpost", "Planetary Construction Depot", "Planetary Outpost", "Planetary Port",
                 "Settlement", "Space Construction Depot", "Surface Settlement")
LOOKUP_WITHIN = (50, 100, 250, 500, 1000, 2500, 10000)   # ly: the page's choices (best price needs a limit)
FAR_LS = 5000             # a station further than this from the star is marked (a long supercruise)
LOOKUP_SIZE = 20
# The services a row lists (Spansh lists every desk and screen): the ones that matter between hauls, in this order
SERVICES_SHOWN = ("Refuel", "Repair", "Restock", "Universal Cartographics", "Vista Genomics", "Shipyard", "Outfitting",
                  "Interstellar Factors Contact", "Black Market")


def market_query(name, mode, tons, ref, within=500, age_days=14, now=None, pad=None, carriers=False, sort="price"):
    """Spansh's /api/stations/search body: stations that take all `tons` of `name` (mode "sell": demand) or have that
    much (mode "buy": supply), within `within` ly of ref {x, y, z}, data newer than age_days, best price or nearest
    first. pad "L" or "M" keeps the stations your ship can land at."""
    now = time.time() if now is None else now
    qty = "demand" if mode == "sell" else "supply"
    filters = {"market": [{"name": name, qty: {"value": [int(tons), 999999999], "comparison": "<=>"}}],
               "market_updated_at": {"value": [_iso(now - age_days * 86400), _iso(now + 86400)], "comparison": "<=>"},
               "distance": {"min": "0", "max": str(within)}}
    if not carriers:
        filters["type"] = {"value": list(STATION_TYPES)}
    if pad == LARGE:
        filters["has_large_pad"] = {"value": True}
    elif pad == MEDIUM:
        filters["medium_pads"] = {"value": [1, 9999], "comparison": "<=>"}
    near = {"distance": {"direction": "asc"}}
    if sort == "near":
        order = [near]
    elif mode == "sell":   # the same price nearer first
        order = [{"market_sell_price": [{"name": name, "direction": "desc"}]}, near]
    else:
        order = [{"market_buy_price": [{"name": name, "direction": "asc"}]}, near]
    return {"filters": filters, "sort": order, "reference_coords": {"x": ref["x"], "y": ref["y"], "z": ref["z"]},
            "size": LOOKUP_SIZE, "page": 0}


def _when(v):
    """Spansh's market time: an ISO string or epoch seconds -> epoch seconds (None if neither)."""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str) and v:
        s = _secs(v.replace(" ", "T"))
        return float(s) if s else None
    return None


def market_rows(results, name, mode, tons, holding=(), avg=None, laden=None, now=None):
    """Spansh's stations as the lookup's rows. holding: [(name, tons)] of what you carry (the station's other buys
    from it, when selling); avg: what you paid per ton (the profit); laden: your range now (a jump count)."""
    now = time.time() if now is None else now
    rows = []
    for r in results or []:
        if not isinstance(r, dict):
            continue
        market = {m.get("commodity"): m for m in r.get("market") or [] if isinstance(m, dict)}
        m = market.get(name)
        if not m:
            continue
        price = m.get("sell_price" if mode == "sell" else "buy_price") or 0
        qty = m.get("demand" if mode == "sell" else "supply") or 0
        offered = {s.get("name") for s in r.get("services") or [] if isinstance(s, dict)}
        services = [s for s in SERVICES_SHOWN if s in offered]
        updated = _when(r.get("market_updated_at"))
        also = []
        if mode == "sell":
            for other, held in holding:
                o = market.get(other)
                if other != name and o and (o.get("demand") or 0) > 0 and (o.get("sell_price") or 0) > 0:
                    also.append({"name": other, "price": o["sell_price"], "demand": o["demand"], "tons": held})
            also.sort(key=lambda a: -a["price"] * a["tons"])
        ly = round(r.get("distance") or 0, 1)
        ls = round(r.get("distance_to_arrival") or 0)
        rows.append({"station": r.get("name"), "system": r.get("system_name"), "id64": str(r.get("system_id64") or ""),
                     "distance": ly, "jumps": jumps_estimate(ly, laden), "ls": ls, "far": ls > FAR_LS,
                     "type": r.get("type"), "carrier": r.get("type") == CARRIER_TYPE,
                     "pad": LARGE if r.get("has_large_pad") or r.get("large_pads") else MEDIUM if r.get("medium_pads") else SMALL,
                     "price": price, "qty": qty, "value": price * tons,
                     "profit": round((price - avg) * tons) if mode == "sell" and avg else None,
                     "age_s": round(now - updated) if updated else None, "services": services,
                     "uc": "Universal Cartographics" in services, "vista": "Vista Genomics" in services,
                     "also": also[:5], "x": r.get("system_x"), "y": r.get("system_y"), "z": r.get("system_z")})
    return rows


# ---- Spansh's trade planner: a Plot Route type in the survey slot (State.riches_*) ----

# the plot form's defaults (Spansh's own field names, but the data age in days; the station, capital, hold and pad come
# from your journal)
TRADE = {"max_hops": 5, "max_hop_distance": 30, "max_system_distance": 5000, "max_price_age_days": 14,
         "allow_planetary": False, "allow_player_owned": False, "allow_prohibited": False, "permit": False, "unique": False}


def _trade_place(p):
    p = p if isinstance(p, dict) else {}
    return {"system": p.get("system"), "id64": p.get("system_id64"), "x": p.get("x"), "y": p.get("y"), "z": p.get("z"),
            "station": p.get("station"), "market_id": p.get("market_id"),
            "ls": round(p["distance_to_arrival"]) if isinstance(p.get("distance_to_arrival"), (int, float)) else None,
            "updated": _when(p.get("market_updated_at"))}


def trade_rows(result):
    """Spansh's trade route (a list of hops: source -> destination with the commodities to carry) as the survey slot's
    stops: the first source station, then each hop's destination. A stop: {system, id64, x, y, z, jumps (None),
    bodies ([]), station, market_id, ls, updated (epoch s), distance (ly from the stop before), sell [{name, amount,
    price, demand}] (what the hop before brought here), buy [{name, amount, price, supply, sell_price}] (for the hop
    after), profit (of the hop that ends here), cumulative}. [] when the answer is not a route."""
    hops = result if isinstance(result, list) else (result or {}).get("result") if isinstance(result, dict) else None
    stops = []
    for h in hops or []:
        if not isinstance(h, dict) or not isinstance(h.get("source"), dict) or not isinstance(h.get("destination"), dict):
            continue
        src = _trade_place(h["source"])
        if not stops or stops[-1]["market_id"] != src["market_id"]:
            stops.append(dict(src, jumps=None, bodies=[], distance=None, sell=[], buy=[], profit=0,
                              cumulative=stops[-1]["cumulative"] if stops else 0))
        cs = [c for c in h.get("commodities") or [] if isinstance(c, dict) and c.get("name")]
        stops[-1]["buy"] = [{"name": c["name"], "amount": _n(c.get("amount")),
                             "price": (c.get("source_commodity") or {}).get("buy_price"),
                             "supply": (c.get("source_commodity") or {}).get("supply"),
                             "sell_price": (c.get("destination_commodity") or {}).get("sell_price")} for c in cs]
        stops.append(dict(_trade_place(h["destination"]), jumps=None, bodies=[],
                          distance=round(h["distance"], 2) if isinstance(h.get("distance"), (int, float)) else None,
                          sell=[{"name": c["name"], "amount": _n(c.get("amount")),
                                 "price": (c.get("destination_commodity") or {}).get("sell_price"),
                                 "demand": (c.get("destination_commodity") or {}).get("demand")} for c in cs],
                          buy=[], profit=h.get("total_profit") or 0,
                          cumulative=h.get("cumulative_profit") or (stops[-1]["cumulative"] + (h.get("total_profit") or 0))))
    return stops if len(stops) > 1 and all(s["system"] and s["station"] for s in stops) else []


def trade_counts(stop, done, key):
    """{name: tonnes} your journal shows traded at a stop (key "sold" or "bought"). A record from before amounts were
    counted is a list of names, each traded in full then."""
    got = (done or {}).get(key) or {}
    if isinstance(got, list):
        plan = {c["name"]: c.get("amount") or 0 for c in stop.get("sell" if key == "sold" else "buy") or []}
        return {n: plan.get(n, 0) for n in got}
    return dict(got)


def trade_left(stop, done):
    """What is still to do at a stop: ("sell", c) for what the hop before brought, then ("buy", c) for the hop after,
    each with the tonnes still to trade as its amount (and the plan's as `planned`). A commodity is done once its
    planned tonnes are traded (Codex F4: one tonne ticked off a hundred); a stop left with part traded is done
    (done["left"]: you moved on, the station had less than Spansh said). done: {"sold": {name: tonnes}, "bought": ...}."""
    done = done or {}
    if done.get("left"):
        return []
    out = []
    for kind, key in (("sell", "sold"), ("buy", "bought")):
        got = trade_counts(stop, done, key)
        for c in stop.get(kind) or []:
            plan, n = c.get("amount") or 0, got.get(c["name"], 0)
            if not n or n < plan:
                out.append((kind, dict(c, amount=plan - n if n else plan, planned=plan)))
    return out


def trade_short(stop, done):
    """What a stop finished with less than planned: "sold 60 of 100 tonnes of Gold", each one."""
    out = []
    for kind, key, verb in (("sell", "sold", "sold"), ("buy", "bought", "bought")):
        got = trade_counts(stop, done, key)
        for c in stop.get(kind) or []:
            plan, n = c.get("amount") or 0, got.get(c["name"], 0)
            if n < plan:
                out.append(f"{verb} {f'{n:,}' if n else 'none'} of {plan:,} tonnes of {c['name']}")
    return out


def _goods(cs):
    return " and ".join(f"{c['amount']:,} tonnes of {c['name']}" for c in cs)


def trade_text(rows, i, left):
    """The spoken line on arriving at a trade stop: where to dock and what to sell and buy there."""
    r = rows[i]
    sells, buys = [c for k, c in left if k == "sell"], [c for k, c in left if k == "buy"]
    parts = []
    if sells:
        parts.append(f"sell {_goods(sells)}")
    if buys:
        nxt = rows[i + 1]["station"] if i + 1 < len(rows) else None
        parts.append(f"buy {_goods(buys)}" + (f" for {nxt}" if nxt else ""))
    if not parts:
        return f"{r['station']}: nothing left to trade here." + (f" Next stop: {rows[i + 1]['station']}." if i + 1 < len(rows) else "")
    return f"Dock at {r['station']}: {', then '.join(parts)}."


def trade_done_text(rows, i, done=None):
    """Said once the trades at stop i are done: the hop's profit and the next stop, or the route's end. A stop left
    with part traded says what fell short instead of the plan's profit."""
    r = rows[i]
    short = trade_short(r, done) if done else []
    said = "; ".join(short)
    if i == len(rows) - 1:
        return f"Trade route complete, {said}." if short else f"Trade route complete: about {r['cumulative']:,} credits in all."
    nxt = rows[i + 1]
    head = (f"Hop {i} done, {said}. " if i else f"Moving on, {said}. ") if short else \
        f"Hop {i} done, about {r['profit']:,} credits. " if i else ""
    return head + f"Next stop: {nxt['station']} in {nxt['system']}" + (f", {nxt['distance']:.0f} light years." if nxt.get("distance") else ".")
