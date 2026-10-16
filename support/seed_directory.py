"""
Fill the admin side with an organisation to look at: users in three
institutions (admins, a user awaiting activation, one deactivated, one
deleted), groups at every point of their quota, customers on both price plans,
onboarding rules, announcements in every state, worker health, notifications
and a few months of finished jobs for the people in them.

Run from inside a scribe-backend checkout with its settings exported:

    PYTHONPATH=. uv run python /path/to/seed_directory.py <dev username>

All made up, on domains that cannot exist (.example). Each thing is added only
when it is not there already, found by its natural key (username, group name
and realm, customer abbreviation, rule name, announcement text), so a restart
adds nothing and what was changed in the UI stays changed. The exception is
worker health, which is brought up to date each time: a worker last seen a
week ago would otherwise be what every launch shows.

What a login does with the rules: every rule here is limited to a realm of its
own, none to the dev user's, apart from one that only activates -- a rule that
denies or demotes would act on the dev user the moment it matched.
"""

import json
import math
import random
import sys
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from db.models import (
    Announcement,
    AnnouncementSeverityEnum,
    AttributeConditionEnum,
    AttributeRule,
    Customer,
    DarkModeEnum,
    Group,
    GroupModelLink,
    GroupUserLink,
    Job,
    JobStatusEnum,
    JobType,
    Model,
    NotificationsSent,
    OutputFormatEnum,
    PricePlanEnum,
    User,
    WorkerHealth,
)
from utils.settings import get_settings

CREATED_BY = "scribe-dev-samples"

MODELS = [
    ("Fast transcription (normal accuracy)", "Quick, good enough for most recordings."),
    ("Slower transcription (higher accuracy)", "Takes longer, makes fewer mistakes."),
]

# (username, role, admin domains, state, notifications, dark mode, days since
# last login). state: active | pending (awaiting activation) | deactivated
# (switched off by an admin) | deleted. role: user | admin.
USERS = [
    ("alice.andersson@uni-a.example", "admin", "uni-a.example", "active", "job,deletion,user,quota", "auto", 0),
    ("bob.berg@uni-a.example", "user", None, "active", "job", "dark", 1),
    ("carol.carlsson@uni-a.example", "user", None, "active", "job,deletion", "light", 45),
    ("dave.dahl@uni-a.example", "user", None, "pending", None, "auto", 0),
    ("erin.ek@uni-a.example", "user", None, "deleted", None, "auto", 200),
    ("frank.forsberg@uni-b.example", "admin", "uni-b.example", "active", "job,user", "auto", 2),
    ("grace.gustafsson@uni-b.example", "user", None, "active", "job,deletion,quota", "auto", 3),
    ("heidi.holm@uni-b.example", "user", None, "deactivated", None, "auto", 90),
    ("ivan.isaksson@uni-b.example", "user", None, "active", "job", "auto", 0),
    ("judy.jonsson@college.example", "user", None, "active", "job,deletion", "auto", 5),
    ("karl.karlsson@college.example", "admin", "college.example", "active", "job,user,quota", "auto", 1),
    ("second.dev@example.com", "user", None, "active", "job", "auto", 7),
]

# (name, realm, description, owner, quota hours or None, [members], models)
# Seconds used this month, set by JOBS_THIS_MONTH below, put each at a
# different point: well under, nearly out, over, and no limit at all.
GROUPS = [
    ("Lecture capture", "uni-a.example", "Recorded lectures, all courses.", "alice.andersson@uni-a.example", 10,
     ["alice.andersson@uni-a.example", "bob.berg@uni-a.example"], ["Fast", "Slower"]),
    ("Research interviews", "uni-a.example", "Interviews for ongoing projects.", "carol.carlsson@uni-a.example", 20,
     ["carol.carlsson@uni-a.example", "dave.dahl@uni-a.example"], ["Slower"]),
    ("Library", "uni-b.example", "Library staff. No limit.", "frank.forsberg@uni-b.example", None,
     ["frank.forsberg@uni-b.example", "grace.gustafsson@uni-b.example"], []),
    ("Media department", "uni-b.example", "Over its quota this month.", "ivan.isaksson@uni-b.example", 2,
     ["ivan.isaksson@uni-b.example", "heidi.holm@uni-b.example"], ["Fast"]),
    ("Staff", "college.example", "Everyone at the college.", "karl.karlsson@college.example", 5,
     ["judy.jonsson@college.example", "karl.karlsson@college.example"], []),
]

