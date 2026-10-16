"""
Add an announcement banner saying this is a development environment.
A restart never adds a second one: the banner is found by its creator, and
only its message is brought up to date.

Run from inside a scribe-backend checkout with its settings exported:

    PYTHONPATH=. uv run python /path/to/seed_banner.py
"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from db.models import Announcement, AnnouncementSeverityEnum
from utils.settings import get_settings

MESSAGE = (
    "This is a development environment. All data here is fake, including the "
    "users, groups, customers and statistics, and may be lost."
)
CREATED_BY = "scribe-dev"


def main() -> None:
    engine = create_engine(get_settings().API_DATABASE_URL)
    SQLModel.metadata.create_all(engine)

    with sessionmaker(bind=engine)() as session:
        banner = session.scalars(
            select(Announcement).where(Announcement.created_by == CREATED_BY)
        ).first()
        if banner:
            banner.message = MESSAGE
            session.commit()
            print("Dev banner already there; message brought up to date.")
            return

        session.add(
            Announcement(
                message=MESSAGE,
                severity=AnnouncementSeverityEnum.MAINTENANCE,
                enabled=True,
                created_by=CREATED_BY,
            )
        )
        session.commit()
        print("Created the development environment banner.")


if __name__ == "__main__":
    main()
