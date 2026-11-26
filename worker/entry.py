"""Cloudflare entry point and the private, two-seat room Durable Object."""
import json
import re
import secrets
import time
from urllib.parse import urlsplit, urlunsplit

from js import WebSocketPair
from workers import DurableObject, Request, Response, WorkerEntrypoint

from hanabi.rooms import Room, RoomError


BASE = "/play/hanabi"
API = f"{BASE}/api"
ROOMS = f"{API}/rooms"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_PATH = re.compile(rf"{ROOMS}/([{ALPHABET}]{{8}})(?:/(join|move|socket))?")
MAX_BODY = 4096
TTL_SECONDS = 24 * 60 * 60
COOKIE = "hanabi_seat"


def reply(data, status=200, *, credential=None):
    headers = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
    if credential:
        code, token = credential
        headers["Set-Cookie"] = (
            f"{COOKIE}={token}; Path={ROOMS}/{code}; Max-Age={TTL_SECONDS}; "
            "Secure; HttpOnly; SameSite=Strict"
        )
    return Response.from_json(data, status=status, headers=headers)


def error(message, status):
    return reply({"error": message}, status)


def seat_cookie(request):
    for item in (request.headers.get("Cookie") or "").split(";"):
        key, sep, value = item.strip().partition("=")
        if key == COOKIE and sep:
            return value
    return None


async def read_body(request):
    if (request.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
        raise ValueError("Send a JSON object.")
    # Read a bounded stream even if the request has no Content-Length.
    raw = getattr(request, "js_object", request)
    if not raw.body:
        raise ValueError("Send a JSON object.")
    reader = raw.body.getReader()
    data = bytearray()
    while True:
        part = await reader.read()
        if part.done:
            break
        if len(data) + part.value.byteLength > MAX_BODY:
            await reader.cancel()
            raise ValueError("Room request is too large.")
        data.extend(part.value.to_bytes())
    try:
        body = json.loads(data)
    except (ValueError, RecursionError):
        raise ValueError("Send a JSON object.") from None
    if not isinstance(body, dict):
        raise ValueError("Send a JSON object.")
    return body


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlsplit(request.url)
        path = url.path
        if path != BASE and not path.startswith(BASE + "/"):
            return error("Not found.", 404)
        if path == BASE or url.hostname == "www.rw-access.dev":
            canonical = url._replace(path=BASE + "/" if path == BASE else path)
            if url.hostname == "www.rw-access.dev":
                canonical = canonical._replace(scheme="https", netloc="rw-access.dev")
            return Response.redirect(urlunsplit(canonical), 308)
        if not path.startswith(API + "/"):
            return await self.env.ASSETS.fetch(request)
        if request.method == "POST" or path.endswith("/socket"):
            if request.headers.get("Origin") != f"{url.scheme}://{url.netloc}":
                return error("Use the same game address to access this room.", 403)
        if path == f"{API}/health" and request.method == "GET":
            return reply({"ok": True})
        if path == ROOMS and request.method == "POST":
            try:
                body = await read_body(request)
            except ValueError as exc:
                return error(str(exc), 400)
            if set(body) != {"mode"} or body["mode"] not in ("online", "solo"):
                return error("Choose online or solo play.", 400)
            for _ in range(8):
                code = "".join(secrets.choice(ALPHABET) for _ in range(8))
                stub = self.env.ROOMS.getByName(code)
                creation = Request(
                    f"https://room.internal/{code}/create",
                    method="POST", headers={"Content-Type": "application/json"}, body=json.dumps(body),
                )
                response = await stub.fetch(creation)
                if response.status != 409:
                    return response
            return error("Unable to allocate a room. Try again.", 503)
        match = ROOM_PATH.fullmatch(path)
        if not match:
            return error("Not found.", 404)
        code, operation = match.groups()
        if request.method != ("POST" if operation in ("join", "move") else "GET"):
            return error("Method not allowed.", 405)
        return await self.env.ROOMS.getByName(code).fetch(request)


class GameRoom(DurableObject):
    """Room reads, transitions and KV writes happen in one synchronous turn."""

    async def fetch(self, request):
        url = urlsplit(request.url)
        creating = url.hostname == "room.internal" and url.path.endswith("/create")
        if creating:
            code, operation = url.path[1:].split("/")
        else:
            match = ROOM_PATH.fullmatch(url.path)
            if not match:
                return error("Not found.", 404)
            code, operation = match.groups()
        body = {}
        if request.method == "POST":
            try:
                body = await read_body(request)
            except ValueError as exc:
                return error(str(exc), 400)
            if operation == "join" and body:
                return error("Invalid join request.", 400)
        storage = self.ctx.storage
        now = int(time.time() * 1000)
        saved = storage.kv.get("room")
        token = seat_cookie(request)
        changed = False
        try:
            if creating:
                if saved:
                    return error("Room code in use.", 409)
                token = secrets.token_urlsafe(32)
                room = Room.create(code, body["mode"], token)
                snapshot = room.view(token)
                changed = True
            else:
                if not saved:
                    return error("Room not found or expired.", 404)
                record = json.loads(saved)
                if record["expires"] <= now:
                    return error("Room not found or expired.", 404)
                room = Room.restore(record["room"])
                if operation == "join" and token is None:
                    token = secrets.token_urlsafe(32)
                    snapshot = room.join(token)
                    changed = True
                elif operation == "move":
                    if set(body) != {"id", "turn", "action"}:
                        return error("Invalid move request.", 400)
                    snapshot = room.act(token, body["id"], body["turn"], body["action"])
                    changed = True
                else:
                    snapshot = room.view(token)
        except RoomError as exc:
            return error(str(exc), exc.status)
        if operation == "socket" and (request.headers.get("Upgrade") or "").lower() != "websocket":
            return error("WebSocket upgrade required.", 426)
        expires = now + TTL_SECONDS * 1000
        # Authenticated reads renew idle sessions too. Coalesce renewal writes when
        # a client fetches another snapshot immediately after its own move.
        if changed or expires - record["expires"] >= 60_000:
            storage.kv.put("room", json.dumps({"expires": expires, "room": room.dump()}, separators=(",", ":")))
            await storage.setAlarm(expires)
        if changed:
            for socket in self.ctx.getWebSockets():
                try:
                    socket.send('{"type":"changed"}')
                except Exception:
                    # A disconnect can race the committed move. The returning
                    # client always obtains a fresh authenticated snapshot.
                    continue
        if operation == "socket":
            client, server = WebSocketPair.new().object_values()
            self.ctx.acceptWebSocket(server)
            # Cover a change between the browser's last GET and its subscription.
            server.send('{"type":"changed"}')
            return Response(None, status=101, web_socket=client)
        return reply(snapshot, 201 if creating else 200, credential=(code, token))

    async def webSocketMessage(self, socket, message):
        socket.close(1008, "Submit moves over HTTP.")

    async def webSocketClose(self, socket, code, reason, was_clean):
        socket.close()

    async def alarm(self):
        storage = self.ctx.storage
        saved = storage.kv.get("room")
        if saved:
            expires = json.loads(saved)["expires"]
            if expires > int(time.time() * 1000):
                await storage.setAlarm(expires)
                return
        for socket in self.ctx.getWebSockets():
            socket.close(1000, "Room expired")
        await storage.deleteAll()
