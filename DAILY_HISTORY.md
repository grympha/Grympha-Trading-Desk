# Today History — dashboard v1.9

The Overview page now has Today History with a date picker, Today button and scope selector (All account trades, Bot magic numbers only, BBMA, Confluence). The source is MT5 history_deals_get, not paper outcomes or the signal journal. All account scope includes manual trades and other instruments, not only the watchlist. Bot/strategy scopes use known magic numbers 816001/816002; they are not a substitute for independently verified ticket ownership when other programs reuse those values.

Displayed values:
- Total trades: distinct nonzero position IDs with exit, close-by or reversal deals on that selected day. Partial exits count once per position per day. A position partially exited on different days can count on each day; this is not a count of new entries, raw deals or strictly fully closed positions.
- Net profit/loss: sum of profit + commission + swap + fee on BUY/SELL deals booked on that day. Includes entry commissions on that day, even if no exit occurred. Costs booked on a different day stay on that different day. This is daily trading cashflow, not lifetime P/L of all positions closed on the day.
- Gross P/L and attached costs shown separately. Deposits, withdrawals, credits and separate non-BUY/SELL balance/commission adjustments are excluded. This is not full balance reconciliation. Floating P/L of still-open positions is excluded and remains on Actual open positions.
- Currency comes from the current MT5 account; USD is not hardcoded.

Day boundary defaults to Malaysia UTC+08:00, 00:00 through the next 00:00 (end exclusive). This fixed offset matches MYT. It is configurable via history_timezone_offset_minutes (default 480). History timestamps follow documented UTC by default. history_timestamp_offset_hours (default 0) is an independent raw-deal correction; do NOT copy quote/bar offsets into it without verifying deal timestamps. The adapter queries a padded window then filters exactly by the selected local day.

Current day data refreshes no more often than every 30 seconds, serviced by the bot's main loop. Selected historical days are queued to the bot, usually loaded within one loop and refreshed after five minutes while selected. History is cached in the existing SQLite database separately by account/server identity and date. No account number or account-scope hash is returned by the daily API. A changed account never reuses the prior account's result. Old history is available only when the active connected account's scope is known.

LOADING means waiting for the next bot history read. UNAVAILABLE means history cannot be read; amounts/counts are withheld, not shown as zero. READY with zero deals is a successful empty broker response. If the bot stops, displayed results are marked last reported/stale. The broker/terminal must provide the requested history; a successful empty response cannot independently prove complete broker history retention.

Upgrade:
1. Stop the bot/launcher; back up your folder while stopped.
2. Copy ALL application files from this complete package into your existing bbma_bot folder. Preserve .env, config.json, state, database and verified timestamp offsets.
3. Double-click START_APP.cmd and start normally. No new pip packages or separate history configuration script are required.
4. Open dashboard and Ctrl+F5. Header must show UI v1.9 / Backend v1.9.
5. In Overview choose a date/scope. Today restores the current MYT date. Calendar/news controls and four tabs remain included.

Source/launcher setup guidance remains in LAUNCHER_GUIDE.md and UPGRADE_v1_8.md; the current bot version is v1.9. Do not overwrite this package with older dashboard patches.

129 automated tests pass. New tests cover MYT midnight boundaries, attached costs, duplicate ticket handling, deposit exclusion, empty vs unavailable history, account changes, separate timestamp correction, account-scoped caching, historical date queue and HTTP date validation. Browser sample QA verified date changes, Today reset, per-strategy summary and an illustrative 4 trades / +25 USD example. Windows/RoboForex history integration remains unverified here. No order was placed during development.
