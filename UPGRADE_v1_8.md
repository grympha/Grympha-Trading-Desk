# Upgrade v1.8 — four tabs + MT5 Calendar

This is a complete package. It includes the four-tab frontend, v1.8 backend, Calendar Bridge EA, MT5 calendar adapter and nine-symbol configuration helper. Do not reapply older v1.7 frontend/tabs patches after installing it.

1. Stop all older bot processes with Ctrl+C. Back up your existing folder while stopped.
2. Copy ALL files inside this ZIP's bbma_bot folder into D:\Grock AI Bot\bbma_bot. Replace application files. Avoid bbma_bot\bbma_bot nesting. Your .env, config.json, state files and database are NOT bundled; preserve them.
3. Run:

```powershell
py -3.11 -m pip install -r requirements.txt
py -3.11 bbma_bot.py --version
py -3.11 configure_v1_8.py
```

Expected: BBMA + Confluence Dashboard v1.8. configure_v1_8.py backs up config and changes ONLY news_filter.provider to mt5 and enabled to true (adds missing 30-minute windows). Watchlists, offsets, risk settings and saved controls are retained. It sends no orders. Do not rerun older configure_v1_7.py as an upgrade step.

4. If nine pairs are already configured, skip this step. Otherwise, with MT5 connected and symbols visible, run `py -3.11 configure_major_pairs.py`. The desired list is XAUUSD, GBPJPY, EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD, shared by both engines. Broker suffixes are resolved from existing config/visible symbols; ambiguous choices block the change. H4/H1/M15/M5 history must be loaded.
5. In the INTENDED MT5 terminal choose File > Open Data Folder. Copy BBMA_MT5_Calendar_Bridge.mq5 from this package into MQL5\Experts. Open in MetaEditor (F4), compile (F7), then attach one instance to one chart. If an older bridge is already attached, remove it first. Keep that chart open. No DLLs, external API key, WebRequest or trade functions are required by the exporter.
6. In EA Inputs verify InpCurrencies=USD,GBP,JPY,EUR,AUD,CAD,CHF,NZD. Existing saved chart inputs may retain the older list. Automatic clock verification needs fresh advancing quotes; initial UNAVAILABLE can last until quotes advance. Do not guess manual offset from an old quote. Sync Windows time. The exporter uses MT5 Calendar server time; it does not use your Python tick/bar offset settings.
7. Confirm file MQL5\Files\bbma_mt5_calendar.json exists in that SAME terminal's data folder. Run:

```powershell
py -3.11 diagnose_mt5_calendar.py
```

This reads the intended terminal and prints the expected local file path, per-pair CLEAR/BLOCKED/UNAVAILABLE, coverage and errors. It does not place orders. CLEAR or an actual news BLOCKED window means the adapter has validated the feed; UNAVAILABLE includes an explanation. A successful read alone is not proof that every calendar event is covered by the provider.
8. Start analysis-only to inspect the interface:

```powershell
py -3.11 bbma_bot.py --dashboard
```

Open http://127.0.0.1:8765 and press Ctrl+F5. Header must show UI v1.8 / Backend v1.8. Four pages: Overview, Demo execution ledger, Actual open positions, Signal journal. News source must show MT5 Economic Calendar. Status DISABLED means config remains off; UNAVAILABLE explains file/clock/connection/coverage problems, not an absent API key.
9. Once verified, stop analysis-only mode, check demo permissions and explicitly enable demo execution if intended:

```powershell
py -3.11 bbma_bot.py --demo-check
py -3.11 bbma_bot.py --dashboard --demo-execution
```

No old setup is traded retroactively. Existing safeguards and independent trade-management switches remain. News blackout is normally 30 minutes before/after relevant HIGH-impact news. An unavailable feed blocks new entries and cancels own pending orders on the next reconciliation; it does not close running positions. Manual positions are never managed. Broker server-side SL/TP remain in effect when bot stops; Python management does not.

Calendar validation: exact terminal data folder and broker server, connected/ready/HEALTHY flags, verified clock, heartbeat <= max(90 seconds, 3 configured heartbeat intervals), event fetch <=15 minutes (or tighter EA limit), calendar coverage covering current/pending entry window, and all symbol currencies exported. Malformed/partial JSON is rejected. The file is re-read before entry gates. No silent Trading Economics fallback. Broker prefixes/suffixes are supported; custom names such as GOLD need news_filter.symbol_currencies overrides.

All comparisons use current server offset and raw server event times projected for the current snapshot. Far-future DST changes can alter projected times; exporter invalidates its cache if observed offset changes. Unknown/all-day event timing receives a whole SERVER-day blackout, including margins. Unscheduled news and provider omissions are not predictable.

If dashboard looks old: verify command-line v1.8 and header UI/backend versions, stop old processes, check you replaced files in the folder you actually run, then Ctrl+F5. Assets carry v1.8 cache keys. Do not launch an old dashboard_server.py from another folder.

115 mock-based automated tests pass; 48 synthetic reference-parity scenarios pass at original 3/5 reference threshold, while production Confluence stays 4/5. Windows MT5 calendar, EA compilation and actual RoboForex execution remain unverified here. No broker order was sent during development.
