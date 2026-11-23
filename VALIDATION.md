# v1.9 daily-history validation

129 automated tests pass. Browser sample date selection, Today reset and strategy totals verified. See DAILY_HISTORY.md for detailed coverage and accounting/timezone definitions. Broker history in Windows MT5 is still unverified here.

# Windows launcher validation

122 automated tests pass, including read-only prerequisite checks, missing/unsupported package versions, account type, connection, stale quotes and history guides. Launcher and worker Python files syntax checked. Windows CMD/Tk GUI, package installation, terminal discovery/EA copy and CTRL_BREAK shutdown are not integration-tested on Windows here. No compiled EXE is supplied; START_APP.cmd bootstraps a Python/Tk application.

# v1.8 validation

115 automated tests pass, including MT5 file absence/malformed JSON, heartbeat/event age, terminal/server identity, ready/stopped, clock mismatch, currency coverage, pending horizon, precise/estimated events and disabled mode. Existing 103 tests remain green. No Windows terminal, MetaEditor compiler or actual broker/calendar feed was available here.

# v1.7 validation

103 automated tests pass. Added coverage: account-currency risk sizing BUY/SELL, volume rounding, minimum lot blocking, broker calculation failure, invalid controls, news blackout/pending horizon/stale/missing key/custom currencies/UTC/estimated-time/malformed feed, risk-sized execution, own-ticket partial once, tiny-lot skip, break-even only tightens, independent trailing, uncertain management no resend, real-account switch guard, stale quotes, precheck rejection, and same-origin controls persistence / cross-origin rejection.

Browser QA checked broker-only dashboard, trade detail, chart levels, PNG preparation, Confluence separation and independent sample controls. Live Windows/RoboForex execution and authenticated Trading Economics feed are unverified. No orders or Telegram messages were sent here.

# v1.6 validation

82 automated tests passed. Coverage: original BBMA indicators/rules and JSON logging; Confluence five-factor scoring and strict RSI bounds; H4 mandatory direction; exact ATR SL/TP geometry; closed-bar and +2-hour normalization; persistent once-per-M15 cache; strategy-separated signal/alert identity; no cross-strategy invalidation; references never treated as pending orders or fills; independent engine failures; migration of v1.3 histories; HTTP API/CSV and credential-file isolation.

The unchanged uploaded reference SHA-256 is `1983d838f8ad49b74b1e5e3eedca78fef84ccf829716e9ac85f1d7fe894a1878`. `audit_reference_parity.py` compiles only its `indicators` and `analyze` functions inside an offline mocked environment; no actual MT5 or Telegram calls are made. Across 48 seeded synthetic multi-timeframe scenarios (28 qualified), direction, score, qualification, rounded entry quote and SL/TP levels matched the uploaded reference in all cases. This is implementation parity, not a backtest or profitability evidence. The audit explicitly uses the uploaded reference's original 3/5 threshold. Production now defaults to the user-requested 4/5; this is an intentional threshold change, not a parity claim for qualification at different thresholds.

The browser demo was checked for BBMA/Confluence tab separation, distinct M5/M15 chart labels, five-factor breakdown, conflicting-bias comparison, both-strategy history filtering, reference-only record details, and pause/stale suppression. JavaScript syntax and Python compilation were checked.

Live Windows MT5 sessions, actual broker data, account-specific symbol names, Telegram transport, real fills and trading results were unavailable in the build environment. The opt-in DEMO adapter was tested with FakeMT5 only; no broker order was sent here. Actual Windows/broker execution remains unverified. Paper price/R monitoring and read-only broker floating P/L are displayed separately.

Tracking coverage additionally includes per-strategy/pair single active setup, immutable levels, bid/ask marks, pending-gap handling, SL/TP2 paper closes, TP1 hold, threshold-based close, cached-candidate consumption, stale-data suppression, restart gaps, legacy reference adoption without historical-fill claims, persisted Telegram message IDs and edit failure without duplicate fallback. Browser views for profit/loss/HOLD and stale REVIEW_DATA were checked. No real Telegram message was sent during automated tests.

DEMO coverage: real/contest/netting rejection, permissions, account changes, lot/price/stop/fill/expiry checks, stale quotes, persistent duplicate suppression, uncertain requests without retry, partial fills, capacity including manual exposure, actual deal-to-position mapping, verified realised P/L with costs, own pending cancellation, no market-close action from review flags, restart reconciliation, and default-mode order_send never called. Browser QA checked the DEMO banner, broker ledger, strategy attribution and separate paper P/L.

Account snapshot tests verify broker values, no account identifier/credentials, disconnect, missing account, non-finite money and API exceptions. JavaScript syntax checked. Live MT5 remains unavailable in this environment.
