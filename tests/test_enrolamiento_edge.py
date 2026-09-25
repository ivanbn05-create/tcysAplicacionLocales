import io
import json
from pathlib import Path
import tempfile
import unittest
from urllib.error import URLError
import uuid

from herramientas.enrolamiento_edge import (
    CORE_MODULES, EnrollmentError, claim, validate_card, validate_receipt,
    write_private_receipt,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "contracts" / "edge-enrollment-v1" / "fixtures"
CARD_BYTES = (FIXTURES / "claim-request-arboledas.json").read_bytes()
RESPONSE_BYTES = (FIXTURES / "claim-response-arboledas.json").read_bytes()
CARD = validate_card(CARD_BYTES)


class FakeResponse:
    status = 201
    headers = type("Headers", (), {"get_content_type": lambda self: "application/json"})()

    def __init__(self, data):
        self.buffer = io.BytesIO(data)

    def read(self, size):
        return self.buffer.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, data):
        self.data = data
        self.calls = 0
        self.request = None

    def open(self, req, timeout):
        self.calls += 1
        self.request = req
        return FakeResponse(self.data)


def changed(**values):
    data = json.loads(RESPONSE_BYTES)
    data.update(values)
    return json.dumps(data).encode()


class EnrollmentTests(unittest.TestCase):
    def test_fixture_claim_matches_central_and_separates_tokens(self):
        opener = FakeOpener(RESPONSE_BYTES)
        receipt = claim("https://central.example.test", CARD, opener=opener)
        self.assertEqual(opener.calls, 1)
        self.assertEqual(
            opener.request.full_url,
            "https://central.example.test/api/v1/enrollment/claim/",
        )
        self.assertEqual(json.loads(opener.request.data), json.loads(CARD_BYTES))
        self.assertEqual(receipt.branch_id, CARD.expected_branch_id)
        self.assertEqual(receipt.edge_id, CARD.expected_edge_id)
        self.assertNotEqual(receipt.central_ingest_token, receipt.central_catalog_token)
        self.assertIn("pedidos_programados", receipt.modules)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "receipt.json"
            write_private_receipt(receipt, path)
            private = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(private["branch"]["id"], CARD.expected_branch_id)
            self.assertNotIn("code", private)
            self.assertEqual(private["credentials"]["ingest_credential_id"], receipt.ingest_credential_id)
            with self.assertRaises(FileExistsError):
                write_private_receipt(receipt, path)

    def test_card_requires_exact_private_identifiers(self):
        data = json.loads(CARD_BYTES)
        data["expected_branch_id"] = str(uuid.uuid4())
        different_card = validate_card(json.dumps(data).encode())
        with self.assertRaises(EnrollmentError):
            validate_receipt(RESPONSE_BYTES, different_card)
        with self.assertRaises(EnrollmentError):
            validate_card(CARD_BYTES.replace(b'"code":', b'"code": "duplicate", "code":', 1))

    def test_rejects_malformed_and_untrusted_response(self):
        original = json.loads(RESPONSE_BYTES)
        variants = [
            {"edge": {"id": str(uuid.uuid4()), "label": "wrong"}},
            {"modules": ["pos"]},
            {"modules": sorted(CORE_MODULES | {"unknown"})},
            {"version": "2.0"},
            {"request_id": str(uuid.uuid4())},
            {"credentials": {
                "ingest": original["credentials"]["ingest"],
                "catalog": original["credentials"]["ingest"],
            }},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                with self.assertRaises(EnrollmentError):
                    validate_receipt(changed(**variant), CARD)
        duplicate = RESPONSE_BYTES.replace(b'"version": "1.0"', b'"version": "1.0", "version": "1.0"', 1)
        with self.assertRaises(EnrollmentError):
            validate_receipt(duplicate, CARD)
        with self.assertRaises(EnrollmentError):
            validate_receipt(b"{" + b"x" * 65537 + b"}", CARD)

    def test_branch_name_respects_pos_storage_limit(self):
        branch = json.loads(RESPONSE_BYTES)["branch"]
        branch["name"] = "A" * 120
        self.assertEqual(validate_receipt(changed(branch=branch), CARD).branch_name, "A" * 120)
        branch["name"] = "A" * 121
        with self.assertRaises(EnrollmentError):
            validate_receipt(changed(branch=branch), CARD)

    def test_pedidos_only_initially_authorized_at_arboledas(self):
        branch = json.loads(RESPONSE_BYTES)["branch"]
        branch["code"] = "SANTA_ANITA"
        with self.assertRaises(EnrollmentError):
            validate_receipt(changed(branch=branch), CARD)

    def test_rejects_insecure_or_cross_origin_endpoints(self):
        bad = [
            ("http://central.example.test", "/api/v1/enrollment/claim/"),
            ("https://user:pass@central.example.test", "/api/v1/enrollment/claim/"),
            ("https://central.example.test/path", "/api/v1/enrollment/claim/"),
            ("https://central.example.test", "//evil.example/claim"),
            ("https://central.example.test", "/../claim"),
            ("https://central.example.test", "/claim?x=1"),
        ]
        for url, endpoint in bad:
            with self.subTest(url=url, endpoint=endpoint):
                with self.assertRaises(EnrollmentError):
                    claim(url, CARD, endpoint=endpoint, opener=FakeOpener(b"{}"))

    def test_network_uncertainty_has_no_auto_retry_or_secret_leak(self):
        class BrokenOpener:
            calls = 0

            def open(self, req, timeout):
                self.calls += 1
                raise URLError(CARD.code)

        opener = BrokenOpener()
        with self.assertRaises(EnrollmentError) as cm:
            claim("https://central.example.test", CARD, opener=opener)
        self.assertEqual(opener.calls, 1)
        self.assertNotIn(CARD.code, str(cm.exception))


if __name__ == "__main__":
    unittest.main()