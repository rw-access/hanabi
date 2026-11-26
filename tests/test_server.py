import concurrent.futures
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from hanabi.bots import choose
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
