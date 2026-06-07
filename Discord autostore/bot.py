"""
bot.py — Main Discord bot
Run:  python bot.py
"""

import asyncio
import io
import logging
import os
import random
import string
import sys
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

import cfg
import database as db
import paypal as pp
import gamblit as gt

# ─── Logging (Windows-safe, no emoji in stream) ───────────────────────────────

class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            msg = self.format(record)
            # Strip non-ascii for Windows cp1252 terminals
            stream = self.stream
            stream.write(msg.encode("ascii", "replace").decode("ascii") + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)

handlers = [
    SafeStreamHandler(),
    logging.FileHandler(cfg.LOG_FILE, encoding="utf-8"),
]
logging.basicConfig(
    level=getattr(logging, cfg.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
    handlers=handlers,
)
log = logging.getLogger("bot")

# ─── Bot ─────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# ─── Helpers ─────────────────────────────────────────────────────────────────

def _note() -> str:
    chars = string.ascii_uppercase + string.digits
    seg   = "".join(random.choices(chars, k=cfg.NOTE_LENGTH * 2))
    return f"{cfg.NOTE_PREFIX}-{seg[:cfg.NOTE_LENGTH]}-{seg[cfg.NOTE_LENGTH:cfg.NOTE_LENGTH*2]}"


def _is_admin(itx: discord.Interaction) -> bool:
    if not itx.guild:
        return False
    if itx.user.guild_permissions.administrator:
        return True
    return any(r.id == cfg.ADMIN_ROLE_ID for r in itx.user.roles)


def _col(name: str) -> int:
    return {"default": cfg.COLOR_DEFAULT, "success": cfg.COLOR_SUCCESS,
            "error": cfg.COLOR_ERROR, "warning": cfg.COLOR_WARNING}.get(name, cfg.COLOR_DEFAULT)


# ─── Shop embed builder ───────────────────────────────────────────────────────

def build_shop_embed() -> discord.Embed:
    embed = discord.Embed(
        title=cfg.SHOP_TITLE,
        description=cfg.SHOP_DESC,
        color=cfg.COLOR_DEFAULT,
    )
    for item in cfg.ITEMS:
        stock = db.stock_count(item["key"])
        stock_icon = "🟢" if stock > 10 else ("🟡" if stock > 0 else "🔴")
        val = (
            f"Buy: **{item['buy_price']}{cfg.CURRENCY_SYMBOL}** per {item['name']}\n"
            f"Sell: **{item['sell_price']}{cfg.CURRENCY_SYMBOL}** per {item['name']}\n"
            f"Order: {item['min_order']}-{item['max_order']} {item['name']}\n"
            f"Stock: {stock_icon} {stock}"
        )
        if item.get("unit_label"):
            val += f"\n{item['unit_label']}"
        embed.add_field(name=f">> {item['name']}", value=val, inline=True)

    if cfg.SHOP_BANNER:
        embed.set_image(url=cfg.SHOP_BANNER)
    embed.set_footer(text=cfg.SHOP_FOOTER or cfg.SHOP_NAME)
    return embed


# ─── Views ────────────────────────────────────────────────────────────────────

class ShopView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buy", style=discord.ButtonStyle.green,
                       custom_id="shop:buy")
    async def buy_btn(self, itx: discord.Interaction, btn: discord.ui.Button):
        items = cfg.ITEMS
        if not items:
            await itx.response.send_message("No items available.", ephemeral=True)
            return
        if len(items) == 1:
            await itx.response.send_modal(AmountModal(items[0]["key"]))
        else:
            await itx.response.send_message(
                "Select an item:",
                view=ItemSelectView(),
                ephemeral=True,
            )

    @discord.ui.button(label="Sell", style=discord.ButtonStyle.red,
                       custom_id="shop:sell")
    async def sell_btn(self, itx: discord.Interaction, btn: discord.ui.Button):
        lines = [f"**{i['name']}**: {i['sell_price']}{cfg.CURRENCY_SYMBOL}" for i in cfg.ITEMS]
        embed = discord.Embed(
            title="Sell Prices",
            description="\n".join(lines) + "\n\nOpen a support ticket to sell.",
            color=cfg.COLOR_WARNING,
        )
        await itx.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="My Account", style=discord.ButtonStyle.blurple,
                       custom_id="shop:account")
    async def account_btn(self, itx: discord.Interaction, btn: discord.ui.Button):
        await _show_account(itx)

    @discord.ui.button(label="Balance", style=discord.ButtonStyle.grey,
                       custom_id="shop:balance")
    async def balance_btn(self, itx: discord.Interaction, btn: discord.ui.Button):
        bal = db.get_balance(str(itx.user.id))
        pts, total_pts = db.get_points(str(itx.user.id))
        pts_needed = max(0, cfg.POINTS_FOR_FREE - pts)
        embed = discord.Embed(
            title="Your Balance",
            description=(
                f"Balance: **{bal:.2f} {cfg.CURRENCY_SYMBOL}**\n"
                f"Points: **{pts:.0f}** (total earned: {total_pts:.0f})\n"
                f"**{pts_needed:.0f}** more points for 1{cfg.CURRENCY_SYMBOL} free!"
            ),
            color=cfg.COLOR_DEFAULT,
        )
        await itx.response.send_message(embed=embed, ephemeral=True)


