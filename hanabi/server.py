"""Serve an in-memory human + bot table on loopback, optionally behind an HTTPS proxy."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .bots import choose
from .game import Game, IllegalMove
from .recordings import RecordingStore

STATIC = Path(__file__).parent / "static"
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/styles.css": ("styles.css", "text/css; charset=utf-8")}
COOKIE = "hanabi_table"
TTL = 24 * 3600
TEAMMATES = {
    "lantern": ("Lantern", "Lantern reads each clue literally."),
    "adaptive": ("Adaptive", "Adaptive starts with the group’s conventions. A number clue touching "
                 "the oldest unclued card asks you to save it. Other clues point to the newest "
                 "matching card as a play. After either player misplays, Adaptive reads clues literally."),
}


@dataclass
class Session:
    game: Game
    deal_id: str
    touched: float
    recording: bool = False

    def public(self):
        return {"game": dict(self.game.view(0), id=self.deal_id, recording=self.recording)}


class LocalTable(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port=8765, *, public_url=None, teammate="lantern", record_db=None, revision="local"):
        if teammate not in TEAMMATES:
            raise ValueError("Unknown teammate")
        self.teammate = teammate
        self.origin = None
        self.cookie_path = "/"
        self.cookie_name = COOKIE
        if public_url is not None:
            # Only canonical HTTPS URLs with a directory path are supported. These parts
            # also become the exact Host/Origin checks and the Set-Cookie path.
            if not re.fullmatch(r"https://[a-z0-9.-]+(?::[0-9]+)?/(?:[a-zA-Z0-9_-]+/)*", public_url):
                raise ValueError("Use a canonical HTTPS URL with a trailing slash for --public-url")
            public = urlsplit(public_url)
            if public.port in (0, 443):
                raise ValueError("Use a valid port or omit the default HTTPS port")
            self.origin = f"https://{public.netloc}"
            self.cookie_path = public.path
            self.cookie_name = f"__Secure-{COOKIE}"
        self.sessions: dict[str, Session] = {}
        self.lock = threading.Lock()
        self.recordings = RecordingStore(record_db, revision=revision) if record_db is not None else None
        super().__init__(("127.0.0.1", port), Handler)
        port = self.server_port
        self.trusted_hosts = ({public.netloc} if self.origin else
                              {f"localhost:{port}", f"127.0.0.1:{port}"})
        if not self.origin and port == 80:
            self.trusted_hosts.update(("localhost", "127.0.0.1"))

    def get_request(self):
        sock, addr = super().get_request()
        sock.settimeout(5)
        return sock, addr


class Handler(BaseHTTPRequestHandler):
    server_version = "Lantern/1"
    sys_version = ""

    def _send(self, content, status=200, mime="application/json; charset=utf-8", cookie=None):
        body = content if isinstance(content, bytes) else json.dumps(content, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; "
                         "base-uri 'none'; form-action 'none'; object-src 'none'")
        if cookie is not None:
            secure = "; Secure" if self.server.origin else ""
            self.send_header("Set-Cookie", f"{self.server.cookie_name}={cookie}; HttpOnly; SameSite=Strict; "
                             f"Path={self.server.cookie_path}; Max-Age={TTL}{secure}")
        self.end_headers()
        self.wfile.write(body)

    def _trusted(self, write=False):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0] not in self.server.trusted_hosts:
            self._send({"error": "Use the table address printed at startup."}, 403)
            return False
        if write:
            expected = self.server.origin or f"http://{hosts[0]}"
            origins = self.headers.get_all("Origin", [] if self.server.origin else [expected])
            if origins != [expected] or "cross-site" in self.headers.get_all("Sec-Fetch-Site", []):
                self._send({"error": "This table accepts same-origin requests only."}, 403)
                return False
        return True

    def _key(self):
        jar = SimpleCookie()
        try:
            jar.load(self.headers.get("Cookie", ""))
        except CookieError:
            return ""
        key = self.server.cookie_name
        return jar[key].value if key in jar else ""

    def _session(self, now):
        sessions = self.server.sessions
        for stale in [k for k, s in sessions.items() if now - s.touched >= TTL]:
            del sessions[stale]
        session = sessions.get(self._key())
        if session is not None:
            session.touched = now
        return session

    def do_GET(self):
        if not self._trusted():
            return
        if self.path in ASSETS:
            name, mime = ASSETS[self.path]
            content = (STATIC / name).read_bytes()
            if self.path == "/":
                teammate, rules = TEAMMATES[self.server.teammate]
                content = content.replace(b"{{teammate}}", teammate.encode()).replace(
                    b"{{teammate_rules}}", rules.encode()).replace(
                    b"{{recording_hidden}}", b"hidden" if self.server.recordings is None else b"")
            return self._send(content, mime=mime)
        if self.path == "/api/game":
            with self.server.lock:
                session = self._session(time.monotonic())
                result = session.public() if session else {"game": None}
            return self._send(result)
        self._send({"error": "Not found."}, 404)

    def do_POST(self):
        if not self._trusted(write=True):
            return
        if self.path not in ("/api/new", "/api/action"):
            return self._send({"error": "Not found."}, 404)
        if self.headers.get_content_type() != "application/json":
            return self._send({"error": "Send JSON."}, 415)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2048:
                return self._send({"error": "Invalid request size."}, 413)
            data = json.loads(self.rfile.read(size))
            if type(data) is not dict:
                raise ValueError
        except (ValueError, RecursionError):
            return self._send({"error": "Invalid JSON object."}, 400)

        try:
            with self.server.lock:
                now = time.monotonic()
                session = self._session(now)
                if self.path == "/api/new":
                    result, status, cookie = self._deal(data, session, now)
                else:
                    result, status = self._act(data, session)
                    cookie = None
        except (sqlite3.Error, OSError):
            return self._send({"error": "Replay storage is unavailable. Try again when storage is ready."}, 503)
        self._send(result, status, cookie=cookie)

    def _deal(self, data, previous, now):
        record = data.get("record", False)
        if set(data) - {"record"} or type(record) is not bool:
            return {"error": "Invalid new deal request."}, 400, None
        store = self.server.recordings
        if record and store is None:
            return {"error": "Replay storage is disabled on this table."}, 400, None
        if previous is None and len(self.server.sessions) >= 128:
            return {"error": "Too many active tables. Try again later."}, 503, None

        deal_id = secrets.token_urlsafe(12)
        replaced = previous.deal_id if previous and previous.recording else None
        if record:
            seed = secrets.token_hex(32)
            game = Game(seed=int(seed, 16))
            store.create(deal_id, seed, self.server.teammate, replaced_id=replaced, game=game)
        else:
            game = Game()
            if replaced is not None:
                store.mark_replaced(replaced)
        session = Session(game, deal_id, now, recording=record)
        cookie = secrets.token_urlsafe(32)
        if previous is not None:
            del self.server.sessions[self._key()]
        self.server.sessions[cookie] = session
        return session.public(), 200, cookie

    def _act(self, data, session):
        if session is None:
            return {"error": "No active deal. Start another game."}, 404
        if set(data) != {"action", "id", "turn"} or type(data["turn"]) is not int:
            return {"error": "Invalid move request."}, 400
        if data["id"] != session.deal_id or data["turn"] != session.game.turn or session.game.current != 0:
            return {"error": "This position has changed. Refresh the table."}, 409

        # Publish recorded positions to the browser only after both moves have committed.
        game = deepcopy(session.game) if session.recording else session.game
        try:
            game.apply(data["action"])
        except IllegalMove as error:
            return {"error": str(error)}, 422
        moves = [(0, data["action"])]
        if not game.over:
            bot_move = choose(game.view(1), self.server.teammate)
            game.apply(bot_move)
            moves.append((1, bot_move))
        if session.recording:
            self.server.recordings.append(session.deal_id, data["turn"], moves, game)
            session.game = game
        return session.public(), 200

    # Local logs deliberately omit paths, cookies, and request bodies.
    def log_request(self, code="-", size="-"):
        pass


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--public-url", help="canonical HTTPS table URL when behind a prefix-stripping proxy")
    p.add_argument("--teammate", choices=TEAMMATES, default="lantern")
    p.add_argument("--record-db", type=Path, help="private local replay database for browser-selected deals")
    p.add_argument("--revision", default="local", help="application revision to include in recorded deals")
    args = p.parse_args()
    if not 1 <= args.port <= 65535:
        p.error("Port must be between 1 and 65535")
    try:
        table = LocalTable(args.port, public_url=args.public_url, teammate=args.teammate,
                           record_db=args.record_db, revision=args.revision)
    except ValueError as error:
        p.error(str(error))
    with table as server:
        url = args.public_url or f"http://127.0.0.1:{server.server_port}/"
        print(f"{TEAMMATES[args.teammate][0]} • Hanabi at {url} (Ctrl-C to stop)", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
