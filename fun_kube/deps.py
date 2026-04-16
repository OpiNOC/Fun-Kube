"""
Verifica e installazione automatica dei tool richiesti sulla macchina bootstrap.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
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


# Tool richiesti: (nome, comando versione)
_REQUIRED = [
    ("python3",          ["python3", "--version"]),
    ("ansible-playbook", ["ansible-playbook", "--version"]),
    ("ansible-galaxy",   ["ansible-galaxy", "--version"]),
    ("kubectl",          ["kubectl", "version", "--client"]),
    ("helm",             ["helm", "version", "--short"]),
    ("ssh",              ["ssh", "-V"]),
    ("scp",              ["scp", "-V"]),
    ("git",              ["git", "--version"]),
]

# Collezioni Ansible obbligatorie
_ANSIBLE_COLLECTIONS = [
    "community.general",
]


# ---------------------------------------------------------------------------
# Auto-install
# ---------------------------------------------------------------------------

def auto_install() -> None:
    """Installa i tool mancanti sulla macchina bootstrap. Idempotente."""
    _ensure_ansible()
    _ensure_kubectl()
    _ensure_helm()
    _ensure_ansible_collection("community.general")


def _apt(packages: List[str]) -> None:
    subprocess.run(
        ["sudo", "apt-get", "install", "-y", "-q"] + packages,
        check=True,
    )


def _ensure_ansible() -> None:
    if shutil.which("ansible-playbook"):
        return
    console.print("  [cyan]▶[/]  installazione Ansible (apt)...")
    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=True)
    _apt(["ansible"])
    console.print("  [green]✓[/]  Ansible installato")


def _ensure_kubectl() -> None:
    if shutil.which("kubectl"):
        return
    console.print("  [cyan]▶[/]  installazione kubectl...")
    with urllib.request.urlopen("https://dl.k8s.io/release/stable.txt", timeout=15) as r:
        version = r.read().decode().strip()
    arch_raw = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    # dpkg usa amd64/arm64, dl.k8s.io usa la stessa nomenclatura
    url = f"https://dl.k8s.io/release/{version}/bin/linux/{arch_raw}/kubectl"
    with tempfile.NamedTemporaryFile(delete=False, suffix="kubectl") as tmp:
        tmp_path = tmp.name
    urllib.request.urlretrieve(url, tmp_path)
    subprocess.run(
        ["sudo", "install", "-o", "root", "-g", "root", "-m", "0755", tmp_path, "/usr/local/bin/kubectl"],
        check=True,
    )
    Path(tmp_path).unlink(missing_ok=True)
    console.print(f"  [green]✓[/]  kubectl {version} installato")


def _ensure_helm() -> None:
    if shutil.which("helm"):
        return
    console.print("  [cyan]▶[/]  installazione Helm...")
    with tempfile.NamedTemporaryFile(delete=False, suffix=".sh", mode="w") as tmp:
        tmp_path = tmp.name
    with urllib.request.urlopen("https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3", timeout=15) as r:
        Path(tmp_path).write_bytes(r.read())
    subprocess.run(["bash", tmp_path], check=True, env={**__import__("os").environ, "VERIFY_CHECKSUM": "true"})
    Path(tmp_path).unlink(missing_ok=True)
    console.print("  [green]✓[/]  Helm installato")


def _ensure_ansible_collection(collection: str) -> None:
    if not shutil.which("ansible-galaxy"):
        return  # ansible non ancora installato, verrà riprovato dopo
    result = subprocess.run(
        ["ansible-galaxy", "collection", "list"],
        capture_output=True, text=True, timeout=15,
    )
    if any(line.strip().startswith(collection) for line in result.stdout.splitlines()):
        return
    console.print(f"  [cyan]▶[/]  installazione collection {collection}...")
    subprocess.run(
        ["ansible-galaxy", "collection", "install", collection],
        check=True,
    )
    console.print(f"  [green]✓[/]  {collection} installata")


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

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
        raise DepsError(f"Dipendenze mancanti: {names}")


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
