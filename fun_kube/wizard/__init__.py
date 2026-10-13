"""Interactive TUI wizard that writes a Fun-Kube .env file and optionally runs `up`."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TEXTUAL_REQUIREMENT = "textual>=8,<9"


def _ensure_textual() -> None:
    """Textual is installed with the venv on first run; older venvs get it here."""
    try:
        import textual  # noqa: F401
    except ImportError:
        print("fun-kube: installing the wizard UI library (textual)...", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", TEXTUAL_REQUIREMENT], check=True)


def launch(project_dir: Path) -> None:
    """Run the wizard; then, if requested, hand over to `fun-kube up` / `upgrade` (the real run:
    it may ask for confirmations, so it runs in the plain terminal). Dry-runs run inside the wizard."""
    _ensure_textual()
    from .app import run
    from .model import command

    result = run(project_dir)
    if not result:
        print("Wizard closed without saving.")
        return
    path, action, upgrade_to = result
    print(f"Saved {path}")
    if action == "save":
        if upgrade_to:
            print(f"Next: ./fun-kube upgrade --to {upgrade_to} {path.name} --dry-run   "
                  f"(then without --dry-run)")
        else:
            print(f"Next: ./fun-kube up {path.name} --dry-run   (then ./fun-kube up {path.name})")
        return
    args = command(path, action, upgrade_to)
    os.execv(sys.executable, args)
