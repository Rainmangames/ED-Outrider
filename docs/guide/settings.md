[ED Outrider](../../README.md) · [What's new](whats-new.md) · [Install and run](install.md) · [The views](views.md) · [Plot Route](plot-route.md) · [Cargo and trading](cargo-and-trading.md) · [Voice and alerts](voice-and-alerts.md) · [Automation](automation.md) · [On a tablet](tablet.md) · [In-game overlay](overlay.md) · [Uploads](uploads.md) · **Settings and good to know** · [For the curious](for-developers.md)

# Settings and good to know

## ⚙️ Settings

<details>
<summary>Outrider needs no configuration — but everything can be changed.</summary>
<br>

**⚙ Settings** (top right) holds everything, in folding sections: Alerts, Voice, What is said, Sounds, Values, Risk
& warnings, Surface map, Auto honk, Uploads, In-game overlay, Display, Sharing, **Server** and Spoken lines. Most are this browser's
own (Sharing exports them or makes them the defaults for new browsers). **Server** is the config file
itself, every key of it but the upload switches (`[eddn]`, `[edsm]`, `[inara]`: Settings → Uploads): the network and the client password, the journal folders, paths, backups, Spansh, the
Highway, the voice's AI layer and more. Saving there writes `ed_outrider.toml` (only the keys you changed; its
comments stay, and the previous file is kept as `ed_outrider.toml.bak`), and Outrider uses them from its next start.
The passwords (`[server]`'s and `[mcp]`'s) and the AI key are never shown, only whether they are set.

You can also edit the file by hand: copy `ed_outrider.toml.example` to `ed_outrider.toml` next to the script and
edit the lines you need. The example explains every key. Relative paths in it (`db`, `backup_dir`, `speech_file`, `sound_dir`,
`background_image`) are relative to the Outrider folder; the first three default to `data/ed_outrider.sqlite`, `data/backups`
and `resources/speech.json`.
`python3 ed_outrider.py --write-config` writes one with the settings in effect. Switches take a bare `true` or
`false`; a wrong value is reported at start and the default kept.

> [!IMPORTANT]
> **Windows paths in the file:** use forward slashes, `"C:/Users/you/Saved Games/..."`, or single quotes,
> `'C:\Users\you\Saved Games\...'`. In double quotes a backslash starts an escape (`"C:\Users"` is an error),
> and a file that cannot be read is ignored as a whole: every setting back at its default, with one line in the
> console saying why. Settings → Server takes paths either way and writes them correctly.

