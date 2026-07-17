from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "python/src/pyxbot2_diagnostics"


def test_package_source_parses_as_python38() -> None:
    feature_version = (3, 8) if sys.version_info >= (3, 9) else 8
    for path in PACKAGE_ROOT.rglob("*.py"):
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=str(path),
            feature_version=feature_version,
        )


def test_python38_incompatible_dataclass_slots_are_not_used() -> None:
    for path in PACKAGE_ROOT.rglob("*.py"):
        assert "slots=True" not in path.read_text(encoding="utf-8"), path
