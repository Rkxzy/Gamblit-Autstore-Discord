"""
gamblit.py — Gamblit.net tip automation via cookie-based session.

HOW GAMBLIT AUTH WORKS
──────────────────────
Gamblit.net sits behind Cloudflare and uses a browser-cookie session.
There is no public REST API with Bearer tokens.  After you log in through
the real browser the site sets several cookies that must be replayed on
every request:

  _iidt          – Cloudflare Bot Management device token
  _vid_t         – Cloudflare visitor-identity token
  cf_clearance   – Cloudflare challenge clearance (proves you passed JS challenge)
  intercom-device-id-<id>  – Intercom analytics device id
  intercom-session-<id>    – Intercom session token
  (plus any session/auth cookie the site itself sets)

Because cf_clearance can only be obtained by a real browser executing
Cloudflare's JS challenge, these cookies CANNOT be obtained
programmatically with plain aiohttp.  You must:

  1. Log in to gamblit.net in your browser once.
  2. Copy the cookies (use DevTools → Application → Cookies, or a
     browser extension like "EditThisCookie").
  3. Paste them into  config/gamblit.json  (see setup.py).

The bot then replays those cookies on every request.  cf_clearance
typically lasts several hours to a few days; when it expires you repeat
step 1-3.

TIP FLOW
────────
The UI tip flow (the little avatar in the bottom-right → Tip) calls:

  POST /api/tips          (or /api/user/tip — whichever returns 200)
  Content-Type: application/json
  Cookie: <all cookies above>

  Body: { "username": "<recipient>", "amount": <dl_integer> }

We mirror that exact call.  1 BGL = cfg.BGL_TO_DL DL (default 100).
"""

import logging
from http.cookiejar import CookieJar
from typing import Optional

import aiohttp

import cfg

log = logging.getLogger(__name__)

# ── Module-level session (reused across calls) ────────────────────────────────
_session: Optional[aiohttp.ClientSession] = None

# Known tip endpoints in priority order – we try each until one returns 2xx.
_TIP_ENDPOINTS = [
    "/api/tips",
    "/api/user/tip",
    "/api/tip",
]

# Known balance endpoints in priority order.
_BAL_ENDPOINTS = [
    "/api/user/me",
    "/api/user",
    "/api/profile",
    "/api/user/balance",
]


def _base() -> str:
    return cfg.GAMBLIT_BASE_URL.rstrip("/")


def _build_cookies() -> dict:
    """
    Pull every cookie from gamblit config and return a plain dict.

    Expected keys in gamblit.json:
        cookies: {
            "_iidt":                     "...",
            "_vid_t":                    "...",
            "cf_clearance":              "...",
            "intercom-device-id-xxxxx":  "...",
            "intercom-session-xxxxx":    "...",
            "<site session cookie>":     "..."
        }
    Any extra cookies present are forwarded as-is.
    """
    raw: dict = cfg.gamblit.get("cookies", {})
    if not raw:
        raise RuntimeError(
            "No Gamblit cookies configured.\n"
            "  1. Log in to gamblit.net in your browser.\n"
            "  2. Copy cookies from DevTools → Application → Cookies.\n"
            "  3. Paste them into config/gamblit.json under the 'cookies' key.\n"
            "  Required cookies: _iidt, _vid_t, cf_clearance, "
            "intercom-device-id-*, intercom-session-*"
        )
    return raw


def _build_headers() -> dict:
    """Headers that mimic a real browser making an XHR/fetch request."""
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept":          "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type":    "application/json",
        "Origin":          _base(),
        "Referer":         _base() + "/",
        "X-Requested-With": "XMLHttpRequest",
    }


async def _get_session() -> aiohttp.ClientSession:
    """Return (or create) the shared aiohttp session with Gamblit cookies pre-loaded."""
    global _session
    if _session is None or _session.closed:
        jar = aiohttp.CookieJar(unsafe=True)  # unsafe=True allows IP/localhost cookies too
        _session = aiohttp.ClientSession(
            cookie_jar=jar,
            headers=_build_headers(),
        )
        # Inject cookies into the jar for the Gamblit domain
        cookies = _build_cookies()
        _session.cookie_jar.update_cookies(
            cookies,
            response_url=aiohttp.client_reqrep.URL(_base()),
        )
        log.debug(f"Gamblit session created with {len(cookies)} cookies.")
    return _session


async def _refresh_session():
    """Force-close and recreate the session (e.g. after cookie update)."""
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None
    return await _get_session()


# ── Public API ────────────────────────────────────────────────────────────────

