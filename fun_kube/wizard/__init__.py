"""Interactive TUI wizard that writes a Fun-Kube .env file and optionally runs `up`."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TEXTUAL_REQUIREMENT = "textual>=8,<9"
_FUN_KUBE = Path(__file__).resolve().parents[2] / "fun-kube"


def _ensure_textual() -> None:
    """Textual is installed with the venv on first run; older venvs get it here."""
    try:
        import textual  # noqa: F401
    except ImportError:
        print("fun-kube: installing the wizard UI library (textual)...", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", TEXTUAL_REQUIREMENT], check=True)


def launch(project_dir: Path) -> None:
    """Run the wizard; then, if requested, hand over to `fun-kube up` (dry-run or real)."""
    _ensure_textual()
    from .app import run

    result = run(project_dir)
    if not result:
        print("Wizard closed without saving.")
        return
    path, action = result
    print(f"Saved {path}")
    if action == "save":
        print(f"Next: ./fun-kube up {path.name} --dry-run   (then ./fun-kube up {path.name})")
        return
    args = [sys.executable, str(_FUN_KUBE), "up", str(path)] + (["--dry-run"] if action == "dry-run" else [])
    os.execv(sys.executable, args)
