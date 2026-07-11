"""API tests via Starlette TestClient. Skipped unless httpx (TestClient's transport)
and the real data are present. The live API was also verified end-to-end in a browser."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_have_data = (ROOT / "data" / "processed" / "prices").exists() and \
             any((ROOT / "data" / "processed" / "prices").glob("*.json"))
try:
    from starlette.testclient import TestClient  # needs httpx
    _have_client = True
except Exception:
    _have_client = False


@unittest.skipUnless(_have_client and _have_data, "httpx/TestClient or data not present")
class TestApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from fahrschule.api import app
        cls.ctx = TestClient(app)
        cls.client = cls.ctx.__enter__()   # runs the startup lifespan (loads the store)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.__exit__(None, None, None)

    def test_health(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")
        self.assertEqual(r.json()["sheets_loaded"], 44)

    def test_conversation_to_price(self):
        sid = self.client.post("/api/session?language=de").json()["session_id"]
        for key in ["neu", "nur_b", "manuell", "standard"]:
            if key == "neu":
                self.client.post("/api/message", json={"session_id": sid, "text": "Was kostet Klasse B?"})
            r = self.client.post("/api/message", json={"session_id": sid, "option_key": key})
        reply = r.json()["reply"]
        self.assertEqual(reply["kind"], "price")
        self.assertEqual(reply["price"]["gesamtbetrag"], 2696.0)

    def test_document_download(self):
        r = self.client.get("/api/document/B")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/pdf")

    def test_archived_document_404(self):
        self.assertEqual(self.client.get("/api/document/C_CE_BA").status_code, 404)

    def test_widget_served(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Fahrschul", r.text)


if __name__ == "__main__":
    unittest.main()
