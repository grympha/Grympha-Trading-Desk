# Grympha Trading Desk · Automated Trading Agent (launcher 1.11)

## Start with a double click

Extract the COMPLETE ZIP into a writable folder such as Documents\BBMA_Bot. Do not run inside ZIP or Program Files. Open the bbma_bot folder and double-click START_APP.cmd. Keep the accompanying console window open (it can be minimized); it enables graceful Stop signaling. This is a Python/Tk desktop launcher with a Windows click-to-start bootstrap, not a standalone compiled EXE or MSI installer.

No PowerShell commands are required for normal setup/run. If Python is missing, the bootstrap shows instructions and offers to open the official Python download page. Install Windows Python 3.11 64-bit with pip, Tcl/Tk and IDLE, Python Launcher, and Add Python to PATH. Then double-click START_APP.cmd again. Other Python versions are not silently substituted. The official 3.11.9 release offers a 64-bit Windows installer; select that file, not embeddable or source archives. Python/MT5 downloads and account login remain user actions, not silent installations.

Python: https://www.python.org/downloads/release/python-3119/
MT5: use your broker's official terminal download, or https://www.metatrader5.com/en/download

## First PC setup

1. Click Setup / Install requirements. The app creates missing config.json/.env from examples, creates a private .venv, and installs requirements from PyPI. Existing config, credentials, database and state are preserved. Installation needs internet access; errors appear in the log. It does not enable DEMO execution. A copied/broken .venv is backed up and recreated. Do not copy .venv between PCs.
2. Install/open RoboForex MT5. Log into your intended account yourself. Do not share passwords with the launcher. The launcher stores only the executable path, not login credentials.
3. Click Choose MT5 terminal and select the intended terminal64.exe. Common Program Files and uninstall-registry locations are searched; portable installations can be chosen manually. Multiple installations require a deliberate choice. The selection is backed up in config.json and does not replace offsets or watchlists.
4. In Market Watch show all nine desired instruments. Click Configure 9 pairs. The helper resolves broker suffixes and uses the same list for BBMA/Confluence; Confluence threshold remains 4/5.
5. Click Install Calendar EA. The EA source is copied to the chosen terminal's MQL5\Experts with a backup. In MT5 press F4, open that EA, press F7 to compile, return to MT5 and attach ONE Calendar Bridge to ONE chart. Verify currencies USD,GBP,JPY,EUR,AUD,CAD,CHF,NZD and keep chart open. Compilation/attachment still requires you; the launcher does not claim to complete those steps. DLL/WebRequest permissions are not required by the bridge.
6. Click Enable MT5 news to select/enable the MT5 provider without changing other controls. Click Calendar diagnostics; ready data reports CLEAR or a scheduled BLOCKED window. Missing/stale data blocks new entries. Initial automatic clock verification waits for advancing quotes. Fix Windows time and investigate feed timestamps rather than guessing offsets.
7. Open H4/H1/M15/M5 charts and load history per symbol. Check requirements reports missing history. It needs >=250 H4/H1/M15 closed bars and >=150 M5 bars; the strategy also checks candle freshness during actual analysis.
8. Click Check requirements. Select any failing row to see its guide. This check sends no orders. It can connect/select symbols and load market history in your MT5 terminal. It is a current snapshot, not a guarantee that connectivity/prices/permissions will remain valid.
9. Keep Analysis only selected, click Start bot, and inspect the browser dashboard. Four tabs, MT5 account panel and calendar source remain included. Open dashboard reopens it at the chosen local port. If the port is occupied, older bot/server must be stopped or another port chosen.
10. For actual DEMO entries, stop analysis mode, choose DEMO execution and click Start bot. The app asks an explicit confirmation, runs readiness checks plus --demo-check, and launches only if no FAIL remains. Real/contest/netting accounts are blocked. Disabled-news WARN is not silently changed: to enforce news protection click Enable MT5 news first. Calendar scheduled BLOCKED is valid data; the execution gate will wait/block the qualifying setup.

## Buttons and behavior

