import json
import tempfile
import unittest
from pathlib import Path

from dashboard_store import Store
from trade_controls import DEFAULTS, controls, validate


class TradeLimit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = Store(Path(self.tmp.name) / 'd.sqlite3')

    def tearDown(self):
        self.tmp.cleanup()

    def test_validation_range(self):
        self.assertEqual(validate({'max_positions': 10})['max_positions'], 10)
        self.assertEqual(validate({'max_positions': 12.0})['max_positions'], 12)  # JSON may send 12.0
        for bad in (0, 31, 2.5, True, '6', None):
            with self.assertRaises(ValueError):
                validate({'max_positions': bad})

    def test_defaults_to_config_then_dashboard_value_wins(self):
        cfg = {'trade_controls': {}, 'real_execution': {'max_positions': 6}}
        with self.store.connect() as db:  # controls saved before this setting existed
            old = {k: v for k, v in DEFAULTS.items() if k != 'max_positions'}
            db.execute("INSERT OR REPLACE INTO meta VALUES ('trade_controls',?)", (json.dumps(old),))
        self.assertEqual(controls(self.store, cfg)['max_positions'], 6)
        with self.store.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES ('trade_controls',?)", (json.dumps(validate({'max_positions': 9})),))
        self.assertEqual(controls(self.store, cfg)['max_positions'], 9)
        self.assertEqual(controls(self.store, cfg)['risk_percent'], DEFAULTS['risk_percent'])  # other settings untouched


if __name__ == '__main__':
    unittest.main()
