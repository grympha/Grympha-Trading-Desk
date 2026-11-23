Current v1.8 installation/news provider instructions: **UPGRADE_v1_8.md**. MT5 Calendar is now supported; references below to the v1.7 Trading Economics requirement apply only to that older provider.

# DEMO execution v1.8

Read DASHBOARD_SETUP.md for current upgrade commands, news credentials, risk sizing and independent management controls. This document replaces the v1.6 execution policy summary.

Default command remains analysis-only. Actual entry and management requests require --demo-execution and a verified DEMO hedging account. BBMA uses native pending stops after H1 -> M15 -> M5 qualification. Confluence uses a deliberately added market-entry policy when a new reference qualifies at 4/5 with mandatory H4 direction; the original reference strategy has no separate entry trigger.

SL and TP2 are attached at submission. TP1 can optionally trigger a partial close and/or break-even. Trailing can be independently enabled. All management features default OFF. Signal-based CLOSE_REVIEW does not automatically close a position. Broker SL/TP2 can close it; enabling TP1 partial sends a partial market-close request only to the verified own position.

Risk controls saved in the dashboard determine risk sizing versus fixed lot. v1.8 preserves existing risk/management settings. MT5 Calendar is supported via the included EA; configure_v1_8.py selects and enables that provider. Missing/stale/error calendar blocks new entries and cancels own pending orders; open positions are not closed for news. No Trading Economics credential is needed for MT5 Calendar.

Max six exposures by default counts all open account positions/pending orders plus unresolved own submissions. Uncertain submissions are never automatically resent. Partial entry fills are reconciled without sending remaining requested volume. No historical setup is traded retroactively. Keep dashboard.sqlite3; it contains ledger, management intents and saved controls. The account must not be switched while running. Observed changes block sends but account-switch checks cannot be atomic with external terminal operations.

Stopping the bot does not close positions; server-side protections remain. Local Python management and news monitoring stop until the bot resumes. Real broker demo integration remains to be verified on your PC; all automated tests here used mocks.
