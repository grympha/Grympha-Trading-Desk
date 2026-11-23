import json
import unittest
from types import SimpleNamespace as N

import duplicate_guard as dg
from trade_labels import MAX_LEN, action_comment, entry_comment


class TradeLabels(unittest.TestCase):
    def test_entry_comments_are_readable_and_fit_mt5(self):
        cases = {('BBMA', 'CSA_REENTRY', None): 'BBMA Reentry CSA #',
                 ('BBMA', 'MOMENTUM_REENTRY', None): 'BBMA Reentry Momentum #',
                 ('BBMA', 'MHV_REVERSAL', None): 'BBMA MHV Reversal #',
                 ('CONFLUENCE', 'FIVE_FACTOR_CONFLUENCE', 4): 'Confluence 4/5 #',
                 ('CONFLUENCE', 'FIVE_FACTOR_CONFLUENCE', 5): 'Confluence 5/5 #',
                 ('CONFLUENCE', 'FIVE_FACTOR_CONFLUENCE', None): 'Confluence #'}
        for (strategy, setup, score), prefix in cases.items():
            c = entry_comment(strategy, setup, score, 'acct' * 16, 'BBMA|XAUUSD:key')
            self.assertTrue(c.startswith(prefix), c)
            self.assertLessEqual(len(c), MAX_LEN)
            self.assertRegex(c, r'#[0-9a-f]{6}$')

    def test_ids_are_stable_and_distinct(self):
        a = entry_comment('BBMA', 'CSA_REENTRY', None, 'acct', 'k1')
        self.assertEqual(a, entry_comment('BBMA', 'CSA_REENTRY', None, 'acct', 'k1'))
        self.assertNotEqual(a, entry_comment('BBMA', 'CSA_REENTRY', None, 'acct', 'k2'))
        self.assertNotEqual(a, entry_comment('BBMA', 'CSA_REENTRY', None, 'other', 'k1'))

    def test_action_comments(self):
        self.assertEqual(action_comment('BBMA', 'TP1 partial'), 'BBMA TP1 partial')
        self.assertEqual(action_comment('CONFLUENCE', 'early exit'), 'Confluence early exit')

    def test_new_comment_still_identifies_own_trade(self):
        tag = entry_comment('CONFLUENCE', 'FIVE_FACTOR_CONFLUENCE', 4, 'acct', 'k1')
        rows = [{'payload': json.dumps({'tag': tag})}]
        mine = N(ticket=99, identifier=99, magic=816002, comment=tag, symbol='GBPUSD', type=1, volume=0.01, time=1)
        other = N(ticket=98, identifier=98, magic=816002, comment='SDCe260f922237fe24c', symbol='GBPJPY', type=1, volume=0.2, time=1)
        found = dg.foreign_trades([mine, other], [], rows, {'BBMA': 816001, 'CONFLUENCE': 816002})
        self.assertEqual([t['ticket'] for t in found], [98])


if __name__ == '__main__':
    unittest.main()
