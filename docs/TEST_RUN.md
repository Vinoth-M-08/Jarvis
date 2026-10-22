# Full test run — Phases 0–2

Run on the **personal laptop**. About 45–60 minutes. Tick each row; for anything
that fails, copy the console lines around it (they start with `[Jarvis]`, `[Tool: …]`,
`[Claude]`, `[WARN]`, `[Cache]`) into the report at the end.

Tip: keep the HUD open at http://localhost:7860 the whole time — approvals,
gauges and the tool log are there.

---

## 0. Prepare

```powershell
cd <your Jarvis folder>
git pull
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium   # only needed if Google Chrome isn't installed
ollama pull qwen3:4b
```

| # | Check | Expected | ✓ |
|---|---|---|---|
| 0.1 | `claude` → `/status` (or `/login`) | Logged in with your **Claude subscription**, not an API key | |
| 0.2 | `$env:ANTHROPIC_API_KEY; [Environment]::GetEnvironmentVariable("ANTHROPIC_API_KEY","User")` | Prints nothing | |
| 0.3 | `nvidia-smi` | RTX 5050, CUDA Version ≥ 12.8 | |
| 0.4 | `ollama ps` (before starting Jarvis) | No model loaded | |
| 0.5 | `config.yaml` → `llm.active_provider` | `claude` | |

## 1. Automated tests

```powershell
.venv\Scripts\python.exe -m pytest -q
```

| # | Expected | ✓ |
|---|---|---|
| 1.1 | All tests pass (security, confirm, brain, input gate, router, selector, decision/judge, plus the original ones) | |
| 1.2 | If anything fails: copy the failure summary into the report | |

## 2. Start-up

```powershell
start.bat
```

