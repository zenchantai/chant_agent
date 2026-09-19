"""Keep application import-time initialization away from the user's database."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


_application_data = TemporaryDirectory(prefix="chant-agent-tests-")
os.environ["CHANT_AGENT_DB_PATH"] = str(Path(_application_data.name) / "application.db")
os.environ["CHANT_AGENT_BACKGROUND_SYNC"] = "0"
