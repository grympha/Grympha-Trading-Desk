# Portfolio Risk Guard + Execution Audit + Health Monitor — v1.12

This release focuses on the two requested areas. Existing strategy rules, shared
pair lots, demo-only account guards, position management and AI SHADOW remain.
It does not add a daily-loss limit, automatic close-all, automatic restart,
counterfactual replay, remote access or model-driven execution.

## Portfolio Risk Guard

Default: MONITOR ONLY. Initial total/per-pair/per-currency limits of 3 / 1.5 / 2
percent are editable placeholders, NOT a recommended risk budget. Existing
saved controls take precedence on restart. Portfolio-limit blocks are not introduced
by default; entry submission retains a final executable-quote freshness check. Choose your own limits and explicitly enable enforcement.

Dashboard: Execution & Health > Portfolio limits.
- Total risk (% equity).
- Per exact broker pair risk (% equity), combining both strategies and manual
  positions/pending orders in that instrument.
- Per-currency gross stop-risk concentration (% equity).
- Enforce portfolio limits checkbox and Save portfolio limits.

Only NEW bot submissions are gated. Enabling the guard does not resize/close
open positions, cancel existing pending orders, or disable existing management.
The executor checks projected exposure before its broker precheck and again
immediately before send. Its own PRECHECK reservation is excluded from the
second calculation, preventing double counting. The account guard is retained.
Other unresolved submissions contribute an UNKNOWN exposure condition.

Scope is the entire connected account, not only watched pairs or bot magic
numbers. Manual/other-EA exposure counts toward admission. The guard has no
permission to manage those manual positions.

### Calculation basis

MT5 order_calc_profit supplies estimates in account currency:
- OPEN POSITION: additional downside from a fresh executable bid (BUY) or ask
  (SELL) to the broker SL. This includes giving back floating profits even when
  the SL is already above the original entry on a BUY.
- PENDING ORDER: remaining pending volume, intended execution price to SL.
  Stop-limit orders use their stop-limit price, when available.
- PROPOSED ENTRY: request volume/price to SL.

Risk is not netted between opposing positions. Currency legs use broker metadata
where available, with a known-major/metal symbol fallback. Each currency/metal
leg receives the FULL trade stop-risk value in account currency. Gross/long/short
values are a concentration PROXY, not currency units, notional exposure, measured
correlation, or a guarantee of simultaneous worst-case loss. Currency buckets
must not be added together to recreate total portfolio risk (legs repeat risk).
Strategy buckets use magic attribution; matching magic alone is not proof of
ownership. Portfolio limits do not rely on strategy ownership.

Missing SL, equity, quote, calculation, usable price/volume, unsupported order
state, unresolved requests or unmapped currency legs are UNKNOWN. Known subtotal
is shown separately; UNKNOWN is not zero or an assumed empty account. When
ENFORCING, unknown exposure blocks new entries. When MONITOR ONLY, it is reported
without changing existing admission rules.

All stop-risk estimates exclude commissions, swaps, fees, gaps and slippage.
They are not a guaranteed maximum loss or a margin simulation. External/manual
activity can change after the final snapshot; no atomic broker-wide reservation
is possible here. Existing pending orders can still fill after the guard is
activated, since this release does not cancel or modify them.

Unknown issues should be diagnosed from the row-level data-quality reason.
Verify real broker stops, quote availability, exact symbol metadata and account
connection. Never substitute a fabricated SL or guessed currency to bypass it.

Optional config bootstrap (used only until saved controls exist):
  "portfolio_guard": {
    "enabled": false,
    "total_percent": 3.0,
    "pair_percent": 1.5,
    "currency_percent": 2.0
  }
Controls persist separately from existing trade_controls in dashboard.sqlite3.

## Execution audit

An additive, append-only table records:
- Entry requests and their executable quote/spread snapshot.
- Portfolio blocks and terminal states.
- Broker order_check and order_send timing (perf_counter milliseconds), retcode,
  operation, request fields and available response fill metrics.
