# Run Jarvis as a standard (non-admin) Windows user

> PLAN.md §5.4 / Phase 1. The gatekeeper is code — code can have bugs. Running
> Jarvis as its own **standard** Windows user puts a real OS wall behind it:
> even if a check is missed, Windows itself refuses to let Jarvis touch system
> folders, other users' files, or anything you didn't share with it.

Do this on the **personal laptop**. Takes about 20 minutes. You need your own
(admin) account for steps 1–3.

---

## What changes

| | Before | After |
|---|---|---|
| Jarvis runs as | you (often an admin) | user **`jarvis`** (standard user) |
| Can write `C:\Windows`, Program Files | yes, with admin rights | **no** — Windows blocks it |
| Can read your other folders | yes | **only** the folders you share in step 3 |
| Your browser logins, saved passwords | reachable | **not reachable** (different profile) |
| Apps Jarvis opens | open as you | open as `jarvis` (e.g. Chrome with an empty profile) |

---

## 1. Create the `jarvis` user

Open **PowerShell as Administrator** (Start → type *PowerShell* → *Run as administrator*):

```powershell
$pw = Read-Host -AsSecureString "Password for the jarvis user"
New-LocalUser -Name "jarvis" -Password $pw -FullName "Jarvis agent" -Description "Runs the Jarvis voice agent" -PasswordNeverExpires
```

Check it is **not** an administrator (the command should print nothing for `jarvis`):

```powershell
Get-LocalGroupMember -Group "Administrators" | Where-Object Name -like "*jarvis*"
```

New local users are standard users by default. Never add `jarvis` to Administrators.

## 2. Put Jarvis where the `jarvis` user can run it

Your own user folder isn't readable by other users, so move the repo to a shared place:

```powershell
New-Item -ItemType Directory -Force C:\Jarvis | Out-Null
Move-Item "$env:USERPROFILE\path\to\Jarvis\*" C:\Jarvis   # adjust the source path
icacls C:\Jarvis /grant "jarvis:(OI)(CI)RX"                 # read + run the code
New-Item -ItemType Directory -Force C:\Jarvis\data | Out-Null
icacls C:\Jarvis\data /grant "jarvis:(OI)(CI)M"            # write its own data (audit log, memory, usage)
```

Python must be readable by `jarvis` too:
- If you installed Python **"for all users"** (under `C:\Program Files\Python312`), it's fine.
- If it was a per-user install (under your `AppData`), reinstall Python 3.12 with
  **"Install for all users"** ticked, then recreate the venv in step 4.

## 3. Share only the free folders

These match `tools.allowed_paths` in `config.yaml` (Documents, Desktop, Downloads).
Give `jarvis` **Modify** on each — nothing else in your profile:

```powershell
$me = $env:USERPROFILE
foreach ($f in "Documents", "Desktop", "Downloads") {
    icacls "$me\$f" /grant "jarvis:(OI)(CI)M"
}
```

Because `~` means the **jarvis** user's home when Jarvis runs as `jarvis`,
change `config.yaml` to full paths of **your** folders:

```yaml
tools:
  allowed_paths:
  - C:/Users/<you>/Documents
  - C:/Users/<you>/Desktop
  - C:/Users/<you>/Downloads
```

To share another folder later, add it to both `icacls` and `allowed_paths`.

## 4. First-time setup as `jarvis`

Open a terminal **as the jarvis user** (from your own account):

```powershell
runas /user:jarvis "powershell -NoExit -Command cd C:\Jarvis"
```

In that window:

```powershell
whoami                                   # → <pc>\jarvis
uv venv --python 3.12 .venv              # if the venv needs recreating
uv pip install --python .venv\Scripts\python.exe -r requirements.txt pip
.venv\Scripts\python.exe -m spacy download en_core_web_sm
claude                                   # then /login with your Claude subscription
```

Notes:
- **Claude login is per user** — `/login` must be done here, as `jarvis`.
- **Ollama** runs as a background service on `localhost:11434`; `jarvis` can use it as is.
- **Speech models** (Whisper, Kokoro, wake word) download again into the `jarvis`
  profile on the first run (~1–2 GB, one time).

## 5. Microphone access for `jarvis`

Windows mic permission is per user. Sign in to the `jarvis` account once
(Start → your account picture → *Switch user*), then:

**Settings → Privacy & security → Microphone** → turn on *Microphone access* and
*Let desktop apps access your microphone*. Sign out of `jarvis` and back into your account.

## 6. Start Jarvis as `jarvis`

Make a shortcut on your desktop with this target:

```
C:\Windows\System32\runas.exe /user:jarvis /savecred "C:\Jarvis\start.bat"
```

`/savecred` asks for the password the first time and then stores it in **your**
Windows Credential Manager. Leave it out if you prefer typing the password each time.

Jarvis opens on your desktop as usual — the voice, the HUD at
`http://localhost:7860` and the tools all work, but run with `jarvis`'s rights.

## 7. Check it worked

In the Jarvis console at startup you should **not** see:

```
[WARN] Jarvis is running with administrator rights ...
```

Then, in a `runas /user:jarvis powershell` window:

```powershell
whoami                                                   # <pc>\jarvis
whoami /groups | Select-String "S-1-5-32-544"           # nothing (not in Administrators)
"test" | Out-File C:\Windows\jarvis-test.txt             # Access denied  ✔
"test" | Out-File "C:\Users\<you>\Documents\jarvis-test.txt"   # works  ✔ (delete it after)
Get-ChildItem "C:\Users\<you>\AppData"                    # Access denied  ✔
```

And by voice: *"Read C:\Users\<you>\AppData\Local\something.txt"* — the gatekeeper
asks for approval; even if you allow it, Windows denies the read.

## Limits

- Windows UI automation (typing, clicking) works across users on the same screen,
  so desktop control still goes through the gatekeeper — the account doesn't
  restrict *what Jarvis can click*, only *what files and settings it can change*.
- Approving something in the HUD can't give `jarvis` more rights than Windows does.
  To widen access, change `icacls` and `allowed_paths` yourself.

## Undo

```powershell
foreach ($f in "Documents", "Desktop", "Downloads") { icacls "$env:USERPROFILE\$f" /remove jarvis }
icacls C:\Jarvis /remove jarvis
Remove-LocalUser -Name "jarvis"
```

Then delete `C:\Users\jarvis` if it exists, and remove the saved credential from
**Credential Manager → Windows Credentials** if you used `/savecred`.
