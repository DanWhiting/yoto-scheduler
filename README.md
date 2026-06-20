# Yoto Scheduler

A LAN-only Python bridge that adds time-of-day volume caps, card whitelists, and scheduled playback events to a family of Yoto players. Runs as a Docker container on a Raspberry Pi (or any LAN device) and exposes a mobile-friendly web UI on port 8765.

## What it does

- **Routines** — sets the volume cap on each player at chosen times of day. Different caps for morning / daytime / bedtime / overnight, applied automatically. Each routine can also carry a card or group whitelist; anything else the kids try to play is stopped within a couple of seconds.
- **Events** — schedules a specific card (or radio station, or alarm tone) to start playing at a chosen time on chosen days. One-off "play the breakfast story at 07:00 on weekdays" style entries.
- **Logs** — a recent-activity view of what fired, what played, and what got blocked.

## Status

Personal-use project. The author runs it against their own family's players; you're welcome to clone and use it, but don't expect feature requests to be prioritised. Bug reports are welcome.

## Requirements

- A Raspberry Pi (4 or 5 — `linux/arm64`) or any Linux/Mac/Windows host with Docker.
- A Yoto OAuth client. Register a **Public Client** at Yoto's developer console, add an Allowed Callback URL matching the host you'll run the bridge on (e.g. `http://192.168.1.94:8765/auth/callback`), and grab the client ID.

## Deploy

```bash
git clone https://github.com/DanWhiting/yoto-scheduler.git
cd yoto-scheduler

# Per-deployment secrets. Both are required; never commit this file (.env is gitignored).
cat > .env <<EOF
YOTO_CLIENT_ID=<your_yoto_oauth_client_id>
YOTO_REDIRECT_URI=http://<your-host-ip>:8765/auth/callback
EOF
chmod 600 .env

# The container runs as uid 1000 — give it ownership of the bind mount.
mkdir -p user_data
sudo chown 1000:1000 user_data

docker compose up -d --build
```

Then open `http://<your-host-ip>:8765/ui/settings` and click **Connect Yoto Account**. You'll be sent to Yoto's sign-in page and bounced back once you approve.

The container has `restart: unless-stopped`, so it comes back automatically after a reboot or crash.

## Update later

```bash
cd yoto-scheduler && git pull && docker compose up -d --build
```

## Safety: dry-run mode

Set `YOTO_BRIDGE_DRY_RUN=1` in your `.env` (or flip the value in `docker-compose.yml`) to make every mutating Yoto API call a logging no-op. The UI still works, you can configure routines and events, but no real volume caps or playback commands actually reach the players. Useful for the first run when you're not yet sure your routines are set right.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `YOTO_CLIENT_ID` | _(required)_ | Yoto OAuth client ID. |
| `YOTO_REDIRECT_URI` | _(required)_ | Full callback URL, e.g. `http://192.168.1.94:8765/auth/callback`. Must match an Allowed Callback URL on your OAuth client. |
| `YOTO_BRIDGE_DRY_RUN` | unset | `1` to log-and-skip mutating Yoto API calls. |
| `YOTO_BRIDGE_HOST` | `0.0.0.0` (in container) | Bind host. |
| `YOTO_BRIDGE_PORT` | `8765` | Bind port. |
| `TZ` | `Europe/London` | Container timezone. Routine and event times are wall-clock, so this needs to match where you live for BST/DST to be handled correctly. |
| `YOTO_USER_DATA_DIR` | `/data` (in container) | Where the bridge stores tokens, schedule, and events. Bind-mounted to `./user_data` on the host by default. |

## Development

```bash
# Run from source (no Docker), against the same .env:
YOTO_BRIDGE_HOST=0.0.0.0 YOTO_BRIDGE_DRY_RUN=1 uv run python -m yoto_bridge
```

`/` redirects to `/ui/routines`. JSON API is at the top level (`/healthz`, `/schedule`, `/events`, `/logs`, etc.).

See [CLAUDE.md](CLAUDE.md) for architecture notes, gotchas, and patterns to reuse. UI design language is in [docs/STYLE.md](docs/STYLE.md).

## Limitations

- **Reactive whitelist enforcement.** Yoto has no API to refuse a card outright at the device, so the bridge waits for an MQTT update saying a disallowed card is playing, then stops it. Expect 1–3 seconds of audio before the stop lands. If the bridge process is offline, nothing is enforced.
- **Public LAN only.** No authentication on the UI — anyone on your LAN can edit your schedule. Don't expose port 8765 to the internet.
- **One Yoto family at a time.** Tokens are per-deployment.
