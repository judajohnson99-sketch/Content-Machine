"""Load the repository's .env into os.environ - the one way every entry
point (CLI, Django, Celery) sees the same configuration.

Standard library only, on purpose: the render/generation path must not
grow a dependency for this. Semantics match what an operator expects from
a .env file: KEY=VALUE per line, `#` comments and blank lines ignored,
optional single/double quotes stripped, and a variable already present in
the real environment is never overridden - the shell wins over the file.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / ".env"


def parse_env_file(path):
    """Return {KEY: VALUE} for a .env file, or {} if it does not exist."""
    path = Path(path)
    if not path.is_file():
        return {}
    values = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_env_file(path=DEFAULT_PATH, environ=None):
    """Apply the file to ``environ`` (os.environ by default) without
    overriding keys that are already set. Returns the keys it added."""
    environ = os.environ if environ is None else environ
    added = []
    for key, value in parse_env_file(path).items():
        if key not in environ:
            environ[key] = value
            added.append(key)
    return added
