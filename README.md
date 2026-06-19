# discord_bot

A Discord bot written in Python using [discord.py](https://discordpy.readthedocs.io/).

## Setup

1. Clone the repo:
   ```bash
   git clone https://github.com/yangarwen/discord_bot.git
   cd discord_bot
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate    # Windows
   source .venv/bin/activate # macOS/Linux
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Copy `.env.example` to `.env` and add your bot token:
   ```bash
   cp .env.example .env
   ```

5. Run the bot:
   ```bash
   python bot.py
   ```

## Getting a Bot Token

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Create a **New Application**, then add a **Bot**.
3. Under the bot settings, enable the **Message Content Intent**.
4. Copy the token into your `.env` file.
5. Use the OAuth2 URL Generator (scope `bot`) to invite the bot to your server.