# username -> seconds of finished jobs this month. A group's quota is used up
# by what its members transcribed in the calendar month.
JOBS_THIS_MONTH = {
    "alice.andersson@uni-a.example": [7200, 5400, 5400],   # Lecture capture: 30600 of 36000
    "bob.berg@uni-a.example": [7200, 5400],
    "carol.carlsson@uni-a.example": [3600, 1800],          # Research interviews: 5400 of 72000
    "frank.forsberg@uni-b.example": [3600, 3600],          # Library: no limit
    "grace.gustafsson@uni-b.example": [1800],
    "ivan.isaksson@uni-b.example": [5400, 3600],           # Media department: 9000 of 7200
    "judy.jonsson@college.example": [1800],                # Staff: 1800 of 18000
    "karl.karlsson@college.example": [],
    "second.dev@example.com": [900],
}

CUSTOMERS = [
    dict(customer_abbr="UNIA", partner_id="1001", name="University A",
         contact_email="it@uni-a.example", support_contact_email="scribe-support@uni-a.example",
         priceplan=PricePlanEnum.FIXED, base_fee=12000, blocks_purchased=3,
         realms="uni-a.example", notes="Fixed plan, three blocks. Invoiced quarterly.",
         drive_enabled=True, drive_url="https://uni-a.drive.sunet.se", drive_display_name="UniA Drive"),
    dict(customer_abbr="UNIB", partner_id="1002", name="University B",
         contact_email="it@uni-b.example", support_contact_email=None,
         priceplan=PricePlanEnum.VARIABLE, base_fee=0, blocks_purchased=0,
         realms="uni-b.example", notes="Pays for what is used.",
         drive_enabled=False, drive_url=None, drive_display_name=None),
    dict(customer_abbr="COLL", partner_id="1003", name="College C",
         contact_email="office@college.example", support_contact_email="help@college.example",
         priceplan=PricePlanEnum.FIXED, base_fee=4000, blocks_purchased=1,
         realms="college.example", notes=None,
         drive_enabled=True, drive_url="https://college.drive.sunet.se", drive_display_name=None),
    dict(customer_abbr="NEWC", partner_id="1005", name="New customer",
         contact_email="hello@newcust.example", support_contact_email=None,
         priceplan=PricePlanEnum.VARIABLE, base_fee=0, blocks_purchased=0,
         realms="newcust.example", notes="Signed last week, nobody has logged in yet.",
         drive_enabled=False, drive_url=None, drive_display_name=None),
    # The dev user's own realm, so the help dialog has a support address and
    # Drive has something to offer. The Drive here is not real: connecting to
    # it fails, which is a state worth seeing too.
    dict(customer_abbr="DEV", partner_id="9999", name="Development",
         contact_email="dev@example.com", support_contact_email="dev-support@example.com",
         priceplan=PricePlanEnum.VARIABLE, base_fee=0, blocks_purchased=0,
         realms="example.com", notes="The development environment's own organisation.",
         drive_enabled=True, drive_url="https://dev.drive.sunet.se", drive_display_name="Demo Drive"),
]

# (name, attribute, condition, value, realm, owner domains, enabled, actions,
# group name or None). Attributes are the backend's defaults: email,
# preferred_username, domain, affiliation, realm.
RULES = [
    ("Activate employees at University A", "affiliation", AttributeConditionEnum.CONTAINS, "employee",
     "uni-a.example", "uni-a.example", True,
     dict(activate=True, notify_job=True, notify_deletion=True), "Lecture capture"),
    ("IT staff are admins at University A", "email", AttributeConditionEnum.ENDS_WITH, "@it.uni-a.example",
     "uni-a.example", "uni-a.example", True, dict(activate=True, admin=True), None),
    ("Deny students at University B", "affiliation", AttributeConditionEnum.EQUALS, "student",
     "uni-b.example", "uni-b.example", True, dict(deny=True), None),
    ("Library staff to the Library group", "preferred_username", AttributeConditionEnum.REGEX_MATCH, "^lib-.*",
     "uni-b.example", "uni-b.example", True, dict(activate=True), "Library"),
    ("Pilot: media department", "affiliation", AttributeConditionEnum.CONTAINS, "media",
     "uni-b.example", "uni-b.example", False, dict(activate=True), "Media department"),
    ("Activate everyone at the college", "domain", AttributeConditionEnum.EQUALS, "college.example",
     "college.example", "college.example", True, dict(activate=True), "Staff"),
    ("Activate everyone in the dev realm", "realm", AttributeConditionEnum.EQUALS, "example.com",
     "example.com", "example.com", True, dict(activate=True), None),
]

WORKERS = ["dev-gpu-1", "dev-gpu-2", "dev-gpu-3"]


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def user_id_of(username: str) -> str:
    # The same uuid5 the fake OIDC server derives, so a login as one of these
    # finds the row rather than making a second.
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, username))


