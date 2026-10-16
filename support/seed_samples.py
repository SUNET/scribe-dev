"""
Give the dev user finished jobs to open, each there to try something:

  Sample subtitles                      the ordinary case
  Sample interview                      a transcription with two speakers
  Sample subtitles with errors          breaks every rule "Validate" checks
  Sample subtitles without word timings what a job from before word timings
                                        looks like: no timeline, no review
  Sample subtitles with awkward text    markup, template syntax, other
                                        scripts, emoji: nothing may be
                                        interpreted, only shown
  Sample interview with awkward text    the same in speaker names and turns
  Sample recording                      made in the browser: keeps its
                                        original, which Export can include
  Sample long subtitles (1 hour)        600 captions, to feel the editor at
                                        the size a lecture really is
  Sample subtitles at the limits        42 characters, two lines, 0.8 s,
                                        captions touching: nothing to report
  Sample subtitles without confidence   word timings but no scores: no review
                                        controls
  Sample audio only                     no picture
  Sample expiring soon                  deleted by the backend in a day
  Sample pending / in progress /        the other states a job passes
  uploading / uploaded / failed         through, which have no result to open

All with the same short sample video (the recording plays audio only), and
word timings unless it is the one meant to have none.

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
import random
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
# What a recording plays back as (the worker's audio-only mp4), and what the
# browser made of it: the original, a webm as MediaRecorder writes one.
SAMPLE_AUDIO = Path(__file__).parent / "samples" / "sample-audio.mp4"
SAMPLE_ORIGINAL = Path(__file__).parent / "samples" / "sample-recording.webm"

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

# Subtitles that sit exactly on every limit "Validate" checks, and none past
# it: lines of 42 characters (CHARACTER_LIMIT), two of them (MAX_SUBTITLE_LINES),
# a caption on screen for exactly 0.8 s (MIN_CAPTION_SECONDS), and captions
# touching, one ending the instant the next begins. There is nothing to report.
# Where the boundary is drawn is what this is for: a check that is off by one,
# or by a float's last digit, flags these.
LIMIT_SUBTITLES = [
    (0.0, 3.0, "Forty-two characters is exactly the limit.\nTwo lines are as many as the limit allows."),
    (3.1, 3.9, "Exactly the shortest it may stay up."),
    (4.0, 7.0, "This one ends where the next begins."),
    (7.0, 10.0, "And this one begins where that ended."),
    (10.5, 11.3, "Another one of exactly 0.8 seconds."),
    (13.0, 16.0, "A short one on a single line."),
    (16.5, 19.5, "Two lines are as many as the limit allows."),
    (20.0, 23.0, "Short first line,\nand a short second line."),
]

# Text that must only ever be shown. Markup that would run if it were ever
# treated as HTML, the template syntax of the things this passes through, other
# scripts and directions, emoji, characters that are there and invisible, and
# one word too long for any column. Lines stay under the guideline so that
# Validate has nothing to say about all this except the one long word.
AWKWARD_SUBTITLES = [
    (0.5, 3.5, "<img src=x onerror=alert(1)>\n<script>alert(2)</script>"),
    (4.0, 7.0, "&lt;b&gt;not bold&lt;/b&gt; &amp; &copy;\n<b>really bold?</b>"),
    (7.5, 10.5, "{{ 7 * 7 }} ${7*7} {% raw %}\n%s %(name)s {0} \\n"),
    (11.0, 14.0, "\u00c5ke, \u00d6sten och \u00c4rlig \u00e5t gr\u00f6t\nSm\u00f6rg\u00e5sbord och bl\u00e5b\u00e4r"),
    (14.5, 17.5, "S\u00e1megiella: boazodoallu, \u0161uok\u014ba\n\u0111 \u0167 \u014b \u0161 \u017e \u010d \u2013 Northern S\u00e1mi"),
    (18.0, 21.0, "\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645\u060c \u0647\u0630\u0627 \u0646\u0635\n\u05e9\u05dc\u05d5\u05dd \u05e2\u05d5\u05dc\u05dd, \u05d6\u05d4 \u05d8\u05e7\u05e1\u05d8"),
    (21.5, 24.5, "\U0001F600 \U0001F469\u200d\U0001F469\u200d\U0001F467\u200d\U0001F466 \U0001F1F8\U0001F1EA \u266a Singing \u266a\n\u266a \u266a \u266b \U0001F3B5"),
    (25.0, 28.0, "Zero\u200bwidth\u200bspaces\u200bhere\nand a soft\u00adhyphen too"),
    (28.5, 31.5, "Supercalifragilisticexpialidocious_and_then_some_more_letters_without_any_break"),
    (32.0, 35.0, "Quotes \" ' ` and a backslash \\\nand a tab\there"),
    (35.5, 38.0, "**bold** _italic_ [x](javascript:alert(3))\n# not a heading"),
    (38.5, 40.0, "a --> b, and 00:00:01,000 --> 00:00:02,000\nlooks like a timing line"),
]

# (speaker, start, end, text) -- the same in a transcription, where the
# speaker's name is shown too and a reader can rename it to anything.
AWKWARD_CONVERSATION = [
    ("<img src=x onerror=alert(1)>", 0.5, 5.0, "<script>alert(2)</script> is what my name looks like"),
    ('\u00c5sa "the boss" <b>Ek</b>', 5.5, 10.0, "{{ 7 * 7 }} and &amp; and \u00c5ke, \u00d6sten och \u00c4rlig"),
    ("<img src=x onerror=alert(1)>", 10.5, 16.0, "\u0645\u0631\u062d\u0628\u0627 \u05e9\u05dc\u05d5\u05dd \U0001F600 \u266a"),
    ('\u00c5sa "the boss" <b>Ek</b>', 16.5, 22.0, "**bold** [link](javascript:alert(3)) %s ${7*7}"),
]

# Words to build a lecture's worth of captions from.
LONG_WORDS = (
    "the of and to in is that it was for on are with as this be at have from "
    "or by one had not but what all were when we there can an your which their "
    "said if do will each about how up out them then she many some so these "
    "would other into has more her two like him see time could no make than "
    "first been its who now people my made over did down only way find use may "
    "water long little very after words called just where most know"
).split()


def make_long_subtitles(count: int = 600, seconds: float = 6.0) -> list:
    """
    An hour of captions, two lines at most, each held for a second less than
    its slot. Seeded, so every environment gets the same hour.
    """

    rng = random.Random(7)
    captions = []

    for number in range(count):
        words = [rng.choice(LONG_WORDS) for _ in range(rng.randint(4, 8))]
        words[0] = words[0].capitalize()
        lines, line = [], ""

        for word in words:
            if line and len(line) + 1 + len(word) > 40:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()

        lines.append(line + ".")
        start = number * seconds + 0.5
        captions.append((start, start + seconds - 1.0, "\n".join(lines[:2])))

    return captions


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


def make_words(spans: list[tuple[float, float, str]], confidence: bool = True) -> dict:
    """
    Word timings spread over each span by word length. Confidence is made up
    but stable (a checksum of the word and its place), with a few low ones so
    the review marking has something to show. Without it (`confidence=False`)
    the words carry no score at all, as when the worker ran with
    WORD_CONFIDENCE=false.
    """

    words = []

    for start, end, text in spans:
        tokens = text.replace("\n", " ").split()
        total = sum(len(token) for token in tokens)
        cursor = start

        for token in tokens:
            length = (end - start) * len(token) / total
            seed = zlib.crc32(f"{token}{start}".encode()) % 100
            score = round(0.2 + seed / 400, 2) if seed < 9 else round(0.8 + seed / 500, 2)
            word = {
                "t": token,
                "s": round(cursor, 2),
                "e": round(cursor + length * 0.9, 2),
            }

            if confidence:
                word["c"] = min(score, 0.99)

            words.append(word)
            cursor += length

    return {"version": 1, "words": words}


def make_conversation(conversation=None) -> dict:
    return {
        "segments": [
            {"start": start, "end": end, "text": text, "speaker": speaker}
            for speaker, start, end, text in conversation or CONVERSATION
        ]
    }


async def add_job(
    user_id,
    public_key,
    storage,
    name,
    output_format,
    result_kwargs,
    media=SAMPLE_VIDEO,
    original=None,
):
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
    # Always called .mp4: the worker converts whatever it was given to one.
    await asyncio.to_thread(
        encrypt_data_to_file,
        public_key,
        media.read_bytes(),
        str(folder / f"{job['uuid']}.mp4.enc"),
    )

    # What a recording made in the browser keeps besides: the file as the
    # browser made it, encrypted for the user (utils/recordings.py in the
    # backend). Its existing is what makes a job a recording.
    if original is not None:
        await asyncio.to_thread(
            encrypt_data_to_file,
            public_key,
            original.read_bytes(),
            str(folder / f"{job['uuid']}.orig.enc"),
        )

    # The words first, then the text: job_result_save json-encodes `result`
    # when it is what creates the row (the worker never does that; it sends
    # the words or the SRT first), which would store the transcription
    # double-encoded. A job from before word timings existed has no words at
    # all, which is what None here is for.
    words, text = result_kwargs(public_key)

    if words is not None:
        await job_result_save(job["uuid"], user_id, result_words=words)

    await job_result_save(job["uuid"], user_id, **text)
    await job_update(job["uuid"], status=JobStatusEnum.COMPLETED, transcribed_seconds=40)
    return job["uuid"]


async def add_state_job(user_id, public_key, name, status, error=None):
    """
    A job on its way somewhere, or that did not get there: no recording and no
    result, which is what such a job has. What the jobs list shows for each
    state is what these are for.
    """

    job = await job_create(
        user_id=user_id,
        job_type=JobType.TRANSCRIPTION,
        language="English",
        model_type="Slower transcription (higher accuracy)",
        filename=encrypt_string(public_key, name),
        output_format="txt",
    )
    await job_update(job["uuid"], status=status, error=error)
    return job["uuid"]


# Jobs that are in a state other than finished, by name: (status, error).
STATE_SAMPLES = {
    "Sample pending.mp3": (JobStatusEnum.PENDING, None),
    "Sample in progress.mp3": (JobStatusEnum.IN_PROGRESS, None),
    "Sample uploading.mp3": (JobStatusEnum.UPLOADING, None),
    "Sample uploaded, not transcribed.mp3": (JobStatusEnum.UPLOADED, None),
    "Sample failed.mp3": (
        JobStatusEnum.FAILED,
        "Transcription failed: the audio could not be decoded.",
    ),
}

# How long a sample is kept, where it is not the ten years every other one gets.
# The backend removes a job when its date passes, and a sample that is gone is
# added again by the next launch.
LIFETIMES = {"Sample expiring soon.mp4": timedelta(hours=20)}


SAMPLE_SUBTITLES = "Sample subtitles.mp4"
SAMPLE_INTERVIEW = "Sample interview.mp4"
SAMPLE_INVALID = "Sample subtitles with errors.mp4"
SAMPLE_NO_WORDS = "Sample subtitles without word timings.mp4"
SAMPLE_AWKWARD = "Sample subtitles with awkward text.mp4"
SAMPLE_AWKWARD_INTERVIEW = "Sample interview with awkward text.mp4"
SAMPLE_RECORDING = "Sample recording.webm"
SAMPLE_LONG = "Sample long subtitles (1 hour).mp4"
SAMPLE_LIMITS = "Sample subtitles at the limits.mp4"
SAMPLE_NO_CONFIDENCE = "Sample subtitles without confidence.mp4"
SAMPLE_AUDIO_ONLY = "Sample audio only.mp3"
SAMPLE_EXPIRING = "Sample expiring soon.mp4"


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
        SAMPLE_NO_WORDS: (
            "srt",
            lambda key: (None, {"result_srt": encrypt_string(key, make_srt())}),
        ),
        SAMPLE_AWKWARD: (
            "srt",
            lambda key: (
                encrypt_string(
                    key, json.dumps(make_words(list(AWKWARD_SUBTITLES)))
                ),
                {"result_srt": encrypt_string(key, make_srt(AWKWARD_SUBTITLES))},
            ),
        ),
        SAMPLE_AWKWARD_INTERVIEW: (
            "txt",
            lambda key: (
                encrypt_string(
                    key,
                    json.dumps(
                        make_words([(s, e, t) for _, s, e, t in AWKWARD_CONVERSATION])
                    ),
                ),
                {
                    "result": encrypt_string(
                        key, json.dumps(make_conversation(AWKWARD_CONVERSATION))
                    )
                },
            ),
        ),
        SAMPLE_RECORDING: (
            "txt",
            lambda key: (
                encrypt_string(
                    key,
                    json.dumps(make_words([(s, e, t) for _, s, e, t in CONVERSATION])),
                ),
                {"result": encrypt_string(key, json.dumps(make_conversation()))},
            ),
        ),
        SAMPLE_LONG: (
            "srt",
            lambda key: (
                encrypt_string(key, json.dumps(make_words(make_long_subtitles()))),
                {"result_srt": encrypt_string(key, make_srt(make_long_subtitles()))},
            ),
        ),
    }

    samples[SAMPLE_LIMITS] = (
        "srt",
        lambda key: (
            encrypt_string(key, json.dumps(make_words(list(LIMIT_SUBTITLES)))),
            {"result_srt": encrypt_string(key, make_srt(LIMIT_SUBTITLES))},
        ),
    )
    samples[SAMPLE_NO_CONFIDENCE] = (
        "srt",
        lambda key: (
            encrypt_string(
                key, json.dumps(make_words(list(SUBTITLES), confidence=False))
            ),
            {"result_srt": encrypt_string(key, make_srt())},
        ),
    )
    samples[SAMPLE_AUDIO_ONLY] = (
        "srt",
        lambda key: (
            encrypt_string(key, json.dumps(make_words(list(SUBTITLES)))),
            {"result_srt": encrypt_string(key, make_srt())},
        ),
    )
    samples[SAMPLE_EXPIRING] = (
        "srt",
        lambda key: (
            encrypt_string(key, json.dumps(make_words(list(SUBTITLES)))),
            {"result_srt": encrypt_string(key, make_srt())},
        ),
    )

    # Samples that bring a file of their own: the recording plays as audio and
    # keeps the browser's original beside it; so does a job that is only audio.
    files = {
        SAMPLE_RECORDING: {"media": SAMPLE_AUDIO, "original": SAMPLE_ORIGINAL},
        SAMPLE_AUDIO_ONLY: {"media": SAMPLE_AUDIO},
    }

    added = []
    names = []
    lifetimes: dict = {}

    for name, (output_format, result_kwargs) in samples.items():
        if name in existing:
            continue

        added.append(
            await add_job(
                user_id,
                public_key,
                storage,
                name,
                output_format,
                result_kwargs,
                **files.get(name, {}),
            )
        )
        names.append(name)
        lifetimes[added[-1]] = LIFETIMES.get(name, timedelta(days=3650))

    for name, (status, error) in STATE_SAMPLES.items():
        if name in existing:
            continue

        added.append(await add_state_job(user_id, public_key, name, status, error))
        names.append(name)
        lifetimes[added[-1]] = LIFETIMES.get(name, timedelta(days=3650))

    if not added:
        print("Sample jobs: all there already; nothing added.")
        return

    # Jobs are removed when their deletion date passes; these should stay.
    with sessionmaker(bind=engine)() as session:
        for job in session.scalars(select(Job).where(Job.uuid.in_(added))):
            job.deletion_date = datetime.now() + lifetimes[job.uuid]
        session.commit()

    print(f"Sample jobs: added {', '.join(names)}.")


if __name__ == "__main__":
    asyncio.run(main())

    # The backend's async engine leaves its database thread running, which
    # would keep this one-shot script alive for ever.
    sys.stdout.flush()
    os._exit(0)
