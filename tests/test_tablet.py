"""The tablet layout (PLAN-tablet phase 3), the server's side: GET /tablet is the same page in a tablet mode with its
shell and theme stylesheets, the desktop page is left as it was, the fonts are OFL with their licences, and a font of
your own comes from data/fonts/ by a safe name only. The page itself is checked by tests/page_smoke.js."""
import asyncio
import os
import re
import tempfile
import unittest
import unittest.mock

from support import ed_outrider  # also puts the repository root on sys.path

import outrider.auth  # noqa: E402


class Tablet(unittest.TestCase):
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

    def test_tablet_page_and_desktop_page(self):
        async def go(c):
            return [(r.status, await r.text()) for r in (await c.get("/tablet"), await c.get("/"))]
        (ts, tablet), (ds, desk) = self.client(go)
        self.assertEqual((ts, ds), (200, 200))
        self.assertIn('<body class="tablet">', tablet)
        self.assertRegex(tablet, r'<html lang="en" data-theme="lcars">')
        for name in ed_outrider.TABLET_STYLES:   # stamped like page.css, so a changed theme is fetched at once
            self.assertRegex(tablet, rf'href="static/{re.escape(name)}\?v=\d+"')
            self.assertTrue(os.path.isfile(os.path.join(ed_outrider.STATIC_DIR, name)), name)
        # the server's data is filled in the same as on the desktop page
        self.assertNotIn("/*SEARCH_OPTIONS*/null", tablet)
        # the desktop page: no tablet class, no theme set by the server, no tablet stylesheet (its shell's markup stays
        # hidden); the themes' stylesheets are linked (stamped), for a desktop theme of this browser's choosing
        self.assertNotIn('class="tablet"', desk)
        self.assertNotIn("data-theme=", desk)
        self.assertNotIn("tablet.css", desk)
        for t in ed_outrider.TABLET_THEMES:
            self.assertRegex(desk, rf'href="static/themes/{t}\.css\?v=\d+"')
        self.assertIn('<nav id="tabNav" class="tb-nav" aria-label="Pages" hidden>', desk)
        # every theme the page offers has its stylesheet, scoped to its data-theme
        for t in ed_outrider.TABLET_THEMES:
            with open(os.path.join(ed_outrider.STATIC_DIR, "themes", f"{t}.css"), encoding="utf-8") as f:
                css = f.read()
            self.assertIn(f'[data-theme="{t}"]', css)
            self.assertIsNone(re.search(r"^:root\s*\{", css, re.M), "a theme sets nothing outside its data-theme")
            self.assertIn(f'<option value="{t}">', tablet)

    def test_desktop_theme_picker(self):
        """Desktop themes (the author, 2026-10-04): Settings > Display offers Default - Outrider and every tablet theme
        under the tablet's label; the browser's choice is set before the first paint (no flash of the default)."""
        with open(os.path.join(ed_outrider.STATIC_DIR, "page.html"), encoding="utf-8") as f:
            html = f.read()
        tab = dict(re.findall(r'<option value="(\w+)">([^<]+)</option>', re.search(r'<select id="tabTheme">(.*?)</select>', html).group(1)))
        self.assertEqual(list(tab), list(ed_outrider.TABLET_THEMES))
        desk = re.search(r'<select id="deskTheme"[^>]*>(.*?)</select>', html, re.S).group(1)
        self.assertIn('<option value="">Default - Outrider</option>', desk)
        head = html[:html.index("</head>")]
        self.assertIn('localStorage.getItem("desktopTheme")', head)   # before the stylesheets apply: no flash
        self.assertLess(head.index('localStorage.getItem("desktopTheme")'), head.index('static/page.css'))

    def test_theme_text_contrast(self):
        """Every theme's text colours read on its background and its panels (WCAG 4.5:1), the desktop page's Default
        too: the page writes small text in each of them (Sith's crimson as text was 3.5:1)."""
        def lum(h):
            h = h.strip().lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
            f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4   # noqa: E731
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)

        def ratio(a, b):
            la, lb = sorted((lum(a), lum(b)), reverse=True)
            return (la + 0.05) / (lb + 0.05)

        def colours(block):
            d = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))
            def res(v, n=0):
                m = re.fullmatch(r"var\((--[\w-]+)\)", v.strip())
                return res(d[m.group(1)], n + 1) if m and n < 10 else v.strip()
            return {k: res(v) for k, v in d.items()}
        with open(os.path.join(ed_outrider.STATIC_DIR, "page.css"), encoding="utf-8") as f:
            blocks = {"default": re.search(r"^:root \{(.*?)\n\}", f.read(), re.S | re.M).group(1)}
        for t in ed_outrider.TABLET_THEMES:
            with open(os.path.join(ed_outrider.STATIC_DIR, "themes", f"{t}.css"), encoding="utf-8") as f:
                blocks[t] = re.search(r':root\[data-theme="%s"\]\s*\{(.*?)\n\}' % t, f.read(), re.S).group(1)
        for t, block in blocks.items():
            c = colours(block)
            for k in ("--text", "--muted", "--accent", "--good", "--warn", "--bad", "--info"):
                for ground in ("--bg", "--panel"):
                    self.assertGreaterEqual(round(ratio(c[k], c[ground]), 2), 4.5, f"{t}: {k} {c[k]} on {ground} {c[ground]}")

    def test_desktop_theme_sections(self):
        """Each theme has its desktop section (body:not(.tablet): the shapes and the pills' size), and the view pill
        that is on reads on its fill (4.5:1): white on Dark's blue and black on Sith's crimson did not."""
        def lum(h):
            h = h.strip().lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
            f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4   # noqa: E731
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
        for t in ed_outrider.TABLET_THEMES:
            with open(os.path.join(ed_outrider.STATIC_DIR, "themes", f"{t}.css"), encoding="utf-8") as f:
                css = f.read()
            self.assertIn(f'[data-theme="{t}"] body:not(.tablet)', css, t)
            self.assertRegex(css, r":root\[data-theme=\"%s\"\] \{ --desk-pill: " % t)
            block = re.search(r':root\[data-theme="%s"\]\s*\{(.*?)\n\}' % t, css, re.S).group(1)
            d = dict(re.findall(r"(--[\w-]+):\s*([^;]+);", block))
            res = lambda v: res(d[m.group(1)]) if (m := re.fullmatch(r"var\((--[\w-]+)\)", v.strip())) else v.strip()   # noqa: E731
            on = re.search(r"\.views button\.on \{ background: ([^;]+); color: ([^;]+);", css)
            bg, fg = res(on.group(1)) if on else res(d["--accent"]), res(on.group(2)) if on else res(d["--bg"])
            fg = "#ffffff" if fg == "#fff" else "#000000" if fg == "#000" else fg
            la, lb = sorted((lum(bg), lum(fg)), reverse=True)
            self.assertGreaterEqual(round((la + 0.05) / (lb + 0.05), 2), 4.5, f"{t}: the pill that is on, {fg} on {bg}")

    def test_name_fields_are_not_auto_capitalised(self):
        """A tablet keyboard capitalises a field's first letter and corrects words: system names and search terms
        must reach Outrider as typed (found on a tablet's on-screen keyboard)."""
        with open(os.path.join(ed_outrider.STATIC_DIR, "page.html"), encoding="utf-8") as f:
            html = f.read()
        for id_ in ("findName", "hwyFrom", "hwyTo", "lFilter", "mFilter", "bFilter"):
            tag = re.search(rf'<input [^>]*id="{id_}"[^>]*>', html).group(0)
            for attr in ('autocapitalize="off"', 'autocorrect="off"', 'spellcheck="false"'):
                self.assertIn(attr, tag, id_)

    def test_page_stamp(self):
        """An open page reloads itself when Outrider has newer page files (a tablet left open over a restart kept its
        old Search): the stamp it is served with, the same in every payload, changes with any page file."""
        async def go(c):
            pages = [await (await c.get(u)).text() for u in ("/", "/tablet")]
            return pages, (await (await c.get("/api/nearby")).json())["page_stamp"]
        pages, stamp = self.client(go)
        self.assertRegex(stamp, r"^[0-9a-f]{12}$")
        for html in pages:
            self.assertIn(f'window.__PAGE_STAMP__ = "{stamp}";', html)
        with tempfile.TemporaryDirectory() as d:
            for name in ed_outrider.PAGE_FILES:
                os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
                with open(os.path.join(d, name), "w") as f:
                    f.write("x")
            a = ed_outrider.page_stamp(d)
            self.assertEqual(ed_outrider.page_stamp(d), a)
            path = os.path.join(d, "themes", "lcars.css")
            os.utime(path, ns=(1, os.stat(path).st_mtime_ns + 10 ** 9))
            self.assertNotEqual(ed_outrider.page_stamp(d), a)

    def test_restart_needed(self):
        """Outrider's code changed on disk but it was not restarted: the payload says so, so an open page waits for the
        restart before reloading onto page files that may need the new server (found on the tablet: Ask before /api/ask)."""
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "outrider"))
            for name in ("ed_outrider.py", os.path.join("outrider", "a.py")):
                with open(os.path.join(d, name), "w") as f:
                    f.write("x = 1\n")
            a = ed_outrider.code_stamp(d)
            p = os.path.join(d, "outrider", "a.py")
            os.utime(p, ns=(1, os.stat(p).st_mtime_ns + 10 ** 9))
            self.assertNotEqual(ed_outrider.code_stamp(d), a)
        self.assertFalse(ed_outrider.restart_needed(now=1e12))   # the code running is the code on disk
        self.assertFalse(self.state.payload()["restart_needed"])
        with unittest.mock.patch.object(ed_outrider, "CODE_STAMP_START", "older000000"):
            self.assertTrue(ed_outrider.restart_needed(now=2e12))
            self.assertTrue(self.state.payload()["restart_needed"])
        ed_outrider.restart_needed(now=3e12)

    def test_dialog_buttons_typed(self):
        """R11: a button in a <form method="dialog"> without a type is a submit button, so Enter in any field of the
        form "presses" the first one (the rail editor's ✕: typed labels thrown away). Every one says what it is."""
        with open(os.path.join(ed_outrider.STATIC_DIR, "page.html"), encoding="utf-8") as f:
            html = f.read()
        forms = re.findall(r'<form[^>]*method="dialog"[^>]*>(.*?)</form>', html, re.S)
        self.assertGreaterEqual(len(forms), 5)
        untyped = [b for f in forms for b in re.findall(r"<button[^>]*>", f) if "type=" not in b]
        self.assertEqual(untyped, [])

    def test_ids_unique(self):
        """Every id in the page is used once: the Nearest finder's table was given the Nearby tab's id (nearTable), so
        the finder's styles (13px text, cell padding) reached the Nearby tab and a finder row's tap on the tablet
        opened the Nearby tab's detail sheet."""
        with open(os.path.join(ed_outrider.STATIC_DIR, "page.html"), encoding="utf-8") as f:
            html = f.read()
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertGreater(len(ids), 100)
        self.assertEqual(sorted({i for i in ids if ids.count(i) > 1}), [])

    def reset_stamps(self):
        for name in ("_stamps", "_page_stamp", "_code_stamp"):   # the stamps' cache, forgotten (a test's clock is far ahead)
            if isinstance(getattr(ed_outrider, name, None), dict):
                getattr(ed_outrider, name)["at"] = None

    def test_stamps_change_together(self):
        """R3: the page stamp and restart_needed come from one refresh, so a payload never pairs the new page files'
        stamp with a stale "no restart needed" (the page would reload onto files the running server can't serve)."""
        self.addCleanup(self.reset_stamps)
        self.reset_stamps()
        t = 1e13
        with tempfile.TemporaryDirectory() as d:
            for name in ed_outrider.PAGE_FILES:
                os.makedirs(os.path.dirname(os.path.join(d, name)), exist_ok=True)
                with open(os.path.join(d, name), "w") as f:
                    f.write("x")
            with unittest.mock.patch.object(ed_outrider, "STATIC_DIR", d):
                old = ed_outrider.page_stamp(now=t)
                self.assertFalse(ed_outrider.restart_needed(now=t + 3))
                # an update: new page files and new code on disk, the running server not restarted
                with open(os.path.join(d, "page.js"), "w") as f:
                    f.write("new")
                with unittest.mock.patch.object(ed_outrider, "code_stamp", lambda root=None: "updated00000"):
                    for now in (t + 4, t + 6, t + 8.5, t + 12):
                        new_page, restart = ed_outrider.page_stamp(now=now), ed_outrider.restart_needed(now=now)
                        self.assertFalse(new_page != old and not restart, now)

    def test_index_stamp_fresh(self):
        """R3: a page served right after an update carries the stamp of the files it got, not the cached one, and the
        payload then agrees with it (no reload loop)."""
        self.addCleanup(self.reset_stamps)
        cached = ed_outrider.page_stamp()

        async def go(c):
            html = await (await c.get("/")).text()
            return html, (await (await c.get("/api/nearby")).json())["page_stamp"]
        with unittest.mock.patch.object(ed_outrider, "PAGE_FILES", ed_outrider.PAGE_FILES + ("added-by-an-update.css",)):
            html, payload_stamp = self.client(go)
            fresh = ed_outrider.page_stamp(ed_outrider.STATIC_DIR)
        self.assertNotEqual(fresh, cached)
        self.assertIn(f'window.__PAGE_STAMP__ = "{fresh}";', html)
        self.assertEqual(payload_stamp, fresh)

    def test_fonts_are_ofl_and_shipped_with_their_licences(self):
        fonts = os.path.join(ed_outrider.STATIC_DIR, "fonts")
        files = os.listdir(fonts)
        ttf = [f for f in files if f.endswith((".ttf", ".otf", ".woff", ".woff2"))]
        self.assertTrue(ttf)
        for f in ttf:   # Antonio-VF.ttf -> OFL-Antonio.txt; BarlowCondensed-Medium.ttf -> OFL-BarlowCondensed.txt
            family = f.split("-")[0]
            lic = os.path.join(fonts, f"OFL-{family}.txt")
            self.assertTrue(os.path.isfile(lic), f"{f}: no {lic}")
            with open(lic, encoding="utf-8") as fh:
                self.assertIn("SIL Open Font License, Version 1.1", fh.read())
        # every font a stylesheet asks for from static/fonts/ is there (a typo would silently fall back)
        for css in ["tablet.css"] + [os.path.join("themes", f"{t}.css") for t in ed_outrider.TABLET_THEMES]:
            with open(os.path.join(ed_outrider.STATIC_DIR, css), encoding="utf-8") as fh:
                for name in re.findall(r'url\("\.\./fonts/([^"]+)"\)', fh.read()):
                    self.assertIn(name, files, css)

    def test_faction_emblems(self):
        """The Babylon 5 and Star Wars themes show their faction's emblem in the free space under the page list (the
        author's request): local copies (the Android app blocks anything that is not Outrider), credited with their
        licences, one per theme and none for the others, served as images."""
        emblems = {"elite": "elite.webp", "babylon5": "babylon5.webp", "narn": "narn.webp", "minbari": "minbari.webp", "centauri": "centauri.webp",
                   "vorlon": "vorlon.webp", "shadow": "shadow.webp", "sith": "sith.svg", "alliance": "alliance.svg"}
        folder = os.path.join(ed_outrider.STATIC_DIR, "emblems")
        with open(os.path.join(folder, "CREDITS.txt"), encoding="utf-8") as f:
            credits = f.read()
        for t in ed_outrider.TABLET_THEMES:
            with open(os.path.join(ed_outrider.STATIC_DIR, "themes", f"{t}.css"), encoding="utf-8") as f:
                refs = set(re.findall(r'url\("\.\./emblems/([^"]+)"\)', f.read()))
            self.assertEqual(refs, {emblems[t]} if t in emblems else set(), t)
            if t in emblems:
                self.assertTrue(os.path.isfile(os.path.join(folder, emblems[t])), t)
                # never on a painted block: the column's free space is unfilled while the emblem shows (Narn's rust
                # fill made an orange box of it, on the author's tablet)
                with open(os.path.join(ed_outrider.STATIC_DIR, "themes", f"{t}.css"), encoding="utf-8") as f:
                    css = f.read()
                self.assertTrue("--tb-fill: transparent" in css or
                                f':root[data-theme="{t}"]:not(.tb-noemblem) .tb-navfill {{ background: transparent;' in css, t)
                self.assertIn(emblems[t], credits)
        with open(os.path.join(ed_outrider.STATIC_DIR, "page.js"), encoding="utf-8") as f:   # the page offers the setting for these
            listed = re.search(r"const TB_EMBLEMS = \[([^\]]*)\]", f.read()).group(1)
        self.assertEqual(set(re.findall(r'"(\w+)"', listed)), set(emblems))
        for words in ("CC BY-SA 4.0", "Warner Bros", "Lucasfilm", "public domain", "with permission of Frontier Developments plc"):
            self.assertIn(words, credits)
        with open(os.path.join(ed_outrider.STATIC_DIR, "page.html"), encoding="utf-8") as f:
            self.assertRegex(f.read(), r'<div class="tb-navfill" aria-hidden="true"><div class="tb-emblem"></div></div>')

        async def go(c):
            out = []
            for name in ("babylon5.webp", "sith.svg"):
                r = await c.get(f"/static/emblems/{name}")
                out.append((r.status, r.headers.get("Content-Type", "").split(";")[0]))
            return out
        # as on Python 3.12 (the Docker image): aiohttp's own table of static file types without .webp
        import mimetypes
        import aiohttp.web_fileresponse as fr
        bare = mimetypes.MimeTypes()
        for strict in (True, False):
            bare.types_map[strict].pop(".webp", None)
            bare.types_map_inv[strict].pop("image/webp", None)
        with unittest.mock.patch.object(fr, "CONTENT_TYPES", bare):
            ed_outrider.register_static_types()
            self.assertEqual(self.client(go), [(200, "image/webp"), (200, "image/svg+xml")])

    def test_dark_theme_icons(self):
        """The dark theme's line icons are Lucide's (ISC: its licence ships beside them), inline as CSS masks written by
        scripts/dark_icons.py (no request per icon), and only under data-theme="dark"."""
        icons = os.path.join(ed_outrider.STATIC_DIR, "icons")
        with open(os.path.join(icons, "LICENSE-lucide.txt"), encoding="utf-8") as f:
            self.assertIn("ISC License", f.read())
        with open(os.path.join(ed_outrider.STATIC_DIR, "themes", "dark.css"), encoding="utf-8") as f:
            css = f.read()
        block = css[css.index("/* ---- icons: written by scripts/dark_icons.py ---- */"):]
        rules = [r for r in block.splitlines() if "mask:" in r]
        self.assertGreaterEqual(len(rules), 40)
        self.assertTrue(all(r.startswith('[data-theme="dark"] body.tablet ') and "data:image/svg+xml," in r for r in rules))
        self.assertNotIn("url(\"../icons", css)

    def test_user_fonts(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "lcars-display.ttf"), "wb") as f:
                f.write(b"\x00\x01\x00\x00font")
            with open(os.path.join(d, "notes.txt"), "w") as f:
                f.write("not a font")
            with unittest.mock.patch.object(ed_outrider, "FONT_DIR", d):
                async def go(c):
                    r = await c.get("/userfonts/lcars-display.ttf")
                    out = [(r.status, await r.read())]
                    for bad in ("/userfonts/notes.txt", "/userfonts/missing.ttf", "/userfonts/..%2Fsecret.ttf",
                                "/userfonts/.hidden.ttf"):
                        out.append((await c.get(bad)).status)
                    return out
                got = self.client(go)
        self.assertEqual(got[0], (200, b"\x00\x01\x00\x00font"))
        self.assertEqual(got[1:], [404, 404, 404, 404])

    def test_tablet_needs_the_password_from_the_network(self):
        self.state.password = "pw"
        p = unittest.mock.patch.object(outrider.auth, "is_loopback", lambda remote: False)
        p.start()
        self.addCleanup(p.stop)

        async def go(c):
            r = await c.get("/tablet", allow_redirects=False)
            app = await c.get("/tablet", headers={"User-Agent": "Mozilla/5.0 OutriderApp/1.0.0"}, allow_redirects=False)
            font = await c.get("/userfonts/x.ttf")
            return r.status, r.headers.get("Location"), app.status, font.status
        status, where, app, font = self.client(go)
        self.assertEqual((status, app, font), (302, 401, 401))
        self.assertTrue(where.startswith("/signin?next=") and "tablet" in where)


if __name__ == "__main__":
    unittest.main()