class ItemSelectView(discord.ui.View):
    """Shown when there are multiple items — user picks which to buy."""
    def __init__(self):
        super().__init__(timeout=60)
        for item in cfg.ITEMS:
            btn = discord.ui.Button(
                label=item["name"],
                style=discord.ButtonStyle.blurple,
                # No custom_id here — this is a non-persistent ephemeral view
            )
            btn.callback = self._make_cb(item["key"])
            self.add_item(btn)

    def _make_cb(self, key: str):
        async def cb(itx: discord.Interaction):
            await itx.response.send_modal(AmountModal(key))
        return cb


class AmountModal(discord.ui.Modal):
    def __init__(self, item_key: str):
        item = cfg.get_item(item_key)
        super().__init__(title=f"Buy {item['name']}")
        self.item_key = item_key
        self.amount_input = discord.ui.TextInput(
            label=f"Amount ({item['min_order']}-{item['max_order']} {item['name']})",
            placeholder=f"e.g. {item['min_order']}",
            min_length=1,
            max_length=6,
        )
        self.gamblit_input = discord.ui.TextInput(
            label="Gamblit Username (tip delivery)",
            placeholder="Your Gamblit username",
            min_length=1,
            max_length=32,
            required=True,
        )
        self.add_item(self.amount_input)
        self.add_item(self.gamblit_input)

    async def on_submit(self, itx: discord.Interaction):
        item = cfg.get_item(self.item_key)
        try:
            qty = float(self.amount_input.value.strip())
        except ValueError:
            await itx.response.send_message("Enter a valid number.", ephemeral=True)
            return
        if qty < item["min_order"] or qty > item["max_order"]:
            await itx.response.send_message(
                f"Amount must be {item['min_order']}-{item['max_order']}.", ephemeral=True
            )
            return
        total = round(qty * item["buy_price"], 2)
        gamblit_username = self.gamblit_input.value.strip()
        await _create_deposit(itx, self.item_key, qty, total, gamblit_username)


# ─── Deposit flow ─────────────────────────────────────────────────────────────

async def _create_deposit(itx: discord.Interaction, item_key: str, qty: float,
                          total: float, gamblit_username: str = ""):
    note = _note()
    db.create_deposit(
        discord_id=str(itx.user.id),
        note=note,
        item_key=item_key,
        amount_eur=total,
        quantity=qty,
        gamblit_username=gamblit_username,
    )
    expire_ts = int(datetime.now(timezone.utc).timestamp()) + cfg.DEPOSIT_EXPIRE
    item = cfg.get_item(item_key)

    embed = discord.Embed(
        title="Deposit - PayPal F&F",
        description=f"Send exactly **{total:.2f}{cfg.CURRENCY_SYMBOL}** via PayPal **Friends & Family**.",
        color=cfg.COLOR_DEFAULT,
    )
    embed.add_field(name="PayPal Address", value=f"```{cfg.PP_RECEIVER_EMAIL}```", inline=False)
    embed.add_field(name="Note - copy exactly", value=f"```{note}```", inline=False)
    if gamblit_username:
        embed.add_field(name="Gamblit Username", value=f"`{gamblit_username}`", inline=True)
    embed.add_field(
        name="Order",
        value=f"**{qty} {item['name']}** @ {item['buy_price']}{cfg.CURRENCY_SYMBOL} each",
        inline=True,
    )
    embed.add_field(name="Expires", value=f"<t:{expire_ts}:R>", inline=True)
    embed.set_footer(text="Payment detected automatically - you'll receive a DM when confirmed.")

    await itx.response.send_message(
        embed=embed,
        view=DepositActionsView(note, total),
        ephemeral=True,
    )


class DepositActionsView(discord.ui.View):
    def __init__(self, note: str, amount: float):
        super().__init__(timeout=cfg.DEPOSIT_EXPIRE)
        self.note   = note
        self.amount = amount

        email_user = cfg.PP_RECEIVER_EMAIL.split("@")[0]
        paypal_link = f"https://www.paypal.com/paypalme/{email_user}/{self.amount:.2f}{cfg.CURRENCY}"
        self.add_item(discord.ui.Button(
            label=f"Pay via PayPal ({amount:.2f}{cfg.CURRENCY_SYMBOL})",
            url=paypal_link,
            style=discord.ButtonStyle.link,
        ))

    @discord.ui.button(label="Copy Note", style=discord.ButtonStyle.grey,
                       custom_id="dep:copy")
    async def copy_note(self, itx: discord.Interaction, _):
        await itx.response.send_message(f"```{self.note}```", ephemeral=True)

    @discord.ui.button(label="I've Paid", style=discord.ButtonStyle.green,
                       custom_id="dep:paid")
    async def ive_paid(self, itx: discord.Interaction, _):
        row = db.get_deposit(self.note)
        if not row:
            await itx.response.send_message("Deposit not found.", ephemeral=True)
            return
        status = row["status"]
        if status == "confirmed":
            await itx.response.send_message("Already confirmed!", ephemeral=True)
        elif status == "expired":
            await itx.response.send_message("Expired. Please start a new deposit.", ephemeral=True)
        else:
            await itx.response.send_message(
                "Checking your payment now - you'll receive a DM when confirmed.", ephemeral=True
            )


