"""
database.py — SQLite persistence
"""
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Optional

import cfg

log = logging.getLogger(__name__)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(cfg.DB_FILE)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS deposits (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id       TEXT    NOT NULL,
            note             TEXT    NOT NULL UNIQUE,
            item_key         TEXT    NOT NULL,
            amount_eur       REAL    NOT NULL,
            quantity         REAL    NOT NULL,
            status           TEXT    NOT NULL DEFAULT 'pending',
            created_at       TEXT    NOT NULL,
            confirmed_at     TEXT,
            paypal_txn_id    TEXT,
            gamblit_username TEXT
        );

        CREATE TABLE IF NOT EXISTS tip_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id       TEXT    NOT NULL,
            gamblit_username TEXT    NOT NULL,
            amount_bgl       REAL    NOT NULL,
            amount_dl        REAL    NOT NULL,
            note             TEXT    NOT NULL,
            status           TEXT    NOT NULL DEFAULT 'pending',
            error            TEXT,
            tipped_at        TEXT
        );

        CREATE TABLE IF NOT EXISTS orders (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            discord_id    TEXT NOT NULL,
            deposit_note  TEXT NOT NULL,
            item_key      TEXT NOT NULL,
            quantity      REAL NOT NULL,
            delivery_data TEXT,
            status        TEXT NOT NULL DEFAULT 'pending',
            created_at    TEXT NOT NULL,
            delivered_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS stock (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            item_key   TEXT    NOT NULL,
            value      TEXT    NOT NULL,
            used       INTEGER NOT NULL DEFAULT 0,
            added_at   TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS balances (
            discord_id  TEXT PRIMARY KEY,
            balance_eur REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS seen_txns (
            txn_id TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS points (
            discord_id  TEXT PRIMARY KEY,
            points      REAL NOT NULL DEFAULT 0,
            total_earned REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS gamblit_state (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)
    log.info("DB ready.")


# ─── Deposits ────────────────────────────────────────────────

def create_deposit(discord_id: str, note: str, item_key: str,
                   amount_eur: float, quantity: float,
                   gamblit_username: str = ""):
    now = _now()
    with _conn() as c:
        c.execute(
            "INSERT INTO deposits "
            "(discord_id,note,item_key,amount_eur,quantity,status,created_at,gamblit_username) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (discord_id, note, item_key, amount_eur, quantity, "pending", now, gamblit_username)
        )


def get_deposit(note: str) -> Optional[sqlite3.Row]:
    with _conn() as c:
        return c.execute("SELECT * FROM deposits WHERE note=?", (note,)).fetchone()


def confirm_deposit(note: str, txn_id: str):
    with _conn() as c:
        c.execute(
            "UPDATE deposits SET status='confirmed', confirmed_at=?, paypal_txn_id=? "
            "WHERE note=?",
            (_now(), txn_id, note)
        )


def expire_old_deposits():
    with _conn() as c:
        c.execute(
            "UPDATE deposits SET status='expired' WHERE status='pending' "
            "AND (strftime('%s','now') - strftime('%s', created_at)) > ?",
            (cfg.DEPOSIT_EXPIRE,)
        )


def get_pending_deposits() -> list:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM deposits WHERE status='pending'"
        ).fetchall()


# ─── Orders ──────────────────────────────────────────────────

def create_order(discord_id: str, deposit_note: str,
                 item_key: str, quantity: float) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO orders (discord_id,deposit_note,item_key,quantity,status,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (discord_id, deposit_note, item_key, quantity, "pending", _now())
        )
        return cur.lastrowid


def complete_order(order_id: int, delivery_data: str):
    with _conn() as c:
        c.execute(
            "UPDATE orders SET status='delivered', delivery_data=?, delivered_at=? WHERE id=?",
            (delivery_data, _now(), order_id)
        )


def get_orders(discord_id: str, limit=5) -> list:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM orders WHERE discord_id=? ORDER BY id DESC LIMIT ?",
            (discord_id, limit)
        ).fetchall()


# ─── Stock ───────────────────────────────────────────────────

def add_stock(item_key: str, values: list[str]):
    now = _now()
    with _conn() as c:
        c.executemany(
            "INSERT INTO stock (item_key, value, used, added_at) VALUES (?,?,0,?)",
            [(item_key, v, now) for v in values]
        )


def pop_stock(item_key: str) -> Optional[str]:
    with _conn() as c:
        row = c.execute(
            "SELECT id, value FROM stock WHERE item_key=? AND used=0 LIMIT 1",
            (item_key,)
        ).fetchone()
        if not row:
            return None
        c.execute("UPDATE stock SET used=1 WHERE id=?", (row["id"],))
        return row["value"]


def stock_count(item_key: str) -> int:
    with _conn() as c:
        return c.execute(
            "SELECT COUNT(*) FROM stock WHERE item_key=? AND used=0",
            (item_key,)
        ).fetchone()[0]


# ─── Balances ────────────────────────────────────────────────

def get_balance(discord_id: str) -> float:
    with _conn() as c:
        row = c.execute(
            "SELECT balance_eur FROM balances WHERE discord_id=?", (discord_id,)
        ).fetchone()
        return row["balance_eur"] if row else 0.0


def add_balance(discord_id: str, amount: float):
    with _conn() as c:
        c.execute(
            "INSERT INTO balances (discord_id, balance_eur) VALUES (?,?) "
            "ON CONFLICT(discord_id) DO UPDATE SET balance_eur = balance_eur + ?",
            (discord_id, amount, amount)
        )


def set_balance(discord_id: str, amount: float):
    with _conn() as c:
        c.execute(
            "INSERT INTO balances (discord_id, balance_eur) VALUES (?,?) "
            "ON CONFLICT(discord_id) DO UPDATE SET balance_eur = ?",
            (discord_id, amount, amount)
        )


def deduct_balance(discord_id: str, amount: float) -> bool:
    """Returns False if insufficient balance."""
    bal = get_balance(discord_id)
    if bal < amount - 0.001:
        return False
    set_balance(discord_id, round(bal - amount, 4))
    return True


# ─── Seen txns ────────────────────────────────────────────────

def is_txn_seen(txn_id: str) -> bool:
    with _conn() as c:
        return c.execute(
            "SELECT 1 FROM seen_txns WHERE txn_id=?", (txn_id,)
        ).fetchone() is not None


def mark_txn_seen(txn_id: str):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO seen_txns (txn_id) VALUES (?)", (txn_id,))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Tip Log ─────────────────────────────────────────────────

def log_tip(discord_id: str, gamblit_username: str, amount_bgl: float,
            amount_dl: float, note: str, status: str, error: str = "") -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO tip_log "
            "(discord_id,gamblit_username,amount_bgl,amount_dl,note,status,error,tipped_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (discord_id, gamblit_username, amount_bgl, amount_dl,
             note, status, error, _now())
        )
        return cur.lastrowid


def get_tip_log(limit: int = 20) -> list:
    with _conn() as c:
        return c.execute(
            "SELECT * FROM tip_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


# ─── Points ──────────────────────────────────────────────────

def get_points(discord_id: str) -> tuple[float, float]:
    """Returns (current_points, total_earned_points)."""
    with _conn() as c:
        row = c.execute(
            "SELECT points, total_earned FROM points WHERE discord_id=?", (discord_id,)
        ).fetchone()
        return (row["points"], row["total_earned"]) if row else (0.0, 0.0)


def add_points(discord_id: str, amount: float):
    """Add points and update total earned."""
    with _conn() as c:
        c.execute(
            "INSERT INTO points (discord_id, points, total_earned) VALUES (?,?,?) "
            "ON CONFLICT(discord_id) DO UPDATE SET "
            "points = points + ?, total_earned = total_earned + ?",
            (discord_id, amount, amount, amount, amount)
        )


def deduct_points(discord_id: str, amount: float) -> bool:
    """Deduct points. Returns False if insufficient."""
    pts, _ = get_points(discord_id)
    if pts < amount - 0.001:
        return False
    with _conn() as c:
        c.execute(
            "INSERT INTO points (discord_id, points, total_earned) VALUES (?,0,0) "
            "ON CONFLICT(discord_id) DO UPDATE SET points = points - ?",
            (discord_id, amount)
        )
    return True


# ─── Gamblit state ───────────────────────────────────────────

def get_gamblit_balance():
    """Returns last known Gamblit balance, or None."""
    with _conn() as c:
        row = c.execute(
            "SELECT value FROM gamblit_state WHERE key='balance'"
        ).fetchone()
        if row:
            try:
                return float(row["value"])
            except (ValueError, TypeError):
                return row["value"]
        return None


def set_gamblit_balance(value):
    with _conn() as c:
        c.execute(
            "INSERT INTO gamblit_state (key, value) VALUES ('balance', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=?",
            (str(value), str(value))
        )
