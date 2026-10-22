"""Load only the project's local environment file, without replacing shell settings."""
from pathlib import Path
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_project_env() -> None:
    # Explicit root is independent of cwd; never return or log dotenv contents.
    load_dotenv(PROJECT_ROOT / ".env", override=False, encoding="utf-8-sig")
    # Support the user's existing provider names without copying keys to files.
    for target, source in (("OPENAI_API_KEY", "DEEPSEEK_API_KEY"),
                           ("OPENAI_MODEL", "DEEPSEEK_MODEL"),
                           ("OPENAI_BASE_URL", "DEEPSEEK_API_URL")):
        if target not in os.environ and os.environ.get(source):
            os.environ[target] = os.environ[source]
