#!/usr/bin/env python3
"""Recon a permit portal and report exactly what to put in SELECTORS.

Run this LOCALLY, never on Apify.

USAGE
    python tools/recon_portal.py                      # bundled Chromium
    python tools/recon_portal.py --browser brave      # your installed Brave
    python tools/recon_portal.py --browser chrome
    python tools/recon_portal.py --browser edge
    python tools/recon_portal.py --browser brave --profile
    python tools/recon_portal.py --url "https://other-city.gov/search"

    --profile  uses your REAL browser profile (cookies, extensions, history).
               Close every window of that browser first or it will refuse to
               start. Most likely to load a stubborn site -- and also the
               biggest warning sign, see below.

WHY THE BROWSER CHOICE MATTERS MORE THAN IT LOOKS
    Apify runs headless Chromium in a Linux container with no profile, no
    extensions, and a datacenter IP. That is the harshest possible case.

    So read your results this way:

      loads in bundled Chromium          -> the actor will work. Proceed.
      needs Brave/Chrome but no profile  -> probably a UA or TLS check. Usually
                                            fixable in the actor. Proceed with
                                            care.
      needs your real profile/cookies    -> the site is gating on session or
                                            device identity. A headless cloud
                                            actor will NOT reproduce this.
                                            Stop and rethink before writing
                                            any selectors.

    This script prints a verdict on exactly that question at the end.

NOTE ON BRAVE
    Brave Shields blocks scripts and third-party frames by default, which can
    itself break a portal. If a page misbehaves under Brave, click the Shields
    icon and set it to "Shields down" for that site, then reload.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from playwright.async_api import async_playwright

DEFAULT_URL = "https://www.houstonpermittingcenter.org/sold-permits-search"

BROWSER_PATHS: dict[str, list[str]] = {
    "brave": [
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(
            r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"
        ),
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/usr/bin/brave-browser",
    ],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
}

PROFILE_DIRS: dict[str, list[str]] = {
    "brave": [
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\User Data"),
        os.path.expanduser("~/Library/Application Support/BraveSoftware/Brave-Browser"),
        os.path.expanduser("~/.config/BraveSoftware/Brave-Browser"),
    ],
    "chrome": [
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data"),
        os.path.expanduser("~/Library/Application Support/Google/Chrome"),
        os.path.expanduser("~/.config/google-chrome"),
    ],
    "edge": [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data"),
    ],
}

STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-first-run",
    "--no-default-browser-check",
]

PROTECTION_SIGNS: dict[str, tuple[str, ...]] = {
    "Cloudflare": ("cf-browser-verification", "cf_chl", "checking your browser",
                   "__cf_bm", "cf-turnstile"),
    "Akamai": ("_abck", "bm_sz", "akamai"),
    "Imperva/Incapsula": ("incap_ses", "visid_incap", "_incapsula_"),
    "PerimeterX": ("perimeterx", "px-captcha"),
    "DataDome": ("datadome",),
    "reCAPTCHA": ("recaptcha", "g-recaptcha"),
    "hCaptcha": ("hcaptcha",),
}


def resolve_browser(name: str) -> str | None:
    for candidate in BROWSER_PATHS.get(name, []):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def resolve_profile(name: str) -> str | None:
    for candidate in PROFILE_DIRS.get(name, []):
        if candidate and Path(candidate).exists():
            return candidate
    return None


async def dump(page, label: str) -> dict:
    print(f"\n{'=' * 72}\n  {label}\n  {page.url}\n{'=' * 72}")

    try:
        print(f"\nTITLE: {await page.title()}")
    except Exception:
        print("\nTITLE: <unavailable>")

    try:
        html = (await page.content()).lower()
    except Exception as exc:
        print(f"could not read page content: {exc}")
        return {"protection": [], "html_size": 0, "controls": 0}

    print(f"HTML SIZE: {len(html):,} bytes")

    hits = [
        vendor for vendor, needles in PROTECTION_SIGNS.items()
        if any(n in html for n in needles)
    ]
    if hits:
        print(f"\n*** BOT PROTECTION DETECTED: {', '.join(hits)} ***")
        print("    Selector work is premature. See the verdict at the end.")

    try:
        links = await page.eval_on_selector_all(
            "a[href]",
            """els => els.map(e => ({
                text: (e.innerText || '').trim().slice(0, 60), href: e.href
            })).filter(l => l.text)""",
        )
    except Exception:
        links = []
    interesting = [
        l for l in links
        if any(k in (l["text"] + l["href"]).lower()
               for k in ("search", "permit", "sold", "aca", "accela", "portal", "citizen"))
    ]
    print(f"\n--- LINKS mentioning search/permit ({len(interesting)}) ---")
    for l in interesting[:25]:
        print(f"  {l['text'][:44]:46} -> {l['href'][:88]}")
    if not interesting:
        print("  (none -- likely already the search page)")

    try:
        controls = await page.eval_on_selector_all(
            "input, select, textarea, button",
            """els => els.map(e => ({
                tag: e.tagName.toLowerCase(), type: e.type || '',
                id: e.id || '', name: e.name || '',
                placeholder: e.placeholder || '',
                value: (e.value || '').slice(0, 30),
                text: (e.innerText || '').trim().slice(0, 40),
                visible: !!(e.offsetWidth || e.offsetHeight)
            }))""",
        )
    except Exception:
        controls = []
    visible = [c for c in controls if c["visible"]]
    print(f"\n--- FORM CONTROLS ({len(visible)} visible / {len(controls)} total) ---")
    for c in visible:
        ident = c["id"] or c["name"] or c["placeholder"] or c["text"] or "(anon)"
        print(f"  {c['tag']:8} {c['type']:10} {ident[:40]:42} "
              f"id={c['id'][:26]:28} name={c['name'][:22]}")

    dateish = [
        c for c in visible
        if any(k in (c["id"] + c["name"] + c["placeholder"] + c["text"]).lower()
               for k in ("date", "from", "to", "start", "end", "issued"))
    ]
    if dateish:
        print("\n--- LIKELY DATE FIELDS (paste into SELECTORS) ---")
        for c in dateish:
            if c["id"]:
                print(f'        "#{c["id"]}",')
            elif c["name"]:
                print(f"        \"{c['tag']}[name='{c['name']}']\",")

    submits = [
        c for c in visible
        if c["type"] == "submit" or "search" in (c["text"] + c["value"] + c["id"]).lower()
    ]
    if submits:
        print("\n--- LIKELY SUBMIT BUTTONS ---")
        for c in submits:
            if c["id"]:
                print(f'        "#{c["id"]}",')
            elif c["text"]:
                print(f"        \"{c['tag']}:has-text('{c['text']}')\",")

    try:
        tables = await page.eval_on_selector_all(
            "table",
            """els => els.map((e, i) => ({
                idx: i, id: e.id || '', cls: e.className || '', rows: e.rows.length,
                headers: Array.from(e.querySelectorAll('th'))
                    .map(th => (th.innerText||'').trim()).slice(0, 14)
            }))""",
        )
    except Exception:
        tables = []
    print(f"\n--- TABLES ({len(tables)}) ---")
    for t in tables:
        print(f"  [{t['idx']}] rows={t['rows']:4} id='{t['id'][:24]}' class='{t['cls'][:24]}'")
        if t["headers"]:
            print(f"       headers: {t['headers']}")

    hints = {
        "Accela Citizen Access (ASP.NET WebForms)": "ctl00_placeholdermain",
        "ASP.NET (__VIEWSTATE)": "__viewstate",
        "Tyler / EnerGov": "energov",
        "CityView": "cityview",
        "OpenGov / ViewPoint": "viewpointcloud",
        "Angular SPA": "ng-version",
        "React SPA": "__react",
    }
    print("\n--- FRAMEWORK FINGERPRINT ---")
    matched = [n for n, needle in hints.items() if needle in html]
    for n in matched:
        print(f"  MATCH: {n}")
    if not matched:
        print("  no known fingerprint")
    if len(html) < 5000:
        print("  WARNING: tiny page -- content probably loads via XHR.")

    # --- iframes ---------------------------------------------------------
    # A blank-looking results page is very often a page whose real content
    # lives in a frame. Report every frame and whether it holds a table.
    frames = page.frames
    if len(frames) > 1:
        print(f"\n--- IFRAMES ({len(frames) - 1} besides main) ---")
        for fr in frames:
            if fr == page.main_frame:
                continue
            try:
                n_tables = await fr.eval_on_selector_all("table", "els => els.length")
                n_inputs = await fr.eval_on_selector_all("input", "els => els.length")
                headers = await fr.eval_on_selector_all(
                    "th", "els => els.map(e => (e.innerText||'').trim()).slice(0,12)"
                )
            except Exception as exc:
                print(f"  frame url={fr.url[:80]}  <unreadable: {type(exc).__name__}>")
                continue
            print(f"  frame name='{fr.name[:24]}' url={fr.url[:76]}")
            print(f"        tables={n_tables} inputs={n_inputs}")
            if headers:
                print(f"        *** TABLE HEADERS FOUND IN FRAME: {headers}")
                print(f"        -> target it with page.frame_locator(...) in the adapter")

    return {"protection": hits, "html_size": len(html), "controls": len(visible)}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", default="chromium",
                    choices=["chromium", "brave", "chrome", "edge"])
    ap.add_argument("--profile", action="store_true",
                    help="use your real browser profile (close that browser first)")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("url_positional", nargs="?", default=None)
    args = ap.parse_args()
    url = args.url_positional or args.url

    exec_path = None
    if args.browser != "chromium":
        exec_path = resolve_browser(args.browser)
        if not exec_path:
            print(f"Could not find {args.browser}. Looked in:")
            for p in BROWSER_PATHS[args.browser]:
                print(f"   {p}")
            print("\nEdit BROWSER_PATHS with the real path, or use --browser chromium.")
            sys.exit(1)
        print(f"Using {args.browser}: {exec_path}")

    failed_requests: list[str] = []
    api_calls: list[str] = []
    console_errors: list[str] = []

    async with async_playwright() as pw:
        if args.profile:
            profile_dir = resolve_profile(args.browser)
            if not profile_dir:
                print(f"No profile directory found for {args.browser}.")
                sys.exit(1)
            print(f"Using REAL profile: {profile_dir}")
            print("If this fails, close ALL windows of that browser and retry.\n")
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                executable_path=exec_path,
                headless=False,
                args=STEALTH_ARGS,
                viewport={"width": 1500, "height": 950},
                accept_downloads=True,
            )
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await pw.chromium.launch(
                executable_path=exec_path, headless=False, args=STEALTH_ARGS
            )
            context = await browser.new_context(
                viewport={"width": 1500, "height": 950}, accept_downloads=True
            )
            page = await context.new_page()

        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )

        # --- track NEW TABS / POPUPS -------------------------------------
        # A search that "opens a blank page and stops" is usually results
        # landing in a popup this script was not watching.
        all_pages: list = [page]
        downloads: list[str] = []

        def on_new_page(p):
            all_pages.append(p)
            print(f"\n>>> NEW TAB/POPUP OPENED: {p.url or '(about:blank, still loading)'}")
            p.on("download", lambda d: downloads.append(d.suggested_filename))
        context.on("page", on_new_page)

        page.on("download", lambda d: downloads.append(d.suggested_filename))

        # --- log EVERY document/navigation response ----------------------
        # Reveals POST results, 302s, and file downloads masquerading as pages.
        doc_responses: list[str] = []

        def on_any_response(resp):
            rt = resp.request.resource_type
            ct = resp.headers.get("content-type", "")[:40]
            if rt in ("document", "xhr", "fetch"):
                doc_responses.append(
                    f"{resp.status} {resp.request.method:4} {rt:8} {ct:40} {resp.url[:100]}"
                )
            if "json" in ct and rt in ("xhr", "fetch"):
                api_calls.append(f"{resp.status} {resp.request.method} {resp.url[:140]}")

        page.on("response", on_any_response)
        context.on("page", lambda p: p.on("response", on_any_response))

        page.on("requestfailed", lambda r: failed_requests.append(
            f"{(r.failure or 'failed')}  {r.resource_type:10} {r.url[:110]}"))

        def on_console(m):
            if m.type in ("error", "warning"):
                console_errors.append(f"{m.type}: {m.text[:130]}")
        page.on("console", on_console)

        print(f"opening {url} ...")
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            print(f"HTTP status: {resp.status if resp else 'unknown'}")
        except Exception as exc:
            print(f"NAVIGATION FAILED: {type(exc).__name__}: {exc}")

        await asyncio.sleep(3)
        info = await dump(page, "INITIAL PAGE")

        if failed_requests:
            print(f"\n--- FAILED REQUESTS ({len(failed_requests)}) -- why it looks broken ---")
            for f in failed_requests[:20]:
                print(f"  {f}")
        if console_errors:
            print(f"\n--- CONSOLE ERRORS ({len(console_errors)}) ---")
            for c in console_errors[:15]:
                print(f"  {c}")

        # --- interactive loop -------------------------------------------
        print("\n" + "=" * 72)
        print("  Browser is open. Drive it by hand, then come back here.")
        print("     Enter  = dump EVERY open tab (do this after your search)")
        print("     r      = show network log (statuses, redirects, downloads)")
        print("     q      = quit")
        print("=" * 72)

        info2 = {}
        while True:
            choice = input("\n[Enter/r/q] > ").strip().lower()

            if choice == "q":
                break

            if choice == "r":
                print(f"\n--- DOCUMENT / XHR RESPONSES ({len(doc_responses)}) ---")
                for d in doc_responses[-40:]:
                    print(f"  {d}")
                if downloads:
                    print(f"\n--- DOWNLOADS TRIGGERED ({len(downloads)}) ---")
                    for d in downloads:
                        print(f"  {d}")
                    print("  A download means the portal exports a FILE.")
                    print("  Fetching that file directly beats scraping any table.")
                if failed_requests:
                    print(f"\n--- FAILED REQUESTS ({len(failed_requests)}) ---")
                    for f in failed_requests[-15:]:
                        print(f"  {f}")
                continue

            # default: dump every open tab
            live = [p for p in all_pages if not p.is_closed()]
            print(f"\n{len(live)} open tab(s)")
            for i, p in enumerate(live):
                try:
                    info2 = await dump(p, f"TAB {i + 1} of {len(live)}")
                except Exception as exc:
                    print(f"  could not dump tab {i + 1}: {type(exc).__name__}: {exc}")

            if api_calls:
                print("\n--- JSON ENDPOINTS SEEN (investigate these FIRST) ---")
                for c in api_calls[-20:]:
                    print(f"  {c}")

        print("\n" + "=" * 72)
        print("  VERDICT -- can a headless cloud actor do this?")
        print("=" * 72)
        protection = set(info.get("protection", [])) | set(info2.get("protection", []))
        if protection:
            print(f"  Bot protection present: {', '.join(sorted(protection))}")
            print("  A headless datacenter actor will likely be blocked.")
            print("  Options, best first:")
            print("    1. Call a JSON endpoint from above directly")
            print("    2. Look for a bulk export / records request instead")
            print("    3. Apify residential proxies + slower pacing (costs margin)")
            print("    4. Pick a different city -- not every source is worth it")
        elif args.profile:
            print("  You ran with your REAL profile, which proves little about the")
            print("  cloud. Re-run WITHOUT --profile before writing selectors.")
        elif args.browser != "chromium":
            print(f"  Worked in {args.browser} with no profile.")
            print("  Likely a user-agent or TLS check -- usually fixable in the actor.")
            print("  Re-run with --browser chromium to confirm the real environment.")
        else:
            print("  Loaded in bundled Chromium with no profile -- the same engine")
            print("  Apify runs. Green light: write your selectors.")

        print("\nclosing browser...")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
