"""Guard the seam between the pages and their shared JavaScript.

The console is a plain multi-page app with no build step, so nothing checks that
`pages/*.html` and `static/console.js` still agree. Renaming a shared helper
passes every other test and breaks the page at runtime, in front of the operator.
These tests make that a red suite instead.

They are deliberately textual: parsing JS properly would need a real parser, and
the failure mode worth catching (a helper that no longer exists) is visible in
the source text.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CONSOLE = Path(__file__).resolve().parents[1] / "src" / "wildfire" / "console"
PAGES = CONSOLE / "pages"
SHARED_JS = CONSOLE / "static" / "console.js"

# Names the browser or Leaflet provides. Anything here is not our contract.
_PROVIDED = {
    # language
    "Array", "Boolean", "Date", "Error", "Function", "Infinity", "JSON", "Map",
    "Math", "NaN", "Number", "Object", "Promise", "Proxy", "RegExp", "Set",
    "String", "Symbol", "WeakMap", "decodeURI", "decodeURIComponent",
    "encodeURI", "encodeURIComponent", "isFinite", "isNaN", "parseFloat",
    "parseInt", "structuredClone",
    # DOM / BOM
    "Blob", "CustomEvent", "Event", "File", "FileReader", "FormData",
    "Headers", "Image", "IntersectionObserver", "KeyboardEvent", "MouseEvent",
    "MutationObserver", "Request", "Response", "ResizeObserver", "URL",
    "URLSearchParams", "WebSocket", "XMLHttpRequest", "alert", "atob", "btoa",
    "cancelAnimationFrame", "clearInterval", "clearTimeout", "close", "confirm",
    "console", "document", "fetch", "getComputedStyle", "history", "localStorage",
    "location", "matchMedia", "navigator", "open", "performance", "prompt",
    "queueMicrotask", "requestAnimationFrame", "screen", "scrollTo",
    "sessionStorage", "setInterval", "setTimeout", "window",
    # Leaflet's single global
    "L",
}

# Statement keywords that the "identifier followed by (" regex would otherwise
# mistake for function calls.
_KEYWORDS = {
    "async", "await", "case", "catch", "class", "const", "delete", "do", "else",
    "for", "function", "if", "in", "instanceof", "let", "new", "of", "return",
    "super", "switch", "this", "throw", "typeof", "var", "void", "while", "with",
    "yield",
}

_CALL = re.compile(r"(?<![.\w$'\"])([A-Za-z_$][\w$]*)\s*\(")
_DEF_FUNC = re.compile(r"\bfunction\s+([A-Za-z_$][\w$]*)")
_DEF_BIND = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)")
_DEF_CLASS = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")
_PARAMS = re.compile(r"(?:function\s*[\w$]*\s*\(([^)]*)\)|\(([^)]*)\)\s*=>|([\w$]+)\s*=>)")
_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S | re.I)


def _strip_comments(js: str) -> str:
    js = re.sub(r"/\*.*?\*/", " ", js, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", " ", js)


def _blank_strings(js: str) -> str:
    """Blank string literals, keeping `${...}` interpolations.

    The pages build HTML in template literals full of inline CSS, so a raw scan
    reads `calc(`, `minmax(` and `translate(` as calls to undefined functions.
    The interpolations are the opposite - they hold the real helper calls - so
    those are kept and recursed into for nested templates.

    Only used for finding calls, never for finding definitions: an unbalanced
    quote inside a regex literal can only blank out real code here, which makes
    the guard miss something rather than fail on something valid.
    """
    out: list[str] = []
    i, n = 0, len(js)
    while i < n:
        c = js[i]
        if c in "'\"":
            i += 1
            while i < n and js[i] != c:
                i += 2 if js[i] == "\\" else 1
            i += 1
            out.append(" ")
        elif c == "`":
            i += 1
            while i < n and js[i] != "`":
                if js[i] == "\\":
                    i += 2
                elif js[i] == "$" and i + 1 < n and js[i + 1] == "{":
                    i += 2
                    start, depth = i, 1
                    while i < n and depth:
                        if js[i] == "{":
                            depth += 1
                        elif js[i] == "}":
                            depth -= 1
                        i += 1
                    out.append(" " + _blank_strings(js[start:i - 1]) + " ")
                else:
                    i += 1
            i += 1
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _called_in(js: str) -> set[str]:
    """Names invoked as plain functions, ignoring method calls and keywords."""
    code = _blank_strings(_strip_comments(js))
    return {n for n in _CALL.findall(code) if n not in _KEYWORDS}


def _defined_in(js: str) -> set[str]:
    """Every name bound in this source: declarations plus function parameters."""
    js = _strip_comments(js)
    names = set(_DEF_FUNC.findall(js)) | set(_DEF_BIND.findall(js)) | set(_DEF_CLASS.findall(js))
    for groups in _PARAMS.findall(js):
        for group in groups:
            for part in group.split(","):
                # Strip defaults, destructuring braces, rest dots, and any
                # bracket left over from a nested call such as
                # `new Promise((resolve, reject) => ...)`.
                part = part.split("=")[0].strip(" \t\n()[]{}").lstrip(".").strip()
                if re.fullmatch(r"[A-Za-z_$][\w$]*", part):
                    names.add(part)
    return names


def _inline_js(html: str) -> str:
    """Only the page's own script blocks - <script src=...> bodies are empty."""
    return "\n".join(_SCRIPT.findall(html))


