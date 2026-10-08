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

    # I nodi già nel cluster saltano i check di risorse/porte (fallirebbero:
    # i servizi sono in ascolto); i nodi nuovi, anche su cluster esistente,
    # vengono verificati per intero.
    results: List[CheckResult] = []
    for node in cluster.nodes:
        results.extend(_check_node(node, cluster, debug))

    _print_table(results)

    failures = [r for r in results if not r.ok]
    if failures:
        lines = [f"  [{r.node}] {r.check}: {r.detail}" for r in failures]
        raise PreflightError("\n".join(lines))


_JOINED_CHECK = "test -f /etc/kubernetes/kubelet.conf"
_JOINED_LABEL = "già nel cluster — altri check saltati"


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

    if _local(_JOINED_CHECK, debug)[0] == 0:
        return [CheckResult(node=node.hostname, check=_JOINED_LABEL, ok=True)]

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

    if _ssh(node, cluster, _JOINED_CHECK, debug)[0] == 0:
        results.append(CheckResult(node=node.hostname, check=_JOINED_LABEL, ok=True))
        return results

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
    results.append(check("kernel: br_netfilter",
                         "lsmod | grep -q br_netfilter || modinfo br_netfilter >/dev/null 2>&1"))
    results.append(check("kernel: overlay",
                         "lsmod | grep -q overlay || modinfo overlay >/dev/null 2>&1"))
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


# ---------------------------------------------------------------------------
# Confronto .env ↔ cluster esistente
# ---------------------------------------------------------------------------

def check_existing_cluster(cluster: ClusterConfig) -> Tuple[List[str], List[str]]:
    """Se il cluster esiste già, confronta il .env con lo stato reale.

    Ritorna (errori, warning). Gli errori riguardano modifiche che `up` non sa
    applicare e che romperebbero il cluster a metà (endpoint, CIDR, password DB
    NPM). Se il cluster non è raggiungibile (nuovo, o resettato) ritorna liste vuote.
    """
    import base64
    import json
    import os
    import re
    from pathlib import Path

    kubeconfig = next(
        (p for p in (Path(f"/root/.kube/{cluster.cluster_name}"),
                     Path(f"/root/.kube/{cluster.cluster_name}-admin"))
         if os.access(p, os.R_OK)),
        None,
    )
    if kubeconfig is None:
        return [], []

    env = {**os.environ, "KUBECONFIG": str(kubeconfig)}

    def kubectl(*args: str) -> Tuple[int, str]:
        try:
            r = subprocess.run(["kubectl", "--request-timeout=5s", *args],
                               capture_output=True, text=True, env=env, timeout=15)
            return r.returncode, r.stdout
        except Exception:
            return 1, ""

    rc, cc = kubectl("get", "cm", "kubeadm-config", "-n", "kube-system",
                     "-o", "jsonpath={.data.ClusterConfiguration}")
    if rc != 0 or not cc:
        return [], []   # cluster non raggiungibile: niente da confrontare

    errors: List[str] = []
    warnings: List[str] = []

    def cc_value(name: str) -> str:
        m = re.search(rf"^\s*{name}:\s*(\S+)", cc, re.M)
        return m.group(1).strip("\"'") if m else ""

    # --- Endpoint API (VIP keepalived o IP del primo CP) ---
    current_ep = cc_value("controlPlaneEndpoint")
    wanted_ep = f"{cluster.api_endpoint}:6443"
    if current_ep and current_ep != wanted_ep:
        errors.append(
            f"Endpoint API del cluster: {current_ep}, dal .env risulta {wanted_ep}.\n"
            "    L'endpoint (KEEPALIVED_VIP in HA, altrimenti IP del primo CP) è fissato\n"
            "    al kubeadm init: è nei certificati e nei kubeconfig di tutti i nodi.\n"
            "    Cambiarlo (o passare da 1 CP a HA) richiede reinstallare il cluster\n"
            "    (fun-kube reset + up). Ripristinare il valore originale nel .env."
        )

    # --- CIDR ---
    for key, wanted, label in (("podSubnet", cluster.pod_cidr, "POD_CIDR"),
                               ("serviceSubnet", cluster.service_cidr, "SERVICE_CIDR")):
        current = cc_value(key)
        if current and current != wanted:
            errors.append(
                f"{label} del cluster: {current}, nel .env: {wanted}.\n"
                "    I CIDR non sono modificabili su un cluster esistente:\n"
                "    ripristinare il valore originale nel .env."
            )

    # --- Nodi ---
    rc, out = kubectl("get", "nodes", "-o", "json")
    if rc == 0 and out:
        try:
            items = json.loads(out).get("items", [])
        except ValueError:
            items = []
        live = {i["metadata"]["name"]: i for i in items}
        live_workers = [n for n, i in live.items()
                        if "node-role.kubernetes.io/control-plane" not in i["metadata"].get("labels", {})]
        new_workers = [n.hostname for n in cluster.workers if n.hostname not in live]
        if live and not live_workers and new_workers:
            warnings.append(
                "Aggiunta dei primi worker a un cluster solo control-plane: i CP restano\n"
                "    senza taint e continuano a ricevere workload. Per riservarli al control-plane:\n"
                "    kubectl taint nodes -l node-role.kubernetes.io/control-plane "
                "node-role.kubernetes.io/control-plane=:NoSchedule"
            )
        removed = sorted(set(live) - {n.hostname for n in cluster.nodes})
        if removed:
            warnings.append(
                f"Nodi nel cluster ma non nel .env: {', '.join(removed)}. up non li rimuove:\n"
                "    kubectl drain <nodo> --ignore-daemonsets --delete-emptydir-data\n"
                "    kubectl delete node <nodo>   (poi fun-kube reset sul nodo, se raggiungibile)"
            )

    # --- Password DB Nginx Proxy Manager ---
    if cluster.ingress.enabled and cluster.ingress.type == "nginx-proxy-manager":
        rc, out = kubectl("get", "secret", "npm-db", "-n", "npm-system", "-o", "jsonpath={.data}")
        if rc == 0 and out:
            try:
                data = json.loads(out)
                b64 = data.get("MARIADB_PASSWORD") or data.get("MYSQL_PASSWORD")
                current_pw = base64.b64decode(b64).decode() if b64 else None
            except (ValueError, TypeError):
                current_pw = None
            if current_pw is not None and current_pw != cluster.ingress.npm_db_password:
                errors.append(
                    "NPM_DB_PASSWORD diversa da quella in uso: MariaDB mantiene la password\n"
                    "    con cui è stato inizializzato, NPM non riuscirebbe più a connettersi.\n"
                    "    Ripristinare il valore originale, oppure cambiarla prima dentro MariaDB\n"
                    "    (procedura nel file /root/<cluster>-manutenzione.txt)."
                )

    # --- Repliche Longhorn ---
    if cluster.longhorn.enabled:
        rc, out = kubectl("get", "storageclass", "longhorn",
                          "-o", "jsonpath={.parameters.numberOfReplicas}")
        if rc == 0 and out and out != str(cluster.longhorn_replicas):
            warnings.append(
                f"Longhorn: repliche StorageClass {out} → {cluster.longhorn_replicas}. Le StorageClass\n"
                "    longhorn/longhorn-rwx vengono ricreate; i volumi esistenti mantengono le\n"
                "    repliche attuali (modificabili dalla UI Longhorn)."
            )

    return errors, warnings