def month_start(now: datetime, back: int) -> datetime:
    year, month = now.year, now.month - back

    while month <= 0:
        month += 12
        year -= 1

    return datetime(year, month, 1)


def make_user(spec) -> User:
    username, role, domains, state, notifications, dark, days = spec
    now = now_utc()

    return User(
        user_id=user_id_of(username),
        username=username,
        realm=username.split("@")[-1],
        email=username,
        admin=role == "admin",
        admin_domains=domains,
        active=state in ("active",),
        deleted=state == "deleted",
        manually_deactivated=state == "deactivated",
        manually_activated=state == "active" and role == "admin",
        notifications=notifications,
        manually_set_notifications=bool(notifications),
        dark_mode=DarkModeEnum(dark),
        transcribed_seconds=0,
        last_login=now - timedelta(days=days, hours=2),
    )


def job_rows(username: str, rng: random.Random) -> list[Job]:
    """
    This month's finished jobs as listed above, and a few in each of the two
    months before (for the customers' monthly figures).
    """

    now = now_utc()
    user_id = user_id_of(username)
    rows = []

    def add(created: datetime, seconds: int) -> None:
        created = min(created, now - timedelta(minutes=5))
        rows.append(
            Job(
                user_id=user_id,
                status=JobStatusEnum.COMPLETED,
                job_type=JobType.TRANSCRIPTION,
                output_format=OutputFormatEnum.TXT,
                language=rng.choice(["Swedish", "Swedish", "English", "Norwegian"]),
                model_type=rng.choice([m[0] for m in MODELS]),
                speakers=str(rng.randint(1, 3)),
                filename="",
                created_at=created,
                updated_at=created,
                # Kept: the backend deletes a job when its date passes.
                deletion_date=now + timedelta(days=3650),
                transcribed_seconds=seconds,
            )
        )

    for number, seconds in enumerate(JOBS_THIS_MONTH.get(username, [])):
        add(month_start(now, 0) + timedelta(days=min(number * 3, max(now.day - 1, 0)), hours=9 + number), seconds)

    if username in JOBS_THIS_MONTH:
        for back in (1, 2):
            for number in range(rng.randint(2, 5)):
                add(
                    month_start(now, back) + timedelta(days=rng.randint(0, 26), hours=rng.randint(7, 17)),
                    rng.choice([900, 1800, 2700, 3600, 5400]),
                )

    return rows


def seed_users(session) -> list[str]:
    created = []

    for spec in USERS:
        if session.scalars(select(User).where(User.username == spec[0])).first():
            continue

        session.add(make_user(spec))
        created.append(spec[0])

    session.flush()
    return created


def seed_jobs(session, usernames: list[str]) -> int:
    count = 0

    for username in usernames:
        rows = job_rows(username, random.Random(username))
        session.add_all(rows)
        user = session.scalars(select(User).where(User.username == username)).first()
        user.transcribed_seconds = sum(row.transcribed_seconds for row in rows)
        count += len(rows)

    return count


def seed_models(session) -> dict[str, Model]:
    found = {}

    for name, description in MODELS:
        model = session.scalars(select(Model).where(Model.name == name)).first()

        if model is None:
            model = Model(name=name, description=description, active=True)
            session.add(model)

        found[name.split()[0]] = model

    session.flush()
    return found


def seed_groups(session, models: dict[str, Model], dev_username: str) -> int:
    added = 0
    wanted = list(GROUPS) + [
        ("Dev team", "example.com", "The people in this environment.", dev_username, None,
         [dev_username, "second.dev@example.com"], ["Fast", "Slower"]),
    ]

    for name, realm, description, owner, hours, members, model_names in wanted:
        if session.scalars(
            select(Group).where(Group.name == name, Group.realm == realm)
        ).first():
            continue

        group = Group(
            name=name,
            realm=realm,
            description=description,
            owner_user_id=user_id_of(owner),
            quota_seconds=hours * 3600 if hours else None,
        )
        session.add(group)
        session.flush()

        for member in members:
            user = session.scalars(select(User).where(User.username == member)).first()

            if user is None:  # the dev user, if the login has not happened
                continue

            session.add(
                GroupUserLink(
                    group_id=group.id,
                    user_id=user.id,
                    role="owner" if member == owner else "member",
                    in_group=True,
                )
            )

        for model_name in model_names:
            session.add(GroupModelLink(group_id=group.id, model_id=models[model_name].id))

        added += 1

    session.flush()
    return added


def seed_customers(session) -> int:
    added = 0

    for fields in CUSTOMERS:
        if session.scalars(
            select(Customer).where(Customer.customer_abbr == fields["customer_abbr"])
        ).first():
            continue

        session.add(Customer(**fields))
        added += 1

    return added


