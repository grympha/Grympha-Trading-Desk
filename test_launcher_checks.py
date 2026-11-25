import unittest,time
from types import SimpleNamespace as N
from unittest.mock import patch
import launcher_checks as lc

class Prerequisites(unittest.TestCase):
    def mt5(self,mode=0):
        m=N(initialize=lambda **kw:True,shutdown=lambda:None,terminal_info=lambda:N(connected=True,data_path='C:/test',trade_allowed=True,tradeapi_disabled=False),account_info=lambda:N(trade_mode=mode,margin_mode=2,trade_allowed=True,trade_expert=True),symbol_select=lambda *a:True,symbol_info_tick=lambda *a:N(time=int(time.time()),bid=1,ask=1.001),copy_rates_from_pos=lambda *a:[0]*400,TIMEFRAME_H4=1,TIMEFRAME_H1=2,TIMEFRAME_M15=3,TIMEFRAME_M5=4)
        return m
    def cfg(self):return dict(symbols=['AUDUSD'],confluence=dict(symbols=['AUDUSD']),news_filter=dict(enabled=False))
    def test_read_only_checks_no_order_api(self):
        rows,path=lc.inspect_mt5(self.cfg(),self.mt5())
        self.assertFalse(any(r['status']=='FAIL' for r in rows));self.assertEqual(path,'C:/test')
    def account_row(self,cfg,m):
        rows,_=lc.inspect_mt5(cfg,m);return next(r for r in rows if r['name']=='Account type')
    def test_account_type_row_is_informational(self):
        demo=self.account_row(self.cfg(),self.mt5(0))
        self.assertEqual((demo['status'],demo['account_mode']),('PASS','DEMO'))
        m=self.mt5(2);acct=m.account_info();m.account_info=lambda:N(**{**vars(acct),'login':123,'server':'Broker-Live'})
        other=self.account_row(self.cfg(),m)
        self.assertEqual((other['status'],other['matches_real']),('WARN',False))  # REAL but not saved: not a failure
        cfg=dict(self.cfg(),real_execution=dict(account_login=123,account_server='Broker-Live'))
        saved=self.account_row(cfg,m)
        self.assertEqual((saved['status'],saved['account_mode'],saved['matches_real']),('PASS','REAL',True))
        self.assertFalse(any(r['name']=='DEMO account' for r in lc.inspect_mt5(cfg,m)[0]))
    def test_missing_history_guide(self):
        m=self.mt5();m.copy_rates_from_pos=lambda *a:None
        rows,_=lc.inspect_mt5(self.cfg(),m);self.assertTrue(any(r['name'].endswith('history') and r['status']=='FAIL' and r['guide'] for r in rows))
    def test_clock_bad_quote(self):
        m=self.mt5();m.symbol_info_tick=lambda *a:N(time=int(time.time())+7200,bid=1,ask=1.001)
        rows,_=lc.inspect_mt5(self.cfg(),m);self.assertTrue(any(r['name'].endswith('quote') and r['status']=='FAIL' for r in rows))
    def test_missing_offset_names_the_broker_clock(self):
        # New install (no offset) on a UTC+2 broker: quotes look 2 h in the future.
        m=self.mt5();m.symbol_info_tick=lambda *a:N(time=int(time.time())+7200,bid=1,ask=1.001)
        row=next(r for r in lc.inspect_mt5(self.cfg(),m)[0] if r['name'].endswith('quote'))
        self.assertEqual(row['status'],'FAIL');self.assertIn('UTC+2',row['detail']);self.assertIn('Set clock offset',row['guide'])
    def test_dst_change_names_the_new_offset(self):
        # Offset 2 saved, broker moved to UTC+3 for summer time.
        m=self.mt5();m.symbol_info_tick=lambda *a:N(time=int(time.time())+3*3600,bid=1,ask=1.001)
        cfg=dict(self.cfg(),tick_timestamp_offset_hours=2)
        row=next(r for r in lc.inspect_mt5(cfg,m)[0] if r['name'].endswith('quote'))
        self.assertIn('UTC+3',row['detail']);self.assertIn('offset set to 2 h',row['detail'])
    def test_correct_offset_passes(self):
        m=self.mt5();m.symbol_info_tick=lambda *a:N(time=int(time.time())+7200,bid=1,ask=1.001)
        cfg=dict(self.cfg(),tick_timestamp_offset_hours=2)
        self.assertTrue(all(r['status']=='PASS' for r in lc.inspect_mt5(cfg,m)[0] if r['name'].endswith('quote')))
    def test_disconnected(self):
        m=self.mt5();m.terminal_info=lambda:N(connected=False,data_path='C:/test')
        rows,_=lc.inspect_mt5(self.cfg(),m);self.assertTrue(any(r['name']=='MT5 connection' and r['status']=='FAIL' for r in rows))
    def test_missing_packages(self):
        with patch.object(lc.importlib,'import_module',side_effect=ImportError()):rows=lc.package_checks()
        self.assertEqual(len(rows),5);self.assertTrue(all(r['status']=='FAIL' and r['guide'] for r in rows))
    def test_unsupported_versions(self):
        with patch.object(lc.importlib,'import_module'),patch.object(lc.importlib.metadata,'version',return_value='0.0.1'):rows=lc.package_checks()
        self.assertTrue(all(r['status']=='FAIL' for r in rows))