def _pages() -> list[Path]:
    return sorted(PAGES.glob("*.html"))


def test_pages_exist():
    names = {p.name for p in _pages()}
    assert {"dashboard.html", "scans.html", "detail.html",
            "review.html", "map.html", "reports.html", "settings.html"} <= names
    assert SHARED_JS.exists()


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_page_only_calls_helpers_that_exist(page: Path):
    """Every function a page calls is defined - by itself, by console.js, or by
    the browser. This is what catches a renamed or deleted shared helper."""
    html = page.read_text(encoding="utf-8")
    shared = _defined_in(SHARED_JS.read_text(encoding="utf-8"))
    local = _defined_in(_inline_js(html))
    missing = sorted(_called_in(_inline_js(html)) - local - shared - _PROVIDED)
    assert not missing, (
        f"{page.name} calls {missing}, which nothing defines. "
        f"If a helper in console.js was renamed, update the page too."
    )


@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.name)
def test_page_loads_the_shared_script_when_it_uses_it(page: Path):
    """A page that relies on console.js must actually pull it in, and before its
    own inline script - the helpers are plain globals, not modules."""
    html = page.read_text(encoding="utf-8")
    shared = _defined_in(SHARED_JS.read_text(encoding="utf-8"))
    used = _called_in(_inline_js(html)) & shared
    if not used:
        pytest.skip("page does not use the shared helpers")
    tag = html.find("/static/console.js")
    assert tag != -1, f"{page.name} uses {sorted(used)} but never loads console.js"
    first_inline = next((m.start() for m in _SCRIPT.finditer(html)
                         if m.group(1).strip()), len(html))
    assert tag < first_inline, f"{page.name} loads console.js after its own script"


def test_severity_colours_are_not_duplicated_as_hex():
    """SEV in console.js must reference the CSS tokens, not copy them.

    It used to hold hex literals of the :root values. When those were darkened
    for contrast the copies were missed, so the severity mix bar drew #E05555
    while the badge beside it drew #D82929 - two different reds on one row,
    within a single axis. Referencing var() makes that drift impossible.
    """
    src = SHARED_JS.read_text(encoding="utf-8")
    m = re.search(r"const SEV = \{(.*?)\n\};", src, re.S)
    assert m, "SEV block not found in console.js"
    block = m.group(1)
    assert not re.search(r"#[0-9a-fA-F]{3,6}", block), (
        "SEV carries a hex literal again; use var(--red) / var(--amber) / "
        "var(--green-2) so it cannot drift from :root")
    for token in ("var(--red)", "var(--amber)", "var(--green-2)"):
        assert token in block, f"SEV should reference {token}"


