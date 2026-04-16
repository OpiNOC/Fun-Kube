"""
Preflight checks via SSH su tutti i nodi prima del provisioning.
Usa subprocess + ssh standard (nessuna dipendenza aggiuntiva).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import List, Tuple

from rich.console import Console
from rich.table import Table

from .config import ClusterConfig, NodeConfig

console = Console()


class PreflightError(Exception):
    pass


@dataclass
class CheckResult:
    node: str
    check: str
    ok: bool
    detail: str = ""


# ---------------------------------------------------------------------------
# Entry point pubblico
# ---------------------------------------------------------------------------

def run(cluster: ClusterConfig, debug: bool = False) -> None:
    """Esegue tutti i preflight checks. Solleva PreflightError se qualcuno fallisce."""
    if cluster.local_node:
        console.print("  [dim]Modalità local-node: preflight eseguito in locale[/]")

    results: List[CheckResult] = []
    for node in cluster.nodes:
        results.extend(_check_node(node, cluster, debug))

    _print_table(results)

    failures = [r for r in results if not r.ok]
    if failures:
        lines = [f"  [{r.node}] {r.check}: {r.detail}" for r in failures]
        raise PreflightError("\n".join(lines))


# ---------------------------------------------------------------------------
# Check per singolo nodo
# ---------------------------------------------------------------------------

def _check_node(node: NodeConfig, cluster: ClusterConfig, debug: bool) -> List[CheckResult]:
    if cluster.local_node:
        return _check_node_local(node, debug)
    return _check_node_ssh(node, cluster, debug)


def _check_node_local(node: NodeConfig, debug: bool) -> List[CheckResult]:
    """Preflight checks eseguiti localmente (local_node=true)."""
    results = []

    def check(name: str, cmd: str, ok_fn=None) -> CheckResult:
        rc, out, err = _local(cmd, debug)
        ok = ok_fn(rc, out) if ok_fn is not None else rc == 0
        detail = (out + " " + err).strip()[:120] if not ok else ""
        return CheckResult(node=node.hostname, check=name, ok=ok, detail=detail)

    results.append(check(
        "OS: Ubuntu 24.04+",
        "awk -F= '/^VERSION_ID/{gsub(/\"/,\"\",$2); if($2+0 >= 24) exit 0; else exit 1}' /etc/os-release",
    ))
    results.append(check("CPU >= 2 cores", "[ $(nproc) -ge 2 ]"))
    results.append(check(
        "RAM >= 3GB",
        "awk '/MemTotal/{exit ($2 >= 3000000) ? 0 : 1}' /proc/meminfo",
    ))
    results.append(check("swap disabled", "swapon --show",
                         ok_fn=lambda rc, out: out.strip() == ""))
    results.append(check("disk >= 20GB free",
                         "df / | awk 'NR==2 {exit ($4 < 20971520)}'"))
    results.append(check("port 6443 free",
                         "! ss -tlnp 2>/dev/null | grep -q ':6443 '"))
    results.append(check("port 2379-2380 free",
                         "! ss -tlnp 2>/dev/null | grep -qE ':(2379|2380) '"))
    results.append(check("port 10250 free",
                         "! ss -tlnp 2>/dev/null | grep -q ':10250 '"))
    return results


def _check_node_ssh(node: NodeConfig, cluster: ClusterConfig, debug: bool) -> List[CheckResult]:
    """Preflight checks via SSH (topologie remote)."""
    results = []

    def check(name: str, cmd: str, ok_fn=None) -> CheckResult:
        rc, out, err = _ssh(node, cluster, cmd, debug)
        ok = ok_fn(rc, out) if ok_fn is not None else rc == 0
        detail = (out + " " + err).strip()[:120] if not ok else ""
        return CheckResult(node=node.hostname, check=name, ok=ok, detail=detail)

    # Connettività SSH — se fallisce, saltiamo il resto per questo nodo
    results.append(check("SSH connectivity", "echo ok"))
    if not results[-1].ok:
        return results

    results.append(check("sudo no-password", "sudo -n true"))

    # OS: Ubuntu 24.04+
    results.append(check(
        "OS: Ubuntu 24.04+",
        "awk -F= '/^VERSION_ID/{gsub(/\"/,\"\",$2); if($2+0 >= 24) exit 0; else exit 1}' /etc/os-release",
        ok_fn=lambda rc, out: rc == 0,
    ))

    # CPU >= 2
    results.append(check(
        "CPU >= 2 cores",
        "[ $(nproc) -ge 2 ]",
    ))

    # RAM: CP >= 3GB, worker >= 2GB
    ram_threshold = 3000000 if node.role == "control-plane" else 2000000
    ram_label = "RAM >= 3GB" if node.role == "control-plane" else "RAM >= 2GB"
    results.append(check(
        ram_label,
        f"awk '/MemTotal/{{exit ($2 >= {ram_threshold}) ? 0 : 1}}' /proc/meminfo",
    ))

    results.append(check("swap disabled", "swapon --show",
                         ok_fn=lambda rc, out: out.strip() == ""))
    results.append(check("kernel: br_netfilter", "lsmod | grep -q br_netfilter"))
    results.append(check("kernel: overlay", "lsmod | grep -q overlay"))
    results.append(check("disk >= 20GB free",
                         "df / | awk 'NR==2 {exit ($4 < 20971520)}'"))

    if node.role == "control-plane":
        results.append(check("port 6443 free",
                             "! ss -tlnp 2>/dev/null | grep -q ':6443 '"))
        results.append(check("port 2379-2380 free",
                             "! ss -tlnp 2>/dev/null | grep -qE ':(2379|2380) '"))

    results.append(check("port 10250 free",
                         "! ss -tlnp 2>/dev/null | grep -q ':10250 '"))

    other_ips = [n.ip for n in cluster.nodes if n.ip != node.ip]
    if other_ips:
        ping_cmd = " && ".join(f"ping -c1 -W2 {ip} >/dev/null 2>&1" for ip in other_ips)
        results.append(check("inter-node connectivity", ping_cmd))

    return results


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------

def _local(cmd: str, debug: bool) -> Tuple[int, str, str]:
    if debug:
        console.print(f"  [dim]→ local: {cmd}[/]")
    try:
        result = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=30
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


def _ssh(node: NodeConfig, cluster: ClusterConfig, cmd: str, debug: bool) -> Tuple[int, str, str]:
    ssh_cmd = [
        "ssh",
        "-i", str(cluster.ssh_key_path),
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=10",
        "-o", "BatchMode=yes",
        f"{cluster.ssh_user}@{node.ip}",
        cmd,
    ]
    if debug:
        console.print(f"  [dim]→ {cluster.ssh_user}@{node.ip}: {cmd}[/]")

    try:
        result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"
    except Exception as e:
        return 1, "", str(e)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _print_table(results: List[CheckResult]) -> None:
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Node", style="cyan")
    table.add_column("Check")
    table.add_column("Status", justify="center")
    table.add_column("Detail", style="dim")

    for r in results:
        status = "[green]OK[/]" if r.ok else "[red]FAIL[/]"
        table.add_row(r.node, r.check, status, r.detail)

    console.print(table)
