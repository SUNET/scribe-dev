"""
Create the dev user as an active admin and BOFH, so the first login lands
on a working home page instead of "not activated".

Run from inside a scribe-backend checkout with its settings exported:

    PYTHONPATH=. uv run python /path/to/seed_user.py <username>

user_id is the same uuid5 the fake OIDC server derives for that username, so
the login that follows finds this row instead of making a second one.
"""

import sys
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from db.models import User
from utils.settings import get_settings


def main() -> None:
    username = sys.argv[1]
    realm = username.split("@")[-1] if "@" in username else "local"
    user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, username))

    engine = create_engine(get_settings().API_DATABASE_URL)
    SQLModel.metadata.create_all(engine)

    with sessionmaker(bind=engine)() as session:
        user = session.scalars(select(User).where(User.user_id == user_id)).first()

        if user is None:
            user = User(
                user_id=user_id,
                username=username,
                realm=realm,
                transcribed_seconds=0,
                email=username,
            )
            session.add(user)

        user.active = True
        user.admin = True
        user.bofh = True
        session.commit()

    print(f"Dev user {username} ({user_id}) is active, admin and BOFH.")


if __name__ == "__main__":
    main()
