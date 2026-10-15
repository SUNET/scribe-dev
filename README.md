# scribe-dev

A local development environment for Sunet Scribe, started with one command:

```sh
./launch_dev_env.sh
```

It clones `scribe-backend` and `scribe-ui` (from `main`) next to this script,
starts a fake OIDC server that stands in for the login provider, and runs the
backend and the UI. When it is done it prints where to go: open
<http://localhost:8888>, press the login button and you are signed in as
`dev@example.com`, an active admin and BOFH. There is no login form.

You need `git`, `uv`, `curl` and `python3`. On a Mac with
[Homebrew](https://brew.sh), `./launch_dev_env.sh --macos-deps` installs
whichever of them is missing (it does not install Homebrew itself) and then
starts as usual. Nothing else is installed on your machine; each project gets
its own virtualenv.

## Changing the code

`scribe-backend/` and `scribe-ui/` are ordinary git clones. Edit them, branch,
commit and push from there.

- The backend runs under `uvicorn --reload` and the UI under NiceGUI's own
  reload, so a saved change restarts the right half by itself. (A change to a
  `.js` file in the UI is picked up too.)
- A clone with local changes, or on another branch, is never touched when the
  script runs again. A clean clone on `main` is fast-forwarded.
- To work on another branch, check it out in the clone, or start with
  `BRANCH=my-branch ./launch_dev_env.sh` for a fresh clone of it.

## What is where

```
launch_dev_env.sh     the script
support/              what the environment needs besides the two projects
  fake_oidc/          the fake login provider
  seed_user.py        makes the dev user active, admin and BOFH
scribe-backend/       clone, yours to edit   (not committed here)
scribe-ui/            clone, yours to edit   (not committed here)
.dev/                 generated: database, uploaded files, secrets, logs
```

Logs are in `.dev/logs/` (`oidc.log`, `backend.log`, `ui.log`). Stop
everything with Ctrl-C.

## Flags

| Flag | |
|---|---|
| `--macos-deps` | Install missing `git`, `uv`, `curl` and `python3` with Homebrew, then start. macOS only; stops with a pointer to <https://brew.sh> if Homebrew is missing, and never installs Homebrew itself. Tools already present are left alone. |
| `reset` (or `--reset`) | Delete the database, uploaded files and secrets first, for a clean start. |
| `--help` | Show the usage text. |

Flags combine: `./launch_dev_env.sh --macos-deps reset`.

## Options

Environment variables, all optional:

| Variable | Default | |
|---|---|---|
| `BACKEND_PORT` | `8000` | The UI's port, 8888, is fixed by `scribe-ui`. |
| `OIDC_PORT` | `9000` | |
| `DEV_USER` | `dev@example.com` | Who the login signs in as. The part after `@` is the realm. |
| `BRANCH` | `main` | Used when cloning. |
| `BACKEND_REPO`, `UI_REPO` | the SUNET GitHub repos | Point at your forks. |

The settings files in `.dev/` are rewritten on every
run, so a changed port always takes effect.

To sign in as someone else, restart with `DEV_USER=other@example.org`. A
user other than the seeded one is created inactive, as in production, and needs
activating by an admin.

## What it does not do

- **No transcription.** There is no worker, so an uploaded file becomes a job
  that stays queued. Running `scribe-worker` against this backend needs the
  mTLS certificates from `scribe-backend/create_certs.sh`.
- **No mail.** SMTP is not configured, so notification mails are not sent.
- **SQLite, not PostgreSQL.** Good enough to develop against; production uses
  PostgreSQL, so check schema changes there too.
- **Not secure.** The fake provider signs in anyone who asks, over plain HTTP,
  with secure cookies turned off. Never expose these ports.
