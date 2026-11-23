import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from account_status import sample
class AccountStatusTests(unittest.TestCase):
    def fake(self):
        return NS(terminal_info=Mock(return_value=NS(connected=True)),account_info=Mock(return_value=NS(balance=1000,equity=998.5,currency='USD',company='RoboForex Ltd',server='RoboForex-Pro',trade_mode=0,login=123,password='private')))
    def test_values_privacy(self):
        d=sample(self.fake(),100)
        self.assertEqual((d['status'],d['balance'],d['equity']),('HEALTHY',1000,998.5));self.assertNotIn('login',d);self.assertNotIn('password',d)
    def test_disconnected(self):
        m=self.fake();m.terminal_info.return_value.connected=False
        d=sample(m);self.assertEqual(d['status'],'DISCONNECTED');self.assertIsNone(d['balance']);m.account_info.assert_not_called()
    def test_missing_invalid(self):
        m=self.fake();m.account_info.return_value=None
        self.assertEqual(sample(m)['status'],'UNAVAILABLE')
        m=self.fake();m.account_info.return_value.equity=float('nan');self.assertIsNone(sample(m)['equity'])
    def test_exception(self):
        m=self.fake();m.terminal_info.side_effect=RuntimeError('private');self.assertEqual(sample(m)['status'],'UNAVAILABLE')
