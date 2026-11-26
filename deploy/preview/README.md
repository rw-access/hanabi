# Self-hosted Hanabi preview

The systemd unit runs Hanabi as an unprivileged dynamic user on
`127.0.0.1:8765`. Caddy serves it under `/hanabi/` on your HTTPS site.
The server checks the public host and origin and scopes its session cookie
to the configured path.

## Configure the service

Place the Python package and static assets under
`/opt/containers/hanabi-preview/current/`. In that directory create a
root-owned `release.env` with the public URL of your own Caddy site.
For example:

```ini
HANABI_PUBLIC_URL=https://hanabi.example.com/hanabi/
HANABI_REVISION=<full source Git SHA>
```

Install `hanabi-preview.service` in `/etc/systemd/system/`, run
`systemctl daemon-reload`, and start the service. Add the contents of
`hanabi-preview.caddy` to your site's Caddy block, then validate and
reload Caddy. Open `/hanabi/` to play. An anonymous request to
`/hanabi/api/game` should return `{"game":null}`.

## Stop or roll back

Remove the Hanabi handlers from your Caddy site, validate and reload
Caddy, then stop the service. Restarting the service ends active games.
Keep the state directory when updating or rolling back to preserve saved games.

## Saved games

The browser offers replay saving for each new deal. Systemd provides a private
state directory for the service (`StateDirectoryMode=0700`). Its SQLite archive
is `/var/lib/hanabi-preview/games.sqlite3`. An accepted human move and the bot's
reply are saved together. The replay stores the exact 50-card draw order, deal
seed, teammate, software revision, moves, and outcome. The offline reader
reconstructs both seats' views from the cards and checks each public outcome.
Each deal records its rules version in the existing archive. The current reader
reconstructs classic deals and new deals with empty clues, including their
pre-move own-card labels for offline training. If the server is rolled back,
use this release's reader when exporting an archive containing both versions.
It contains no session cookies, IP addresses, or player accounts. Storage is
local to the server. Retention lasts until an operator removes the records.

Run the offline reader on the server from the root-owned release directory.
`summary` shows aggregate counts. `export` streams one JSON object per game and
accepts `--deal-id ID` to select a single deal. Use a private file outside Git
for an export.

```sh
cd /opt/containers/hanabi-preview/current
sudo -n -- /usr/bin/python3 -B -m hanabi.recordings summary /var/lib/hanabi-preview/games.sqlite3
```
