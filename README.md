# Discord Gamblit Shop Bot - README

## 📋 Overview

A Discord bot that integrates with Gamblit to create a fully functional in-server shop system. Users can purchase items, check balances, manage loyalty points, and more - all through Discord slash commands.

## 🚀 Setup Instructions

### 1. Prerequisites

- Python 3.8 or higher
- A Discord Application with Bot
- Gamblit account with API access
- PayPal account (for payment processing)

### 2. Discord Developer Portal Configuration

#### Create your bot:
1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Navigate to the "Bot" tab
4. Click "Add Bot" and confirm

#### Required Intents - **MUST ENABLE**:

In the Bot section of your application, enable these intents:

```
✅ Presence Intent
   - Required for your bot to receive Presence Update events

✅ Server Members Intent  
   - Required for your bot to receive GUILD_MEMBERS events

✅ Message Content Intent
   - Required for your bot to receive message content in most messages
```

> **⚠️ NOTE:** Once your bot reaches 100+ servers, these intents will require verification and approval from Discord.

#### OAuth2 Settings:
1. Go to "OAuth2" → "URL Generator"
2. Select scopes: `bot` and `applications.commands`
3. Select bot permissions: `Administrator` (or at minimum: Send Messages, Manage Messages, Embed Links, Read Message History)
4. Use the generated URL to invite your bot to your server

### 3. Installation

```bash
# Clone or download the bot files
# Ensure you have these files in your directory:
# - setup.py
# - cfg.py
# - gamblit.py
# - bot.py
# - database.py
# - paypal.py

# Install required dependencies
pip install discord.py
pip install requests
pip install aiohttp
```

### 4. Configuration

Run the setup script to configure your bot:
```bash
python setup.py
```

You will need to provide:
- Bot Token (from Discord Developer Portal)
- Gamblit API credentials
- PayPal API credentials (if using PayPal)
- Your Discord Server ID

### 5. Running the Bot

```bash
python bot.py
```

## 📝 All Commands

### Admin Commands
| Command | Description |
|---------|-------------|
| `/accounts` | Reconfigure bot accounts (Gamblit, PayPal) |
| `/addpoints` | Add loyalty points to a user |
| `/check` | Check bot account status and Gamblit balance |
| `/checkbalance` | Check any user's balance |
| `/deliver` | Manually deliver an order |
| `/setbalance` | Set a user's balance to an exact amount |
| `/stockre` | Force refresh Gamblit stock check |
| `/tiplogs` | View recent Gamblit tip log |

### User Commands
| Command | Description |
|---------|-------------|
| `/addbalance` | Add balance to a user's account |
| `/additem` | Add a new item to the shop |
| `/balance` | Check your balance |
| `/banner` | Set or clear the shop banner/GIF |
| `/color` | Set an embed color |
| `/deposit [item_key] [quantity]` | Manually start a deposit |
| `/description` | Set the shop embed description |
| `/items` | List all shop items and their keys |
| `/myorders` | View your recent orders |
| `/points` | Check your loyalty points |
| `/postshop` | Post the shop embed with buttons |
| `/price` | Update buy or sell price for an item |
| `/stock` | Check stock levels for all items |
| `/title` | Set the shop embed title |

## 🛒 Setting Up Your Store

### Step 1: Configure Shop Appearance
```bash
/title [your shop title]
/description [your shop description]
/banner [image URL or GIF]
/color [hex color code]
```

### Step 2: Add Items
```bash
/additem 
# Follow the prompts to add:
# - Item name
# - Item key (unique identifier)
# - Price
# - Stock quantity
# - Description
```

### Step 3: Set Prices
```bash
/price [item_key] [buy_price] [sell_price]
```

### Step 4: Post Your Shop
```bash
/postshop
# This will create an embed with buttons for users to purchase items
```

### Step 5: Manage Stock
```bash
/stock         # Check current stock levels
/stockre       # Force refresh Gamblit stock check
```

## 💳 Payment Processing

The bot supports Gamblit and PayPal integration:

1. **Gamblit Setup**: Configure your Gamblit account using `/accounts`
2. **PayPal Setup**: Add your PayPal API credentials during setup
3. **Deposits**: Users can use `/deposit` to add funds

## 📊 Database

The bot uses a local database to store:
- User balances
- Shop items and stock
- Order history
- Loyalty points
- Configuration settings

## 🔧 Troubleshooting

### Bot not responding to commands:
- Ensure the bot has `applications.commands` scope when invited
- Re-invite the bot with the correct permissions
- Check that intents are enabled in Discord Developer Portal

### Shop embed not showing:
- Verify the bot has "Embed Links" permission
- Check that at least one item exists in the shop

### Payment issues:
- Verify Gamblit/PayPal API credentials using `/check`
- Check API logs for specific error messages

### Stock not updating:
- Run `/stockre` to force refresh
- Verify Gamblit integration status

## 📄 File Structure

```
├── setup.py        # Initial configuration wizard
├── cfg.py          # Configuration handler
├── gamblit.py      # Gamblit API integration
├── bot.py          # Main bot file
├── database.py     # Database operations
└── paypal.py       # PayPal integration
```

## 🆘 Support

If you encounter issues:
1. Check the console output for error messages
2. Verify all intents are enabled
3. Ensure your bot has proper permissions
4. Check that all dependencies are installed

---

**Note**: Once your bot reaches 100 servers, you must submit your bot for verification and approval to continue using Privileged Intents. Plan accordingly!
