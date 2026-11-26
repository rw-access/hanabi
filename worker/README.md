# Hanabi online

The public board offers solo play with Adaptive and invite-only games for two people. Create a room and send the link or eight-character code to a teammate. Opening the link previews the invite. **Join room** claims the second seat. Reloading in the same browser restores your seat. An idle room expires after 24 hours.

## Local development

Use Node.js 22.18+ and Python 3.13 with pip. Run these commands from the project root.

```sh
npm ci
python3 -m pip install --target python_modules --no-deps --no-compile --require-hashes -r worker/requirements.txt
npm run dev:worker
```

Open `http://localhost:8787/play/hanabi/`. To try both seats on one device, use separate browser profiles. Run `npm run build` after editing a runtime module or board asset to refresh the files watched by Wrangler.

## Verification

```sh
python3 -m unittest discover
python3 -m tests.online_runtime http://localhost:8787/play/hanabi/
npx playwright install chromium
npm run test:online
npm run check:worker
```

The runtime test creates disposable rooms. Pass `--pause-for-restart` to stop at the first move, restart Wrangler with the same local storage, and verify recovery. The browser test uses separate desktop and mobile contexts. Set `HANABI_URL` to the running board and `CHROMIUM_EXECUTABLE` to use an existing Chromium installation.

## Deployment and recovery

`wrangler.jsonc` defines the Hanabi Worker, static assets, and one SQLite-backed Durable Object per room. The build stages the public files under `dist/assets/play/hanabi/` and selects the Python modules needed for gameplay. The room stores a private draw order and accepted actions. Browsers receive only their seat’s view. WebSockets notify connected players when to fetch it.

Authenticate Wrangler for the target account, check the four configured routes, run `npm run check:worker`, then `npm run deploy:worker`. Verify the public board, `/play/hanabi/api/health`, an invite and a move from each seat before linking the game in the catalog.

To withdraw the first release, remove its catalog link and four Hanabi routes. Keep the room namespace so existing games remain recoverable through their idle window. For later releases, check stored room format compatibility before restoring the previous Worker version.
