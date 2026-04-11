# JustEvents

A lightweight event mirroring tool that syncs external events (starting with [Meetup.com](https://meetup.com)) into Discord.

It is designed to be run on a schedule (once a day is plenty) and supports three creation modes:

- `direct` — create/update/cancel native Discord Scheduled Events via Discord API.
- `sesh` — generate and post Sesh-style `/create ...` commands to a channel.
- `justevent` — generate and post `/create ...` commands that a long-running JustEvent listener can consume.

You can run scheduled sync from GitHub Actions/cron today, and add the long-running listener later when you have a server.

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
  Event creation mode  ← direct API OR Sesh command OR JustEvent command
        │
        ▼
  Discord Guild        ← events appear in the server's Events tab
```

Each event description includes metadata markers such as `source_id:` and `creation_method:`. On the next sync run, JustEvents reads them back to avoid duplicates and make safer decisions per mode.

---

## Prerequisites

- Python 3.11 or later
- A Discord server where you can add a bot
- A Discord Application / Bot token
- Discord permissions:
  - `Manage Events` (required for `direct` mode and JustEvent listener-created native events)
  - `Send Messages` (required for `sesh` and `justevent` command-posting modes)
  - `Read Message History` + Message Content Intent (required for the optional JustEvent listener)

---

## Step 1 — Create the Discord Bot

1. Go to [https://discord.com/developers/applications](https://discord.com/developers/applications) and create a new application.
2. Under **Bot**, click **Add Bot** and copy the **Token** — you will need this later.
3. Under **OAuth2 → URL Generator**, tick:
   - Scopes: `bot`
  - Bot permissions: `Manage Events`, `Send Messages`, `Read Message History`
4. Open the generated URL in a browser, choose your server, and authorise the bot.

**For JustEvent listener mode:** If you plan to run the long-running JustEvent listener, you must also enable **Message Content Intent** in the Developer Portal:
   - Go to your application page at https://discord.com/developers/applications/
   - Select your application
   - Go to **Bot** in the left sidebar
   - Under **Privileged Gateway Intents**, toggle **Message Content Intent** ON
   - Save changes

Without this, the listener will fail to start with `PrivilegedIntentsRequired` error.
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
    event_creation_method: "direct" # direct, sesh, justevent
    # command_channel_id: "123456789012345678"  # required for sesh/justevent if no global default
    # command_target_channel: "#events"         # inserted into generated /create command
    # default_location: "Gothenburg Boardgamers, Gyllenkrooksgatan, Gothenburg, Sweden"
    # command_ack_timeout_seconds: 30            # justevent only: wait for listener confirmation

sync:
  lookahead_days: 90
  default_event_creation_method: "direct"
```

Optional global command channel defaults:

```yaml
discord:
  command:
    default_channel_id: "123456789012345678"
  justevent_listener:
    listen_channel_id: "123456789012345678"
```

The `guild_id` in `config.yaml` is not a secret. The bot token must stay in `.env` only.

---

## Step 3 — Test locally

### Dry run (no Discord changes)

See what would be created/updated without actually touching Discord:

```bash
python -m src.main --dry-run
```

In `sesh` and `justevent` modes, dry-run prints command payloads without posting them.

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

## Step 4 — Run sync

```bash
python -m src.main
```

The bot will:
1. Fetch events from all configured sources.
2. Query your Discord guild for existing scheduled events that were previously created by JustEvents.
3. Execute actions according to each source's `event_creation_method`:
  - `direct`: create/update/cancel native Discord events.
  - `sesh`: post a Sesh-style `/create ...` command message.
  - `justevent`: post a `/create ...` command message for the JustEvent listener.

### Optional: Run the JustEvent listener (server mode)

When you have a server available and have [enabled Message Content Intent](#for-justevent-listener-mode) in the Discord Developer Portal, start the listener:

```bash
python -m src.justevent_listener
```

The listener provides two ways to create events:

1. **Auto-posted commands from sync jobs** — When sync job runs in `justevent` mode, it automatically posts formatted commands to the listener channel, and the listener automatically creates events from them.

2. **Manual slash command** — Use `/create` slash command directly in any channel to manually create events:
   ```
   /create title: Event Name datetime: 2026-04-12 10:00 UTC description: Event details duration: 2h location: Venue name
   ```

The listener:
- Watches the channel specified in `discord.justevent_listener.listen_channel_id` for auto-posted commands
- Registers a `/create` slash command for manual use
- Automatically creates native Discord scheduled events
- Handles rate limiting with exponential backoff
- Logs confirmation or error messages to the channel
- Remains running indefinitely to process commands as they arrive

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

You can also trigger it manually from the **Actions** tab, with:

- `dry_run` toggle
- `event_creation_method` override (`direct`, `sesh`, `justevent`)

The workflow now runs tests in a matrix across all three methods before the sync job.

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
│   ├── test_meetup_source.py      # Meetup source parser unit tests
│   ├── test_config_creation_methods.py    # Config propagation and validation tests
│   └── test_event_commands.py   # Command formatting and parsing tests
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
creation_method: <direct|sesh|justevent>
```

The metadata footer is used internally to match and process events safely on subsequent runs.

### Command payload format (`sesh` / `justevent`)

Command-based modes emit this format:

```text
/create title: title datetime: YYYY-MM-DD HH:MM UTC description: description duration: 2h location: location channel: #events
```

**Sesh mode:** Posts command templates as plain text messages to a Discord channel. Your team copies the `/create ...` command text and manually pastes it into Discord to execute. This is the expected workflow—Discord restricts bots from executing slash commands as interactions, so using Sesh with manual copy-paste is the intended temporary solution until the JustEvent listener is deployed.

**JustEvent mode:** Posts command payloads that the long-running JustEvent listener bot automatically watches for, parses, and processes to create native Discord events (requires the listener to be running on a server). No manual intervention needed.

---

## Troubleshooting

**`DISCORD_BOT_TOKEN` / `DISCORD_GUILD_ID` missing**
→ Make sure `.env` exists and is filled in, or set the environment variables directly.

**`Config file not found`**
→ Copy `config/config.example.yaml` to `config/config.yaml`.

**`403 Forbidden` from Discord API**
→ The bot is missing required permissions (`Manage Events` and/or `Send Messages`). Re-invite it with the permissions listed above.

**JustEvent command posted, but no event was created**
→ Check that:
   1. The listener process is running: `python -m src.justevent_listener`
   2. The listener config points to the correct channel: `discord.justevent_listener.listen_channel_id`
   3. **Message Content Intent is enabled** in the Developer Portal (see [For JustEvent listener mode](#for-justevent-listener-mode))
   4. The bot has `Manage Events` permission in your server

If `command_ack_timeout_seconds` is set in your config, sync logs an explicit failure when no confirmation is detected in time.

**Using the /create slash command**
→ Once the listener is running, you can manually use `/create` in any Discord channel:
   - `title` — Event name (required)
   - `datetime` — Date and time. Formats:
     - `YYYY-MM-DD` (e.g., `2026-04-12` — defaults to 10:00 UTC)
     - `YYYY-MM-DD HH:MM` (e.g., `2026-04-12 14:30`)
   - `description` — Event details (required)
   - `duration` — Event length. Formats: `2h`, `30m`, `1h30m` (required)
   - `location` — Physical location (required)
   
   The listener will automatically create the event and respond with confirmation. Responses are ephemeral (only visible to you).

**"I don't see a /create slash command for JustEvent"**
→ Make sure:
   1. The listener is running: `python -m src.justevent_listener`
   2. **Message Content Intent is enabled** in the Developer Portal
   3. Give it a moment for Discord to sync the commands (up to 1 hour, but usually seconds)

**Sesh mode posted command text, but event was not auto-created**
→ This is expected! Sesh mode posts command templates as plain text. Your team must manually:
   1. Copy the posted command text
   2. Paste it into Discord
   3. Press Enter to execute the `/create ...` slash command

This is the expected temporary workflow until you deploy the JustEvent listener.

**`404 Not Found` when fetching Meetup iCal**
→ Check that `group_slug` in your config matches the slug in the Meetup URL (e.g. `gothenburg-board-gamers-gobo`).

**Events not updating after changes on Meetup**
→ The bot updates all existing events on every run. If a Discord event still shows old data, check whether the Meetup iCal feed has been updated (Meetup can have a short cache delay).
