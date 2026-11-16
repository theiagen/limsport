"""Test helper: load a YAML config through the real limsport.config.load_config
validator and print its model_dump() as JSON, so the Node test suite can prove
the generator's output is byte-for-byte semantically what Pydantic expects --
not a reimplementation of the schema, the schema itself.

Usage: python3 load_and_dump.py <path-to-config.yaml>
Exit 0 + JSON on stdout if valid. Exit 1 + error message on stderr if invalid.
"""

import json
import sys
from pathlib import Path

from limsport.config import load_config
from limsport.exceptions import LIMSportError

if __name__ == "__main__":
    path = Path(sys.argv[1])
    try:
        config = load_config(path)
    except LIMSportError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    print(json.dumps(config.model_dump(mode="json"), sort_keys=True))
