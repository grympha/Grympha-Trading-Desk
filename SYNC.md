# Syncing Grympha Trading Desk across PCs

Code and docs are shared through the **private GitHub repo**
(`https://github.com/grympha/Grympha-Trading-Desk`). Each PC keeps its **own** account,
settings and trade history locally — those files are gitignored and **never** travel through git:

- `config.json` (account, terminal path, offsets, ORB flags)
- `.env` (Telegram token)
- `dashboard.sqlite3` (+ `-wal` / `-shm`) — trade ledger / history / reviews
- `state.json`, `confluence_state.json`, `orb_state.json`, `launcher_settings.json`
- `decisions.jsonl`, `confluence_decisions.jsonl`, `orb_decisions.jsonl`, `logs/`, `backups/`

**Mental model:** GitHub is the shared *code* hub; *config and data are always local.*
Whoever changes code pushes; every other PC pulls.

---

## A. Push changes (on the PC that made them)

```powershell
git add -A
git commit -m "describe what changed"
git push
```

If the push is rejected with `fetch first`, someone else pushed in the meantime — run
`git pull` to merge, then `git push` again.

---

## B. Get the update (on the PC receiving it)

1. In the launcher: **Stop bot**, then close the launcher window.
2. Pull the latest code:
   ```powershell
   git pull
   ```
   (First pull on a private repo asks you to sign in to GitHub once.)
3. Reopen the launcher, **Start bot**, and **hard-refresh** the dashboard (Ctrl+F5).

`git pull` only updates tracked code/docs. Your `config.json`, `.env`, database and logs are
left exactly as they are.

### If `git pull` complains about local changes
You have uncommitted edits to tracked files. Either keep them:
```powershell
git stash
git pull
git stash pop
```
…or discard them and take the repo version:
```powershell
git checkout -- .
git pull
```
(This never touches gitignored files like `config.json` or the database.)

---

## C. First-time setup on a PC that isn't a git checkout yet

Run **once**, inside that PC's existing Grympha folder (back up `config.json`, `.env` and
`dashboard.sqlite3` first as a precaution):

```powershell
cd "C:\Path\To\Grympha Trading Desk"
git init
git remote add origin https://github.com/grympha/Grympha-Trading-Desk.git
git fetch origin
git checkout -f -b main --track origin/main
```

This overwrites the **code/docs** with the latest version while leaving `config.json`, `.env`,
the database, state and logs untouched. After this, updating is just `git pull` (section B).

---

## D. Per-machine settings are NOT synced — set them on each PC

Because `config.json` is local, these must be configured on each PC separately:

- **Broker timestamp offset** (RoboForex = UTC+2 winter / +3 summer):
  ```json
  "tick_timestamp_offset_hours": 2,
  "bar_timestamp_offset_hours": 2,
  ```
- **ORB trading:** Settings → Strategy setups → ORB → *Allow ORB to place trades*.
- **ORB auto early-exit (REAL):** `"ORB": true` under `real_execution.auto_early_exit` in `config.json`.
- **Account / Telegram:** each PC uses its own `config.json` and `.env`.

---

## Quick reference

| Goal | Command(s) |
|------|-----------|
| Send my code changes up | `git add -A` · `git commit -m "..."` · `git push` |
| Get the latest code | Stop bot → `git pull` → restart → Ctrl+F5 |
| See what changed locally | `git status` |
| See recent history | `git log --oneline -10` |
| Discard my local code edits | `git checkout -- .` |
