"""
Give the Analytics page something to show: about thirty days of made-up
actions and feature use, in the same table the real ones go to.

Run from inside a scribe-backend checkout with its settings exported:

    PYTHONPATH=. uv run python /path/to/seed_analytics.py

Quiet at the weekend, busy in office hours, a few more events each week than
the last. Seeded, so every environment gets the same month. Nothing is added
once the table holds anything older than two days -- history already (made up
here before, or really gathered), and a restart must not double it. Events
recorded since launch are newer than that and are left alone.
"""

import random
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from db.models import PageView
from utils.settings import get_settings

DAYS = 30

# What the backend records as an action, and as a feature (db/analytics.py,
# app.py), with how often each happens relative to the rest.
ACTIONS = {
    "upload": 30,
    "transcription": 26,
    "export": 20,
    "delete_job": 8,
    "bulk_export": 3,
    "bulk_transcription": 2,
    "modify_user": 2,
    "create_group": 1,
    "edit_group": 1,
    "add_group_user": 2,
}

FEATURES = {
    "validation": 14,
    "find_replace": 12,
    "uncertain_words": 8,
    "timeline": 9,
    "subtitle_overlay": 7,
    "my_edits": 4,
    "recording_finished": 6,
    "recording_discarded": 1,
    "drive_connect": 2,
    "drive_import": 3,
    "drive_save": 2,
    "drive_save_original": 1,
}

# UTC hours, weighted: a working day in Sweden, with a lunchtime dip.
HOURS = {6: 2, 7: 6, 8: 10, 9: 12, 10: 12, 11: 10, 12: 6, 13: 11, 14: 12, 15: 10, 16: 7, 17: 4, 18: 2, 19: 1}


def weighted(rng: random.Random, weights: dict):
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def make_events(now: datetime) -> list[PageView]:
    rng = random.Random(2026)
    events = []

    for days_ago in range(DAYS, 0, -1):
        day = (now - timedelta(days=days_ago)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        weekend = day.weekday() >= 5
        # A little growth over the month, and a different amount every day.
        base = 6 if weekend else 38
        count = int(base * (0.7 + 0.5 * (DAYS - days_ago) / DAYS) * rng.uniform(0.7, 1.3))

        for _ in range(count):
            when = day + timedelta(
                hours=weighted(rng, HOURS),
                minutes=rng.randrange(60),
                seconds=rng.randrange(60),
            )

            if rng.random() < 0.6:
                path = f"/action/{weighted(rng, ACTIONS)}"
            else:
                path = f"/feature/{weighted(rng, FEATURES)}"

            events.append(PageView(path=path, timestamp=when))

    return events


def main() -> None:
    engine = create_engine(get_settings().API_DATABASE_URL)
    now = datetime.now(UTC).replace(tzinfo=None)

    with sessionmaker(bind=engine)() as session:
        older = session.scalar(
            select(func.count())
            .select_from(PageView)
            .where(PageView.timestamp < now - timedelta(days=2))
        )

        if older:
            print("Analytics: there is history already; nothing added.")
            return

        events = make_events(now)
        session.add_all(events)
        session.commit()

    print(f"Analytics: added {len(events)} events over {DAYS} days.")


if __name__ == "__main__":
    main()