| Section | What it holds (default) |
|---|---|
| `[journals]` | `live` and `legacy` folders, when auto-detection misses them (both auto-detected when absent; setting `live` turns off legacy auto-detection: list `legacy` too, `[]` for none) |
| `[server]` | `host` ("127.0.0.1"), `port` (8025), `password` ("": none), `game_pc` ("auto": off inside a container), `update_check` (true), `allowed_hosts` ([]), `radius` (25 ly), `radius_choices` ([20, 25, 30, 40, 50]), `db` ("data/ed_outrider.sqlite"), `backup_dir` ("data/backups"), `backup_keep` (7), `backup_every_days` (1), `speech_file` ("resources/speech.json") |
| `[defaults]` | What a new browser starts with: the thresholds `unsold_warn` (50000000), `unsold_urgent` (250000000), `bio_min` (10000000), `body_highlight_level` (500000), `biology_highlight_value` (10000000), `body_max_value_include_bonus` (true), `high_gravity` (2.0 g), `module_warn` (80 %); `sounds` (true), `voice` ("en_GB-cori-medium"), `voice_fallback` ("en_GB-jenny_dioco-medium"), `speech_styles` (["business"]), `speech_profanity` (false), `speech_profanity_pct` (50), `speech_danger_business` (true), `speak_bio_signals` (true), `speak_geo_signals` (true), `speak_mapped` (false), `codex_interesting` (true), `speech_speed` (1.0), `speech_names` ("Boss, Hefay, Sir"); the surface map's `surface_alt` (1000 m), `rig_spacing` (50 m), `surface_map_min` (500 m), `surface_map_strip` (false), `rig_warn` (3500 m) |
| `[spansh]` | `concurrency` (4), `map_max_radius` (250 ly), `map_max_pages` (6), `watch_firsts` (true) |
| `[autohonk]` | `enabled` (false), `key` ("auto": read from your controls preset), `delay` (2.0 s), `hold` (6.0 s), `skip_honked` (true), `announce` (true) |
| `[speech]` | `server_player` ("auto"), for **Play speech and sounds on this PC**; `sound_dir` (none), your own alert sounds |
| `[copilot]` | `enabled` (false), `device` (""), `button` (""), `hold_ms` (600), `double_ms` (400) |
| `[assistant]` | `enabled` (false), `base_url` (""), `api_key` (""), `model` (""), `timeout` (20 s), `max_rounds` (4): the voice's optional AI layer |
| `[mcp]` | `url` (this PC at `[server] port`), `max_rows` (25), `password` (""): for the MCP bridge (see Ask an AI about your game) |
| `[eddn]`, `[edsm]`, `[inara]` | `enabled` (false): written by the switches in Settings → Uploads (the only place to change them) |
| `[overlay]` | `enabled` (false), `theme` ("default"; or lcars, elite, babylon5, narn, minbari, centauri, vorlon, shadow, sith, alliance, dark), `text_size` ("normal"), `system_panel` (true), `body_panel` (true), `radar` (true), `strip_panel` (false), `now_panel` (false), `bio_panel` (false), `system_seconds` (0: while in supercruise there), `radar_range` (800 m) (the overlay is a game-PC feature: see In-game overlay) |
| `[highway]` | `clipboard` (true), `efficiency` (60 %), `conservative` (false), `conservative_ly` (5 ly), `background_image` ("": none), `background_extent` ([-45000, 45000, -20000, 70000]), `background_opacity` (0.6); auto-target: `autotarget` (false), `autotarget_delay` (5 s), `autotarget_entry` ("type"), `autotarget_map_wait` (5 s), `autotarget_search_wait` (2 s), `autotarget_key_delay` (0.05 s), `autotarget_keys` ({}: read from your controls preset), `autotarget_search`, `autotarget_submit`, `autotarget_plot` (the galaxy map's steps: see the example file), `autotarget_dry_run` (false) |

Command-line flags override the file for a single run:

| Flag | |
|---|---|
| `--radius`, `--host`, `--port` | Search radius, address and port |
| `--db PATH` | Database file (for a second copy) |
| `--config PATH` | Config file to use |
| `--write-config` | Write the settings in effect to the config file (never overwrites) |
| `--journals PATH`, `--legacy PATH` | Journal folders to follow, or older ones to import once (repeatable) |
| `--rescan` | Rebuild from the journals, keeping the Spansh cache |
| `--restore [ZIP]`, `--list-backups` | See Backups |
| `--simulate` | For screenshots and demos: the panels show the last known values (fuel...) as if the game were running; auto honk, auto-target, the co-pilot button, the clipboard and the in-game overlay's window are off |

</details>

## 🧭 Good to know

> [!NOTE]
> **"Not on the page" means "nobody with an uploader has reported it."** Players who don't run
> EDMC or a similar tool never reach Spansh or EDSM, so a missing system is *almost* certainly
> undiscovered. A second after you arrive, the arrival star's scan settles it — and the page
> (and the voice) says which it was.

- **Exobiology guesses are possibilities, not promises.** They come from each species' known spawn
  conditions, as maintained by the [BioScan](https://github.com/Silarn/EDMC-BioScan) project, including
  its check on which star types a species appears around. Until every body is found, nothing is ruled out
  on what isn't known yet. The genus is usually right, the species sometimes not, so values show as
  "up to". The ✦ "new to your codex" mark checks the colour variant when it can be told, and names it ("✦ Grey");
  ✪ is a species or colour in your codex nowhere at all ("✪ Cobalt"), worth more effort. A Bacterium's colour
  comes from a rare element in each body's own materials, so bodies in one system can differ. Each start checks GitHub for newer rules. Settings → Display can
  leave out of Here's bio column the species you have finished and the bodies with few signals, and show each species'
  colour word in that colour ("Red" in red; this device). A finished species is one line in Here: its codex entry with what it
  pays ("📖 Bacterium Alcyoneum - Red ✦ 8.3M").
- **Values are estimates** using the community tools' formula, bonuses included (the full-scan bonus too: 1,000 cr
  per body of a system you found complete while all of it was undiscovered). An NPC crew member's cut
  comes off automatically, based on what your past sales paid.
- **Losing your ship loses your data.** Discoveries and samples that went down show as *lost* until you scan
  them again. Scanning a body you've already sold adds nothing; only mapping it still pays.
- **New versions.** Once a day Outrider asks GitHub whether a newer release is out (only that request: nothing
  about you is sent; `[server] update_check = false` turns it off). When there is one, a small **⬆ Update** pill
  appears beside the link pill (on the tablet, beside "linked"), in the theme's colours. It opens what's new and how
  to update this copy: Docker, a git clone or a downloaded release. **Skip this version** hides it until the next
  one (per browser).
- **Small things worth knowing:** the pill at the top right says whether the page is linked to Outrider:
  "linked", "linked · no game" (the game is not running), "unlinked · retrying" (after 30 s without an answer); Here's Dist, Grav, Now and Max headings sort the bodies; a genus's tooltip gives
  its colony distance; Search's mining list includes minerals you have refined, with those bodies first; the
  Settings' chips jump to its sections; and Settings → Display chooses this browser's theme and whether the header tiles fold to one line on a
  small window, on this device.

---

[ED Outrider](../../README.md) · [What's new](whats-new.md) · [Install and run](install.md) · [The views](views.md) · [Plot Route](plot-route.md) · [Cargo and trading](cargo-and-trading.md) · [Voice and alerts](voice-and-alerts.md) · [Automation](automation.md) · [On a tablet](tablet.md) · [In-game overlay](overlay.md) · [Uploads](uploads.md) · **Settings and good to know** · [For the curious](for-developers.md)
