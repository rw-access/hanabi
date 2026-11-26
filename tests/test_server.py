import concurrent.futures
import http.client
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

from hanabi.bots import choose
from hanabi.recordings import EMPTY_CLUE_RULES, export
from hanabi.server import LocalTable


class ApiTests(unittest.TestCase):
    server_options = {}

    @classmethod
    def setUpClass(cls):
        cls.server = LocalTable(0, **cls.server_options)
        cls.port = cls.server.server_port
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def call(self, method, path, data=None, cookie=None, headers=None, full_cookie=False):
        hdr = {"Content-Type": "application/json"}
        if cookie:
            hdr["Cookie"] = cookie
        hdr.update(headers or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        if data is not None and not isinstance(data, bytes):
            data = json.dumps(data)
        conn.request(method, path, data, headers=hdr)
        r = conn.getresponse()
        status, sc, content = r.status, r.getheader("Set-Cookie"), r.read()
        conn.close()
        if r.getheader("Content-Type", "").startswith("application/json"):
            content = json.loads(content)
        return status, sc if full_cookie else (sc or "").partition(";")[0], content


class LocalApiTests(ApiTests):
    def new(self):
        status, cookie, data = self.call("POST", "/api/new", {})
        self.assertEqual(status, 200)
        self.assertTrue(cookie)
        return cookie, data["game"]

    def test_session_isolation_and_refresh(self):
        self.assertEqual(self.call("GET", "/api/game")[2], {"game": None})
        a, v = self.new()
        b, w = self.new()
        self.assertNotEqual(v["id"], w["id"])
        self.assertEqual(self.call("GET", "/api/game", cookie=a)[2]["game"], v)
        self.assertEqual(self.call("GET", "/api/game", cookie=b)[2]["game"], w)
        self.assertEqual(self.call("GET", "/api/game?seat=1", cookie=a)[0], 404)
        self.assertEqual(self.call("POST", "/api/action", {}, cookie="bogus")[0], 404)

    def test_hidden_information_and_double_submit(self):
        cookie, v = self.new()
        for k in v:
            self.assertNotIn("seed", k)
            self.assertNotIn("deck", k)
        for slot in v["hand"]:
            self.assertEqual(set(slot), {"colors", "ranks"})
        hint = next(a for a in v["legal"] if a["type"] == "hint")
        payload = {"id": v["id"], "turn": 0, "action": hint}
        # Simulate double-click or two tabs: exactly one pair of actions commits.
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            statuses = list(pool.map(lambda _: self.call("POST", "/api/action", payload, cookie)[0], range(2)))
        self.assertEqual(sorted(statuses), [200, 409])
        state = self.call("GET", "/api/game", cookie=cookie)[2]["game"]
        self.assertEqual((state["turn"], state["current"], len(state["history"])), (2, 0, 2))
        self.assertEqual(state["history"][0]["matched"],
                         [i for i, c in enumerate(v["partner"]) if c[hint["kind"]] == hint["value"]])

    def test_invalid_move_is_rejected_and_state_unchanged(self):
        cookie, v = self.new()
        garbage = {"action": {"type": "play", "index": -1}, "id": v["id"], "turn": 0}
        self.assertEqual(self.call("POST", "/api/action", garbage, cookie)[0], 422)
        self.assertEqual(self.call("GET", "/api/game", cookie=cookie)[2]["game"], v)
        self.assertEqual(self.call("POST", "/api/action", {**garbage, "turn": False}, cookie)[0], 400)
        self.assertEqual(self.call("POST", "/api/new", {"seed": 42})[0], 400)

    def test_a_game_can_finish_via_the_browser_api(self):
        # Blindly playing never blocks on clues; the outcome may legitimately vary.
        cookie, v = self.new()
        while not v["over"]:
            self.assertLess(v["turn"], 200)
            self.assertEqual(v["current"], 0)
            payload = {"id": v["id"], "turn": v["turn"], "action": {"type": "play", "index": 0}}
            status, _, result = self.call("POST", "/api/action", payload, cookie)
            self.assertEqual(status, 200)
            v = result["game"]
        self.assertEqual(v["legal"], [])
        self.assertIn(v["end"], ["fuse", "deck", "perfect"])
        self.assertEqual(v["score"], 0 if v["end"] == "fuse" else sum(v["fireworks"].values()))
        payload = {"id": v["id"], "turn": v["turn"], "action": {"type": "play", "index": 0}}
        self.assertIn(self.call("POST", "/api/action", payload, cookie)[0], [409, 422])
        self.assertEqual(self.call("GET", "/api/game", cookie=cookie)[2]["game"], v)

    def test_previous_deal_cannot_act_on_new_deal_at_same_turn(self):
        cookie, old = self.new()
        st, newcookie, new = self.call("POST", "/api/new", {}, cookie)
        self.assertEqual(st, 200)
        self.assertNotEqual(old["id"], new["game"]["id"])
        self.assertEqual(self.call("GET", "/api/game", cookie=cookie)[2], {"game": None})
        stale = {"action": old["legal"][0], "turn": 0, "id": old["id"]}
        self.assertEqual(self.call("POST", "/api/action", stale, newcookie)[0], 409)

    def test_local_only_and_request_boundaries(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.call("GET", "/api/game", headers={"Host": "untrusted.invalid"})[0], 403)
        self.assertEqual(self.call("POST", "/api/new", {}, headers={"Origin": "http://elsewhere.invalid"})[0], 403)
        self.assertEqual(self.call("POST", "/api/new", {}, headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.assertEqual(self.call("POST", "/api/new", b"[1]")[0], 400)
        self.assertEqual(self.call("POST", "/api/new", b"x" * 2100)[0], 413)
        self.assertEqual(self.call("POST", "/api/new", {}, headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("GET", "/hanabi/game.py")[0], 404)
        self.assertEqual(self.call("POST", "/api/new", {"record": True})[0], 400)


class PreviewApiTests(ApiTests):
    origin = "https://preview.example"
    server_options = {"public_url": origin + "/hanabi/", "teammate": "adaptive"}
    headers = {"Host": "preview.example", "Origin": origin}

    def test_request_authority_and_origin_are_exact(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        self.assertEqual(self.call("GET", "/", headers={"X-Forwarded-Host": "preview.example"})[0], 403)
        for fields in ({"Host": "preview.example"}, {**self.headers, "Origin": "null"},
                       {**self.headers, "Origin": "http://preview.example"},
                       {**self.headers, "Origin": "https://elsewhere.example"},
                       {**self.headers, "Sec-Fetch-Site": "cross-site"}):
            with self.subTest(fields=fields):
                self.assertEqual(self.call("POST", "/api/new", {}, headers=fields)[0], 403)

    def test_preview_html_cookie_and_configured_teammate(self):
        status, _, html = self.call("GET", "/", headers=self.headers)
        self.assertEqual(status, 200)
        self.assertIn(b'<span id="teammate-name">Adaptive</span>', html)
        self.assertIn(b'After either player misplays', html)
        self.assertIn(b'src="app.js"', html)
        self.assertNotIn(b'{{', html)
        status, header, body = self.call("POST", "/api/new", {}, headers=self.headers, full_cookie=True)
        self.assertEqual(status, 200)
        cookie, *flags = header.split("; ")
        self.assertTrue(cookie.startswith("__Secure-hanabi_table="))
        self.assertEqual(set(flags), {"HttpOnly", "SameSite=Strict", "Path=/hanabi/", "Max-Age=86400", "Secure"})
        game = body["game"]
        payload = {"id": game["id"], "turn": 0, "action": next(a for a in game["legal"] if a["type"] == "hint")}
        with patch("hanabi.server.choose", wraps=choose) as bot:
            status, _, updated = self.call("POST", "/api/action", payload, cookie, self.headers)
        self.assertEqual((status, updated["game"]["turn"]), (200, 2))
        self.assertEqual(bot.call_args.args[1], "adaptive")
        self.assertEqual(self.call("GET", "/api/game", cookie=cookie, headers=self.headers)[2], updated)

    def test_invalid_public_urls(self):
        for url in ("http://preview.example/hanabi/", "https://preview.example/hanabi",
                    "https://preview.example/../", "https://preview.example:65536/hanabi/",
                    "https://name@preview.example/", "https://preview.example/;Secure/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                LocalTable(0, public_url=url)


class RecordingApiTests(ApiTests):
    @classmethod
    def setUpClass(cls):
        directory = TemporaryDirectory()
        cls.addClassCleanup(directory.cleanup)
        cls.db = Path(directory.name) / "replays.sqlite3"
        cls.server_options = {"record_db": cls.db, "revision": "test-build", "teammate": "adaptive"}
        super().setUpClass()

    def deal(self, data, cookie=None):
        status, new_cookie, body = self.call("POST", "/api/new", data, cookie)
        self.assertEqual(status, 200)
        return new_cookie, body["game"]

    def test_empty_clue_and_pre_move_training_targets_round_trip(self):
        with patch("hanabi.server.secrets.token_hex", return_value="2a".zfill(64)):
            cookie, initial = self.deal({"record": True})
        hint = {"type": "hint", "kind": "rank", "value": 1}
        self.assertNotIn(1, [c["rank"] for c in initial["partner"]])
        self.assertIn(hint, initial["legal"])
        status, _, body = self.call("POST", "/api/action", {
            "id": initial["id"], "turn": initial["turn"], "action": hint,
        }, cookie)
        self.assertEqual(status, 200)
        self.assertEqual(body["game"]["history"][0]["matched"], [])
        self.assertEqual(len(body["game"]["beliefs"]), len(initial["hand"]))
        self.assertNotIn("own_card_targets", json.dumps(body))
        replay, = export(self.db, initial["id"])
        first, reply = replay["moves"]
        self.assertEqual(replay["rules"], EMPTY_CLUE_RULES)
        self.assertEqual(first["own_card_targets"], replay["deal"]["draw_order"][:10:2])
        self.assertEqual(reply["own_card_targets"], replay["deal"]["draw_order"][1:10:2])
        self.assertNotIn("own_card_targets", first["observation"])
        self.assertEqual(first["observation"]["beliefs"], initial["beliefs"])
        self.assertTrue(all(all(i % 5 != 0 for i in slot["possible"])
                            for slot in reply["observation"]["beliefs"]))

    def test_records_accepted_moves_through_terminal_and_matching_observations(self):
        cookie, game = self.deal({"record": True})
        self.assertTrue(game["recording"])
        observations = []
        while not game["over"]:
            observations.append({k: v for k, v in game.items() if k not in {"id", "recording"}})
            status, _, body = self.call("POST", "/api/action", {
                "id": game["id"], "turn": game["turn"], "action": {"type": "play", "index": 0},
            }, cookie)
            self.assertEqual(status, 200)
            game = body["game"]
        replay, = export(self.db, game["id"])
        self.assertEqual((replay["status"], replay["ending"], replay["turns"], replay["score"]),
                         ("complete", game["end"], game["turn"], game["score"]))
        self.assertEqual((replay["revision"], replay["teammate"]), ("test-build", "adaptive"))
        self.assertEqual([m["event"] for m in replay["moves"]], game["history"])
        self.assertEqual([m["observation"] for m in replay["moves"] if m["seat"] == 0], observations)
        self.assertNotIn(replay["deal"]["seed_hex"], json.dumps(game))
        self.assertNotIn(cookie.split("=", 1)[1], json.dumps(replay))
        for path in ("/replays.sqlite3", "/api/replays", "/api/replay/" + game["id"]):
            self.assertEqual(self.call("GET", path, cookie=cookie)[0], 404)

    def test_only_explicit_true_records_and_redeal_retains_partial(self):
        for invalid in ({"record": 1}, {"record": None}, {"record": True, "seed": 7}):
            self.assertEqual(self.call("POST", "/api/new", invalid)[0], 400)
        for private in ({}, {"record": False}):
            cookie, recorded = self.deal({"record": True})
            _, game = self.deal(private, cookie)
            self.assertFalse(game["recording"])
            self.assertEqual(list(export(self.db, game["id"])), [])
            prior, = export(self.db, recorded["id"])
            self.assertEqual(prior["status"], "replaced")

    def test_double_submit_records_exactly_one_pair(self):
        cookie, game = self.deal({"record": True})
        payload = {"id": game["id"], "turn": 0, "action": next(a for a in game["legal"] if a["type"] == "hint")}
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            result = list(pool.map(lambda _: self.call("POST", "/api/action", payload, cookie)[0], range(2)))
        self.assertEqual(sorted(result), [200, 409])
        self.assertEqual(self.call("POST", "/api/action", {**payload, "turn": 2, "action": {}}, cookie)[0], 422)
        replay, = export(self.db, game["id"])
        self.assertEqual((replay["status"], replay["turns"]), ("partial", 2))

    def test_storage_failure_preserves_the_accepted_position_and_cookie(self):
        cookie, game = self.deal({"record": True})
        failure = sqlite3.OperationalError("simulated storage failure")
        payload = {"id": game["id"], "turn": 0, "action": game["legal"][0]}
        for method, path, data in (("append", "/api/action", payload),
                                   ("create", "/api/new", {"record": True}),
                                   ("mark_replaced", "/api/new", {"record": False})):
            with self.subTest(method=method), patch.object(self.server.recordings, method, side_effect=failure):
                status, new_cookie, _ = self.call("POST", path, data, cookie)
                self.assertEqual((status, new_cookie), (503, ""))
                self.assertEqual(self.call("GET", "/api/game", cookie=cookie)[2]["game"], game)
                replay, = export(self.db, game["id"])
                self.assertEqual((replay["turns"], replay["status"]), (0, "partial"))
        self.assertEqual(self.call("POST", "/api/action", payload, cookie)[0], 200)
