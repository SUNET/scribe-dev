"""
Give the dev user three finished jobs to open: a set of subtitles, a
transcription with two speakers, and a second set of subtitles that breaks
every rule "Validate" checks. All have word timings and the same short sample
video.

Run from inside a scribe-backend checkout with its settings exported:

    PYTHONPATH=. uv run python /path/to/seed_samples.py <username> <passphrase>

The results are stored the way the worker's are -- encrypted for the user --
which needs the user's key pair, so this also sets the user's encryption
passphrase. It is what the first login asks for. Only the samples the user
does not have yet are added (matched by name), so a restart never adds a
second set, and a sample added later turns up without starting over.
"""

import asyncio
import json
import os
import sys
import uuid
import zlib
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.job import job_create, job_result_save, job_update
from db.models import Job, JobStatusEnum, JobType
from db.user import user_get_private_key, user_get_public_key, user_update
from utils.crypto import (
    decrypt_string,
    deserialize_private_key_from_pem,
    deserialize_public_key_from_pem,
    encrypt_data_to_file,
    encrypt_string,
)
from utils.settings import get_settings

SAMPLE_VIDEO = Path(__file__).parent / "samples" / "sample.mp4"

# (start, end, text) -- the subtitles. Lines kept under the editor's 42
# character guideline, two at most.
SUBTITLES = [
    (0.5, 3.5, "Welcome to the Sunet Scribe\ndevelopment environment."),
    (4.0, 7.0, "Everything you see here is sample data,\nmade for trying things out."),
    (7.5, 10.0, "Open the editor, and press play\nto follow along."),
    (10.5, 13.5, "Click a word, and the video\njumps to where it was said."),
    (14.0, 16.5, "Split a caption with Enter,\nmerge with Backspace."),
    (17.0, 20.0, "Words the model was unsure of\ncan be marked for review."),
    (20.5, 23.0, "Turn on the review control\nand look for the violet marks."),
    (23.5, 26.5, "The timeline shows where someone\nis speaking, and where they are not."),
    (27.0, 30.0, "Drag a caption's edge on the timeline\nto change its timing."),
    (30.5, 33.0, "Validate checks line length\nand how long a caption stays up."),
    (33.5, 36.5, "When you are done, export\nas SRT, WebVTT or plain text."),
    (37.0, 39.5, "Thanks for trying Sunet Scribe."),
]

# Subtitles that break every rule "Validate" checks, one kind at a time (the
# caption's number says which), and caption 10 breaking three at once to show
# that a caption with several problems is one entry. Seconds, as above. Caption
# 8 repeats 7's timing, which is also an overlap, so it is reported as both.
#
# "No text" is not here: an empty caption cannot be written in an SRT file --
# the parser skips a block with no text line, so it is gone on import. Make one
# in the editor ("Add caption after" starts an empty one) and press Validate.
INVALID_SUBTITLES = [
    (0.5, 3.5, "A caption that is fine,\nfor comparison."),
    # 2: line longer than 42 characters
    (4.0, 7.0, "This line is much too long for one subtitle line,\nand it runs well past the guideline."),
    # 3: more than two lines
    (7.5, 10.5, "Three lines\nis one more\nthan the guideline allows."),
    # 4: on screen for less than 0.8 s
    (11.0, 11.4, "Gone in a blink."),
    # 5, 6: overlapping timestamps
    (12.0, 15.0, "Overlaps the next one."),
    (14.0, 17.0, "Starts before the last one ends."),
    # 7, 8: the same timing
    (18.0, 21.0, "Same timing as the next."),
    (18.0, 21.0, "Same timing as the one before."),
    # 9: ends before it starts
    (24.0, 22.0, "Ends before it starts."),
    # 10: two long lines, three lines, and 0.3 s on screen
    (25.0, 25.3, "This first line is far too long for any subtitle to carry\nand this second one is nearly as long as the first\nthird"),
    (30.0, 33.0, "Press Validate to see them all."),
]

# (speaker, start, end, text) -- a conversation, as the worker delivers it:
# short segments in lower case, which the editor tidies into paragraphs.
CONVERSATION = [
    ("SPEAKER_00", 0.5, 4.5, "thanks for taking the time to talk to us today"),
    ("SPEAKER_01", 5.0, 8.0, "happy to, i have been using the service for a while"),
    ("SPEAKER_00", 8.5, 11.5, "what do you mostly use it for"),
    ("SPEAKER_01", 12.0, 17.5, "mainly interviews and some lectures, around an hour each"),
    ("SPEAKER_01", 17.5, 21.0, "the transcript is a good start but i always read it through"),
    ("SPEAKER_00", 21.5, 24.5, "and the highlighted words help with that"),
    ("SPEAKER_01", 25.0, 30.0, "yes, i go straight to those and listen to the recording"),
    ("SPEAKER_00", 30.5, 34.0, "anything you would like to see changed"),
    ("SPEAKER_01", 34.5, 39.5, "not really, it saves me a lot of time every week"),
]


def srt_time(seconds: float) -> str:
    millis = round(seconds * 1000)
    return (
        f"{millis // 3600000:02}:{millis // 60000 % 60:02}:"
        f"{millis // 1000 % 60:02},{millis % 1000:03}"
    )


def make_srt(captions=None) -> str:
    return "\n".join(
        f"{index}\n{srt_time(start)} --> {srt_time(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(captions or SUBTITLES, 1)
    )


def spoken_in_order(captions) -> list[tuple[float, float, str]]:
    """
    One slot of speech per caption, back to back, whatever the caption's own
    timing says. The words of a recording are flat and time-ordered; spans
    taken from captions that overlap, repeat or run backwards would not be.
    """

    return [
        (0.5 + 3.5 * number, 3.5 + 3.5 * number, text)
        for number, (_start, _end, text) in enumerate(captions)
    ]


