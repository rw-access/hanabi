"""Durable two-seat tables. Only authenticated views cross the HTTP boundary."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import secrets

from .bots import choose
from .game import Game, IllegalMove


class RoomError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _token_hash(token):
    if not isinstance(token, str) or not 16 <= len(token) <= 256 or not token.isascii():
        raise RoomError(403, "A valid seat credential is required.")
    return sha256(token.encode("ascii")).hexdigest()


class Room:
    """A private record and its reconstituted game, owned by one room transaction."""

    def __init__(self, record, game):
        self._record = record
        self._game = game

    @classmethod
    def create(cls, code, mode, token):
        if mode not in ("solo", "online"):
            raise RoomError(400, "Unknown table mode.")
        digest = _token_hash(token)
        game = Game(empty_clues=True)
        return cls({
            "format": 1,
            "code": code,
            "mode": mode,
            "deal_id": secrets.token_urlsafe(12),
            "seats": [digest, None],
            "draw_order": game.initial_draw_order(),
            "actions": [],
        }, game)

    @classmethod
    def restore(cls, record):
        if record.get("format") != 1:
            raise ValueError("Unsupported stored room version")
        record = deepcopy(record)
        game = Game.from_draw_order(record["draw_order"], empty_clues=True)
        for action in record["actions"]:
            game.apply(action)
        return cls(record, game)

    def dump(self):
        """Return private versioned storage data, including both seats' hidden cards."""
        return deepcopy(self._record)

    def _seat(self, digest):
        for seat, stored in enumerate(self._record["seats"]):
            if stored is not None and secrets.compare_digest(stored, digest):
                return seat
        return None

    def _authenticated_seat(self, token):
        seat = self._seat(_token_hash(token))
        if seat is None:
            raise RoomError(403, "This credential has no seat at this table.")
        return seat

    @property
    def _waiting(self):
        return self._record["mode"] == "online" and self._record["seats"][1] is None

    def join(self, token):
        digest = _token_hash(token)
        seat = self._seat(digest)
        if seat is None:
            if not self._waiting:
                raise RoomError(409, "Both seats at this table are occupied.")
            seat = 1
            self._record["seats"][seat] = digest
        return self._public(seat)

    def _public(self, seat):
        game = None
        status = "waiting"
        if not self._waiting:
            status = "finished" if self._game.over else "playing"
            game = dict(self._game.view(seat), id=self._record["deal_id"], recording=False)
        return {"code": self._record["code"], "mode": self._record["mode"],
                "seat": seat, "status": status, "game": game}

    def view(self, token):
        return self._public(self._authenticated_seat(token))

    def act(self, token, deal_id, turn, action):
        seat = self._authenticated_seat(token)
        if not isinstance(deal_id, str) or type(turn) is not int:
            raise RoomError(400, "The deal ID and turn are required.")
        if self._waiting:
            raise RoomError(409, "Waiting for your teammate to join.")
        game = self._game
        if deal_id != self._record["deal_id"] or turn != game.turn:
            raise RoomError(409, "This position has changed. Refresh the table.")
        if game.over or seat != game.current:
            raise RoomError(409, "It is not your turn.")
        try:
            game.apply(action)
        except IllegalMove as error:
            raise RoomError(422, str(error)) from error
        self._record["actions"].append(dict(action))
        if self._record["mode"] == "solo" and not game.over:
            reply = choose(game.view(1), "adaptive")
            game.apply(reply)
            self._record["actions"].append(reply)
        return self._public(seat)