def test_hazard_colour_helpers_are_still_wired_up():
    """The hazard-type helpers are what this file mainly exists to protect.

    Colours and icons live in ONE place (KIND in console.js) precisely because
    they had already drifted - detail.html was drawing boxes in a different shade
    than the map. If a page stops routing through these helpers and hardcodes a
    hex value again, that drift is back and no other test would notice.
    """
    shared_src = SHARED_JS.read_text(encoding="utf-8")
    pages_js = "\n".join(_inline_js(p.read_text(encoding="utf-8")) for p in _pages())
    everything = shared_src + "\n" + pages_js
    for helper in ("kindIcon", "kindBadge", "kindFilterHtml", "kindKey",
                   "fallbackPinHtml", "siteDivIcon", "initLeafletSites"):
        assert helper in _defined_in(shared_src), f"{helper} is gone from console.js"
        assert len(re.findall(rf"\b{helper}\s*\(", everything)) > 1, \
            f"{helper} is defined but never called - dead helper?"

    # The retired hazard-TYPE palette must not creep back into a page.
    retired = {"#FFD700": "old dead-tree yellow",
               "#FB8C00": "detail.html's drifted smoke orange",
               "#E53935": "detail.html's drifted flame red",
               "#F0A500": "pre-contrast amber, now --amber",
               "#E05555": "pre-contrast severity red, now --red"}
    for page in _pages():
        # Comments are exempt: the code comments explaining WHY these values were
        # retired necessarily quote them.
        text = _strip_comments(re.sub(r"<!--.*?-->", " ", page.read_text(encoding="utf-8"),
                                      flags=re.S))
        for hexval, what in retired.items():
            assert hexval not in text, (
                f"{page.name} reintroduces {hexval} ({what}); these colours "
                f"belong in KIND in console.js and :root in console.css, not in a page"
            )


def test_pin_filter_is_wired_up():
    """The map legends double as the hazard-type pin filter (a project-lead
    request). Guard its moving parts: the shared chip builder must keep emitting
    real accessible toggles, both map pages must keep routing through it instead
    of hand-rolling chips that would drift from KIND, and both must share the
    persisted choice rather than growing per-page keys.
    """
    shared_src = SHARED_JS.read_text(encoding="utf-8")
    block = re.search(r"function kindFilterHtml\b.*?\n\}", shared_src, re.S)
    assert block, "kindFilterHtml is gone from console.js"
    for needed in ("<button", "data-kind", "aria-pressed"):
        assert needed in block.group(0), (
            f"kindFilterHtml lost {needed!r}; the filter chips must stay real "
            f"buttons with toggle semantics, not styled spans"
        )
    for page in ("dashboard.html", "map.html"):
        page_js = _inline_js((PAGES / page).read_text(encoding="utf-8"))
        assert re.search(r"\bkindFilterHtml\s*\(", page_js), \
            f"{page} no longer renders the kind filter chips"
        assert re.search(r"\bloadKindFilter\s*\(", page_js), \
            f"{page} no longer loads the shared filter state (one choice, both maps)"
        assert "localStorage" not in page_js, (
            f"{page} touches localStorage directly; go through "
            f"loadKindFilter/saveKindFilter so the two maps cannot drift apart"
        )


# --------------------------------------------------------- cache busting
# Gotcha 11: a browser that kept the old HTML and refetched console.js (or the
# reverse) runs a mismatched pair, and the symptom is an "X is not defined" for
# a helper that plainly exists. The server stamps every /static/ link with a
# version that moves when the file does, so the browser cannot mix generations.


def _client():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import tempfile

    from fastapi.testclient import TestClient

    from src.wildfire.config import Settings
    from src.wildfire.console.server import create_app

    tmp = tempfile.mkdtemp()
    return TestClient(create_app(Settings(output_dir=tmp, models_dir=tmp,
                                          model_sources=[])))


ROUTES = ["/", "/scans", "/review", "/map", "/reports", "/settings",
          "/scans/console_20260101_000000"]