Check requirements: Python 3.11 64-bit, package import/version compatibility, configuration/writable folder, terminal selection/connection, account type, hedging, permissions, quotes, history and calendar. Green PASS/red FAIL/amber WARN. No credentials/account numbers are printed by the checker. Live bot logs can contain trading signals/P&L: keep logs private.
Setup: per-folder virtual environment and packages; installation is initiated only by this button. First install uses example defaults, including 0.5% equity risk and news enabled, while execution stays OFF until explicit DEMO mode. Review risk settings in dashboard before DEMO start.
Time diagnostics: prints raw/normalized quote/bar ages; sends no order and does not change offsets.
Configure 9 pairs / Enable MT5 news: launch the same backed-up configuration helpers previously supplied.
Calendar diagnostics: read-only feed inspection and current per-pair state.
Stop bot: sends CTRL_BREAK to the owned child process; bot handles it as KeyboardInterrupt to run cleanup. Existing broker positions are NOT closed. Server-side SL/TP/expiry remain; local trailing/partial management stops. Some native API calls can delay response: wait and keep this app/console open. Stop is not an emergency liquidation button.
Close app while running: offers to stop and waits for process exit. No automatic forced termination. Do not close the accompanying console with its X while running.

One launcher per folder is enforced with a file lock; duplicate external bot copies on other ports/folders/accounts are NOT fully detected. Run one bot for the intended account. You cannot edit setup/terminal while this launcher owns a running bot: stop it first. Dashboard controls are available through the browser as before.

## Moving to another PC

Extract this complete package on the new PC. Run Setup to create a NEW .venv. Choose that PC's terminal and install/attach its Calendar Bridge. Do not copy old terminal_path or timestamp offsets without verifying them. Calendar file is always discovered under the selected terminal's actual data folder.

For migration of an existing installation, stop old bot first, back up its entire folder, then replace application files only. Preserve .env/config.json/state/database. Existing saved trade controls are retained. A new PC should not resume the same active account via two bot copies.

If Python is already installed but app fails: use installer Modify/Repair to include Tcl/Tk and pip, verify 64-bit Python 3.11, then reopen START_APP.cmd. Work/school security policies or antivirus may block command scripts/Python; obtain normal organizational approval instead of bypassing protections. If pip/MT5 downloads fail, follow the printed error; the app does not silently bypass certificate or network checks.

Validation: launcher Python files syntax checked and read-only prerequisite logic tested with mocks. Windows GUI, CMD bootstrap, pip downloads, EA compilation, real MT5 connection, and Windows graceful Stop signaling still require verification on your PC. No broker order was sent during development.

## Launcher 1.10: REAL account, Start with Windows, system tray

**REAL execution account.** Enter the account number and server in the launcher (or click *Use logged-in MT5 account*), then *Save REAL account*. This writes `real_execution.account_login` and `account_server` in `config.json` (a timestamped backup is kept). REAL mode only places orders when MT5 is logged into exactly that account. No password is stored.

**Start with Windows.** Ticking it adds `Grympha Trading Desk.cmd` to your Windows Startup folder (Task Manager → Startup apps shows it; untick or delete the file to remove it). Options: open hidden in the tray (at Windows start), and *Auto-start bot in* *Analysis only* or *DEMO execution*, which applies whenever the launcher opens (20 s delay at Windows start, 3 s otherwise; up to 10 retries while MT5 logs in). **REAL execution** can also be chosen: it asks for one explicit confirmation (showing the saved account), after which REAL starts — and, with *Auto-restart* on, restarts after a crash — without asking. Every automatic REAL start still runs the REAL account check (same account and server, hedging, permissions) and writes the risk settings to the log. Choosing another mode or unticking the box withdraws the permission; the Windows row shows *REAL auto: ON/off*.

**One-time setup.** Everything is saved: MT5 terminal and REAL account (`config.json`), Telegram (`.env`), risk/lot/pair controls (dashboard database), launcher options and the last execution mode (`launcher_settings.json`). Each launch only runs a read-only requirements check.

**System tray.** Minimizing hides the launcher (and its console) to the tray next to the clock. Click the icon to reopen it; right-click for Open dashboard, Stop bot and Exit. The bot keeps running while hidden.

## Telegram alerts (launcher 1.12)

1. In Telegram, open **@BotFather**, send `/newbot`, follow the steps and copy the **bot token**.
2. Open your new bot in Telegram and press **Start** (or send any message).
3. In the launcher's **Telegram alerts** row: paste the token, click **Find my chat ID**, then **Send test**. When the test arrives, tick **Enable** and press **Save**.

The token and chat ID are saved in `.env` on this PC (a backup is kept; other lines are untouched). The running bot picks up changes on its next message; no restart is needed. Choose which events are sent in the dashboard: **Settings → Telegram notifications** (new setup, order placed, filled, TP1, take-profit, stop-loss, early exit, SL moved, rejected, bot started/stopped and another-bot warning). Alerts are sent in the background and never delay or change trading.
