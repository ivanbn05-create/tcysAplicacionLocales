"""Pruebas offline del vector público r7, sin claves privadas versionadas."""
import base64
import copy
from dataclasses import replace
import io
import json
import unittest
import uuid
import warnings
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ventas.pedidos_recovery_v2 import (
    PinnedPedidosKey, RecoveryV2Error, canonical_json, sha256,
    sign_edge_ack, verify_archive_proof, verify_archive_rows,
    verify_baseline, verify_receipt,
    _json,
)

BASELINE_B64 = """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"""
ARCHIVE_B64 = """UEsDBBQAAAAIAAAAIVDmbrTyawAAAI0AAAANAAAAbWFuaWZlc3QuanNvbjWLMQrDMAwA9z5DcwOJZVlyP2NkJJOUUBfcrfTvJaW54eCGe8PeX142gxvkP9NP8ZCceQJXGKsGSuXptlkf5T76Yz/uWYU9IaqJVOYQUTjPjVFykphMrBGmwFQbcVg0L9FbdVWszYXgc/kCUEsDBBQAAAAIAAAAIVBe591NFAEAAOgBAAANAAAAcGVkaWRvcy5qc29ubGWQUWvDIBSF3/cryn3aICkmbKXJWwZ7KIwxWJ8yhhg1nSzRoGYvIf99V9umgV2C0XM+j1cn4Eaok6HD2HSKGyhhf6k0Do/LbL8qSEB2qleaCdzSss5JVJyPS4zUrbJ9WCTQSv7N6FXiymgkcpLvUlKk+e5I8pIQ/LYkVo1blICyKDKceNk7KD8n4Ex7JRgakAUSqatEB2PpYCVGr9yQkcWQs0VHrTyzkXnaRmSwRozcGxpZkq0UbfrGSkTfL8rGKe2lDy/0D6Mo/0iP9Mfh7Yi+GxtvPOtuR+HhoVOG9K9ifrQsZNfVzeqlwH9QDy91tblH8wHmrxDGR+tC2HS+VAJLd6/VM8kww6sh3GtBZ5TWDcx3f1BLAQIUABQAAAAIAAAAIVDmbrTyawAAAI0AAAANAAAAAAAAAAAAAACAAQAAAABtYW5pZmVzdC5qc29uUEsBAhQAFAAAAAgAAAAhUF7n3U0UAQAA6AEAAA0AAAAAAAAAAAAAAIABlgAAAHBlZGlkb3MuanNvbmxQSwUGAAAAAAIAAgB2AAAA1QEAAAAA"""
ACK = "{\"payload\":{\"edge_id\":\"11111111-1111-4111-8111-111111111111\",\"issued_at\":\"2026-09-26T12:00:00.000000Z\",\"key_id\":\"55555555-5555-4555-8555-555555555555\",\"manifest_sha256\":\"d7d803e9c7449800682e3e0d2e59a8db140df9d78d46892e3dc0b0721e3ec540\",\"nonce\":\"66666666-6666-4666-8666-666666666666\",\"orders_sha256\":\"0c9acf3c60bdf359a7f6a01d0503b14dba88d5a79222cde515cd94a661afdde1\",\"pos_branch_id\":\"93a42904-4d09-4a50-94cf-2edbf5aa0e51\",\"pos_prestate_sha256\":\"c4e03f3eaa65b3538586434790c56301e348b5d82739b810ac2b0af760fcf771\",\"received\":[{\"codigo_publico\":\"88888888-8888-4888-8888-888888888888\",\"order_sha256\":\"664c9c40c8249b1326915b8d8c6ba2e690274c38e58de0b2702cdaf1b04117bd\",\"sender_id\":1},{\"codigo_publico\":\"77777777-7777-4777-8777-777777777777\",\"order_sha256\":\"c647602c1de82220f55e5ecc5b530e24ae57f4fbf3fcfc39d6fb60ceafcbc268\",\"sender_id\":2}],\"recovery_id\":\"22222222-2222-4222-8222-222222222222\",\"sender_ids\":[1,2,3,7,8,9],\"snapshot_sha256\":\"a1f6597e87d457f0aed8471c2ee007cdc2e807ec3e67601fa5d99a01ba5d3bc0\",\"tombstones_sha256\":\"b2b8d8dea8fff1a4c4a6ce6e5f4a7e3d96fc07e254d429e1a69337a1e3fca557\",\"type\":\"pedidos.edge.recovery_ack.v2\",\"unresolved\":[]},\"signature_b64\":\"P-QpqGkl-P-8HaOlRf5_Q1tOS5_T813XNmS1Rr3GkNKdgFkX9BYA9VaHANqV6pYaep1kgyxbaT33VEd64agYAQ\"}\n".encode("utf-8")
RECEIPT = "{\"payload\":{\"edge_ack_sha256\":\"33b93073abf4db0491997420a68d2b772c2f65eefcef5301f696da914f452a56\",\"edge_id\":\"11111111-1111-4111-8111-111111111111\",\"issued_at\":\"2026-09-26T12:01:00.000000Z\",\"key_id\":\"44444444-4444-4444-8444-444444444444\",\"manifest_sha256\":\"d7d803e9c7449800682e3e0d2e59a8db140df9d78d46892e3dc0b0721e3ec540\",\"next_cursor\":null,\"next_desde\":\"2026-09-27T00:00:00.000000Z\",\"pos_branch_id\":\"93a42904-4d09-4a50-94cf-2edbf5aa0e51\",\"pos_prestate_sha256\":\"c4e03f3eaa65b3538586434790c56301e348b5d82739b810ac2b0af760fcf771\",\"recovery_id\":\"22222222-2222-4222-8222-222222222222\",\"sender_ids\":[1,2,3,7,8,9],\"snapshot_sha256\":\"a1f6597e87d457f0aed8471c2ee007cdc2e807ec3e67601fa5d99a01ba5d3bc0\",\"status\":\"completada\",\"type\":\"pedidos.recovery_receipt.v2\"},\"signature_b64\":\"rGTeblkJwJcZOXLgkFZnnwEOKYyoop_VDPaIOPOSjMUuux9gMJzHD1-tyxk58LCSGREa1jx457WlXQ24H3iyCA\"}\n".encode("utf-8")
PEDIDOS_PUBLIC_KEY_B64 = "Yr8RCmQi2oeKbMlPMdqikjOycaNZUS3klXzupcbL6cI"
BASELINE_SHA = "a1f6597e87d457f0aed8471c2ee007cdc2e807ec3e67601fa5d99a01ba5d3bc0"
ARCHIVE_SHA = "5debc1537ff07316ce85900f91314b2b6402049bba2b631cdb4cd1d0cece0605"
ACK_SHA = "33b93073abf4db0491997420a68d2b772c2f65eefcef5301f696da914f452a56"
RECEIPT_SHA = "0116fb25e2e0fd6f5887d41ceef8769813c3f390d764fbf5271bb6886f54fa8e"
EDGE_ID = "11111111-1111-4111-8111-111111111111"
BRANCH_ID = "93a42904-4d09-4a50-94cf-2edbf5aa0e51"
PEDIDOS_KEY_ID = "44444444-4444-4444-8444-444444444444"
SENDERS = [1, 2, 3, 7, 8, 9]
PRESTATE = {
    "cursor": "", "ultimo_cursor_confirmado": None,
    "ventana_desde": "2026-09-26T00:00:00.000000Z",
    "ventana_hasta": "2026-09-27T00:00:00.000000Z",
    "agua_alta_hasta": None, "sucursales_origen": SENDERS,
    "estado": "reconciliacion", "version_api": "v2",
}


