"""Guard: every Alembic revision id must fit PostgreSQL's alembic_version.version_num (VARCHAR(32)).

SQLite does not enforce the length, so an over-long id only fails later in PostgreSQL CI at
`alembic upgrade head`. This test needs no database and runs in the non-PostgreSQL CI selection.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = Path(__file__).resolve().parents[1]
MAX_REVISION_ID_LENGTH = 32  # alembic_version.version_num is VARCHAR(32) in PostgreSQL


def test_every_revision_id_fits_alembic_version_column():
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    revisions = list(ScriptDirectory.from_config(config).walk_revisions())
    assert revisions, "no Alembic revisions were loaded"
    too_long = [(rev.revision, len(rev.revision)) for rev in revisions if len(rev.revision) > MAX_REVISION_ID_LENGTH]
    assert not too_long, "; ".join(
        f"revision {name!r} has {length} characters (max {MAX_REVISION_ID_LENGTH})" for name, length in too_long
    )
