"""Patch bot/webapp/dist/index.html so the React app boots OUTSIDE Telegram.

1. Removes the `<script src="https://telegram.org/js/telegram-web-app.js">`
   CDN tag. Outside a real WebView it sets initData="" and races with our
   stub, causing the page to hang on "Инициализирую Mini App…" indefinitely.
2. Injects a `<script>` stub right before the React module bundle that
   defines window.Telegram.WebApp with the minted initData from
   /tmp/init_data.txt and a minimal API surface (ready/expand/MainButton/...).

Usage:
    python3 patch_dist.py

Run AFTER `vite build`. The dist file is restored by the test recipe via
`git checkout -- bot/webapp/dist/index.html`.
"""
import json
import pathlib
import urllib.parse

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
DIST_HTML = REPO_ROOT / "bot" / "webapp" / "dist" / "index.html"
INIT_DATA_PATH = pathlib.Path("/tmp/init_data.txt")

CDN_SCRIPT = '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
MODULE_MARKER = '<script type="module"'


def build_stub_script(init_data: str) -> str:
    parts = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    user = json.loads(parts.get("user", "{}"))
    return (
        "<script>"
        "(function(){"
        "var initData = " + json.dumps(init_data) + ";"
        "var user = " + json.dumps(user) + ";"
        "var existing = (window.Telegram && window.Telegram.WebApp) || {};"
        "window.Telegram = window.Telegram || {};"
        "window.Telegram.WebApp = Object.assign(existing, {"
        "initData: initData,"
        "initDataUnsafe: { user: user, auth_date: Number(\""
        + parts.get("auth_date", "0")
        + "\"), query_id: "
        + json.dumps(parts.get("query_id", ""))
        + ", hash: "
        + json.dumps(parts.get("hash", ""))
        + " },"
        "version: existing.version || \"7.0\","
        "platform: \"web\","
        "colorScheme: \"dark\","
        "themeParams: {},"
        "isExpanded: true,"
        "viewportHeight: window.innerHeight,"
        "ready: function(){},"
        "expand: function(){},"
        "close: function(){},"
        "onEvent: function(){},"
        "offEvent: function(){},"
        "MainButton: { show: function(){}, hide: function(){}, setText: function(){}, onClick: function(){} },"
        "BackButton: { show: function(){}, hide: function(){}, onClick: function(){} },"
        "HapticFeedback: { impactOccurred: function(){}, notificationOccurred: function(){} }"
        "});"
        "})();"
        "</script>"
    )


def main() -> None:
    init_data = INIT_DATA_PATH.read_text().strip()
    if not init_data:
        raise SystemExit(f"{INIT_DATA_PATH} is empty — run mint_initdata.py first")

    src = DIST_HTML.read_text()
    # Step 1: drop the CDN script tag (it races with our stub).
    if CDN_SCRIPT in src:
        src = src.replace(
            CDN_SCRIPT,
            "<!-- CDN telegram-web-app.js removed for offline test rig; stub is below -->",
            1,
        )

    # Step 2: inject the stub right before the React module bundle.
    if MODULE_MARKER not in src:
        raise SystemExit("React module marker not found in dist/index.html")
    stub = build_stub_script(init_data)
    patched = src.replace(MODULE_MARKER, stub + "\n    " + MODULE_MARKER, 1)
    DIST_HTML.write_text(patched)
    print(
        f"patched {DIST_HTML.relative_to(REPO_ROOT)}: +{len(patched) - len(src)} bytes"
    )


if __name__ == "__main__":
    main()