# ─── Account ─────────────────────────────────────────────────────────────────

async def _show_account(itx: discord.Interaction):
    orders = db.get_orders(str(itx.user.id))
    bal    = db.get_balance(str(itx.user.id))
    pts, total_pts = db.get_points(str(itx.user.id))
    pts_needed = max(0, cfg.POINTS_FOR_FREE - pts)

    desc = "\n".join(
        f"{'OK' if o['status'] == 'delivered' else '...'} "
        f"`#{o['id']}` - **{o['quantity']} {o['item_key'].upper()}** - {o['status']}"
        for o in orders
    ) if orders else "No orders yet."

    embed = discord.Embed(title=f"Account: {itx.user.display_name}", color=cfg.COLOR_DEFAULT)
    embed.add_field(name="Balance", value=f"**{bal:.2f}{cfg.CURRENCY_SYMBOL}**", inline=True)
    embed.add_field(
        name="Points",
        value=f"**{pts:.0f} pts** (total: {total_pts:.0f})\n{pts_needed:.0f} more = 1{cfg.CURRENCY_SYMBOL} free",
        inline=True,
    )
    embed.add_field(name="Recent Orders", value=desc, inline=False)
    embed.set_footer(text=f"Every 1{cfg.CURRENCY_SYMBOL} spent = 1 point | {cfg.POINTS_FOR_FREE} points = 1{cfg.CURRENCY_SYMBOL} free")
    await itx.response.send_message(embed=embed, ephemeral=True)


# ─── Delivery ────────────────────────────────────────────────────────────────

async def deliver(discord_id: str, note: str, item_key: str, quantity: float):
    order_id = db.create_order(discord_id, note, item_key, quantity)
    item     = cfg.get_item(item_key) or {"name": item_key}
    stock    = db.pop_stock(item_key)

    if stock:
        db.complete_order(order_id, stock)
        await _dm(int(discord_id),
                  title="Order Delivered!",
                  desc=f"**{quantity} {item['name']}** - your delivery:\n```{stock}```\nThank you!",
                  color=cfg.COLOR_SUCCESS)
    else:
        await _dm(int(discord_id),
                  title="Delivery Pending",
                  desc=f"Payment received for **{quantity} {item['name']}**.\nA staff member will deliver shortly.",
                  color=cfg.COLOR_WARNING)
        await _admin_ping(
            f"**Manual delivery needed**\n"
            f"User: <@{discord_id}>\n"
            f"Item: **{quantity} {item['name']}**\n"
            f"Note: `{note}`  |  Order ID: `{order_id}`"
        )


async def _dm(uid: int, title: str, desc: str, color: int):
    try:
        u = await bot.fetch_user(uid)
        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_footer(text=cfg.SHOP_NAME)
        await u.send(embed=embed)
    except discord.Forbidden:
        log.warning(f"Cannot DM {uid} (DMs closed)")
    except Exception as e:
        log.error(f"DM error {uid}: {e}")


async def _admin_ping(msg: str):
    try:
        ch = bot.get_channel(cfg.ADMIN_LOG_CHANNEL_ID)
        if ch:
            await ch.send(msg)
    except Exception as e:
        log.error(f"Admin ping error: {e}")
    try:
        u = await bot.fetch_user(cfg.MANUAL_DELIVERY_UID)
        await u.send(msg)
    except Exception as e:
        log.error(f"Admin DM error: {e}")


async def _payment_log(discord_id: str, amount: float, quantity: float,
                       item_key: str, note: str, txn_id: str):
    ch_id = cfg.PAYMENT_LOG_CHANNEL_ID
    if not ch_id:
        return
    try:
        ch = bot.get_channel(ch_id)
        if not ch:
            return
        item = cfg.get_item(item_key) or {"name": item_key}
        embed = discord.Embed(
            title="Payment Received",
            color=cfg.COLOR_SUCCESS,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="User", value=f"<@{discord_id}>", inline=True)
        embed.add_field(name="Amount", value=f"**{amount:.2f}{cfg.CURRENCY_SYMBOL}**", inline=True)
        embed.add_field(name="Item", value=f"**{quantity} {item['name']}**", inline=True)
        embed.add_field(name="Note", value=f"`{note}`", inline=True)
        embed.add_field(name="PayPal TXN", value=f"`{txn_id}`", inline=True)
        await ch.send(embed=embed)
    except Exception as e:
        log.error(f"Payment log channel error: {e}")


# ─── PayPal callback ──────────────────────────────────────────────────────────