def make_words(spans: list[tuple[float, float, str]]) -> dict:
    """
    Word timings spread over each span by word length. Confidence is made up
    but stable (a checksum of the word and its place), with a few low ones so
    the review marking has something to show.
    """

    words = []

    for start, end, text in spans:
        tokens = text.replace("\n", " ").split()
        total = sum(len(token) for token in tokens)
        cursor = start

        for token in tokens:
            length = (end - start) * len(token) / total
            seed = zlib.crc32(f"{token}{start}".encode()) % 100
            confidence = round(0.2 + seed / 400, 2) if seed < 9 else round(0.8 + seed / 500, 2)
            words.append(
                {
                    "t": token,
                    "s": round(cursor, 2),
                    "e": round(cursor + length * 0.9, 2),
                    "c": min(confidence, 0.99),
                }
            )
            cursor += length

    return {"version": 1, "words": words}


def make_conversation() -> dict:
    return {
        "segments": [
            {"start": start, "end": end, "text": text, "speaker": speaker}
            for speaker, start, end, text in CONVERSATION
        ]
    }


async def add_job(user_id, public_key, storage, name, output_format, result_kwargs):
    job = await job_create(
        user_id=user_id,
        job_type=JobType.TRANSCRIPTION,
        language="English",
        model_type="Slower transcription (higher accuracy)",
        filename=encrypt_string(public_key, name),
        output_format=output_format,
    )
    folder = storage / user_id
    folder.mkdir(parents=True, exist_ok=True)

    # The recording the editor plays, encrypted for the user like a real one.
    await asyncio.to_thread(
        encrypt_data_to_file,
        public_key,
        SAMPLE_VIDEO.read_bytes(),
        str(folder / f"{job['uuid']}.mp4.enc"),
    )

    # The words first, then the text: job_result_save json-encodes `result`
    # when it is what creates the row (the worker never does that; it sends
    # the words or the SRT first), which would store the transcription
    # double-encoded.
    words, text = result_kwargs(public_key)
    await job_result_save(job["uuid"], user_id, result_words=words)
    await job_result_save(job["uuid"], user_id, **text)
    await job_update(job["uuid"], status=JobStatusEnum.COMPLETED, transcribed_seconds=40)
    return job["uuid"]


SAMPLE_SUBTITLES = "Sample subtitles.mp4"
SAMPLE_INTERVIEW = "Sample interview.mp4"
SAMPLE_INVALID = "Sample subtitles with errors.mp4"


async def main() -> None:
    username, passphrase = sys.argv[1], sys.argv[2]
    settings = get_settings()
    user_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, username))

    engine = create_engine(settings.API_DATABASE_URL)
    with sessionmaker(bind=engine)() as session:
        filenames = [
            job.filename
            for job in session.scalars(select(Job).where(Job.user_id == user_id))
        ]

    existing: set[str] = set()

    if filenames:
        # Which samples are there already? The names are encrypted for the
        # user, so this needs their key -- which the passphrase opens, unless
        # it was changed since, in which case there is no telling and the
        # jobs are left alone.
        try:
            pem = await user_get_private_key(user_id)
            private_key = deserialize_private_key_from_pem(
                pem.encode() if isinstance(pem, str) else pem, passphrase.encode()
            )
        except Exception as error:
            print(
                "Sample jobs: the user already has jobs and the passphrase does "
                f"not open them ({type(error).__name__}); nothing added."
            )
            return

        for filename in filenames:
            try:
                existing.add(decrypt_string(private_key, filename))
            except Exception:
                pass  # Not a name this key can read; not one of ours.

    if await user_get_public_key(user_id) is None:
        await user_update(
            user_id, encryption_password=passphrase, encryption_settings=True
        )

    public_key = deserialize_public_key_from_pem(await user_get_public_key(user_id))
    storage = Path(settings.API_FILE_STORAGE_DIR)

    samples = {
        SAMPLE_SUBTITLES: (
            "srt",
            lambda key: (
                encrypt_string(
                    key, json.dumps(make_words([(s, e, t) for s, e, t in SUBTITLES]))
                ),
                {"result_srt": encrypt_string(key, make_srt())},
            ),
        ),
        SAMPLE_INTERVIEW: (
            "txt",
            lambda key: (
                encrypt_string(
                    key,
                    json.dumps(make_words([(s, e, t) for _, s, e, t in CONVERSATION])),
                ),
                {"result": encrypt_string(key, json.dumps(make_conversation()))},
            ),
        ),
        SAMPLE_INVALID: (
            "srt",
            lambda key: (
                encrypt_string(
                    key,
                    json.dumps(make_words(spoken_in_order(INVALID_SUBTITLES))),
                ),
                {"result_srt": encrypt_string(key, make_srt(INVALID_SUBTITLES))},
            ),
        ),
    }

    added = []
    names = []

    for name, (output_format, result_kwargs) in samples.items():
        if name in existing:
            continue

        added.append(
            await add_job(
                user_id, public_key, storage, name, output_format, result_kwargs
            )
        )
        names.append(name)

    if not added:
        print("Sample jobs: all there already; nothing added.")
        return

    # Jobs are removed when their deletion date passes; these should stay.
    with sessionmaker(bind=engine)() as session:
        for job in session.scalars(select(Job).where(Job.uuid.in_(added))):
            job.deletion_date = datetime.now() + timedelta(days=3650)
        session.commit()

    print(f"Sample jobs: added {', '.join(names)}.")


if __name__ == "__main__":
    asyncio.run(main())

    # The backend's async engine leaves its database thread running, which
    # would keep this one-shot script alive for ever.
    sys.stdout.flush()
    os._exit(0)
