"""
Verifica che tutti i tool richiesti sulla macchina bootstrap siano presenti.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import List

from rich.console import Console
from rich.table import Table

console = Console()


class DepsError(Exception):
    pass


@dataclass
class DepResult:
    name: str
    ok: bool
    version: str = ""
    detail: str = ""


# Tool richiesti: (nome, comando versione, binario da cercare)
_REQUIRED = [
    ("python3",          ["python3", "--version"]),
    ("ansible-playbook", ["ansible-playbook", "--version"]),
    ("ansible-galaxy",   ["ansible-galaxy", "--version"]),
    ("kubectl",          ["kubectl", "version", "--client", "--short"]),
    ("helm",             ["helm", "version", "--short"]),
    ("ssh",              ["ssh", "-V"]),
    ("scp",              ["scp", "-V"]),
    ("git",              ["git", "--version"]),
]

# Collezioni Ansible obbligatorie
_ANSIBLE_COLLECTIONS = [
    "community.general",
]


def run(verbose: bool = False) -> None:
    """Verifica tutte le dipendenze. Solleva DepsError se qualcosa manca."""
    results: List[DepResult] = []

    for name, version_cmd in _REQUIRED:
        results.append(_check_binary(name, version_cmd))

    for col in _ANSIBLE_COLLECTIONS:
        results.append(_check_ansible_collection(col))

    _print_table(results, verbose)

    missing = [r for r in results if not r.ok]
    if missing:
        names = ", ".join(r.name for r in missing)
        raise DepsError(
            f"Dipendenze mancanti: {names}\n"
            f"  Esegui ./bootstrap-setup.sh per installare tutto automaticamente."
        )


def _check_binary(name: str, version_cmd: list) -> DepResult:
    if not shutil.which(version_cmd[0]):
        return DepResult(name=name, ok=False, detail="non trovato nel PATH")

    try:
        result = subprocess.run(
            version_cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (result.stdout + result.stderr).strip()
        version = output.splitlines()[0][:60] if output else "ok"
        return DepResult(name=name, ok=True, version=version)
    except Exception as e:
        return DepResult(name=name, ok=False, detail=str(e)[:80])


def _check_ansible_collection(collection: str) -> DepResult:
    name = f"collection:{collection}"
    try:
        result = subprocess.run(
            ["ansible-galaxy", "collection", "list"],
            capture_output=True, text=True, timeout=15,
        )
        # ansible-galaxy list output: "collection_name  version"
        found = any(
            line.strip().startswith(collection)
            for line in result.stdout.splitlines()
        )
        if found:
            return DepResult(name=name, ok=True, version="installata")
        return DepResult(
            name=name,
            ok=False,
            detail=f"mancante — esegui: ansible-galaxy collection install {collection}",
        )
    except Exception as e:
        return DepResult(name=name, ok=False, detail=str(e)[:80])


def _print_table(results: List[DepResult], verbose: bool) -> None:
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Dipendenza")
    table.add_column("Status", justify="center")
    if verbose:
        table.add_column("Versione / Dettaglio", style="dim")

    for r in results:
        status = "[green]OK[/]" if r.ok else "[red]MANCANTE[/]"
        if verbose:
            detail = r.version if r.ok else r.detail
            table.add_row(r.name, status, detail)
        else:
            table.add_row(r.name, status)

    console.print(table)
