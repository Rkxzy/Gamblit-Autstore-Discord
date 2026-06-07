"""
cfg.py — loads all JSON config files from ./config/
Import this everywhere instead of raw json.load calls.
"""
import json
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
_DIR  = os.path.join(_BASE, "config")


def _load(name: str) -> dict:
    path = os.path.join(_DIR, name)
    if not os.path.exists(path):
        print(f"[ERROR] Config file missing: {path}")
        print("        Run  python setup.py  first.")
        sys.exit(1)
    with open(path) as f:
        return json.load(f)


def _save(name: str, data: dict):
    os.makedirs(_DIR, exist_ok=True)
    with open(os.path.join(_DIR, name), "w") as f:
        json.dump(data, f, indent=2)


# ── Load all configs on import ────────────────────────────────
discord  = _load("discord.json")
paypal   = _load("paypal.json")
shop     = _load("shop.json")
pricing  = _load("pricing.json")
notes    = _load("notes.json")
misc     = _load("misc.json")
gamblit  = _load("gamblit.json")

# ── Convenience accessors ─────────────────────────────────────

# Discord
BOT_TOKEN              = discord["bot_token"]
SHOP_CHANNEL_ID        = discord["shop_channel_id"]
ADMIN_LOG_CHANNEL_ID   = discord["admin_log_channel_id"]
ADMIN_ROLE_ID          = discord["admin_role_id"]
VERIFIED_BUYER_ROLE_ID = discord.get("verified_buyer_role_id", 0)
SUPPORT_CHANNEL_ID     = discord.get("support_channel_id", 0)
PAYMENT_LOG_CHANNEL_ID = discord.get("payment_log_channel_id", 0)
MANUAL_DELIVERY_UID    = discord["manual_delivery_user_id"]

# PayPal
PP_CLIENT_ID      = paypal["client_id"]
PP_CLIENT_SECRET  = paypal["client_secret"]
PP_RECEIVER_EMAIL = paypal["receiver_email"]
PP_SANDBOX        = paypal.get("sandbox", False)
PP_POLL_INTERVAL  = paypal.get("poll_interval_seconds", 15)
DEPOSIT_EXPIRE    = paypal.get("deposit_expire_seconds", 600)

# Shop appearance
SHOP_NAME     = shop.get("name", "Shop")
SHOP_TITLE    = shop.get("title", "BGL Shop")
SHOP_DESC     = shop.get("description", "")
SHOP_BANNER   = shop.get("banner_url", "")
SHOP_FOOTER   = shop.get("footer_text", "")

def _hex(h: str) -> int:
    return int(h.lstrip("#"), 16)

COLOR_DEFAULT = _hex(shop.get("color_default", "5865F2"))
COLOR_SUCCESS = _hex(shop.get("color_success", "57F287"))
COLOR_ERROR   = _hex(shop.get("color_error",   "ED4245"))
COLOR_WARNING = _hex(shop.get("color_warning",  "FEE75C"))

# Pricing
CURRENCY        = pricing.get("currency", "EUR")
CURRENCY_SYMBOL = pricing.get("currency_symbol", "€")
ITEMS: list[dict] = pricing.get("items", [])

def get_item(key: str) -> dict | None:
    for item in ITEMS:
        if item["key"] == key:
            return item
    return None

# Notes
NOTE_PREFIX = notes.get("prefix", "FLOW")
NOTE_LENGTH = notes.get("length", 6)

# Misc
DB_FILE    = os.path.join(_BASE, misc.get("db_file",  "store.db"))
LOG_FILE   = os.path.join(_BASE, misc.get("log_file", "store.log"))
LOG_LEVEL  = misc.get("log_level", "INFO")

# Gamblit
# Auth is cookie-based (Cloudflare-protected site).
# Cookies are stored in gamblit["cookies"] as a plain dict.
GAMBLIT_BASE_URL    = gamblit.get("base_url", "https://gamblit.net")
GAMBLIT_TIP_ENABLED = gamblit.get("tip_enabled", True)
# DL per BGL conversion  (1 BGL = 100 DL by default)
BGL_TO_DL           = gamblit.get("bgl_to_dl", 100)

# Points system: 1 EUR spent = POINTS_PER_EUR points; POINTS_FOR_FREE_EUR points → 1 EUR free
POINTS_PER_EUR    = misc.get("points_per_eur", 1)       # 1€ = 1 point
POINTS_FOR_FREE   = misc.get("points_for_free", 30)     # 30 points = 1€ free


# ── Live-update helpers (used by /banner, /title, etc.) ───────

def set_shop_field(key: str, value: str):
    shop[key] = value
    _save("shop.json", shop)
    # Refresh module-level variables
    global SHOP_NAME, SHOP_TITLE, SHOP_DESC, SHOP_BANNER, SHOP_FOOTER
    global COLOR_DEFAULT, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING
    SHOP_NAME   = shop.get("name", "Shop")
    SHOP_TITLE  = shop.get("title", "BGL Shop")
    SHOP_DESC   = shop.get("description", "")
    SHOP_BANNER = shop.get("banner_url", "")
    SHOP_FOOTER = shop.get("footer_text", "")
    COLOR_DEFAULT = _hex(shop.get("color_default", "5865F2"))
    COLOR_SUCCESS = _hex(shop.get("color_success", "57F287"))
    COLOR_ERROR   = _hex(shop.get("color_error",   "ED4245"))
    COLOR_WARNING = _hex(shop.get("color_warning",  "FEE75C"))


def update_item_price(key: str, buy: float | None, sell: float | None):
    for item in pricing["items"]:
        if item["key"] == key:
            if buy  is not None: item["buy_price"]  = buy
            if sell is not None: item["sell_price"] = sell
    _save("pricing.json", pricing)
    # Refresh ITEMS
    global ITEMS
    ITEMS = pricing["items"]


def add_pricing_item(item: dict):
    pricing["items"].append(item)
    _save("pricing.json", pricing)
    global ITEMS
    ITEMS = pricing["items"]
