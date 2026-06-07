"""
paypal.py — PayPal REST API polling
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

import aiohttp

import cfg

log = logging.getLogger(__name__)

_token: Optional[str] = None
_token_exp: datetime  = datetime.min.replace(tzinfo=timezone.utc)

BASE = (
    "https://api-m.sandbox.paypal.com"
    if cfg.PP_SANDBOX
    else "https://api-m.paypal.com"
)


async def _get_token(session: aiohttp.ClientSession) -> str:
    global _token, _token_exp
    now = datetime.now(timezone.utc)
    if _token and now < _token_exp:
        return _token
    async with session.post(
        f"{BASE}/v1/oauth2/token",
        auth=aiohttp.BasicAuth(cfg.PP_CLIENT_ID, cfg.PP_CLIENT_SECRET),
        data={"grant_type": "client_credentials"},
    ) as r:
        if r.status != 200:
            raise RuntimeError(f"PayPal auth failed {r.status}: {await r.text()}")
        d = await r.json()
        _token     = d["access_token"]
        _token_exp = now + timedelta(seconds=d.get("expires_in", 3600) - 60)
    return _token


async def _fetch_transactions(session: aiohttp.ClientSession) -> list[dict]:
    token = await _get_token(session)
    end   = datetime.now(timezone.utc)
    start = end - timedelta(hours=24)
    params = {
        "start_date":          start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_date":            end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "transaction_status":  "S",
        "fields":              "all",
        "page_size":           100,
    }
    headers = {"Authorization": f"Bearer {token}"}
    txns, page = [], 1
    while True:
        params["page"] = page
        async with session.get(
            f"{BASE}/v1/reporting/transactions",
            headers=headers, params=params
        ) as r:
            if r.status != 200:
                log.warning(f"PayPal txn fetch {r.status}: {await r.text()}")
                break
            d = await r.json()
            txns.extend(d.get("transaction_details", []))
            if page >= d.get("total_pages", 1):
                break
            page += 1
    return txns


def _note(txn: dict) -> str:
    info = txn.get("transaction_info", {})
    return (info.get("transaction_note") or info.get("custom_field") or "").strip().upper()


def _amount(txn: dict) -> float:
    try:
        return float(txn["transaction_info"]["transaction_amount"]["value"])
    except (KeyError, TypeError, ValueError):
        return 0.0


async def poll_loop(on_confirmed: Callable):
    log.info(f"PayPal poller started — sandbox={cfg.PP_SANDBOX} interval={cfg.PP_POLL_INTERVAL}s")
    import database as db
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                db.expire_old_deposits()
                pending = db.get_pending_deposits()
                if pending:
                    by_note = {r["note"].upper(): r for r in pending}
                    for txn in await _fetch_transactions(session):
                        info   = txn.get("transaction_info", {})
                        txn_id = info.get("transaction_id", "")
                        if not txn_id or db.is_txn_seen(txn_id):
                            continue
                        note   = _note(txn)
                        amount = _amount(txn)
                        if note not in by_note:
                            continue
                        row = by_note[note]
                        if abs(round(amount, 2) - round(row["amount_eur"], 2)) > 0.02:
                            log.warning(f"Amount mismatch note={note} exp={row['amount_eur']} got={amount}")
                            continue
                        db.mark_txn_seen(txn_id)
                        db.confirm_deposit(note, txn_id)
                        log.info(f"Confirmed note={note} txn={txn_id}")
                        await on_confirmed(
                            discord_id=row["discord_id"],
                            note=note,
                            item_key=row["item_key"],
                            amount=amount,
                            quantity=row["quantity"],
                            txn_id=txn_id,
                        )
            except Exception as e:
                log.error(f"Poll error: {e}", exc_info=True)
            await asyncio.sleep(cfg.PP_POLL_INTERVAL)
