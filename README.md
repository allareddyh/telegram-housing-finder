# Telegram Housing Finder

Streamlit app that reads housing-related messages from your Telegram groups (last 30 days), then filters by sender, location, rent, and keywords.

Uses the [Telegram Client API (MTProto)](https://core.telegram.org/api) via [Telethon](https://docs.telethon.dev/) with your personal account — not a bot.

## Prerequisites

- Python 3.10+
- A Telegram account
- API credentials from [my.telegram.org](https://my.telegram.org) → **API development tools**

## Setup

1. **Clone the repo**

   ```bash
   git clone https://github.com/allareddyh/telegram-housing-finder.git
   cd telegram-housing-finder
   ```

2. **Create a virtual environment and install dependencies**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

   On macOS/Linux:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure credentials**

   Copy `.env.example` to `.env` and fill in your values:

   ```
   TELEGRAM_API_ID=your_api_id
   TELEGRAM_API_HASH=your_api_hash
   TELEGRAM_SESSION=housing_session
   ```

   You can also enter API ID and Hash directly in the Streamlit sidebar on first run.

4. **Run the app**

   ```powershell
   .\.venv\Scripts\streamlit run app.py
   ```

## Usage

1. **Log in** — Sidebar → enter phone → send code → paste the code from Telegram → verify.
2. **Select groups** — Type group username(s) or click **Load my groups** and pick from the list.
3. **Fetch messages** — Pulls text messages from the last N days (default 30).
4. **Filter results** — Use sender name, location, min/max rent, and sidebar keywords.
5. **Download CSV** — Export matching listings.

## What gets stored locally

| File | Purpose |
|------|---------|
| `.env` | API credentials (never commit) |
| `housing_session.session` | Telegram login session (never commit) |

Both are listed in `.gitignore`.

## Notes

- You must already be a member of the groups you scan.
- Respect Telegram [Terms of Service](https://telegram.org/tos) and group rules.
- Rent parsing supports common English formats (`$1200`, `1200/mo`, `Rs 25,000`, etc.).
- Set **Max rent** to `0` for no upper limit.

## License

Private project — for personal use.