- Later broker-confirmed fill, signed price/points/R slippage versus requested
  price, and observed SL/TP/volume changes.
- Management prechecks/sends (partial close, BE/trailing protection) and pending
  cancellations through the same audited broker-call wrappers.

Positive signed slippage is adverse for the request direction; negative is
favourable. Pending acceptance is not a fill. Response-fill events and later
confirmed-fill events are separate evidence and must not be counted as two
trades. API latency measures the Python broker call, not total exchange fill
latency or a guarantee of broker internal timing. Full-volume and partial-fill
outcomes remain in the existing ledger.

The dashboard shows latest 200 events with details; counts and mean send latency
use all current-account audit rows. Aggregate sends/checks include management
and cancellation operations, not just new entries. Current-account audit scope
comes from the fresh portfolio snapshot; old accounts are not combined.

No credentials, raw account logins or arbitrary broker response dumps are added.
Unknown fill metrics stay unavailable. Optional metric failure cannot convert a
successful broker response into a retry. Audit-write errors are logged/degraded;
the existing durable execution ledger remains mandatory and unchanged. Audit
history is not a cryptographically tamper-proof or broker-independent record.
No new automatic resend of uncertain order/management requests is introduced.

## Health monitor and duplicate prevention

The app writes separate heartbeat/status records for Scanner, Risk Check,
Executor, Dashboard + Journal and Research. Scanner includes blocked-check counts;
risk state distinguishes known/unknown/blocked admission; executor exposes failed
reconciliation/account states; journal/research failures are marked degraded.
Snapshots and card timestamps let the UI distinguish stale and last-reported data.

The main bot also holds an OS file lock in bot.runtime.lock for its lifetime.
A second normal bot in the SAME folder exits before opening another MT5 session.
Read-only diagnose-time/test-Telegram/demo-check paths are exempt. This lock does
not coordinate separate copies in other folders, Windows users or other PCs.
Do not run two execution copies against the same account.

A temporary external watchdog starts automatically during normal continuous bot
runs (not --once or diagnostics). It checks parent process liveness and SQLite
heartbeat every two seconds. Stale threshold: max(60 seconds, poll_seconds * 6).
It distinguishes clean stop, application-error shutdown, dead process, stale heartbeat, database unavailable,
unknown process state and a replacement session.

Local output files beside the database:
- health_watchdog.json: latest observer state.
- health_alerts.jsonl: abnormal status transitions, with timestamps.
Recent local alerts are visible in Execution & Health after restart as well.

The watchdog never opens MT5, changes orders, restarts the bot or sends remote
messages. If the bot dies, its in-process dashboard also stops. The local watchdog
can leave a report, but cannot keep the dashboard available or notify your phone.
A powered-off/failed PC cannot be monitored remotely by a process on that same PC.
Windows/Python security restrictions may prevent launching the observer; this is
reported as unavailable rather than silently treated as healthy.

To disable the external observer only:
  "health_monitor": {"watchdog_enabled": false}
Module health reporting and the same-folder application lock remain enabled.
Normal shutdown signals the observer to exit; no second launcher is needed.

## Validation

203 Python tests passed. Portfolio tests cover current-equity risk, manual and
pending exposure, currency gross vs opposite directions, separate caps, missing
SL/equity/calculation/metadata/quotes, unresolved reservation exclusion and the
second guard after a broker precheck changes account exposure. Tests also verify
management continues and existing pending orders are not cancelled by the toggle.
Audit tests cover accepted, rejected, ambiguous, confirmed-fill and account-scoped
events without duplicate sends. Existing strategy/execution parity tests remain.

A real local subprocess watchdog was tested for clean stop and dead-parent alert;
OS locking was exercised. These runs used the Linux test environment. Windows
process-liveness APIs, Windows process flags, live broker calculations and actual
MT5 order execution still require validation on the user's Windows PC.
JavaScript syntax, portfolio/audit/health UI escaping/staleness/dirty-input/detail
handling, seven-tab wiring, AI/Trade Review UI checks and all 17 chart-clearing
regressions passed. No real browser layout session was exercised.