@pytest.mark.parametrize("route", ROUTES)
def test_served_pages_stamp_every_static_link(route: str):
    body = _client().get(route).text
    bare = re.findall(r'(?:href|src)="(/static/[^"]*)"', body)
    assert bare, f"{route} links no /static asset at all"
    unstamped = [u for u in bare if "?v=" not in u]
    assert not unstamped, f"{route} serves unstamped assets: {unstamped}"


def test_pages_are_served_no_cache():
    """The document naming the versions must never itself be stale."""
    r = _client().get("/")
    assert r.headers.get("cache-control") == "no-cache"
    assert r.headers["content-type"].startswith("text/html")


def test_a_stamped_asset_still_resolves():
    client = _client()
    url = re.search(r'(/static/console\.js\?v=[a-f0-9]+)', client.get("/").text)
    assert url, "console.js is not stamped"
    assert client.get(url.group(1)).status_code == 200


def test_the_stamp_moves_when_the_asset_changes(tmp_path, monkeypatch):
    from src.wildfire.console import server

    def stamp() -> str:
        return re.search(r'console\.js\?v=([a-f0-9]+)', _client().get("/").text).group(1)

    before = stamp()
    original = SHARED_JS.read_bytes()
    try:
        SHARED_JS.write_bytes(original + b"\n// touched by the test\n")
        assert stamp() != before, "editing console.js left the stamp unchanged"
    finally:
        SHARED_JS.write_bytes(original)


# ------------------------------------------------------- unsaved review work
# Review is the longest task in the app, and it used to be one keystroke from
# loss: the d/s/r/m/p/e page shortcuts called location.href directly, and no
# page asked before leaving. These are textual, like the rest of this file.

DETAIL_HTML = PAGES / "detail.html"


def test_page_shortcuts_go_through_the_leave_guard():
    js = SHARED_JS.read_text(encoding="utf-8")
    nav = js[js.index("const NAV_KEYS"):]
    nav = nav[:nav.index("});") + 3]
    assert "wfNavigate(href)" in nav and "location.href" not in nav, \
        "a page shortcut must not leave without asking"
    assert "WF_SUPPRESS_NAV_KEYS" in nav, "review must be able to switch the letters off"
    assert "function wfNavigate" in js and "beforeunload" in js and "function wfConfirm" in js


def test_the_review_page_guards_its_work():
    html = DETAIL_HTML.read_text(encoding="utf-8")
    assert "window.WF_LEAVE_GUARD" in html, "leaving with unsaved boxes must ask"
    assert "window.WF_SUPPRESS_NAV_KEYS = on" in html, "letters are off while reviewing"
    assert "localStorage.setItem(DRAFT_KEY" in html, "work survives a closed window"
    for fn in ("remember()", "changed()"):
        assert html.count(fn) >= 3, f"every box edit should go through {fn}"


def test_number_keys_match_the_label_dropdown_order():
    """Pressing 2 must give the label second in the list, or a reviewer working
    from the dropdown learns the wrong key."""
    html = DETAIL_HTML.read_text(encoding="utf-8")
    keys = re.search(r"const LABEL_KEYS = \[([^\]]+)\]", html).group(1)
    keys = [k.strip().strip('"') for k in keys.split(",")]
    select = re.search(r'<select id="label-pick"[^>]*>(.*?)</select>', html, re.S).group(1)
    options = re.findall(r"<option>([^<]+)</option>", select)
    assert keys == options


def test_wheel_zoom_measures_where_the_frame_landed():
    """#stage is centred in a flex box, so widening it also moves its own edge.
    Predicting the pan from the old edge drifted every wheel step (about 530 px
    of a 5280 px frame per step); the handler must measure after resizing."""
    html = DETAIL_HTML.read_text(encoding="utf-8")
    wheel = html[html.index('previewEl.addEventListener("wheel"'):]
    wheel = wheel[:wheel.index("{ passive: false }")]
    after = wheel[wheel.index("zoom = nz"):]
    assert after.index("applyZoom()") < after.index("getBoundingClientRect()"), \
        "the wheel handler must measure the stage after applying the new zoom"
