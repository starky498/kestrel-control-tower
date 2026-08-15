#!/usr/bin/env python3
"""Download and verify the optional local Ask Kestrel embedding model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kestrel.nlq.model_install import default_manifest_path, default_model_path, install_model


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=default_manifest_path())
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help=(
            "Installation directory. Defaults to KESTREL_NLQ_MODEL_PATH, or the model "
            "directory below KESTREL_RUNTIME_DIR."
        ),
    )
    parser.add_argument("--architecture")
    arguments = parser.parse_args()
    installed = install_model(
        manifest=arguments.manifest.resolve(),
        target=(arguments.target or default_model_path()).resolve(),
        architecture=arguments.architecture,
    )
    print(json.dumps(installed, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
