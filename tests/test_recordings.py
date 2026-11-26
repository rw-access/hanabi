from contextlib import closing
import json
from pathlib import Path
import sqlite3
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from hanabi.game import COLORS, COPIES, RANKS, Game
from hanabi.recordings import EMPTY_CLUE_RULES, FORMAT, RULES, RecordingStore, export, summary


class RecordingTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name, "private", "games.sqlite3")
        self.store = RecordingStore(self.path, revision="test-build")
        self.seed = "4f" * 32
        self.game = Game(int(self.seed, 16))

    def create(self, deal="fixture"):
        self.store.create(deal, self.seed, "adaptive", game=self.game)

    def play_pair(self):
        moves = []
        observations = []
        while len(moves) < 2 and not self.game.over:
            seat = self.game.current
            view = self.game.view(seat)
            if self.game.turn == 0:
                action = next(a for a in view["legal"] if a["type"] == "hint")
            else:
                action = {"type": "play", "index": 0}
            observations.append(view)
            moves.append((seat, action))
            self.game.apply(action)
        return moves, observations

    def test_persistent_actor_observations_and_outcomes(self):
        self.create()
        observations = []
        while not self.game.over:
            turn = self.game.turn
            moves, views = self.play_pair()
            observations.extend(views)
            self.store.append("fixture", turn, moves, self.game)
        reopened = RecordingStore(self.path, revision="a-later-build")
        reopened.mark_replaced("fixture")
        result, = export(self.path)
        self.assertEqual(result["format"], FORMAT)
        self.assertEqual((result["teammate"], result["revision"]), ("adaptive", "test-build"))
        self.assertEqual((result["status"], result["ending"], result["score"]), ("complete", "fuse", 0))
        self.assertEqual([m["observation"] for m in result["moves"]], observations)
        self.assertEqual([m["event"] for m in result["moves"]], self.game.view(0)["history"])
        for step in result["moves"]:
            self.assertEqual(step["actor"], "human" if step["seat"] == 0 else "bot")
            self.assertIn(step["action"], step["observation"]["legal"])
            self.assertNotIn(self.seed, json.dumps(step))
            self.assertNotIn("draw_order", step["observation"])
            self.assertTrue(all(set(slot) == {"colors", "ranks"} for slot in step["observation"]["hand"]))
        self.assertEqual(summary(self.path), {"games": 1, "moves": self.game.turn,
                                             "status_counts": {"complete": 1, "partial": 0, "replaced": 0}})

    def test_draw_order_alternates_seats_and_survives_changed_shuffle(self):
        order = self.game.initial_draw_order()
        self.assertEqual(len(order), 50)
        for seat in (0, 1):
            visible = self.game.view(1 - seat)["partner"]
            self.assertEqual(order[seat:10:2], [{"color": c["color"], "rank": c["rank"]} for c in visible])
        self.create()
        first = self.game.view(0)
        self.game.apply({"type": "play", "index": 0})
        drawn = self.game.view(1)["partner"][-1]
        self.assertEqual(order[10], {"color": drawn["color"], "rank": drawn["rank"]})
        self.store.append("fixture", 0, [(0, {"type": "play", "index": 0})], self.game)
        with patch("hanabi.game.Random.shuffle", side_effect=AssertionError("shuffle invoked")):
            replay, = export(self.path)
        self.assertEqual(replay["deal"]["draw_order"], order)
        self.assertEqual(replay["moves"][0]["observation"], first)
        with self.assertRaisesRegex(ValueError, "before play"):
            self.game.initial_draw_order()

    def test_old_and_new_deals_in_one_archive_use_their_original_rules(self):
        self.create("classic")
        game = Game.from_draw_order(self.game.initial_draw_order(), empty_clues=True)
        self.store.create("empty", self.seed, "adaptive", game=game)
        for deal, table in (("classic", self.game), ("empty", game)):
            action = {"type": "play", "index": 0}
            table.apply(action)
            self.store.append(deal, 0, [(0, action)], table)
        classic, empty = list(export(self.path))
        self.assertEqual([classic["rules"], empty["rules"]], [RULES, EMPTY_CLUE_RULES])
        first, second = (replay["moves"][0] for replay in (classic, empty))
        self.assertNotIn("beliefs", first["observation"])
        self.assertEqual(len(second["observation"]["beliefs"]), 5)
        self.assertEqual(first["own_card_targets"], second["own_card_targets"])

    def test_rejects_incomplete_or_malformed_deck(self):
        canonical = [{"color": c, "rank": r} for c in COLORS for r in RANKS for _ in range(COPIES[r])]
        self.assertEqual(Game.from_draw_order(canonical).initial_draw_order(), canonical)
        for corrupt in (canonical[:-1], canonical + canonical[:1], [canonical[-1]] + canonical[1:],
                        [{"color": "red", "rank": True}] + canonical[1:]):
            with self.assertRaises(ValueError):
                Game.from_draw_order(corrupt)

    def test_partial_replacement_and_duplicate_safety(self):
        self.create()
        moves, _ = self.play_pair()
        self.store.append("fixture", 0, moves, self.game)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append("fixture", 0, moves, self.game)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create("fixture", self.seed, "lantern", replaced_id="fixture")
        self.assertEqual(next(export(self.path))["status"], "partial")
        self.store.create("next", self.seed, "lantern", replaced_id="fixture")
        self.assertEqual(summary(self.path)["moves"], 2)
        prior, = export(self.path, "fixture")
        self.assertEqual((prior["status"], prior["turns"], prior["ending"]), ("replaced", 2, None))
        self.assertEqual(next(export(self.path, "next"))["status"], "partial")

    def test_batch_failure_rolls_back_game_and_both_moves(self):
        self.create()
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("""CREATE TRIGGER fail_move BEFORE INSERT ON moves WHEN NEW.turn=2
                          BEGIN SELECT RAISE(ABORT, 'fixture failure'); END""")
        moves, _ = self.play_pair()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.append("fixture", 0, moves, self.game)
        row, = export(self.path)
        self.assertEqual((row["turns"], row["moves"]), (0, []))

    def test_export_snapshot_does_not_block_new_games_or_include_later_games(self):
        self.create("one")
        self.create("two")
        frozen = export(self.path)
        self.assertEqual(next(frozen)["deal_id"], "one")
        self.create("three")
        self.assertEqual([r["deal_id"] for r in frozen], ["two"])
        self.assertEqual(summary(self.path)["games"], 3)

    def test_private_file_schema_and_read_only_tools(self):
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        missing = self.path.with_name("missing")
        with self.assertRaises(sqlite3.OperationalError):
            summary(missing)
        self.assertFalse(missing.exists())
        self.path.chmod(0o644)
        with self.assertRaises(PermissionError):
            RecordingStore(self.path)
        self.path.chmod(0o600)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA user_version = 99")
        with self.assertRaises(sqlite3.DatabaseError):
            RecordingStore(self.path)
        with self.assertRaises(sqlite3.DatabaseError):
            list(export(self.path))

    def test_replay_rejects_drift_or_corruption(self):
        self.create()
        moves, _ = self.play_pair()
        self.store.append("fixture", 0, moves, self.game)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE games SET score=99")
        with self.assertRaisesRegex(ValueError, "result differs"):
            list(export(self.path))

    def test_directory_permissions_and_state_directory_symlink(self):
        private = self.path.parent
        link = private.parent / "state"
        link.symlink_to(private, target_is_directory=True)
        through_link = RecordingStore(link / self.path.name)
        through_link.create("fixture", self.seed, "adaptive")
        self.assertEqual(summary(self.path)["games"], 1)
        for mode in (0o770, 0o707):
            with self.subTest(mode=mode):
                private.chmod(mode)
                with self.assertRaises(PermissionError):
                    RecordingStore(link / "new-database")
                self.assertFalse((private / "new-database").exists())
