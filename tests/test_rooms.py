import json
import unittest
from unittest.mock import patch

from hanabi.game import Game
from hanabi.rooms import Room, RoomError


HOST = "fixture-host-token"
GUEST = "fixture-guest-token"
STRANGER = "fixture-unseated-token"
CODE = "ABCD2345"


class RoomTests(unittest.TestCase):
    def create(self, mode="online"):
        with patch("hanabi.rooms.Game", return_value=Game(31, empty_clues=True)):
            return Room.create(CODE, mode, HOST)

    def rejected(self, status, operation, *args):
        with self.assertRaises(RoomError) as caught:
            operation(*args)
        self.assertEqual(caught.exception.status, status)

    def test_join_reconnect_and_full(self):
        room = self.create()
        waiting = {"code": CODE, "mode": "online", "status": "waiting", "seat": 0, "game": None}
        self.assertEqual(room.join(HOST), waiting)
        before = room.dump()
        self.rejected(403, room.view, GUEST)
        self.rejected(403, room.join, "")
        self.rejected(409, room.act, HOST, "old-deal", 0, {})
        self.assertEqual(room.dump(), before)
        guest = room.join(GUEST)
        self.assertEqual((guest["seat"], guest["status"], guest["game"]["legal"]), (1, "playing", []))
        self.assertEqual(room.join(GUEST), guest)
        self.rejected(409, room.join, STRANGER)
        self.rejected(403, room.view, STRANGER)
        self.assertEqual(room.join(HOST), room.view(HOST))
        self.assertIn("beliefs", room.view(HOST)["game"])
        self.assertTrue(room.view(HOST)["game"]["empty_clues"])

    def test_wire_views_hide_private_fields_and_unknown_identity_for_each_seat(self):
        room = self.create()
        room.join(GUEST)
        saved = room.dump()
        for token in (HOST, GUEST):
            self.assertNotIn(token, json.dumps(saved))
        for seat, token in enumerate((HOST, GUEST)):
            view = room.view(token)
            wire = json.dumps(view)
            for private in ("draw_order", "actions", "seed", "own_card_targets", *saved["seats"]):
                self.assertNotIn(private, wire)
            swapped = room.dump()
            deck = swapped["draw_order"]
            index = next(i for i in range(10, len(deck)) if deck[i] != deck[seat])
            deck[index], deck[seat] = deck[seat], deck[index]
            alternate = Room.restore(swapped)
            self.assertEqual(alternate.view(token), view)
            other = (GUEST, HOST)[seat]
            self.assertNotEqual(alternate.view(other)["game"]["partner"], room.view(other)["game"]["partner"])

    def test_moves_validate_before_mutation_and_stale_retry_applies_only_once(self):
        room = self.create()
        room.join(GUEST)
        initial = room.view(HOST)["game"]
        move = {"type": "hint", "kind": "rank", "value": 3}
        deal_id = initial["id"]
        for status, args in (
            (403, (STRANGER, deal_id, 0, move)),
            (409, (GUEST, deal_id, 0, move)),
            (409, (HOST, "another-deal", 0, move)),
            (400, (HOST, deal_id, False, move)),
            (422, (HOST, deal_id, 0, {"type": "play", "index": True})),
        ):
            self.rejected(status, room.act, *args)
            self.assertEqual(room.view(HOST)["game"], initial)
        accepted = room.act(HOST, deal_id, 0, move)["game"]
        self.assertEqual((accepted["turn"], accepted["current"], accepted["legal"]), (1, 1, []))
        self.rejected(409, room.act, HOST, deal_id, 0, move)
        self.assertEqual(room.view(HOST)["game"], accepted)
        self.assertEqual(room.view(GUEST)["game"]["history"], accepted["history"])

    def test_persist_and_restore_both_seats_through_end_of_game(self):
        room = self.create()
        room.join(GUEST)
        while not room.view(HOST)["game"]["over"]:
            current = room.view(HOST)["game"]
            token = (HOST, GUEST)[current["current"]]
            room.act(token, current["id"], current["turn"], {"type": "play", "index": 0})
            restored = Room.restore(json.loads(json.dumps(room.dump())))
            for player in (HOST, GUEST):
                self.assertEqual(restored.view(player), room.view(player))
            room = restored
        final = room.view(HOST)
        self.assertEqual(final["status"], "finished")
        self.rejected(409, room.act, HOST, final["game"]["id"], final["game"]["turn"], {})

    def test_solo_adaptive_reply_is_part_of_same_position_and_survives_restore(self):
        room = self.create("solo")
        first = room.view(HOST)["game"]
        self.rejected(409, room.join, GUEST)
        move = {"type": "hint", "kind": "rank", "value": 2}
        result = room.act(HOST, first["id"], 0, move)
        self.assertEqual((result["game"]["turn"], result["game"]["current"]), (2, 0))
        self.assertEqual([entry["player"] for entry in result["game"]["history"]], [0, 1])
        with patch("hanabi.rooms.choose", side_effect=AssertionError("Restore must use the saved reply")):
            self.assertEqual(Room.restore(room.dump()).view(HOST), result)