async def send_tip(username: str, amount_dl: float) -> dict:
    """
    Tip `username` with `amount_dl` DL on Gamblit.net.

    amount_dl should already be in DL units.  The bot.py layer converts
    BGL → DL using  amount_dl = bgl_qty * cfg.BGL_TO_DL  before calling here.

    Returns {"ok": bool, "message": str, "data": dict|None}
    """
    if not cfg.GAMBLIT_TIP_ENABLED:
        return {"ok": False, "message": "Tip sending is disabled in config."}

    if not cfg.gamblit.get("cookies"):
        return {
            "ok": False,
            "message": (
                "Gamblit cookies not configured. "
                "Log in via your browser, copy the cookies, "
                "and add them to config/gamblit.json under 'cookies'."
            ),
        }

    session = await _get_session()
    payload = {"username": username, "amount": int(amount_dl)}

    last_status, last_body = None, None

    for endpoint in _TIP_ENDPOINTS:
        url = f"{_base()}{endpoint}"
        try:
            async with session.post(url, json=payload) as r:
                last_status = r.status
                try:
                    last_body = await r.json(content_type=None)
                except Exception:
                    last_body = {"raw": await r.text()}

                if r.status in (200, 201):
                    log.info(f"Gamblit tip OK: {amount_dl} DL → {username} via {endpoint}")
                    return {"ok": True, "message": "Tip sent successfully.", "data": last_body}

                if r.status == 401:
                    log.warning(
                        "Gamblit returned 401 — cookies have expired. "
                        "Re-log in to gamblit.net and update config/gamblit.json."
                    )
                    return {
                        "ok": False,
                        "message": (
                            "Gamblit session expired (401). "
                            "Please re-log in at gamblit.net, copy your cookies, "
                            "and update the 'cookies' block in config/gamblit.json."
                        ),
                    }

                if r.status == 403:
                    log.warning(
                        f"Gamblit 403 on {endpoint} — likely Cloudflare block. "
                        "cf_clearance cookie may be stale."
                    )
                    # Don't try further endpoints; CF will block all of them.
                    return {
                        "ok": False,
                        "message": (
                            "Cloudflare blocked the request (403). "
                            "Your cf_clearance cookie is probably expired. "
                            "Re-log in at gamblit.net and update config/gamblit.json."
                        ),
                    }

                log.debug(f"Tip endpoint {endpoint} returned {r.status}, trying next…")

        except aiohttp.ClientError as e:
            log.warning(f"Network error on {endpoint}: {e}")
            continue

    # All endpoints failed
    msg = (
        f"All tip endpoints failed (last status={last_status}). "
        f"Response: {str(last_body)[:200]}"
    )
    log.error(f"Gamblit tip failed for {username}: {msg}")
    return {"ok": False, "message": msg, "data": last_body}


async def get_balance() -> dict:
    """
    Fetch the bot account's current DL balance.
    Returns {"ok": bool, "balance": float|None, "message": str}
    """
    if not cfg.gamblit.get("cookies"):
        return {"ok": False, "balance": None, "message": "Gamblit cookies not configured."}

    session = await _get_session()

    for endpoint in _BAL_ENDPOINTS:
        url = f"{_base()}{endpoint}"
        try:
            async with session.get(url) as r:
                if r.status == 401:
                    return {
                        "ok": False,
                        "balance": None,
                        "message": "Gamblit session expired (401). Update cookies in config/gamblit.json.",
                    }
                if r.status == 403:
                    return {
                        "ok": False,
                        "balance": None,
                        "message": "Cloudflare blocked request (403). Update cf_clearance cookie.",
                    }
                if r.status not in (200, 201):
                    continue

                data = await r.json(content_type=None)

                # Gamblit may nest the user object differently across versions
                candidates = [
                    data.get("balance"),
                    data.get("dl"),
                    data.get("amount"),
                    (data.get("data") or {}).get("balance"),
                    (data.get("data") or {}).get("dl"),
                    (data.get("user") or {}).get("balance"),
                    (data.get("user") or {}).get("dl"),
                ]
                bal = next((v for v in candidates if v is not None), None)
                if bal is not None:
                    try:
                        bal = float(bal)
                    except (TypeError, ValueError):
                        pass
                    log.debug(f"Gamblit balance fetched via {endpoint}: {bal}")
                    return {"ok": True, "balance": bal, "message": "OK"}

        except aiohttp.ClientError as e:
            log.warning(f"Network error fetching balance from {endpoint}: {e}")
            continue

    return {"ok": False, "balance": None, "message": "Could not fetch balance from any endpoint."}


async def close():
    """Cleanly close the aiohttp session (call on bot shutdown)."""
    global _session
    if _session and not _session.closed:
        await _session.close()
        _session = None


def reload_cookies():
    """
    Call this after updating config/gamblit.json at runtime so the new
    cookies take effect without restarting the bot.
    """
    import importlib
    importlib.reload(cfg)
    global _session
    if _session and not _session.closed:
        import asyncio
        asyncio.get_event_loop().run_until_complete(_refresh_session())
    log.info("Gamblit cookies reloaded from config.")