class RecoveryV2CodecTests(unittest.TestCase):
    def setUp(self):
        self.baseline_bytes = base64.b64decode(BASELINE_B64)
        self.archive_bytes = base64.b64decode(ARCHIVE_B64)
        self.pins = {
            PEDIDOS_KEY_ID: PinnedPedidosKey(PEDIDOS_PUBLIC_KEY_B64)
        }

    def baseline(self, **changes):
        args = dict(
            expected_snapshot_sha256=BASELINE_SHA,
            pedidos_keys=self.pins,
            edge_id=EDGE_ID,
            pos_branch_id=BRANCH_ID,
            sender_ids=SENDERS,
            expected_prestate=PRESTATE,
        )
        args.update(changes)
        return verify_baseline(self.baseline_bytes, **args)

    def proof(self, baseline):
        return verify_archive_proof(
            baseline, self.archive_bytes,
            "99999999-9999-4999-8999-999999999999",
        )

    def test_public_e2e_vector_and_exact_hash_chain(self):
        self.assertEqual(sha256(self.baseline_bytes), BASELINE_SHA)
        self.assertEqual(sha256(self.archive_bytes), ARCHIVE_SHA)
        self.assertEqual(sha256(ACK), ACK_SHA)
        self.assertEqual(sha256(RECEIPT), RECEIPT_SHA)
        baseline = self.baseline()
        self.assertEqual(len(baseline.orders), 1)
        self.assertEqual(len(baseline.recovered_orders), 1)
        self.assertEqual(len(baseline.tombstones), 1)
        self.assertEqual(len(self.proof(baseline)), 1)
        original = verify_archive_rows(
            baseline, self.archive_bytes,
            "99999999-9999-4999-8999-999999999999",
        )
        self.assertEqual(next(iter(original.values())).estado, "confirmado")
        self.assertTrue(next(iter(original.values())).importable)
        result = verify_receipt(
            baseline, ACK, RECEIPT, pedidos_keys=self.pins,
            archive_proven=self.proof(baseline),
        )
        self.assertTrue(result.completed)
        self.assertEqual(result.receipt_sha256, RECEIPT_SHA)
        self.assertEqual(
            result.payload["next_desde"], PRESTATE["ventana_hasta"]
        )

    def test_nonempty_cursor_must_match_signed_prestate(self):
        state = copy.deepcopy(PRESTATE)
        state["cursor"] = "cursor-v2-firmado"
        with self.assertRaises(RecoveryV2Error):
            self.baseline(expected_prestate=state)

    def test_wrong_identity_sender_or_key_fails_closed(self):
        for overrides in (
            {"edge_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
            {"pos_branch_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"},
            {"sender_ids": [1, 2]},
            {"pedidos_keys": {}},
            {"pedidos_keys": {
                PEDIDOS_KEY_ID: PinnedPedidosKey(
                    PEDIDOS_PUBLIC_KEY_B64, revoked=True,
                )
            }},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(RecoveryV2Error):
                self.baseline(**overrides)

    def test_snapshot_mutation_and_truncation_fail(self):
        with self.assertRaises(RecoveryV2Error):
            verify_baseline(
                self.baseline_bytes[:-1],
                expected_snapshot_sha256=BASELINE_SHA,
                pedidos_keys=self.pins, edge_id=EDGE_ID,
                pos_branch_id=BRANCH_ID, sender_ids=SENDERS,
                expected_prestate=PRESTATE,
            )
        with self.assertRaises(RecoveryV2Error):
            self.baseline(expected_snapshot_sha256="0" * 64)

    def test_zip_extra_member_and_duplicate_member_fail_even_with_correct_external_sha(self):
        for names in (
            ["../escape.txt"], ["orders.jsonl"],
        ):
            output = io.BytesIO()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(
                    io.BytesIO(self.baseline_bytes)
                ) as original, zipfile.ZipFile(output, "w") as changed:
                    for info in original.infolist():
                        changed.writestr(info.filename, original.read(info))
                    for name in names:
                        changed.writestr(name, b"{}")
            data = output.getvalue()
            with self.subTest(names=names), self.assertRaises(RecoveryV2Error):
                verify_baseline(
                    data, expected_snapshot_sha256=sha256(data),
                    pedidos_keys=self.pins, edge_id=EDGE_ID,
                    pos_branch_id=BRANCH_ID, sender_ids=SENDERS,
                    expected_prestate=PRESTATE,
                )

    def test_json_duplicate_key_float_nan_and_invalid_utf8_rejected(self):
        for raw in (
            b'{"a":1,"a":2}', b'{"a":1.2}', b'{"a":NaN}', b'\xff',
        ):
            with self.subTest(raw=raw), self.assertRaises(RecoveryV2Error):
                _json(raw, maximum=100)

    def test_archive_missing_or_altered_fails(self):
        baseline = self.baseline()
        with self.assertRaises(RecoveryV2Error):
            verify_archive_proof(
                baseline, self.archive_bytes[:-1],
                "99999999-9999-4999-8999-999999999999",
            )
        with self.assertRaises(RecoveryV2Error):
            verify_archive_proof(
                baseline, self.archive_bytes,
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            )
        with self.assertRaises(RecoveryV2Error):
            verify_receipt(
                baseline, ACK, RECEIPT, pedidos_keys=self.pins,
            )

    def test_verified_terminal_archive_row_is_manual(self):
        baseline = self.baseline()
        with zipfile.ZipFile(io.BytesIO(self.archive_bytes)) as original:
            row = json.loads(original.read("pedidos.jsonl"))
            manifest = json.loads(original.read("manifest.json"))
        row["estado"] = "enviado"
        lines = canonical_json(row) + b"\n"
        manifest["sha256_pedidos_jsonl"] = sha256(lines)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as altered:
            altered.writestr("manifest.json", canonical_json(manifest) + b"\n")
            altered.writestr("pedidos.jsonl", lines)
        archive_bytes = output.getvalue()
        tombstone = copy.deepcopy(baseline.tombstones[0])
        tombstone["archive_sha256"] = sha256(archive_bytes)
        current = baseline.orders[0]
        pair = (current["sender_id"], current["order"]["codigo_publico"])
        variant = replace(
            baseline, recovered_orders=(), tombstones=(tombstone,),
            order_hashes={pair: baseline.order_hashes[pair]},
        )
        export_id = tombstone["exportacion_id"]
        proofs = verify_archive_rows(variant, archive_bytes, export_id)
        self.assertEqual(next(iter(proofs.values())).estado, "enviado")
        self.assertFalse(next(iter(proofs.values())).importable)
        self.assertEqual(
            verify_archive_proof(variant, archive_bytes, export_id),
            frozenset(),
        )

    def test_ack_signing_requires_exact_coverage_and_archive_proof(self):
        baseline = self.baseline()
        private = Ed25519PrivateKey.generate()
        received = copy.deepcopy(
            json.loads(ACK.decode("utf-8"))["payload"]["received"]
        )
        kwargs = dict(
            edge_key_id="55555555-5555-4555-8555-555555555555",
            edge_private_key=private,
            nonce=str(uuid.uuid4()),
            issued_at="2026-09-26T12:00:00.000000Z",
            received=received, unresolved=[],
        )
        with self.assertRaises(RecoveryV2Error):
            sign_edge_ack(baseline, **kwargs)
        raw = sign_edge_ack(
            baseline, **kwargs, archive_proven=self.proof(baseline),
        )
        envelope = json.loads(raw)
        self.assertEqual(envelope["payload"]["snapshot_sha256"], BASELINE_SHA)
        self.assertEqual(raw, canonical_json(envelope) + b"\n")
        bad = copy.deepcopy(received)
        bad[0]["order_sha256"] = "0" * 64
        with self.assertRaises(RecoveryV2Error):
            sign_edge_ack(
                baseline, **{**kwargs, "received": bad},
                archive_proven=self.proof(baseline),
            )
        with self.assertRaises(RecoveryV2Error):
            sign_edge_ack(
                baseline, **{**kwargs, "received": received[:1]},
                archive_proven=self.proof(baseline),
            )

    def test_receipt_signature_ack_hash_and_manual_state_fail_safe(self):
        baseline = self.baseline()
        proof = self.proof(baseline)
        altered_receipt = copy.deepcopy(json.loads(RECEIPT))
        altered_receipt["payload"]["next_desde"] = None
        with self.assertRaises(RecoveryV2Error):
            verify_receipt(
                baseline, ACK, canonical_json(altered_receipt) + b"\n",
                pedidos_keys=self.pins, archive_proven=proof,
            )
        with self.assertRaises(RecoveryV2Error):
            verify_receipt(
                baseline, ACK + b" ", RECEIPT, pedidos_keys=self.pins,
                archive_proven=proof,
            )


if __name__ == "__main__":
    unittest.main()
