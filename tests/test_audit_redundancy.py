from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_redundancy_audit_avoids_known_false_positives():
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "audit_redundancy.py")],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "unused import 'annotations'" not in completed.stdout
    assert "\n  slidenote.cli\n" not in completed.stdout
    assert "slidenote.parser_adapters.supports  ==  slidenote.parser_adapters.supports" not in completed.stdout
