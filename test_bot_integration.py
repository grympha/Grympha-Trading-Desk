"""Offline mocked MT5 integration: production run path -> JSON log -> SQLite -> clean shutdown."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import bbma_bot
from dashboard_store import Store

class BotIntegration(unittest.TestCase):
    def test_one_cycle_publishes_to_dashboard_without_telegram(self):
        now=int(time.time())
        def rates(symbol,seconds,start,count):
            end=(now//seconds)*seconds-seconds
            return [dict(time=end-(159-i)*seconds,open=100+i*.01,
                         high=101+i*.01,low=99+i*.01,close=100.5+i*.01)
                    for i in range(160)]
        mt5=SimpleNamespace(initialize=Mock(return_value=True),shutdown=Mock(),order_send=Mock(side_effect=AssertionError('Normal mode must never send an order')),
                            terminal_info=lambda:SimpleNamespace(connected=True),
                            positions_get=lambda:[SimpleNamespace(ticket=55,symbol='XAUUSD',type=0,volume=.1,price_open=100.,price_current=101.,sl=95.,tp=110.,profit=7.5,swap=-.1)],
                            account_info=lambda:SimpleNamespace(currency='USD'),
                            symbol_select=lambda *_:True,
                            symbol_info_tick=lambda _:SimpleNamespace(time=now,bid=100.,ask=100.1),
                            symbol_info=lambda _:SimpleNamespace(trade_tick_size=.01,point=.01,digits=2),
                            copy_rates_from_pos=rates,TIMEFRAME_H1=3600,TIMEFRAME_M15=900,TIMEFRAME_M5=300)
        plan=dict(id='integration-test',side='BUY',order_idea='BUY_STOP',entry=105.,stop_loss=95.,
                  tp1=115.,tp2=125.,setup='CSA_REENTRY',
                  confirmation_utc=bbma_bot.stamp(now),expires_utc=bbma_bot.stamp(now+600))
        result=dict(condition=dict(name='BULLISH',side='BUY',momentum=False),
                    setup=dict(type='CSA_REENTRY'),entry_plan=plan,reason='Confirmed')
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            cfg=json.loads((Path(__file__).parent/'config.example.json').read_text())
            cfg['confluence']={'enabled':False};cfg['symbols']=['XAUUSD'];(root/'config.json').write_text(json.dumps(cfg))
            args=SimpleNamespace(config=str(root/'config.json'),once=True,test_telegram=False,
                                 diagnose_time=False,dashboard=False,dashboard_port=8765)
            with patch.object(bbma_bot,'ROOT',root),patch.object(bbma_bot,'assess',return_value=result),\
                 patch.dict('sys.modules',{'MetaTrader5':mt5,'dotenv':SimpleNamespace(load_dotenv=lambda *_:None)}),\
                 patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'','TELEGRAM_CHAT_ID':''}),\
                 contextlib.redirect_stdout(io.StringIO()):
                bbma_bot.run(args)
            log=json.loads((root/'decisions.jsonl').read_text().strip())
            self.assertEqual(log['health'],'OK')
            self.assertNotIn('chart',log)  # candles are no longer copied into the decision log
            self.assertIn('chart_last_bar',log)
            snap=Store(root/'dashboard.sqlite3').snapshot()
            self.assertEqual(snap['signals'][0]['telegram'],'DISABLED')
            self.assertEqual(snap['symbols'][0]['symbol'],'XAUUSD')
            self.assertFalse(snap['bot_online']) # --once clean shutdown
            self.assertEqual(snap['meta']['mt5_positions']['positions'][0]['profit'],7.5)
            self.assertEqual(snap['meta']['mt5_positions']['positions'][0]['attribution'],'UNASSIGNED')
            mt5.shutdown.assert_called_once()
            mt5.order_send.assert_not_called()

if __name__=='__main__': unittest.main()

class IndependentEngineIntegration(unittest.TestCase):
    def test_confluence_continues_when_bbma_analysis_fails(self):
        now=int(time.time())
        def rates(symbol,seconds,start,count):
            end=(now//seconds)*seconds-seconds
            return [dict(time=end-(159-i)*seconds,open=100+i*.01,high=101+i*.01,
                         low=99+i*.01,close=100.5+i*.01) for i in range(160)]
        mt5=SimpleNamespace(initialize=Mock(return_value=True),shutdown=Mock(),
            terminal_info=lambda:SimpleNamespace(connected=True),symbol_select=lambda *_:True,
            symbol_info_tick=lambda _:SimpleNamespace(time=now,bid=100.,ask=100.1),
            symbol_info=lambda _:SimpleNamespace(trade_tick_size=.01,point=.01,digits=2),
            copy_rates_from_pos=rates,TIMEFRAME_H1=3600,TIMEFRAME_M15=900,TIMEFRAME_M5=300)
        reference=dict(id='independent',side='SELL',order_idea='QUOTE_REFERENCE',signal_kind='REFERENCE',
                       entry=100.,stop_loss=103.,tp1=96.,tp2=93.,setup='FIVE_FACTOR_CONFLUENCE',
                       confirmation_utc=bbma_bot.stamp(now),expires_utc=bbma_bot.stamp(now+600))
        cr=dict(strategy='CONFLUENCE',symbol='XAUUSD',analysis_id='independent',health='OK',
                condition=dict(name='BEARISH',side='SELL'),score=4,qualified=True,entry_plan=reference,
                setup=dict(type='FIVE_FACTOR_CONFLUENCE'),reason='Reference only',analysis_utc=bbma_bot.stamp(now))
        runner=SimpleNamespace(c=dict(enabled=True,check_seconds=300,symbols=['XAUUSD']),next_due=0,
                               check=Mock(return_value=cr))
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);cfg=json.loads((Path(__file__).parent/'config.example.json').read_text())
            cfg['symbols']=['XAUUSD'];(root/'config.json').write_text(json.dumps(cfg))
            args=SimpleNamespace(config=str(root/'config.json'),once=True,test_telegram=False,
                                 diagnose_time=False,dashboard=False,dashboard_port=8765)
            with patch.object(bbma_bot,'ROOT',root),patch.object(bbma_bot,'assess',side_effect=RuntimeError('Simulated BBMA failure')),\
                 patch('confluence_strategy.Runner',return_value=runner),\
                 patch.dict('sys.modules',{'MetaTrader5':mt5,'dotenv':SimpleNamespace(load_dotenv=lambda *_:None)}),\
                 patch.dict('os.environ',{'TELEGRAM_BOT_TOKEN':'','TELEGRAM_CHAT_ID':''}),\
                 contextlib.redirect_stdout(io.StringIO()),self.assertLogs(level='WARNING'):
                bbma_bot.run(args)
            snap=Store(root/'dashboard.sqlite3').snapshot()
            rows={r['strategy']:r for r in snap['symbols']}
            self.assertEqual(rows['BBMA']['health'],'BLOCKED')
            self.assertEqual(rows['CONFLUENCE']['health'],'OK')
            self.assertEqual(snap['signals'][0]['strategy'],'CONFLUENCE')
            self.assertFalse(snap['signals'][0]['actionable'])
            self.assertEqual(snap['signals'][0]['tracking']['mode'],'PAPER')
            runner.check.assert_called_once()
