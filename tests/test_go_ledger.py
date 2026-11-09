"""Go API balances and schema-2 trace fields remain authoritative in the panel."""

import json
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from app.services.monitor_service import MonitorService, TokenAdminError
from app.utils.log_parser import LogParser


class GoLedgerTest(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.monitor = MonitorService(
            nodes=(), monitor_database=root / 'index.db',
            service_database=root / 'old.db', service_url='http://127.0.0.1:43338',
            service_admin_secret='synthetic-admin', ledger_backend='go',
        )

    def test_live_api_balances_are_used(self):
        data = {'tokens': [{
            'token': 'synthetic-token', 'token_hint': 'synthetic-token',
            'remaining': 7, 'used': 3, 'pending': 1, 'enabled': True, 'expires_at': None,
        }]}
        with patch.object(self.monitor, 'list_token_records', return_value=data):
            result = self.monitor.get_token_usage()
        self.assertTrue(result.available)
        self.assertEqual((result.remaining, result.used, result.pending), (7, 3, 1))
        self.assertEqual(result.tokens[0].token, 'synthetic-token')

    def test_api_failure_never_falls_back_to_sqlite(self):
        self.monitor.service_database.write_text('old snapshot is not a live ledger')
        with patch.object(self.monitor, 'list_token_records',
                          side_effect=TokenAdminError(503, 'unavailable')):
            result = self.monitor.get_token_usage()
        self.assertFalse(result.available)

    def test_unknown_backend_is_rejected(self):
        with self.assertRaises(ValueError):
            MonitorService(
                nodes=(), monitor_database=Path(self.temp.name) / 'other.db',
                service_database=Path(self.temp.name) / 'old.db',
                service_url='http://127.0.0.1:43338', ledger_backend='unknown',
            )

    def test_flat_go_trace_preserves_timing_and_device_identity(self):
        record = {
            'ts': datetime.now().isoformat(), 'level': 'INFO', 'schema_version': 2,
            'event': 'hcaptcha_trace', 'request_id': 'synthetic-go-trace',
            'data': {
                'attempt': 1,
                'timing': {'total_ms': 2345, 'http_total_ms': 1234},
                'dimensions': {'profile_variant': 'm5-fixture', 'safari_version': '27.2'},
            },
        }
        entry = LogParser.parse_line(json.dumps(record))
        self.assertIsNotNone(entry)
        self.assertEqual(self.monitor.repository.insert_entries([entry], 'node://189', '189'), 1)
        detail = self.monitor.get_request_detail('synthetic-go-trace')
        self.assertIsNotNone(detail)
        self.assertEqual(detail.trace_metrics.total_ms, 2345)
        self.assertEqual(detail.trace_metrics.http_total_ms, 1234)
        self.assertEqual(detail.fingerprint.profile_variant, 'm5-fixture')
        self.assertEqual(detail.fingerprint.safari_version, '27.2')
