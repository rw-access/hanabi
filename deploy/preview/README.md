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
```

Install `hanabi-preview.service` in `/etc/systemd/system/`, run
`systemctl daemon-reload`, and start the service. Add the contents of
`hanabi-preview.caddy` to your site's Caddy block, then validate and
reload Caddy. Open `/hanabi/` to play. An anonymous request to
`/hanabi/api/game` should return `{"game":null}`.

## Stop or roll back

Remove the Hanabi handlers from your Caddy site, validate and reload
Caddy, then stop the service. Restarting the service ends active games.