async def on_payment_confirmed(discord_id, note, item_key, amount, quantity, txn_id):
    log.info(f"Payment confirmed user={discord_id} note={note} txn={txn_id}")

    db.add_balance(discord_id, amount)

    # Grant role
    if cfg.VERIFIED_BUYER_ROLE_ID:
        try:
            for guild in bot.guilds:
                member = guild.get_member(int(discord_id))
                if member:
                    role = guild.get_role(cfg.VERIFIED_BUYER_ROLE_ID)
                    if role:
                        await member.add_roles(role)
        except Exception as e:
            log.warning(f"Role grant failed: {e}")

    await _admin_ping(
        f"**Payment Received**\n"
        f"User: <@{discord_id}>\n"
        f"Amount: **{amount:.2f}{cfg.CURRENCY_SYMBOL}**\n"
        f"Qty: **{quantity}**  Item: **{item_key}**\n"
        f"Note: `{note}`  Txn: `{txn_id}`"
    )
    await _payment_log(discord_id, amount, quantity, item_key, note, txn_id)

    # Points: 1 EUR = 1 point, auto-redeem at POINTS_FOR_FREE
    pts_earned = int(amount * cfg.POINTS_PER_EUR)
    if pts_earned > 0:
        db.add_points(discord_id, pts_earned)
        current_pts, _ = db.get_points(discord_id)
        redeemed_eur = 0.0
        while current_pts >= cfg.POINTS_FOR_FREE:
            if db.deduct_points(discord_id, cfg.POINTS_FOR_FREE):
                db.add_balance(discord_id, 1.0)
                redeemed_eur += 1.0
                current_pts -= cfg.POINTS_FOR_FREE
            else:
                break
        if redeemed_eur > 0:
            await _dm(int(discord_id),
                title="Points Redeemed!",
                desc=(
                    f"You reached **{cfg.POINTS_FOR_FREE} points** and earned "
                    f"**{redeemed_eur:.0f}{cfg.CURRENCY_SYMBOL} free** added to your balance!\n"
                    f"Remaining points: **{current_pts:.0f}**"
                ),
                color=cfg.COLOR_SUCCESS)
        else:
            pts_now, _ = db.get_points(discord_id)
            pts_needed = max(0, cfg.POINTS_FOR_FREE - pts_now)
            await _dm(int(discord_id),
                title="Points Earned!",
                desc=(
                    f"You earned **{pts_earned} point{'s' if pts_earned != 1 else ''}**!\n"
                    f"Total: **{pts_now:.0f} pts** - "
                    f"**{pts_needed:.0f}** more for 1{cfg.CURRENCY_SYMBOL} free!"
                ),
                color=cfg.COLOR_DEFAULT)

    # Gamblit tip
    deposit_row = db.get_deposit(note)
    gamblit_username = (deposit_row["gamblit_username"] if deposit_row else "") or ""

    if gamblit_username and cfg.GAMBLIT_TIP_ENABLED:
        dl_amount = round(quantity * cfg.BGL_TO_DL, 2)
        tip_result = await gt.send_tip(gamblit_username, dl_amount)

        if tip_result["ok"]:
            tip_status = "sent"
            tip_msg = (
                f"**Gamblit Tip Sent**\n"
                f"User: <@{discord_id}>\n"
                f"Gamblit: `{gamblit_username}`\n"
                f"Amount: **{quantity} BGL -> {dl_amount} DL**\n"
                f"Note: `{note}`"
            )
        else:
            tip_status = "failed"
            tip_msg = (
                f"**Gamblit Tip FAILED**\n"
                f"User: <@{discord_id}>\n"
                f"Gamblit: `{gamblit_username}`\n"
                f"Amount: **{quantity} BGL -> {dl_amount} DL**\n"
                f"Note: `{note}`\n"
                f"Error: `{tip_result['message']}`\n"
                f"Manual tip required!"
            )

        db.log_tip(
            discord_id=discord_id,
            gamblit_username=gamblit_username,
            amount_bgl=quantity,
            amount_dl=dl_amount,
            note=note,
            status=tip_status,
            error="" if tip_result["ok"] else tip_result["message"],
        )
        await _admin_ping(tip_msg)

        if tip_result["ok"]:
            await _dm(int(discord_id),
                title="Gamblit Tip Sent!",
                desc=f"**{quantity} BGL ({dl_amount} DL)** tipped to `{gamblit_username}` on Gamblit.net!",
                color=cfg.COLOR_SUCCESS)
        else:
            await _dm(int(discord_id),
                title="Gamblit Tip Issue",
                desc=(
                    f"Payment received but tip to `{gamblit_username}` failed.\n"
                    f"Our team has been notified and will sort it out shortly!"
                ),
                color=cfg.COLOR_WARNING)

    await deliver(discord_id, note, item_key, quantity)


# ─── Gamblit stock poller (every 15s) ─────────────────────────────────────────

async def gamblit_stock_poll_loop():
    """
    Every 15 seconds, fetch the bot account's Gamblit balance/stock
    and update DB stock for each item if Gamblit reports inventory.
    Logs to admin channel on changes.
    """
    log.info("Gamblit stock poller started.")
    last_balance = None
    while True:
        try:
            result = await gt.get_balance()
            if result["ok"]:
                bal = result.get("balance", 0)
                if bal != last_balance:
                    log.info(f"Gamblit balance updated: {bal}")
                    db.set_gamblit_balance(bal)
                    last_balance = bal
            else:
                log.warning(f"Gamblit balance check failed: {result.get('message')}")
        except Exception as e:
            log.error(f"Gamblit stock poll error: {e}")
        await asyncio.sleep(15)


# ─── Crash reporter ───────────────────────────────────────────────────────────

