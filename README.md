# JustEvents

A lightweight bot that mirrors events from external websites (starting with [Meetup.com](https://meetup.com)) into Discord as **Scheduled Events**.

It is designed to be run on a schedule — once a day is plenty — and requires no persistent server process. It can run via GitHub Actions, a cron job on a Raspberry Pi, or any regular PC.

---

## How it works

```
Meetup.com (iCal feed)
        │
        ▼
  MeetupSource         ← fetches & parses events into a common Event model
        │
        ▼
    src/main.py        ← compares against existing Discord events
        │
        ▼
  DiscordClient        ← creates / updates / cancels Discord Scheduled Events
        │
        ▼
  Discord Guild        ← events appear in the server's Events tab
```

Each event posted to Discord contains an embedded `source_id` in its description (e.g. `source_id: meetup:313653098`). On the next sync run, the bot reads this marker back from Discord to avoid creating duplicates.

---

## Prerequisites

- Python 3.11 or later
- A Discord server where you can add a bot
- A Discord Application / Bot token with the **Manage Events** permission

---

## Step 1 — Create the Discord Bot

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications) and create a new application.
2. Under **Bot**, click **Add Bot** and copy the **Token** — you will need this later.
3. Under **OAuth2 → URL Generator**, tick:
   - Scopes: `bot`
   - Bot permissions: `Manage Events`
4. Open the generated URL in a browser, choose your server, and authorise the bot.

> **Tip — finding your Server ID:**
> In Discord, open *Settings → Advanced* and enable **Developer Mode**.
> Then right-click your server name and choose **Copy Server ID**.

---

## Step 2 — Local setup

```bash
# Clone the repository
git clone https://github.com/<you>/JustEvents.git
cd JustEvents

# Create a virtual environment (recommended)
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Configure secrets

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

```env
DISCORD_BOT_TOKEN=your-bot-token-here
DISCORD_GUILD_ID=your-guild-id-here
```

`.env` is listed in `.gitignore` and will never be committed.

### Configure sources

```bash
cp config/config.example.yaml config/config.yaml
```

Edit `config/config.yaml`:

```yaml
discord:
  guild_id: "YOUR_GUILD_ID_HERE"   # Same as DISCORD_GUILD_ID in .env

sources:
  - type: meetup
    name: "Gothenburg Board Gamers (GoBo)"
    group_slug: "gothenburg-board-gamers-gobo"

sync:
  lookahead_days: 90
```

The `guild_id` in `config.yaml` is not a secret. The bot token must stay in `.env` only.

---

## Step 3 — Test locally

### Dry run (no Discord changes)

See what would be created/updated without actually touching Discord:

```bash
python -m src.main --dry-run
```

### Test with local sample files (no internet required)

Enable `local_sample` in your config to parse a bundled `.ics` file instead of fetching from Meetup:

```yaml
sources:
  - type: meetup
    name: "GoBo (local test)"
    group_slug: "gothenburg-board-gamers-gobo"
    local_sample: "data/samples/meetup/ical_example.ics"
```

Then run:

```bash
python -m src.main --dry-run
```

### Run the test suite

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

---

## Step 4 — Run the bot

```bash
python -m src.main
```

The bot will:
1. Fetch events from all configured sources.
2. Query your Discord guild for existing scheduled events that were previously created by JustEvents.
3. **Create** new events, **update** changed ones, and **cancel** events that have been removed from the source.

---

## Automated deployment

### Option A — GitHub Actions (recommended, no server required)

The workflow at [.github/workflows/sync_events.yml](.github/workflows/sync_events.yml) runs the sync automatically at **07:00 UTC every day**.

**Setup:**

1. Push this repository to GitHub.
2. Go to **Settings → Secrets and variables → Actions** and add:
   - `DISCORD_BOT_TOKEN` — your bot token
   - `DISCORD_GUILD_ID` — your server ID
3. The workflow starts running on the next scheduled trigger.

You can also trigger it manually from the **Actions** tab, with an optional **dry run** toggle.

### Option B — Raspberry Pi or Linux server (cron)

```bash
# Edit crontab
crontab -e

# Add this line to run every day at 08:00 local time
0 8 * * * cd /home/pi/JustEvents && .venv/bin/python -m src.main >> /var/log/justevents.log 2>&1
```

### Option C — Windows Task Scheduler

1. Open **Task Scheduler** and create a new basic task.
2. Set the trigger to **Daily** at a time of your choice.
3. Action: **Start a program**
   - Program: `C:\path\to\JustEvents\.venv\Scripts\python.exe`
   - Arguments: `-m src.main`
   - Start in: `C:\path\to\JustEvents`

---

## Adding more event sources

The architecture is designed for extensibility. To add a new source (e.g. Eventbrite, Facebook Events):

1. Create `src/sources/<name>.py` and subclass `BaseSource`:

   ```python
   from src.sources.base import BaseSource
   from src.models.event import Event
   from typing import List

   class EventbriteSource(BaseSource):
       @property
       def source_type(self) -> str:
           return "eventbrite"

       @property
       def name(self) -> str:
           return self._name

       def fetch_events(self) -> List[Event]:
           ...  # fetch, parse, return list of Event objects
   ```

2. Register the class in `src/main.py`:

   ```python
   SOURCE_REGISTRY = {
       "meetup": MeetupSource,
       "eventbrite": EventbriteSource,   # add this
   }
   ```

3. Add an entry under `sources:` in `config/config.yaml`:

   ```yaml
   sources:
     - type: eventbrite
       name: "My Eventbrite Org"
       # ... source-specific fields
   ```

---

## Project structure

```
JustEvents/
├── .env.example                    # Template — copy to .env
├── .gitignore
├── README.md
├── requirements.txt                # Runtime dependencies
├── requirements-dev.txt            # Test-only dependencies
│
├── config/
│   └── config.example.yaml        # Template — copy to config.yaml
│
├── data/
│   └── samples/
│       └── meetup/
│           ├── calendar_example.xml   # Meetup RSS feed example
│           ├── rsvp_example.xml       # Meetup RSVP RSS feed example
│           └── ical_example.ics       # Meetup iCal feed example (with event times)
│
├── src/
│   ├── __init__.py
│   ├── main.py                    # Entry point & sync orchestration
│   ├── config.py                  # Config loader (YAML + env vars)
│   ├── discord_client.py          # Discord REST API client
│   ├── models/
│   │   └── event.py               # Common Event dataclass
│   └── sources/
│       ├── base.py                # Abstract base class for all sources
│       └── meetup.py              # Meetup.com iCal / RSS source
│
├── tests/
│   └── test_meetup_source.py      # Parser unit tests
│
└── .github/
    └── workflows/
        └── sync_events.yml        # GitHub Actions: daily sync job
```

---

## Meetup data source details

JustEvents uses Meetup's **iCal calendar feed** as the primary data source because it contains accurate event start and end times (which are required by Discord).

| Feed | URL pattern | Contains event times? |
|------|-------------|----------------------|
| iCal (primary) | `.../events/ical/` | ✅ Yes |
| RSS (fallback) | `.../events/rss/` | ❌ No |

The iCal feed is fetched without authentication, so no Meetup API key is required.

If the iCal fetch fails, the bot falls back to RSS and logs a warning. RSS events will appear in Discord with a note that the exact schedule is unavailable.

---

## Discord event details

| Field | Source |
|-------|--------|
| Name | Event title (max 100 chars) |
| Description | Event description (max 1000 chars, truncated if needed) + source footer |
| Start / End time | From iCal `DTSTART` / `DTEND` |
| Location | Physical address from iCal, or Meetup URL as fallback |
| Event type | External (no Discord voice/stage channel required) |

Each description ends with:
```
🔗 *Mirrored from <Source Name>*
source_id: meetup:<event_id>
```

The `source_id:` line is used internally to match Discord events back to their source on subsequent runs.

---

## Troubleshooting

**`DISCORD_BOT_TOKEN` / `DISCORD_GUILD_ID` missing**
→ Make sure `.env` exists and is filled in, or set the environment variables directly.

**`Config file not found`**
→ Copy `config/config.example.yaml` to `config/config.yaml`.

**`403 Forbidden` from Discord API**
→ The bot is missing the **Manage Events** permission in your server. Re-invite it using the OAuth2 URL Generator with that permission checked.

**`404 Not Found` when fetching Meetup iCal**
→ Check that `group_slug` in your config matches the slug in the Meetup URL (e.g. `gothenburg-board-gamers-gobo`).

**Events not updating after changes on Meetup**
→ The bot updates all existing events on every run. If a Discord event still shows old data, check whether the Meetup iCal feed has been updated (Meetup can have a short cache delay).
