"""The HTTP layer (needs starlette: pip install ".[web]")."""
import os
import unittest

try:
    from library_robot.web import create_app
    HAVE_WEB = True
except ImportError:
    HAVE_WEB = False

from tests.asgi_client import call
from tests.helpers import fresh_db_path
from tests.test_service import qr_bytes


@unittest.skipUnless(HAVE_WEB, "starlette is not installed")
class WebTest(unittest.TestCase):
    def setUp(self):
        self.app = create_app(fresh_db_path())

    def get(self, path):
        return call(self.app, "GET", path)

    def post(self, path, body):
        return call(self.app, "POST", path, json=body)

    def test_home_page_and_security_headers(self):
        r = self.get("/")
        self.assertEqual(r["status"], 200)
        self.assertIn(b"Library Robot", r["body"])
        self.assertIn("default-src 'self'", r["headers"]["content-security-policy"])
        self.assertEqual(r["headers"]["x-content-type-options"], "nosniff")

    def test_lists(self):
        self.assertEqual(self.get("/api/overview")["json"]["books"], 7)
        self.assertEqual(len(self.get("/api/books")["json"]), 7)
        self.assertEqual({u["name"] for u in self.get("/api/users")["json"]}, {"Ahmed", "Sara", "Karim"})
        self.assertEqual(len(self.get("/api/loans")["json"]), 2)
        self.assertTrue(self.get("/api/rules")["json"]["inference"])
        self.assertEqual(self.get("/api/events?n=3")["status"], 200)

    def test_checkout_return_roundtrip(self):
        r = self.post("/api/checkout", {"user": "Karim", "book": "B30"})
        self.assertEqual((r["status"], r["json"]["outcome"]), (200, "lent"))
        self.assertEqual(self.post("/api/checkout", {"user": "Sara", "book": "B30"})["json"]["outcome"], "borrowed")
        r = self.post("/api/return", {"user": "Karim", "book": "B30"})
        self.assertEqual(r["json"]["outcome"], "returned")
        self.assertEqual(len(self.get("/api/loans")["json"]), 2)

    def test_refusals_are_normal_answers_with_proofs(self):
        r = self.post("/api/checkout", {"user": "Karim", "book": "B25"})
        self.assertEqual(r["status"], 200)
        self.assertFalse(r["json"]["ok"])
        self.assertEqual(r["json"]["proofs"][0]["rule"], "R5_reserved")

    def test_reserve(self):
        self.assertTrue(self.post("/api/reserve", {"user": "Karim", "book": "B30"})["json"]["ok"])

    def test_book_detail_and_404(self):
        d = self.get("/api/books/B15")
        self.assertEqual(d["json"]["target"]["shelf"], "S7")
        r = self.get("/api/books/B999")
        self.assertEqual(r["status"], 404)
        self.assertIn("error", r["json"])

    def test_admin_endpoints_and_conflicts(self):
        self.assertEqual(self.post("/api/users", {"name": "Layla"})["status"], 200)
        self.assertEqual(self.post("/api/users", {"name": "Layla"})["status"], 409)
        self.assertEqual(self.post("/api/shelves", {"id": "S20", "room": "Room3", "row": "RowC1"})["status"], 200)
        r = self.post("/api/books", {"id": "B60", "title": "Deep Learning", "shelf": "S20", "copies": 2})
        self.assertEqual(r["status"], 200)
        self.assertEqual(self.post("/api/books", {"id": "B60", "title": "x", "shelf": "S20"})["status"], 409)
        self.assertEqual(self.post("/api/books", {"id": "B61", "title": "x", "shelf": "S404"})["status"], 404)

    def test_bad_input_is_a_clean_400(self):
        self.assertEqual(call(self.app, "POST", "/api/checkout", data=b"{oops")["status"], 400)
        self.assertEqual(call(self.app, "POST", "/api/checkout", data=b"[1,2]",
                              headers={"content-type": "application/json"})["status"], 400)
        r = self.post("/api/checkout", {"user": "Karim"})
        self.assertEqual(r["status"], 400)
        self.assertIn("book", r["json"]["error"])
        self.assertEqual(self.post("/api/users", {"name": "<script>"})["status"], 400)
        self.assertEqual(self.post("/api/ask", {"query": "Nope(1)"})["status"], 400)
        self.assertEqual(self.post("/api/ask", {"query": "Book(?x)", "given": "QRRead(B1)"})["status"], 400)

    def test_ask(self):
        r = self.post("/api/ask", {"query": "TargetShelf(B15, ?s)", "given": ["QRRead(B15)"]})
        self.assertEqual(r["json"]["answers"][0]["atom"], "TargetShelf(B15, S7)")

    def test_scan_and_qr(self):
        r = call(self.app, "POST", "/api/scan", data=qr_bytes("B20"), headers={"content-type": "image/png"})
        self.assertEqual(r["json"]["book"], "B20")
        self.assertEqual(call(self.app, "POST", "/api/scan", data=b"junk")["status"], 400)
        png = self.get("/api/qr/B25.png")
        self.assertEqual((png["status"], png["headers"]["content-type"]), (200, "image/png"))
        self.assertEqual(self.get("/api/qr/..x.png")["status"], 400)

    def test_scan_rejects_huge_upload(self):
        r = call(self.app, "POST", "/api/scan", data=b"0", headers={"content-length": str(50 * 1024 * 1024)})
        self.assertEqual(r["status"], 400)

    def test_unknown_route_and_method(self):
        self.assertEqual(self.get("/api/nothing")["status"], 404)
        self.assertEqual(call(self.app, "DELETE", "/api/books")["status"], 405)


if __name__ == "__main__":
    unittest.main()
