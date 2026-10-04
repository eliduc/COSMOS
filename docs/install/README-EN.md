# Installing and Removing COSMOS

The kit is meant for a non-specialist: the program examines the computer
itself, decides on its own everything that can be inferred from the
facts, and asks only about the things a person is responsible for. The
dialogue runs in one of five languages — English, Arabic, Uzbek, Kazakh,
Russian.

## What is in the kit

| File | What it is for |
|---|---|
| `INSTALL-COSMOS.cmd` (also `УСТАНОВИТЬ-COSMOS.cmd`) | the one you click to install |
| `UNINSTALL-COSMOS.cmd` (also `УДАЛИТЬ-COSMOS.cmd`) | the one you click to remove |
| `install_bootstrap.ps1` | preparation: finds Python and git, and installs what is missing if you agree |
| `uninstall_bootstrap.ps1` | preparation for removal: finds Python and the uninstaller itself |
| `install_cosmos.py` | the installer: survey → plan → installation → verification |
| `uninstall_cosmos.py` | removal of the installation |
| `UNINSTALL-PLAN-RU.md` | the reasoning: why removal is arranged the way it is |

The pairs of names are one and the same file: the Latin name is there
for sites where a Cyrillic file name cannot be read.

## Installation

Click `INSTALL-COSMOS.cmd`. The first question is the language; the
whole dialogue then runs in it, and it also becomes the language of the
program itself. The second question is where to install. Everything
else is answered with the number of an option or with the Enter key:
every question has a safe default answer.

The questions that matter:

- **whether cloud AI models are allowed** at this site. An answer of
  "no" means that AI hints are prepared by the local model only. An
  answer of "yes" means the cloud is permitted for data sets marked as
  open data; the customer's own data sets are marked "local only" and
  never go to the cloud;
- **the local model** (the Ollama server). If it does not answer at the
  usual address, the installer will ask for the address — the server may
  be on another machine or another port. If there are several models, it
  will ask which one to use. If there is no local model at all, it will
  say so plainly: AI hints on the customer's data will not work, but the
  program itself is fully operational — the pairing (building chains of
  flights) and rostering (assigning crews to those chains) calculations
  do not depend on AI models;
- **how people will sign in** — with a password (works right away) or
  with a one-time code through Cloudflare Access;
- **which computers may open** the program — only this one, or all the
  computers on the local network;
- **whether to install JDK and Maven** if they are absent: they are
  needed for the rostering calculation and are placed in the
  installation directory, the system itself is not modified.

At the end the installer shows the **sign-in password**, a "how to start
the program" section, and the path to the **installation log**:

```
installation log (needed for removal): C:\Users\<you>\AppData\Local\cosmos-install\install-<date>.jsonl
```

Next to the log the **installation manifest** (`manifest-<date>.jsonl.gz`)
is placed — a list of everything the installation left behind. Removal
needs both files; they live in your user profile and outlive the removal
of the program itself. You do not need to read them.

As its last question the installer will offer to **start COSMOS right
away**. If you agree, it brings the program up, waits for it to respond
and opens it in the browser — the browser opens only once the program
really answers.

## How to start it later

- **Start:** double-click `START-COSMOS.cmd` in the installation
  directory (usually `C:\cosmos`). It brings the program up and opens
  the browser.
- **Address:** `http://127.0.0.1:8090`; if you allowed access from the
  network — from other computers at this machine's address, for example
  `http://192.168.1.50:8090`.
- **Stopping:** close the window that opened at start-up.
- **Rostering (stage 2)** is a separate service: `run-solver.cmd`, port 9096.
- **Airline data processing module** is a separate service:
  `run-cdpm.cmd`, port 8095, address `http://127.0.0.1:8095`. The module
  turns airline documents (rules, data books) into a task statement for
  the calculation. It listens on this machine only; to open it to the
  network, start it with `--host 0.0.0.0` — the module has no sign-in of
  its own, so do that only inside a protected network.
- **Template catalog** is a separate service: `run-catalog.cmd`, port
  8105, address `http://127.0.0.1:8105`; on a customer installation it
  is view only (edits and version releases are made by the owner). If
  the service is not part of your kit, the installation creates no
  startup file for it and says so.
- **Forgotten password:** delete `webapp\config\auth.json` and start the
  program again — it will create a new one.

## Removal

Click `UNINSTALL-COSMOS.cmd`. The program first runs all its checks
**without changing anything** and shows exactly what it is going to
remove, and only then asks for your consent. Until you answer "yes"
nothing changes; after "yes" all the checks are run again — this time
immediately before the removal itself.

For those who find the command line more convenient, the same thing is
available directly:

```bash
C:\Python314\python.exe uninstall_cosmos.py
```

```bash
C:\Python314\python.exe uninstall_cosmos.py --apply
```

Useful switches: `--list` — which installations were found on this
computer; `--target <directory>` — if there are several installations;
`--lang <code>` — the language of the output (by default the language
chosen during installation is used).

The uninstaller also arrives together with the program itself
(`C:\cosmos\uninstall_cosmos.py`), so an installation can be removed
without the kit as well: running it straight from there is safe — it
steps out of the directory being removed on its own.

Removing the firewall rule and the auto-start task requires
**administrator rights**: if they are needed and you do not have them,
the program stops honestly and says so.

### What the removal does and does not do

Only what the installation created is removed: the program directory,
the data directory (if it is empty, just as the installation created
it), the start-up files, the firewall rule and the auto-start task — if
it was this installation that created them — and also the processes left
over from it.

Some things are never removed, as a matter of principle: whatever was
already on the computer before the installation (Java, Python, git and
the rest); shared resources — the pip cache and the Maven repository,
which other projects use; SSH keys, because they may already have been
handed out to repositories. All of this is listed in the output rather
than passed over in silence.

At any ambiguity the removal **stops and shows the object** instead of
deciding for you. Every check prints how many objects it examined: "no
violations" without the number examined by that check does not count.

### Where such thoroughness comes from

The manifest taken by the installer makes it possible to verify **every**
object in the tree. Without it whole subtrees would have to be taken on
trust — the clone with its history, the python environment, the tool set
— and that is sixteen thousand objects out of nineteen; someone else's
file moved there by the owner would go unnoticed and would perish
together with the installation. As it is now, every object is either
listed in the manifest, or produced by the work of the program itself
(logs, caches, working files), or recognised as foreign — and then the
removal stops.

### If the installation was interrupted

No harm done: the log is written as the work proceeds and is flushed to
disk immediately, so it states exactly what managed to be created.
Removal will clear away precisely that. If there is no log at all (the
installation was made by an earlier version), removal will make do with
indirect signs: a marker in the start-up file and a comparison of the
tree; it will say so plainly, because that path is less precise.

## Languages

The texts of the dialogue are kept in the repository as separate files
per language:
`crew-demo/handoff/i18n-installer/messages-<language>.json`. Russian is
the source; the others repeat it key for key.

To correct a wording or add a language: change the file in question and
rebuild —

```bash
C:\Python314\python.exe crew-demo\handoff\i18n-installer\embed_i18n.py
```

The build embeds the dictionaries directly into `install_cosmos.py` and
`uninstall_cosmos.py` (both files must work on their own, without
external dictionaries) and **refuses to build** if some language is
missing keys or the number of substitutions has diverged — otherwise the
line would fail at the customer's site already. After the build, copy
both files into this kit.
