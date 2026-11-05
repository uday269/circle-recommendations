"""Scenario + invariant tests. Run: python -m unittest -v"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from app import db
from app.server import make_handler
from app.service import BadRequest, NotFound, get_recommendations

FULL, INACTIVE, LEVEL_UP = 20, 21, 22
NEGOTIATION_DUPES = {10, 11, 12}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = db.build_database(":memory:")

    def recs(self, user_id, **kw):
        return get_recommendations(self.conn, user_id, debug=True, **kw)

    def ids(self, user_id, **kw):
        return [r["circle"]["id"] for r in self.recs(user_id, **kw)["recommendations"]]


class PlantedScenarios(Base):
    def test_cold_start_user_42(self):
        body = self.recs(42)
        self.assertEqual(body["personalization"], "cold_start")
        self.assertEqual(len(body["recommendations"]), 5)
        self.assertIn("next_step", body)  # nudge to add interests
        topics = [r["circle"]["topic"]["slug"] for r in body["recommendations"]]
        self.assertEqual(len(topics), len(set(topics)), "cold start should show a spread of topics")
        for r in body["recommendations"]:
            self.assertNotIn(r["circle"]["topic"]["slug"],
                             {"working-motherhood", "career-breaks", "being-the-only-one"},
                             "no identity Circles for a member we know nothing about")

    def test_obvious_matches_user_7(self):
        ids = self.ids(7)
        self.assertEqual(set(ids[:2]), {5, 6}, "Women Who Lead + Speak Up Circle should lead")
        first = self.recs(7)["recommendations"][0]
        self.assertIn("one of your interests", first["explanation"])

    def test_duplicates_user_15(self):
        body = self.recs(15)
        ids = [r["circle"]["id"] for r in body["recommendations"]]
        shown = NEGOTIATION_DUPES & set(ids)
        self.assertEqual(len(shown), 1, f"exactly one negotiation Circle expected, got {shown}")
        self.assertEqual(ids[0], shown.pop(), "the negotiation pick should rank first")
        alternatives = {c["id"] for c in body["recommendations"][0]["similar_circles"]}
        self.assertEqual(alternatives, NEGOTIATION_DUPES - {ids[0]})

    def test_full_and_inactive_user_23(self):
        body = self.recs(23)
        ids = [r["circle"]["id"] for r in body["recommendations"]]
        self.assertEqual(ids[0], LEVEL_UP)
        self.assertNotIn(FULL, ids)
        self.assertNotIn(INACTIVE, ids)
        excluded = {e["id"]: e["reasons"] for e in body["debug"]["excluded"]}
        self.assertIn("full", excluded[FULL])
        self.assertIn("inactive", excluded[INACTIVE])


class Invariants(Base):
    def test_every_user_gets_sensible_list(self):
        for uid in range(1, 101):
            with self.subTest(user=uid):
                body = self.recs(uid)
                recs = body["recommendations"]
                self.assertEqual(len(recs), 5)
                excluded = {e["id"] for e in body["debug"]["excluded"]}
                for r in recs:
                    self.assertNotIn(r["circle"]["id"], excluded)
                    self.assertTrue(r["explanation"].strip())
                scores = [r["score"]["total"] for r in recs]
                topics = [r["circle"]["topic"]["slug"] for r in recs]
                self.assertEqual(len(set(topics)), 5, "no repeated topics while distinct ones remain")
                self.assertEqual(scores, sorted(scores, reverse=True))

    def test_never_recommends_joined_circles(self):
        rows = self.conn.execute("SELECT user_id, circle_id FROM memberships").fetchall()
        joined = {}
        for r in rows:
            joined.setdefault(r["user_id"], set()).add(r["circle_id"])
        for uid, cids in joined.items():
            self.assertFalse(set(self.ids(uid)) & cids, f"user {uid}")

    def test_inactive_relative_to_as_of(self):
        # On the snapshot date Level Up is active; ten weeks later with no new data it isn't.
        self.assertIn(LEVEL_UP, self.ids(23))
        later = self.recs(23, as_of="2026-12-01T00:00:00Z")
        self.assertIn(LEVEL_UP, {e["id"] for e in later["debug"]["excluded"] if "inactive" in e["reasons"]})

    def test_deterministic(self):
        self.assertEqual(self.recs(7), self.recs(7))

    def test_errors(self):
        with self.assertRaises(NotFound):
            get_recommendations(self.conn, 9999)
        with self.assertRaises(BadRequest):
            get_recommendations(self.conn, 7, limit=0)
        with self.assertRaises(BadRequest):
            get_recommendations(self.conn, 7, as_of="yesterday")


class HttpApi(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.conn, quiet=True))
        cls.base = f"http://127.0.0.1:{cls.httpd.server_address[1]}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_recommendations_endpoint(self):
        status, body = self.get("/users/7/recommendations?limit=3")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["recommendations"]), 3)
        self.assertNotIn("debug", body)
        self.assertNotIn("score", body["recommendations"][0])

    def test_error_codes(self):
        self.assertEqual(self.get("/users/9999/recommendations")[0], 404)
        self.assertEqual(self.get("/users/7/recommendations?limit=abc")[0], 400)
        self.assertEqual(self.get("/nope")[0], 404)
        self.assertEqual(self.get("/health")[0], 200)

    def test_landing_page_links_to_scenarios(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("text/html", resp.headers["Content-Type"])
            html = resp.read().decode()
        for uid in (7, 15, 23, 42):
            self.assertIn(f'href="/users/{uid}/recommendations"', html)


if __name__ == "__main__":
    unittest.main()
