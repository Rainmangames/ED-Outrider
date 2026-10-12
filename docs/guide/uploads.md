[ED Outrider](../../README.md) · [What's new](whats-new.md) · [Install and run](install.md) · [The views](views.md) · [Plot Route](plot-route.md) · [Cargo and trading](cargo-and-trading.md) · [Voice and alerts](voice-and-alerts.md) · [Automation](automation.md) · [On a tablet](tablet.md) · [In-game overlay](overlay.md) · **Uploads** · [Settings and good to know](settings.md) · [For the curious](for-developers.md)

# Uploads: EDDN, EDSM and Inara

Outrider can send what your journals say to three community services, as EDMC does, so you need not run EDMC just to
upload. **All are off unless you switch them on**, in Settings → Uploads. The switch applies at once and is kept in
the config file (`[eddn] enabled`, `[edsm] enabled`, `[inara] enabled`) for the next start; it is the only place to
switch them (they are not among the Server settings).

- **EDDN**, the Elite Dangerous Data Network: systems, scans, signals, codex entries, biology samples, settlement
  approaches, docking answers, markets, outfitting, shipyards, carrier materials, plotted routes, sent as they happen.
  Spansh, EDSM, Inara and others read it. EDDN gets your
  commander name and hashes it before anyone else sees it; personal details (fines, fuel, wanted, where your ship is)
  are taken out first. Codex entries and biology samples keep where on the planet they were found: that is the data
  (a sample's place only when it was read live, at the moment of the scan).
  Station data goes once per visit: each docking sends it again, changed or not, since the sites date a station's
  data by it.
- **EDSM**, the Elite Dangerous Star Map: your flight log, scans, materials, ship and cargo, to your own EDSM account.
  It goes in batches: each jump or docking sends what waited, and nothing waits more than five minutes. EDSM says
  which events it does not want, and those are not sent.
  - It needs your EDSM commander name and API key ([edsm.net → Settings → API key](https://www.edsm.net/en/settings/api)),
    per in-game commander, set in Settings → Uploads. The key stays on this Outrider: the page shows only its first
    and last four characters, to compare with edsm.net.
  - A commander with no key sends nothing, and Settings says so.
  - A key EDSM refuses stops the sending (not the recording) until you change the key or switch EDSM off and on:
    what you played meanwhile then goes.
  - If EDSM keeps refusing a key you know is right, make a new key on edsm.net and paste that one.
- **Inara**: keeps your commander profile there current, as EDMC's Inara upload does, so a player who uses Outrider
  alone loses nothing on it:
  - your flight log and location (jumps, carrier jumps, dockings, landings), credits (once per session, the game's own
    figures at login), the game's statistics;
  - every rank and its progress (Combat, Trade, Exploration, CQC, Mercenary, Exobiologist, the two navies), Powerplay,
    the engineers;
  - reputation with the superpowers and the factions you meet;
  - materials, your ship's cargo and the ship locker; the ship you fly and its loadout, your fleet (bought, sold,
    swapped, sent elsewhere, renamed, where each one is) and your stored modules;
  - community goals you take part in, missions, the combat log, friends and suit loadouts.

  Never anything about your fleet carrier (Inara's API has no carrier hold) or the SRV's cargo.
  - It goes in batches (each jump or docking, the end of your login, at most five minutes' wait), at most one request
    every 35 seconds, and only what changed since your profile last got it: the game rewrites your ranks, cargo and
    loadout often, and Inara asks apps not to send the same thing twice.
  - It needs your Inara API key ([inara.cz → Settings → API key](https://inara.cz/elite/cmdr-settings-api/)), per
    in-game commander, set in Settings → Uploads. The key stays on this Outrider: the page shows only its first and
    last four characters.
  - A commander with no key sends nothing, and Settings says so. Once you enter a key (or another one), your ranks,
    ship, inventory and the rest go whole again the next time the game writes them, most of it at your next login.
  - **Switch Inara off in any other app first** (EDMC, EDDI, EDDiscovery, EDDLite): two apps sending can get your key
    revoked. Outrider sees EDMC's setting on this PC and holds its own while EDMC sends; it cannot see the others.
  - A key or app Inara refuses stops the sending (not the recording) with Inara's words in Settings, until you change
    the key or switch Inara off and on. A refusal for sending too often waits an hour by itself.

## Catching up, and what is never sent

Each upload remembers how far through your journals it has got. If Outrider was not running while you played (or the
server was down), the next start sends what you played meanwhile: to EDSM and Inara up to a week back, to EDDN only the last
hour, since EDDN's readers take what arrives as current. For the same reason an EDDN message that could not go for an
hour (EDDN unreachable) is dropped, not sent late. A journal re-read sends nothing twice, a restored backup starts
from where your journals are now, and switching an upload on starts from that moment: your history is never uploaded.

Markets, outfitting, shipyards, carrier materials and plotted routes come from files the game rewrites each time, so a
catch-up can send one only while the file is still the one that visit wrote (the last one, after a short gap). Live,
Outrider waits up to about half a minute for the game to write the file (it can write a fleet carrier's market well
after the line); if it never comes, that one is not sent, and Outrider's log says so with what the file held. A codex
entry caught up late has no body name, and a biology sample caught up late no position: both come from the live
Status.json.

Never sent:

- Anything older than a week (an hour for EDDN), or from a legacy folder (journals imported once).
- Anything from the game's beta, or from the Legacy game (3.8): Settings says so ("unavailable: the Legacy game").
- Anything while you are crew in another commander's ship.
- Anything under `--simulate`.

## One uploader at a time

Two Outriders reading the same journals (the game PC's and a server's), or Outrider and EDMC, would send everything
twice: EDDN has no way to tell. So:

- **A note in the journal folder.** Each uploading Outrider leaves one (`.outrider/uploads-<id>.json`, rewritten every
  minute). Another Outrider that sees a fresh note will not start the same upload ("Already uploading from
  erangel"). If two are switched on for the same upload anyway (both started with it on), the one already sending
  keeps it and the other gives way and says so; started together, they agree on one. An Outrider older than
  2026.10.19.1 is always given way to.
  - A note left by a crash goes stale after five minutes; one untouched for an hour is ignored at once.
  - When an Outrider stops, its note stays with how far it got: switch the upload on in another one and it starts
    there, with nothing missed and nothing sent twice. An Outrider that was giving way takes over from there too
    (it notices within a minute, then sends what was played since the stop). After a crash it takes over from
    the crashed one's last note, once that has gone stale.
- **While another uploader has it.** While EDMC or another Outrider sends a service, this one follows the journal
  without sending, and catches none of that up later.
- **Catching up cannot know everything.** If EDMC, or a read-only Outrider (which leaves no note), sent while this
  one was not running, it is sent again. Keep one uploader.
- **A read-only journal folder.** An Outrider that cannot write there (a read-only share) still reads the others'
  notes. If another one already uploads, it refuses ("Filesystem is read-only and another instance is set for
  upload"); otherwise it asks first: "Is this the only Outrider uploading? Other instances can't see this one".
- **In Docker** the journals are mounted read-only.
  - To give the server its note folder, create it on the share (`mkdir -p "$JOURNALS/.outrider"`, as the share's
    owner) and uncomment the `.outrider` line in `docker-compose.yml`.
  - If the share itself is read-only on the server (an `ro` export or mount), leave that line commented: Docker
    cannot create the folder there and the container would not start. Outrider then works as a read-only instance.
    It sees the game PC's note, but the game PC cannot see its own, so if the server uploads, keep the game PC's
    uploads off yourself (that is what the "only Outrider" question is about).
  - Its note names it "outrider-docker"; set `OUTRIDER_HOST` in `.env` for another name.
- **EDMC on the same PC.** If it runs with its own EDDN, EDSM or Inara upload on, Outrider holds that upload and says
  so. Switch EDMC's off (its File → Settings → EDDN / EDSM / Inara tabs) before switching Outrider's on. EDMC on another
  computer cannot be seen: switch it off there yourself, as in EDDI or EDDiscovery for Inara.

## Status

The header's Data tile shows, live, a line per upload in use: what it sent, has waiting and had refused in the last
day. Settings → Uploads shows the same per service, with why nothing can be sent now (held by another uploader, a key
EDSM or Inara refused, the beta or Legacy game) and the last message (a refusal, "saved").

If EDDN refuses one kind of message (markets, say) three times within an hour, or once with 426 (a version it no
longer takes), Outrider stops sending that kind until it restarts: usually a game update EDDN does not take yet.
The other kinds still go.

A developer's switch shows next to the service in Settings → Uploads: "(test schemas only)" when `OUTRIDER_EDDN_TEST`
is set, "(dry run: nothing sent)" when `OUTRIDER_EDSM_DRYRUN` or `OUTRIDER_INARA_DRYRUN` is (the requests go to
`data/edsm-dryrun.jsonl` / `data/inara-dryrun.jsonl`, never the key), "(development mode)" when `OUTRIDER_INARA_DEV`
is (Inara's `isBeingDeveloped`: for a test account).

---

[ED Outrider](../../README.md) · [What's new](whats-new.md) · [Install and run](install.md) · [The views](views.md) · [Plot Route](plot-route.md) · [Cargo and trading](cargo-and-trading.md) · [Voice and alerts](voice-and-alerts.md) · [Automation](automation.md) · [On a tablet](tablet.md) · [In-game overlay](overlay.md) · **Uploads** · [Settings and good to know](settings.md) · [For the curious](for-developers.md)
