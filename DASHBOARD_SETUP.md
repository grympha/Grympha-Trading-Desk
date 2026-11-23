# Dashboard v1.8

For installation and MT5 Calendar setup, follow **UPGRADE_v1_8.md**. This complete package includes the four-tab dashboard. The following risk/management behavior remains applicable; earlier Trading Economics-specific instructions are superseded by UPGRADE_v1_8.md.

## Dashboard

There is no paper simulation display. Internal legacy tracking records remain for setup deduplication/Telegram continuity; no historical paper outcome is presented as a broker fill.

- Account: MT5 health, broker company/server, balance, equity, currency and account type. Healthy means connected plus account read, not fresh quotes or trade permission. Missing/stale balances are hidden.
- Market cards: current market condition -> setup -> entry/action state. A setup without an accepted broker order remains WAITING, with its block/rejection reason in the ledger.
- Execution ledger and trade journal: broker attempts, pending orders, verified fills/closures and broker P/L; select a row for details. Both strategies remain independent. Cancelled/rejected requests remain WAITING with their raw broker status/note, not CLOSED positions.
- Labels: WAITING, PENDING, RUNNING PROFIT, RUNNING LOSS, REVIEW CLOSE, CLOSED. A zero floating mark uses RUNNING PROFIT with 0.00 displayed. Stale marks are labelled last reported; REVIEW CLOSE never sends a full market close.
- Details: condition/setup at submission, planned entry, actual average fill, ticket, volume, original/current broker SL/TP, TP1, exit price and broker exit reason, risk estimates, and management activity. Older records without saved context show unavailable, never reconstructed context.
- Charts: MT5 closed candles captured at submission, with planned entry/fill/SL/TP1/TP2 overlays. Download chart PNG exports that chart, not a screenshot of the MT5 terminal. It is a historical candle snapshot with latest known broker levels, not tick replay.
- Export CSV downloads broker ledger rows for the chosen strategy. The old server signal-export endpoint is legacy; the new dashboard export uses broker records only.

Account, position and management checks normally run each 15-second main loop; dashboard refreshes every 5 seconds. Confluence retains its separate 300-second schedule and once-per-closed-M15 calculation cache. Local time is shown in the browser (MYT on your PC); storage and calendar comparisons use UTC. Trading Economics calendar times are not shifted by broker feed offsets.

## Risk sizing and estimates

Risk-based sizing is controlled by the dashboard checkbox and risk % equity field (0.01..2%). Equity and symbol limits are read from MT5. The bot uses order_calc_profit to calculate loss at SL in ACCOUNT CURRENCY, sizes to equity * risk_percent / 100, and rounds DOWN to volume_step. A below-minimum lot is blocked, never enlarged. Broker max volume caps the size. Per-trade limits are not a daily or aggregate risk limit.

SL determines lot size; TP1/TP2 determine expected gross profit and reward/risk. Fresh market entry price is used for actual Confluence request sizing; pending entry price is used for BBMA. The pre-submission preview can differ from final sizing as equity/quotes move. Dashboard changes affect NEW entries only, not existing position volume. With risk sizing OFF, --demo-lot (default 0.01) is the fixed size.

Estimated SL loss / TP gains exclude commission, swap, slippage and gaps; they are not guaranteed outcomes. TP estimates assume full volume, so enabled partial close or SL management changes realised outcomes. Floating P/L is the remaining position's broker profit; realised-to-date reports deal cashflows/costs when available. Final realised net P/L is confirmed from closed-position deal history, including reported commission, swap and fees.

## News filter

MT5 Economic Calendar is the default provider in v1.8. Install the included read-only EA in the same terminal and run configure_v1_8.py to select and enable it. No API credential is required. See UPGRADE_v1_8.md for exact installation, clock/freshness/coverage checks, per-pair diagnostics and blackout rules. Data errors block new entries; no silent external-provider fallback is used. Explicit provider=tradingeconomics retains optional legacy support with its own credential, but is not selected by the v1.8 migration.

## Independent management switches

Save controls in the dashboard. Changes apply next main broker loop to existing and new VERIFIED BOT-OWNED DEMO positions; manual/unassigned positions are never managed. Each feature can run independently. Settings persist locally; the server accepts validated JSON from the same loopback origin only.

| Feature | Behavior |
|---|---|
| Partial close at TP1 | Once per ticket, close configured % of the CURRENT remaining volume when a fresh executable quote reaches TP1. Round down. Both close size and remainder must satisfy minimum lot; otherwise skip, never full-close instead. |
| Break-even at TP1 | Move SL to actual entry plus a configurable favorable point offset. Before costs: price break-even does not guarantee net break-even. |
| Trailing SL | Start at configured profit in initial R; trail by configured initial-R distance. BUY SL only rises; SELL SL only falls. |

Initial R uses actual fill to ORIGINAL SL, not a moving stop. SL changes keep broker TP unchanged, obey tick grid/stop/freeze distance, and never loosen an existing SL. If partial close executes, reconcile remaining broker volume before another management action. Existing server-side SL/TP remain when features are disabled; turning a switch off does not undo an earlier stop change.

Durable management intents are written before order_send. An ambiguous/partial/timeout response blocks further automatic management on that position until manual reconciliation; no automatic replay. A skipped/rejected TP1 partial is not retried repeatedly. Broker state appears on the next read; accepted responses do not fabricate fills. Management requires the Python bot running and connected; it is not an EA installed inside MT5. Only attached SL/TP2 and pending expiry remain server-side when the bot stops.

## Validation limits

115 automated tests pass using mocks; 48 synthetic reference parity scenarios pass at the ORIGINAL reference threshold of 3/5 (production remains user-requested 4/5). Browser sample QA verified broker-only views, details, controls and PNG preparation. No RoboForex order, live economic-calendar credential, Telegram transport or profitability test was available here. Test execution on your demo terminal and inspect broker logs before judging results.

Official references:
- https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcprofit_py
- https://www.mql5.com/en/book/automation/experts/experts_close
- https://www.mql5.com/en/book/automation/experts/experts_modify_position
- https://docs.tradingeconomics.com/economic_calendar/country/
- https://docs.tradingeconomics.com/economic_calendar/schema/
- https://docs.tradingeconomics.com/get_started/authentication/