def seed_rules(session) -> int:
    added = 0

    for name, attribute, condition, value, realm, domains, enabled, actions, group in RULES:
        if session.scalars(select(AttributeRule).where(AttributeRule.name == name)).first():
            continue

        group_id = None

        if group:
            found = session.scalars(
                select(Group).where(Group.name == group, Group.realm == realm)
            ).first()
            group_id = str(found.id) if found else None

        session.add(
            AttributeRule(
                name=name,
                attribute_name=attribute,
                attribute_condition=condition,
                attribute_value=value,
                realm=realm,
                owner_domains=domains,
                enabled=enabled,
                assign_to_group=group_id,
                **actions,
            )
        )
        added += 1

    return added


def seed_announcements(session) -> int:
    now = now_utc()
    wanted = [
        ("Scribe can now record from the browser. "
         '<a href="https://example.com/news">Read more</a>.',
         AnnouncementSeverityEnum.INFO, True, None, None),
        ("Transcriptions are delayed while we look into a problem with the workers.",
         AnnouncementSeverityEnum.MAJOR_INCIDENT, False, None, None),
        ("Planned maintenance: Scribe will be unavailable for about two hours.",
         AnnouncementSeverityEnum.MAINTENANCE, True, now + timedelta(days=3), now + timedelta(days=3, hours=2)),
        ("Last week's maintenance is finished.",
         AnnouncementSeverityEnum.INFO, True, now - timedelta(days=9), now - timedelta(days=2)),
    ]
    added = 0

    for message, severity, enabled, starts, ends in wanted:
        if session.scalars(
            select(Announcement).where(
                Announcement.message == message, Announcement.created_by == CREATED_BY
            )
        ).first():
            continue

        session.add(
            Announcement(
                message=message, severity=severity, enabled=enabled,
                starts_at=starts, ends_at=ends, created_by=CREATED_BY,
            )
        )
        added += 1

    return added


def seed_notifications(session, usernames: list[str], dev_username: str) -> int:
    rng = random.Random(5)
    now = now_utc()
    added = 0

    for username in usernames:
        user_id = user_id_of(username)

        for kind in rng.sample(["job", "deletion", "quota", "account_activated"], 2):
            session.add(
                NotificationsSent(
                    user_id=user_id,
                    notification_type=kind,
                    uuid=str(uuid.uuid4()) if kind != "account_activated" else user_id,
                    sent_at=now - timedelta(days=rng.randint(1, 20)),
                )
            )
            added += 1

    return added


def seed_worker_health(session) -> int:
    """
    A few hours of readings for three workers: one busy, one idle, one that
    stopped reporting a while ago. Replaced on every run, so the newest
    reading is always recent.
    """

    session.execute(delete(WorkerHealth).where(WorkerHealth.worker_id.in_(WORKERS)))
    now = now_utc()
    rng = random.Random(11)
    rows = 0

    for index, worker in enumerate(WORKERS):
        silent_for = timedelta(hours=4) if index == 2 else timedelta(0)

        for step in range(180):
            when = now - silent_for - timedelta(minutes=179 - step)
            wave = 0.5 + 0.5 * math.sin(step / 14 + index)
            busy = index == 0 and step % 60 < 35
            gpu = round(rng.uniform(55, 95) if busy else rng.uniform(0, 4), 1)
            session.add(
                WorkerHealth(
                    worker_id=worker,
                    load_avg=round(0.4 + 3.5 * wave + (2 if busy else 0), 2),
                    memory_usage=round(32 + 25 * wave + rng.uniform(-2, 2), 1),
                    gpu_usage=json.dumps(
                        [
                            {
                                "utilization": gpu,
                                "memory_used": int(2000 + gpu * 150),
                                "memory_total": 24576,
                            }
                        ]
                    ),
                    created_at=when,
                )
            )
            rows += 1

    return rows


def main() -> None:
    dev_username = sys.argv[1] if len(sys.argv) > 1 else "dev@example.com"
    engine = create_engine(get_settings().API_DATABASE_URL)
    SQLModel.metadata.create_all(engine)

    with sessionmaker(bind=engine)() as session:
        created = seed_users(session)
        models = seed_models(session)
        groups = seed_groups(session, models, dev_username)
        customers = seed_customers(session)
        rules = seed_rules(session)
        announcements = seed_announcements(session)
        jobs = seed_jobs(session, created)
        notifications = seed_notifications(session, created, dev_username)
        readings = seed_worker_health(session)
        session.commit()

    print(
        f"Directory: {len(created)} users, {groups} groups, {customers} customers, "
        f"{rules} rules, {announcements} announcements, {jobs} jobs, "
        f"{notifications} notifications added; {readings} worker readings refreshed."
    )


if __name__ == "__main__":
    main()