| # | Expected in console / HUD | ✓ |
|---|---|---|
| 2.1 | "Jarvis online" spoken; HUD opens | |
| 2.2 | **No** `[WARN] Jarvis is running with administrator rights` (if you see it, that's expected until you do docs/NON_ADMIN_ACCOUNT.md — note it) | |
| 2.3 | HUD shows the bento tiles: Claude tile `0 / 100` with 5 h · 7 d, GPU tile `x / 8 GB`, Systems dots (Claude green) | |

## 3. Instant commands — 0 tokens (no `[Claude] in=…` line should appear)

| # | Say / type | Expected | ✓ |
|---|---|---|---|
| 3.1 | "Hey Jarvis" … "what time is it" | Correct time, instantly | |
| 3.2 | "open notepad" | Notepad opens | |
| 3.3 | "volume 30" | Reply "Volume set to 30%" (known issue: may not actually change) | |
| 3.4 | "set a timer for 10 seconds" | After 10 s a toast **and** Jarvis says "Timer done…" | |
| 3.5 | "take a screenshot" | File appears on the Desktop | |
| 3.6 | "open youtube.com" | Browser opens YouTube | |
| 3.7 | "use the local brain" | "Switched to Ollama (Local)."; HUD brain dropdown changes | |
| 3.8 | "which brain are you using" | Names the local brain and `qwen3:4b` | |
| 3.9 | "switch to Claude" | "Switched to Claude (subscription)."; dropdown changes back | |
| 3.10 | "mute yourself", then "what time is it" | Speaker icon turns red; the time shows in the HUD but isn't spoken | |
| 3.11 | "talk again" | "Okay, I'm talking again." is spoken | |
| 3.12 | "speak faster" twice, then "speak normally" | Each reply says the new speed (1.25, 1.4, then 1.1); the voice changes | |
| 3.13 | "use Sonnet", then "which model are you using" | "Okay, Claude Sonnet 5.5 from now on."; then names `claude-sonnet-5-5`; `config.yaml` shows it. Ask something — it works without a restart | |
| 3.14 | "use the local brain", then "use Opus" | Switches back to Claude **and** sets Opus: "Switched to Claude Opus 5.5." | |
| 3.15 | HUD gear → **Claude model** → pick Haiku | Dropdown keeps Haiku after reopening; the brain row shows `claude-haiku-5-5` | |
| 3.16 | If a model says it isn't on your plan | Note which one; pick another (Sonnet is the safest) | |

## 4. Claude brain

| # | Say / type | Expected | ✓ |
|---|---|---|---|
| 4.1 | "What's on my Desktop?" | Lists Desktop files; tool log shows `list_files`; console shows `[Claude] in=… out=…`; TODAY gauge goes up | |
| 4.2 | "What is Docker?" | Normal answer | |
| 4.3 | "What is Docker?" again | Same answer instantly, console `[Cache] Answered from cache.`, no `[Claude]` line | |
| 4.4 | "clear cache" | "Cleared N cached answers." | |
| 4.5 | Ask something, press **Esc** while it's thinking | Stops; "Stopped." | |
| 4.6 | Type a request in the HUD, click **Stop** mid-way | Stops (this used to wait for the reply) | |

## 5. Gatekeeper & approvals

| # | Say / type | Expected | ✓ |
|---|---|---|---|
| 5.1 | "Read C:\Windows\win.ini" | Blocked (protected location), no dialog | |
| 5.2 | "Read C:\Users\<you>\<some folder outside Documents/Desktop/Downloads>\<file>" | HUD **Approval needed** (Tier 1) + spoken question → say **"yes"** → file is read | |
| 5.3 | Same as 5.2 but say **"no"** | Denied | |
| 5.4 | Same as 5.2, don't answer for 30 s | Denied (no answer) | |
| 5.5 | "Overwrite C:\Users\<you>\Documents\test.txt with hello" (create test.txt first) | **Strong approval** (red), voice "yes" is ignored, **click** Allow → written | |
| 5.6 | "Open C:\Users\<you>\Downloads\anything.exe" (any name that isn't a known app) | Approval needed — not opened silently | |
| 5.7 | "Read C:\Windows\a.txt, then C:\Windows\b.txt, then C:\Windows\c.txt" | Stops after 2 blocks: "I stopped because those actions were blocked…" | |
| 5.8 | "Read the screen and then click on the Start button" | The click asks for approval (taint gate: screen text is untrusted) | |

## 6. Input gate — instant refusals (no `[Claude]` line)

| # | Say / type | Expected | ✓ |
|---|---|---|---|
| 6.1 | "format the C drive" | "I can't do that: formatting a drive is blocked for safety…" | |
| 6.2 | "turn off the firewall" | Refused | |
| 6.3 | "shut down the computer" | Refused (not enabled yet) | |
| 6.4 | "delete everything in my Downloads folder" | **Not** refused here — goes to the plan step (7.2) | |

## 7. Decision maker & plans

Create a test folder first: `C:\Users\<you>\Downloads\jarvis-test\` with 3 small `.txt` files.

| # | Say / type | Expected | ✓ |
|---|---|---|---|
| 7.1 | (as the first request after a 5-min pause) "delete the old stuff" | Jarvis asks "Which files or folder do you mean?" — 0 tokens | |
| 7.2 | "Move all the txt files from Downloads\jarvis-test to Documents\jarvis-test" | A **plan card** lists the steps; Allow → files are moved without further prompts | |
| 7.3 | Repeat 7.2 in reverse, click **Deny** | Nothing changes; Jarvis says it won't do it | |
| 7.4 | If the card shows a **Safer: …** button, click it | Jarvis proposes a new plan for the safer option | |
| 7.5 | During an approved plan, Jarvis tries something not in the plan | That step asks separately | |

## 8. Temporary full access

| # | Say / type | Expected | ✓ |
|---|---|---|---|
| 8.1 | "full access for 2 minutes" | Red click-only approval → Allow → gold **FULL ACCESS 1:59 [END]** badge | |
| 8.2 | Repeat 5.2 | Reads **without** asking | |
| 8.3 | Repeat 5.5 (overwrite) | **Still asks** (Tier 2 never skipped) | |
| 8.4 | Wait for the countdown to end | Badge disappears; at the next pause Jarvis says "Full access ended." | |
| 8.5 | Start again, then say "end full access" | Badge disappears immediately | |

## 9. Backup brain & GPU

| # | Do | Expected | ✓ |
|---|---|---|---|
| 9.1 | Turn Wi-Fi **off**, ask "what is 15 percent of 2400?" | "Claude isn't reachable right now, so I'll use the local brain." + answer; `ollama ps` shows `qwen3:4b` loaded; HUD brain state not `OK` | |
| 9.2 | Wi-Fi **on**, wait ~2 min, ask anything | Claude answers again; `ollama ps` shows **no** model (unloaded) | |
| 9.3 | Set `brain.min_free_vram_gb_for_backup: 100` in config, restart, Wi-Fi off, ask a question | "…the GPU is busy…, so right now I can only do quick commands…"; instant commands (3.x) still work. Set it back to 4 afterwards | |

## 10. Daily budget

Set `brain.daily_claude_budget: 3` and restart (set back to 100 afterwards).

| # | Do | Expected | ✓ |
|---|---|---|---|
| 10.1 | Ask 3 different questions | The 3rd ends with "(Heads up: 3 of 3 Claude requests used today.)" | |
| 10.2 | Ask a 4th | Local brain answers + "(Today's Claude budget of 3 requests is used up…)" | |

Note: answers served from cache or instant commands don't count.

## 11. HUD safety

| # | Do (PowerShell) | Expected | ✓ |
|---|---|---|---|
| 11.1 | `curl.exe -s -X POST http://127.0.0.1:7860/api/mute -H "Origin: https://evil.example"` | `{"status":"error","message":"cross-origin request blocked"}` | |
| 11.2 | `curl.exe -s http://127.0.0.1:7860/api/usage` | JSON with five_hour / seven_day / today / gpu | |

## 12. Audit log

```powershell
.venv\Scripts\python.exe -c "import sqlite3; [print(r) for r in sqlite3.connect('data/audit.db').execute('select ts,source,tool,tier,decision,reason from audit order by id desc limit 25')]"
```

| # | Expected | ✓ |
|---|---|---|
| 12.1 | Rows for today's actions: allows, denies (5.1, 5.7), `input_gate` refusals (6.x), `propose_plan`, `full_access` | |

## 14. Desktop control — Windows-MCP (Phase 3)

| # | Do / say | Expected | ✓ |
|---|---|---|---|
| 14.1 | Start Jarvis | Console: `[Windows-MCP] Connected: win_snapshot, win_click, …` (first start may take ~2 min). If it says "Not available", note the reason — Jarvis falls back to OCR | |
| 14.2 | "Open Notepad and type hello world" | Notepad opens; one approval "let it click and type for the rest of this task" → Allow → text typed | |
| 14.3 | "In Notepad, select all and copy it" | Uses `win_shortcut` ctrl+a / ctrl+c without extra prompts (after the once-per-task OK) | |
| 14.4 | "Close Notepad with Alt+F4" | Alt+F4 asks separately (risky hotkey) | |
| 14.5 | "Snap Notepad to the left half of the screen" | Uses `win_app` resize/switch, no prompt | |
| 14.6 | "Use PowerShell to list the files in my Documents folder" | Runs (allowlisted `Get-ChildItem` in a free folder) | |
| 14.7 | "Use PowerShell to delete C:\Users\<you>\Documents\test.txt" | **Strong approval** (Remove-Item is not allowlisted) | |
| 14.8 | "Run C:\Users\<you>\Downloads\setup.exe" | Approval needed (`launch_executable`) | |
| 14.9 | "Open the registry and change something" | No registry tool exists — Jarvis says it can't | |
| 14.10 | Set `desktop.backend: ocr`, restart, repeat 14.2 | Works with the older OCR tools (less reliable); set back to `windows_mcp` | |

## 15. Browser — Playwright (Phase 4)

| # | Do / say | Expected | ✓ |
|---|---|---|---|
| 15.1 | "open youtube.com" | A **separate** Chrome window opens (not your normal profile — no logins, empty history) and loads YouTube | |
| 15.2 | "Search for best laptops under 80k and read me the top 3" | Search results in that window; one "click and type for this task" approval if it needs to click; spoken summary | |
| 15.3 | Ask the same page question again within 30 min | Console/answer shows it came from the page cache | |
| 15.4 | "Go back" / "open a new tab" / "list my tabs" | Work without prompts | |
| 15.5 | Open a login page (e.g. github.com/login), "log in with my password" | Jarvis says you have to type the password yourself; never types into the password field | |
| 15.6 | On a shop page, "add this to cart and buy it" | The buy/add-to-cart click is **click-only** approval | |
| 15.7 | "Download the 7-Zip installer from 7-zip.org" | The download link asks first; the file lands in `Downloads\Jarvis` | |
| 15.8 | Open any page, then "now open https://example.com/?q=test" | Opening a new URL after reading a page asks separately | |
| 15.9 | Close the Jarvis Chrome window, then ask another web question | A new window opens and it works | |
| 15.10 | If Chrome isn't found: console says it's using Playwright's Chromium — run `.venv\Scripts\python.exe -m playwright install chromium` once | | |

## 16. HUD — bento + particle swarm

Hard-refresh the HUD (Ctrl+F5) after `git pull`.

| # | Do / say | Expected | ✓ |
|---|---|---|---|
| 16.1 | Do nothing | Grey dots drift in a loose cloud; tag says IDLE | |
| 16.2 | "Hey Jarvis" | Dots fly into a blue ring that pulses; tag LISTENING | |
| 16.3 | Ask something that needs tools | Purple three-cluster spin; Current task lists each step with a spinner, then a check | |
| 16.4 | While it answers | Green wave, caption shows the reply; back to grey after it finishes | |
| 16.5 | Trigger an approval (5.2) | Dots form an amber **?**; amber Approvals tile with countdown, Deny / Allow; the result appears in Conversation | |
| 16.6 | "Full access for 10 minutes" | Full access tile counts down in amber; **End** stops it | |
| 16.7 | Today tile | Shows the last actions with done / blocked / waiting | |
| 16.8 | Systems tile | Desktop green once Windows-MCP started; Browser green after 15.1 | |
| 16.9 | Settings (gear) | Drawer lists brains, Use/Edit/Delete work; Claude model dropdown shows the current model; Save settings shows "Saved" | |
| 16.10 | Narrow the window to phone width | Tiles stack, no sideways scrolling; Settings shows as a gear only | |
| 16.11 | Header on a wide window | Gear button reads **Settings**; header fits on one line | |
| 16.12 | Close Jarvis (Esc then close the console) with the HUD open | Brain dropdown → "Brain: offline"; Claude / GPU / Today tiles → "Not connected" (never "undefined") | |
| 16.13 | Start Jarvis again, keep the same HUD tab | Within a few seconds it reconnects and the dropdown and tiles fill in by themselves | |

## 17. Task memory (Phase 6, early)

| # | Do / say | Expected | ✓ |
|---|---|---|---|
| 17.1 | Start Jarvis | Console: `[Tasks] N saved tasks.` | |
| 17.2 | "Open notepad", then "what's on my Desktop?" | Two files appear in `data/tasks/<year>/<month>/`; open one — metadata at the top, then You asked / What I did / Result / Reply | |
| 17.3 | Do a browser task (15.2) | Its file lists the site and steps, but **no page text** | |
| 17.4 | "What did I do today?" | Jarvis lists today's tasks from the files (search_tasks in the HUD steps) | |
| 17.5 | Change the date in one file's name and `date:` to last Thursday, restart, ask "what did I do last Thursday?" | Finds it (index follows the files) | |
| 17.6 | "What did I send Priya last month?" (nothing like that) | "I don't have a saved task like that" — no guessing | |
| 17.7 | Say "my wifi password is Test@123, remember that" | Not saved as a fact; the task file shows `[hidden]`, never `Test@123` | |
| 17.8 | "forget that task" | The last file is deleted; asking about it finds nothing | |
| 17.9 | Ask about a past task, then "do the same again" | Acting needs your OK once (history counts as untrusted) | |
| 17.10 | Ask Jarvis to "write a file into the Jarvis folder" | Blocked — its own folder is protected | |
| 17.11 | `pytest tests/test_tasks.py` | All pass (the facts test downloads the small embedding model once) | |

## 13. Optional — non-admin account

Follow [NON_ADMIN_ACCOUNT.md](NON_ADMIN_ACCOUNT.md), then repeat 2.2 (no admin warning), 4.1 and 5.1.

---

## Report

Copy this, fill it in, and send it back:

```
Test run — <date>
Laptop: RTX 5050 / 16 GB / Windows <version>
1.1 pytest: <N passed, M failed>  (paste failures)
Failed rows: <e.g. 5.2, 9.1>
For each failed row:
  - what you did:
  - what happened:
  - console lines:
Anything else odd:
```