async def _send_crash_report(exc: BaseException):
    try:
        ch = bot.get_channel(cfg.ADMIN_LOG_CHANNEL_ID)
        if not ch:
            return
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        if len(tb) > 3900:
            tb = "..." + tb[-3900:]
        embed = discord.Embed(
            title="Bot Crashed!",
            description=f"```python\n{tb}\n```",
            color=cfg.COLOR_ERROR,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Exception", value=f"`{type(exc).__name__}: {exc}`", inline=False)
        embed.set_footer(text="Bot has stopped - restart it manually.")
        await ch.send(embed=embed)
    except Exception as e:
        log.error(f"Failed to send crash report: {e}")


# ─── on_ready ────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} ({bot.user.id})")
    db.init()
    bot.add_view(ShopView())
    await tree.sync()
    log.info("Commands synced.")
    asyncio.create_task(pp.poll_loop(on_payment_confirmed))
    asyncio.create_task(gamblit_stock_poll_loop())


# ═══════════════════════════════════════════════════════════════════════════════
#  SLASH COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

# ── /postshop ────────────────────────────────────────────────

@tree.command(name="postshop", description="Post the shop embed with buttons")
async def cmd_postshop(itx: discord.Interaction):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    ch = bot.get_channel(cfg.SHOP_CHANNEL_ID)
    if not ch:
        await itx.response.send_message("Shop channel not found. Check SHOP_CHANNEL_ID.", ephemeral=True); return
    await ch.send(embed=build_shop_embed(), view=ShopView())
    await itx.response.send_message("Shop posted!", ephemeral=True)


# ── /banner ───────────────────────────────────────────────────

@tree.command(name="banner", description="Set or clear the shop banner/GIF")
@app_commands.describe(url="Direct image or GIF URL (leave blank to remove)")
async def cmd_banner(itx: discord.Interaction, url: str = ""):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    cfg.set_shop_field("banner_url", url)
    await itx.response.send_message(
        f"Banner {'set to ' + url if url else 'removed'}. Re-run /postshop to update.",
        ephemeral=True,
    )


# ── /title ───────────────────────────────────────────────────

@tree.command(name="title", description="Set the shop embed title")
@app_commands.describe(title="New title text")
async def cmd_title(itx: discord.Interaction, title: str):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    cfg.set_shop_field("title", title)
    await itx.response.send_message(f"Title set to **{title}**. Re-run /postshop.", ephemeral=True)


# ── /description ─────────────────────────────────────────────

@tree.command(name="description", description="Set the shop embed description")
@app_commands.describe(text="New description text")
async def cmd_description(itx: discord.Interaction, text: str):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    cfg.set_shop_field("description", text)
    await itx.response.send_message("Description updated. Re-run /postshop.", ephemeral=True)


# ── /items ────────────────────────────────────────────────────

@tree.command(name="items", description="List all shop items and their keys")
async def cmd_items(itx: discord.Interaction):
    if not cfg.ITEMS:
        await itx.response.send_message("No items configured.", ephemeral=True); return
    lines = []
    for i in cfg.ITEMS:
        lines.append(
            f"**{i['name']}** (`{i['key']}`)\n"
            f"  Buy: {i['buy_price']}{cfg.CURRENCY_SYMBOL}  |  "
            f"Sell: {i['sell_price']}{cfg.CURRENCY_SYMBOL}  |  "
            f"Stock: {db.stock_count(i['key'])}"
        )
    embed = discord.Embed(
        title="Shop Items",
        description="\n\n".join(lines),
        color=cfg.COLOR_DEFAULT,
    )
    await itx.response.send_message(embed=embed, ephemeral=True)


# ── /price ────────────────────────────────────────────────────

@tree.command(name="price", description="Update buy or sell price for an item")
@app_commands.describe(
    item_key="Item key (use /items to see keys)",
    buy="New buy price (leave blank to keep)",
    sell="New sell price (leave blank to keep)",
)
async def cmd_price(itx: discord.Interaction, item_key: str,
                    buy: float = None, sell: float = None):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    item = cfg.get_item(item_key.lower())
    if not item:
        await itx.response.send_message(f"Item `{item_key}` not found. Use /items.", ephemeral=True); return
    cfg.update_item_price(item_key.lower(), buy, sell)
    updated = cfg.get_item(item_key.lower())
    await itx.response.send_message(
        f"**{updated['name']}** prices updated:\n"
        f"Buy: **{updated['buy_price']}{cfg.CURRENCY_SYMBOL}**  |  "
        f"Sell: **{updated['sell_price']}{cfg.CURRENCY_SYMBOL}**\n"
        "Re-run /postshop to update the embed.",
        ephemeral=True,
    )


# ── /additem ─────────────────────────────────────────────────

@tree.command(name="additem", description="Add a new item to the shop")
@app_commands.describe(
    name="Display name (e.g. BGL)",
    buy="Buy price per unit",
    sell="Sell price per unit",
    min_order="Minimum order quantity",
    max_order="Maximum order quantity",
    unit_label="Optional unit label (e.g. 1 BGL = 100 DL)",
)
async def cmd_additem(itx: discord.Interaction, name: str, buy: float, sell: float,
                      min_order: int = 1, max_order: int = 500, unit_label: str = ""):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    key = name.lower().replace(" ", "_")
    if cfg.get_item(key):
        await itx.response.send_message(f"Item `{key}` already exists. Use /price to update.", ephemeral=True); return
    cfg.add_pricing_item({
        "key": key, "name": name, "buy_price": buy, "sell_price": sell,
        "min_order": min_order, "max_order": max_order, "unit_label": unit_label,
    })
    await itx.response.send_message(f"Item **{name}** added. Re-run /postshop to update.", ephemeral=True)


# ── /stock ────────────────────────────────────────────────────

@tree.command(name="stock", description="Check stock levels for all items")
async def cmd_stock(itx: discord.Interaction):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    lines = []
    for item in cfg.ITEMS:
        count = db.stock_count(item["key"])
        icon  = "OK" if count > 10 else ("LOW" if count > 0 else "OUT")
        lines.append(f"[{icon}] **{item['name']}**: {count} items")

    gamblit_bal = db.get_gamblit_balance()
    if gamblit_bal is not None:
        lines.append(f"\nGamblit Account Balance: **{gamblit_bal}** (last checked every 15s)")

    embed = discord.Embed(
        title="Stock Levels",
        description="\n".join(lines) if lines else "No items configured.",
        color=cfg.COLOR_DEFAULT,
    )
    await itx.response.send_message(embed=embed, ephemeral=True)


# ── /stockre ──────────────────────────────────────────────────

@tree.command(name="stockre", description="Force refresh Gamblit stock check now")
async def cmd_stockre(itx: discord.Interaction):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    await itx.response.defer(ephemeral=True)
    try:
        result = await gt.get_balance()
        if result["ok"]:
            bal = result.get("balance", 0)
            db.set_gamblit_balance(bal)
            await itx.followup.send(
                f"Gamblit balance refreshed: **{bal}**", ephemeral=True
            )
        else:
            await itx.followup.send(
                f"Gamblit check failed: {result.get('message', 'Unknown error')}", ephemeral=True
            )
    except Exception as e:
        await itx.followup.send(f"Error: {e}", ephemeral=True)


# ── /deliver ─────────────────────────────────────────────────

@tree.command(name="deliver", description="Manually deliver an order")
@app_commands.describe(order_id="Order ID", item="Delivery string to send the user")
async def cmd_deliver(itx: discord.Interaction, order_id: int, item: str):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    with db._conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if not row:
        await itx.response.send_message("Order not found.", ephemeral=True); return
    db.complete_order(order_id, item)
    await _dm(int(row["discord_id"]),
              title="Order Delivered!",
              desc=f"Your order has been delivered:\n```{item}```",
              color=cfg.COLOR_SUCCESS)
    await itx.response.send_message(
        f"Order `#{order_id}` delivered to <@{row['discord_id']}>.", ephemeral=True
    )


# ── /addbalance ───────────────────────────────────────────────

@tree.command(name="addbalance", description="Add balance to a user's account")
@app_commands.describe(user="Target user", amount="Amount in euros to add")
async def cmd_addbalance(itx: discord.Interaction, user: discord.Member, amount: float):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    if amount <= 0:
        await itx.response.send_message("Amount must be positive.", ephemeral=True); return
    db.add_balance(str(user.id), amount)
    new_bal = db.get_balance(str(user.id))
    embed = discord.Embed(
        title="Balance Added",
        description=(
            f"Added **{amount:.2f}{cfg.CURRENCY_SYMBOL}** to {user.mention}\n"
            f"New balance: **{new_bal:.2f}{cfg.CURRENCY_SYMBOL}**"
        ),
        color=cfg.COLOR_SUCCESS,
    )
    await itx.response.send_message(embed=embed, ephemeral=False)
    await _dm(user.id,
              title="Balance Added",
              desc=f"**{amount:.2f}{cfg.CURRENCY_SYMBOL}** was added to your account by an admin.\nNew balance: **{new_bal:.2f}{cfg.CURRENCY_SYMBOL}**",
              color=cfg.COLOR_SUCCESS)


# ── /setbalance ───────────────────────────────────────────────

@tree.command(name="setbalance", description="Set a user's balance to an exact amount")
@app_commands.describe(user="Target user", amount="New balance in euros")
async def cmd_setbalance(itx: discord.Interaction, user: discord.Member, amount: float):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    db.set_balance(str(user.id), amount)
    await itx.response.send_message(
        f"{user.mention}'s balance set to **{amount:.2f}{cfg.CURRENCY_SYMBOL}**.", ephemeral=True
    )


# ── /balance ──────────────────────────────────────────────────

@tree.command(name="balance", description="Check your balance")
async def cmd_balance(itx: discord.Interaction):
    bal = db.get_balance(str(itx.user.id))
    embed = discord.Embed(
        title="Your Balance",
        description=f"**{bal:.2f} {cfg.CURRENCY_SYMBOL}**",
        color=cfg.COLOR_DEFAULT,
    )
    await itx.response.send_message(embed=embed, ephemeral=True)


# ── /checkbalance (admin) ─────────────────────────────────────

@tree.command(name="checkbalance", description="Check any user's balance (admin)")
@app_commands.describe(user="User to check")
async def cmd_checkbalance(itx: discord.Interaction, user: discord.Member):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    bal = db.get_balance(str(user.id))
    await itx.response.send_message(
        f"{user.mention} balance: **{bal:.2f}{cfg.CURRENCY_SYMBOL}**", ephemeral=True
    )


# ── /myorders ─────────────────────────────────────────────────

@tree.command(name="myorders", description="View your recent orders")
async def cmd_myorders(itx: discord.Interaction):
    await _show_account(itx)


# ── /deposit ──────────────────────────────────────────────────

@tree.command(name="deposit", description="Manually start a deposit")
@app_commands.describe(item_key="Item key (use /items)", quantity="Amount to buy")
async def cmd_deposit(itx: discord.Interaction, item_key: str, quantity: float):
    item = cfg.get_item(item_key.lower())
    if not item:
        await itx.response.send_message(f"Unknown item `{item_key}`. Use /items.", ephemeral=True); return
    if quantity < item["min_order"] or quantity > item["max_order"]:
        await itx.response.send_message(
            f"Amount must be {item['min_order']}-{item['max_order']}.", ephemeral=True
        ); return
    total = round(quantity * item["buy_price"], 2)
    await _create_deposit(itx, item_key.lower(), quantity, total)


# ── /footer ───────────────────────────────────────────────────

@tree.command(name="footer", description="Set the shop embed footer text")
@app_commands.describe(text="Footer text")
async def cmd_footer(itx: discord.Interaction, text: str):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    cfg.set_shop_field("footer_text", text)
    await itx.response.send_message("Footer updated. Re-run /postshop.", ephemeral=True)


# ── /color ────────────────────────────────────────────────────

@tree.command(name="color", description="Set an embed color")
@app_commands.describe(which="Which color to change", hex_color="Hex color code (e.g. 5865F2)")
@app_commands.choices(which=[
    app_commands.Choice(name="default", value="color_default"),
    app_commands.Choice(name="success", value="color_success"),
    app_commands.Choice(name="error",   value="color_error"),
    app_commands.Choice(name="warning", value="color_warning"),
])
async def cmd_color(itx: discord.Interaction, which: str, hex_color: str):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    try:
        int(hex_color.lstrip("#"), 16)
    except ValueError:
        await itx.response.send_message("Invalid hex color.", ephemeral=True); return
    cfg.set_shop_field(which, hex_color.lstrip("#"))
    await itx.response.send_message(f"Color `{which}` set to `#{hex_color.lstrip('#')}`.", ephemeral=True)


# ── /tiplogs ──────────────────────────────────────────────────

@tree.command(name="tiplogs", description="View recent Gamblit tip log (admin)")
@app_commands.describe(limit="Number of entries to show (default 10)")
async def cmd_tiplogs(itx: discord.Interaction, limit: int = 10):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    rows = db.get_tip_log(min(limit, 25))
    if not rows:
        await itx.response.send_message("No tip logs yet.", ephemeral=True); return
    lines = []
    for r in rows:
        icon = "OK" if r["status"] == "sent" else "FAIL"
        lines.append(
            f"[{icon}] `#{r['id']}` **{r['gamblit_username']}** - "
            f"{r['amount_bgl']} BGL / {r['amount_dl']} DL - "
            f"<@{r['discord_id']}> - `{r['status']}`"
            + (f"\n   -> {r['error']}" if r["error"] else "")
        )
    embed = discord.Embed(
        title="Gamblit Tip Log",
        description="\n".join(lines),
        color=cfg.COLOR_DEFAULT,
    )
    await itx.response.send_message(embed=embed, ephemeral=True)


# ── /points ───────────────────────────────────────────────────

@tree.command(name="points", description="Check your loyalty points")
async def cmd_points(itx: discord.Interaction):
    pts, total_pts = db.get_points(str(itx.user.id))
    pts_needed = max(0, cfg.POINTS_FOR_FREE - pts)
    embed = discord.Embed(
        title="Your Loyalty Points",
        description=(
            f"**Current points:** {pts:.0f}\n"
            f"**Total ever earned:** {total_pts:.0f}\n\n"
            f"**{pts_needed:.0f}** more points to earn **1{cfg.CURRENCY_SYMBOL} free!**\n\n"
            f"Every 1{cfg.CURRENCY_SYMBOL} spent = 1 point\n"
            f"Every {cfg.POINTS_FOR_FREE} points = 1{cfg.CURRENCY_SYMBOL} automatically added"
        ),
        color=cfg.COLOR_DEFAULT,
    )
    await itx.response.send_message(embed=embed, ephemeral=True)


# ── /addpoints (admin) ────────────────────────────────────────

@tree.command(name="addpoints", description="Add loyalty points to a user (admin)")
@app_commands.describe(user="Target user", amount="Points to add")
async def cmd_addpoints(itx: discord.Interaction, user: discord.Member, amount: float):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    if amount <= 0:
        await itx.response.send_message("Amount must be positive.", ephemeral=True); return
    db.add_points(str(user.id), amount)
    pts, _ = db.get_points(str(user.id))
    await itx.response.send_message(
        f"Added **{amount:.0f} points** to {user.mention}. They now have **{pts:.0f} pts**.",
        ephemeral=True,
    )


# ── /accounts ─────────────────────────────────────────────────

@tree.command(name="accounts", description="Reconfigure bot accounts (Gamblit, PayPal) (admin)")
async def cmd_accounts(itx: discord.Interaction):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    await itx.response.send_modal(AccountsModal())


class AccountsModal(discord.ui.Modal, title="Reconfigure Bot Accounts"):
    gamblit_user = discord.ui.TextInput(
        label="Gamblit Username",
        placeholder="Leave blank to keep current",
        required=False,
        max_length=64,
    )
    gamblit_pass = discord.ui.TextInput(
        label="Gamblit Password",
        placeholder="Leave blank to keep current",
        required=False,
        max_length=128,
    )
    paypal_email = discord.ui.TextInput(
        label="PayPal Receiver Email",
        placeholder="Leave blank to keep current",
        required=False,
        max_length=128,
    )
    paypal_id = discord.ui.TextInput(
        label="PayPal Client ID",
        placeholder="Leave blank to keep current",
        required=False,
        max_length=256,
    )
    paypal_secret = discord.ui.TextInput(
        label="PayPal Client Secret",
        placeholder="Leave blank to keep current",
        required=False,
        max_length=256,
    )

    async def on_submit(self, itx: discord.Interaction):
        import json, os
        changes = []

        # Gamblit
        g_user = self.gamblit_user.value.strip()
        g_pass = self.gamblit_pass.value.strip()
        if g_user or g_pass:
            gamblit_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "gamblit.json")
            with open(gamblit_path) as f:
                gcfg = json.load(f)
            if g_user:
                gcfg["username"] = g_user
                cfg.GAMBLIT_USERNAME = g_user
                changes.append(f"Gamblit username -> `{g_user}`")
            if g_pass:
                gcfg["password"] = g_pass
                cfg.GAMBLIT_PASSWORD = g_pass
                changes.append("Gamblit password updated")
            with open(gamblit_path, "w") as f:
                json.dump(gcfg, f, indent=2)
            # Reset gamblit session so it re-logins
            await gt.close()

        # PayPal
        pp_email  = self.paypal_email.value.strip()
        pp_id     = self.paypal_id.value.strip()
        pp_secret = self.paypal_secret.value.strip()
        if pp_email or pp_id or pp_secret:
            pp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config", "paypal.json")
            with open(pp_path) as f:
                ppcfg = json.load(f)
            if pp_email:
                ppcfg["receiver_email"] = pp_email
                cfg.PP_RECEIVER_EMAIL = pp_email
                changes.append(f"PayPal email -> `{pp_email}`")
            if pp_id:
                ppcfg["client_id"] = pp_id
                cfg.PP_CLIENT_ID = pp_id
                changes.append("PayPal Client ID updated")
            if pp_secret:
                ppcfg["client_secret"] = pp_secret
                cfg.PP_CLIENT_SECRET = pp_secret
                changes.append("PayPal Client Secret updated")
            with open(pp_path, "w") as f:
                json.dump(ppcfg, f, indent=2)

        if changes:
            await itx.response.send_message(
                "Accounts updated:\n" + "\n".join(f"- {c}" for c in changes),
                ephemeral=True,
            )
        else:
            await itx.response.send_message("No changes made (all fields were blank).", ephemeral=True)


