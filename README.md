# scribe-dev

A local development environment for Sunet Scribe, started with one command:

```sh
./launch_dev_env.sh
```

It clones `scribe-backend` and `scribe-ui` (from `main`) next to this script,
starts a fake OIDC server that stands in for the login provider, and runs the
backend and the UI. When it is done it prints where to go: open
<http://localhost:8888>, press the login button and you are signed in as
`dev@example.com`, an active admin and BOFH. There is no login form. You are
then asked for an encryption passphrase: it is `devpassphrase`.

My files starts with two finished jobs to open: **Sample subtitles** (twelve
captions, to try the subtitle editor, timeline, overlay and Validate) and
**Sample interview** (a transcription with two speakers). Both have word
timings, with a few low-confidence words so the review marking has something
to show, and a 40-second sample video to play.

You need `git`, `uv`, `curl` and `python3`, plus libpq's `pg_config`: the
backend's `psycopg2` is built from source. That is only the client library;
no PostgreSQL server is needed or started. On a Mac with
[Homebrew](https://brew.sh), `./launch_dev_env.sh --macos-deps` installs
whichever of them is missing, `pg_config` included (it does not install
Homebrew itself), and then starts as usual. Nothing else is installed on your machine; each project gets
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
  seed_samples.py     adds the dev user's sample jobs, one for each thing worth
                      trying (see "Sample data"; each only once)
  seed_directory.py   adds users, groups, customers, rules, announcements, worker
                      health and a few months of jobs for them (each only once)
  seed_banner.py      adds the "development environment" banner (once)
  seed_analytics.py   adds a month of made-up usage for the Analytics page (once)
  samples/            the video the samples play, and audio for the recording
scribe-backend/       clone, yours to edit   (not committed here)
scribe-ui/            clone, yours to edit   (not committed here)
.dev/                 generated: database, uploaded files, secrets, logs
```

## Sample data

Everything is made up and only added when it is missing, so a restart adds
nothing and what you changed stays changed. `reset` starts over.

**The dev user's jobs** each exist to try one thing:

| Job | What it is for |
|---|---|
| Sample subtitles / Sample interview | The ordinary cases |
| …with errors | Breaks every Validate rule. "No text" cannot be written in an SRT file: press "Add caption after" for an empty caption, then Validate |
| …at the limits | 42-character lines, two lines, exactly 0.8 s, captions touching: Validate has nothing to say |
| …without word timings | A job from before word timings: no timeline, no review marking |
| …without confidence | Word timings but no scores: no review controls |
| …with awkward text (and an interview) | Markup, template syntax, other scripts, emoji, in captions and speaker names: only ever shown, never interpreted |
| Sample recording | Made in the browser, keeps its original: the Recording badge, and Export's "Include the original recording" |
| Sample audio only | No picture |
| Sample long subtitles (1 hour) | 600 captions. The video is only 40 s, so this is for scrolling and editing, not playback |
| Sample expiring soon | Deleted by the backend in about a day; the next launch adds it again |
| Pending, in progress, uploading, uploaded, failed | The other states of a job. They have no result, so there is nothing to open |

**The organisation** (`seed_directory.py`), all on `.example` domains:

- **Users** at three institutions: admins, one awaiting activation, one
  deactivated, one deleted. Log in as one by setting `DEV_USER`.
- **Groups** at every point of their quota: well under, nearly out, over, and
  no limit. Quota is the group's total for the calendar month.
- **Customers** on both price plans, one with nobody in it yet, and one for the
  dev user's own realm, whose Drive is not real (connecting fails).
- **Rules**, one of them disabled. Each is limited to a realm of its own: a rule
  that denies or demotes would act on the dev user the moment it matched.
- **Announcements** in every state: showing, disabled, scheduled, expired.
- **Worker health** for three workers, one of them silent for hours. Refreshed
  at every launch; workers count as online for the two minutes after.
- A few months of finished jobs for these users, for the customers' figures,
  and a month of made-up usage for the Analytics page.

### How the seeding works

The launch script runs five scripts from `support/` once the tables exist, in
this order: `seed_user.py`, `seed_samples.py`, `seed_directory.py`,
`seed_banner.py`, `seed_analytics.py`. Each runs inside `scribe-backend` with
the backend's own settings, so it uses the same database and the same code as
the backend does.

- **Only what is missing is added.** Everything is found by a natural key
  (username, job name, group name and realm, customer abbreviation, rule name,
  announcement text), so a restart adds nothing and what you changed in the UI
  stays changed.
- **Samples are matched by name.** Delete a sample in My files and the next
  launch adds it again. A sample added to `seed_samples.py` later turns up on
  the next launch too, with no `reset`. The names are encrypted for the user,
  so this needs `DEV_PASSPHRASE` to be what it was on the first run; if it was
  changed, the script says so and adds nothing.
- **Two things are refreshed every launch**: the banner's text, and the worker
  health readings, which are replaced so the newest is always recent.
- **Analytics history is added only when there is none** (nothing in the table
  older than two days), so it is not doubled by a restart and what you record
  yourself is left alone.
- **It is the same data every time.** Where something is random it is seeded,
  so every environment gets the same month, the same hour of subtitles and the
  same users' jobs.

To run one by hand, for instance after changing it:

```sh
cd scribe-backend
set -a; . ../.dev/backend.env; set +a
PYTHONPATH=. uv run python ../support/seed_directory.py dev@example.com
```

`seed_samples.py` also takes the passphrase (`... seed_samples.py dev@example.com devpassphrase`),
`seed_user.py` and `seed_directory.py` the username, and `seed_banner.py` and
`seed_analytics.py` nothing.

To add a sample job, add its text to `seed_samples.py`, give it a name and an
entry in `samples` (and in `files` if it plays its own audio or keeps an
original), and relaunch. For something that breaks a rule, see
`INVALID_SUBTITLES` there: a comment on each caption says which rule it is
for, and nothing checks that automatically, so press Validate on the result.

Logs are in `.dev/logs/` (`oidc.log`, `backend.log`, `ui.log`). Stop
everything with Ctrl-C.

## Flags

| Flag | |
|---|---|
| `--macos-deps` | Install missing `git`, `uv`, `curl`, `python3` and `libpq` with Homebrew, then start. macOS only; stops with a pointer to <https://brew.sh> if Homebrew is missing, and never installs Homebrew itself. Tools already present are left alone. |
| `reset` (or `--reset`) | Delete the database, uploaded files and secrets first, for a clean start. |
| `--help` | Show the usage text. |

Flags combine: `./launch_dev_env.sh --macos-deps reset`.

## Options

Environment variables, all optional:

| Variable | Default | |
|---|---|---|
| `BACKEND_PORT` | `8000` | The UI's port, 8888, is fixed by `scribe-ui`. |
| `OIDC_PORT` | `9000` | |
| `DEV_PASSPHRASE` | `devpassphrase` | The encryption passphrase asked for after signing in. The sample files are encrypted with it, so set it before the first run (or `reset`). |
| `DEV_USER` | `dev@example.com` | Who the login signs in as. The part after `@` is the realm. |
| `BRANCH` | `main` | Used when cloning. |
| `BACKEND_REPO`, `UI_REPO` | the SUNET GitHub repos | Point at your forks. |

Your data is kept between runs: the database and uploaded files live in
`.dev/data/`, and nothing touches them except `reset`. The dev user is created
once and then left alone, so roles and groups you change in the UI survive a
restart. See "How the seeding works" for what is added again and what is not.

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
