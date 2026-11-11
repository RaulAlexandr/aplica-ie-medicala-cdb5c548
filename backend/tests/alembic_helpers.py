from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def configured_alembic_head() -> str:
    """Return the sole head configured by this checkout's Alembic graph."""
    backend_dir = Path(__file__).resolve().parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()
