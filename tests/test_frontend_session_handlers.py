"""Run actual static/uni handlers with controlled platform and transport seams."""
from pathlib import Path
import subprocess


def test_session_reset_and_retained_photo_handlers():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node','--test','frontend/session-behavior.test.mjs'],
                            cwd=root,capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
