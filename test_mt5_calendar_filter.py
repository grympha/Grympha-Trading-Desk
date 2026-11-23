import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace as N
from mt5_calendar_filter import MT5CalendarFilter
class Calendar(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'MQL5'/'Files').mkdir(parents=True)
        self.file=self.root/'MQL5'/'Files'/'bbma_mt5_calendar.json';self.now=time.time()
        self.mt5=N(terminal_info=lambda:N(connected=True,data_path=str(self.root)),account_info=lambda:N(server='Demo'))
        self.d=dict(schema='bbma.mt5.calendar.v1',terminal_data_path=str(self.root),server='Demo',ready=True,status='HEALTHY',clock_verified=True,connected=True,updated_epoch=self.now,fetched_epoch=self.now,server_now_epoch=self.now+7200,server_utc_offset_seconds=7200,heartbeat_seconds=30,max_cache_age_seconds=900,coverage_from_server_epoch=self.now+7200-86400,coverage_to_server_epoch=self.now+7200+86400*5,currencies=['USD','GBP','JPY','EUR','AUD','CAD','CHF','NZD'],events=[])
        self.f=MT5CalendarFilter({'news_filter':dict(enabled=True,provider='mt5')},self.mt5)
        self.save()
    def save(self):self.file.write_text(json.dumps(self.d),encoding='utf-8')
    def tearDown(self):self.tmp.cleanup()
    def test_empty_high_impact_list_valid(self):self.assertFalse(self.f.check('AUDUSD')['blocked'])
    def test_missing_file(self):self.file.unlink();self.assertTrue(self.f.check('AUDUSD')['blocked'])
    def test_stale_heartbeat(self):self.d['updated_epoch']-=200;self.save();self.assertTrue(self.f.check('AUDUSD')['blocked'])
    def test_stale_events(self):self.d['fetched_epoch']-=901;self.save();self.assertTrue(self.f.check('AUDUSD')['blocked'])
    def test_wrong_terminal_and_server(self):
        self.d['server']='Other';self.save();self.assertTrue(self.f.check('AUDUSD')['blocked'])
        self.d['server']='Demo';self.d['terminal_data_path']='other';self.save();self.assertTrue(self.f.check('AUDUSD')['blocked'])
    def test_stopped_not_ready(self):self.d['ready']=False;self.d['status']='STOPPED';self.save();self.assertTrue(self.f.check('AUDUSD')['blocked'])
    def test_clock_mismatch(self):self.d['server_now_epoch']+=3600;self.save();self.assertTrue(self.f.check('AUDUSD')['blocked'])
    def test_currency_coverage(self):self.d['currencies'].remove('NZD');self.save();self.assertTrue(self.f.check('NZDUSD')['blocked'])
    def test_blackout_and_pending_horizon(self):
        self.d['events']=[dict(currency='USD',importance=3,title='CPI',estimated_time=False,server_epoch=self.now+7200+1900)];self.save()
        self.assertFalse(self.f.check('AUDUSD')['blocked']);self.assertTrue(self.f.check('AUDUSD',until=self.now+300)['blocked'])
        self.assertFalse(self.f.check('GBPJPY',until=self.now+300)['blocked'])
    def test_estimated_whole_server_day(self):
        self.d['events']=[dict(currency='USD',importance=3,title='Policy',estimated_time=True,server_epoch=self.now+7200)];self.save();self.assertTrue(self.f.check('XAUUSD')['blocked'])
    def test_partial_json_and_disabled(self):
        self.file.write_text('{');self.assertTrue(self.f.check('USDJPY')['blocked'])
        self.f.enabled=False;self.assertEqual(self.f.check('USDJPY')['status'],'DISABLED')
    def test_coverage_horizon(self):self.assertTrue(self.f.check('AUDUSD',until=self.now+86400*8)['blocked'])