# ── /check ────────────────────────────────────────────────────

@tree.command(name="check", description="Check bot account status and Gamblit balance (admin)")
async def cmd_check(itx: discord.Interaction):
    if not _is_admin(itx):
        await itx.response.send_message("Admins only.", ephemeral=True); return
    await itx.response.defer(ephemeral=True)

    embed = discord.Embed(title="Bot Account Status", color=cfg.COLOR_DEFAULT,
                          timestamp=datetime.now(timezone.utc))

    # Gamblit check
    try:
        result = await gt.get_balance()
        if result["ok"]:
            bal = result.get("balance", "N/A")
            db.set_gamblit_balance(bal)
            embed.add_field(
                name="Gamblit Account",
                value=f"OK\nUsername: `{cfg.GAMBLIT_USERNAME}`\nBalance: **{bal}**",
                inline=False,
            )
        else:
            embed.add_field(
                name="Gamblit Account",
                value=f"FAILED\nUsername: `{cfg.GAMBLIT_USERNAME}`\nError: {result.get('message')}",
                inline=False,
            )
    except Exception as e:
        embed.add_field(name="Gamblit Account", value=f"ERROR: {e}", inline=False)

    # PayPal check
    embed.add_field(
        name="PayPal",
        value=(
            f"Email: `{cfg.PP_RECEIVER_EMAIL}`\n"
            f"Sandbox: {'Yes' if cfg.PP_SANDBOX else 'No'}\n"
            f"Poll interval: {cfg.PP_POLL_INTERVAL}s"
        ),
        inline=False,
    )

    # DB stock summary
    stock_lines = [f"{i['name']}: {db.stock_count(i['key'])}" for i in cfg.ITEMS]
    embed.add_field(name="Stock", value="\n".join(stock_lines) or "No items", inline=False)

    # Points config
    embed.add_field(
        name="Points System",
        value=f"1{cfg.CURRENCY_SYMBOL} = {cfg.POINTS_PER_EUR} pt | {cfg.POINTS_FOR_FREE} pts = 1{cfg.CURRENCY_SYMBOL} free",
        inline=False,
    )

    await itx.followup.send(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    async def _main():
        async with bot:
            try:
                await bot.start(cfg.BOT_TOKEN)
            except Exception as exc:
                log.critical(f"Bot crashed: {exc}", exc_info=True)
                await _send_crash_report(exc)
                raise
            finally:
                await gt.close()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user.")
    except Exception as exc:
        log.critical(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)
