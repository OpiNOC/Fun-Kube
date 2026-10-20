"""
Reset del cluster — comando `fun-kube reset`.

Fasi:
  1. arresto pulito dei workload (se il cluster risponde): cordon di tutti i nodi,
     cancellazione dei DaemonSet e dei pod dei namespace applicativi e attesa che sui nodi
     non restino mount NFS/Longhorn né client NFS del kernel. I client NFS (volumi RWX) si staccano PRIMA che spariscano gli
     share-manager: altrimenti restano processi in stato D per sempre (osservato nel lab:
     load ~3 su due worker dopo un reset, "nfs: server ... not responding").
  2. kubeadm reset + pulizia su ogni nodo (umount normale prima del lazy)
  3. controllo processi bloccati (stato D) e riavvio dei nodi se richiesto (--reboot)
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import List, Tuple

from rich.console import Console

from .config import ClusterConfig, NodeConfig

console = Console()

# Namespace di sistema/addon: i loro pod non vengono cancellati nella fase 1
# (servono a staccare i volumi in modo pulito: CSI Longhorn, share-manager, CNI).
_SYSTEM_NAMESPACES = {
    "kube-system", "kube-public", "kube-node-lease",
    "calico-system", "calico-apiserver", "tigera-operator",
    "longhorn-system", "metallb-system", "cert-manager", "local-path-storage",
}

_NODE_RESET_SCRIPT = (
    # Stop kubelet first to release CSI/iSCSI/NFS mounts (e.g. Longhorn volumes)
    # that would otherwise cause `kubeadm reset` to hang indefinitely.
    "sudo systemctl stop kubelet 2>/dev/null || true; "
    # umount normale (forzato) prima, lazy solo come ultima risorsa: il lazy nasconde
    # il mount ma lascia appesi per sempre i processi che lo stavano usando.
    "for m in $(grep -E 'kubelet|longhorn|csi' /proc/mounts 2>/dev/null | awk '{print $2}' | sort -r); do "
    "  sudo timeout 20 umount -f \"$m\" 2>/dev/null || sudo umount -f -l \"$m\" 2>/dev/null || true; "
    "done; "
    # Le sessioni iSCSI di Longhorn sopravvivono a kubeadm reset: i dischi vecchi restano
    # collegati e, siccome tutti i volumi Longhorn hanno lo stesso WWID, multipathd li
    # accoppia a quelli nuovi del cluster successivo ("device in use", mkfs fallisce).
    "sudo multipath -F 2>/dev/null || true; "
    "for t in $(sudo iscsiadm -m session 2>/dev/null | grep -o 'iqn\\.2019-10\\.io\\.longhorn:[^ ]*'); do "
    "  sudo timeout 30 iscsiadm -m node -T \"$t\" --logout || true; "
    "  sudo iscsiadm -m node -T \"$t\" -o delete 2>/dev/null || true; "
    "done; "
    "sudo systemctl stop iscsid 2>/dev/null || true; "
    "sudo kubeadm reset -f 2>/dev/null || true; "
    # /var/lib/longhorn e /dev/longhorn: repliche dei volumi del cluster distrutto, mai riusate
    "sudo rm -rf /etc/kubernetes /var/lib/etcd /var/lib/kubelet "
    "         /etc/cni/net.d /opt/cni/bin /root/.kube /var/lib/longhorn /dev/longhorn; "
    "sudo iptables -F && sudo iptables -t nat -F && "
    "sudo iptables -t mangle -F && sudo iptables -X 2>/dev/null || true; "
    "sudo ip link delete cni0 2>/dev/null || true; "
    "sudo ip link delete flannel.1 2>/dev/null || true; "
    "sudo ip link delete calico.1 2>/dev/null || true"
)

# Solo mount di kubelet (volumi dei pod): NFS dei volumi RWX e dischi Longhorn.
# Mount NFS dell'host non legati al cluster non vengono contati.
# Più i client NFS del kernel: possono sopravvivere ai mount visibili (riferimenti da altri
# mount namespace, es. il plugin CSI di Longhorn) e, se lo share-manager sparisce prima,
# il loro state manager resta in stato D per sempre (e rallenta lo spegnimento di minuti).
_COUNT_VOLUME_MOUNTS = ("m=$(grep ' /var/lib/kubelet/' /proc/mounts "
                        "| grep -cE ' nfs4? |driver.longhorn.io'); "
                        "c=$(tail -n +2 /proc/fs/nfsfs/servers 2>/dev/null | wc -l); "
                        "echo $((m + c))")
_LIST_D_STATE = "ps -eo state,pid,etime,comm --no-headers | awk '$1 ~ /D/'"


def _node_cmd(cluster: ClusterConfig, node: NodeConfig, cmd: str, timeout: int = 120,
              capture: bool = True) -> Tuple[int, str]:
    if cluster.local_node:
        args = ["bash", "-c", cmd]
    else:
        args = ["ssh", "-i", str(cluster.ssh_key_path), "-o", "StrictHostKeyChecking=no",
                "-o", "ConnectTimeout=10", f"{cluster.ssh_user}@{node.ip}", cmd]
    try:
        r = subprocess.run(args, capture_output=capture, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") if capture else ""
    except subprocess.TimeoutExpired:
        return 124, ""


def _kubectl(kubeconfig: Path, *args: str, timeout: int = 60) -> Tuple[int, str]:
    env = {**os.environ, "KUBECONFIG": str(kubeconfig)}
    try:
        r = subprocess.run(["kubectl", "--request-timeout=10s", *args],
                           capture_output=True, text=True, env=env, timeout=timeout)
        return r.returncode, r.stdout
    except subprocess.TimeoutExpired:
        return 124, ""


def stop_workloads(cluster: ClusterConfig) -> None:
    """Fase 1: arresto pulito dei workload che usano volumi. Best effort."""
    kubeconfig = next((p for p in (Path(f"/root/.kube/{cluster.cluster_name}"),
                                   Path(f"/root/.kube/{cluster.cluster_name}-admin"))
                       if os.access(p, os.R_OK)), None)
    if kubeconfig is None or _kubectl(kubeconfig, "get", "--raw", "/readyz")[0] != 0:
        console.print("  [yellow]⚠[/]  cluster non raggiungibile: arresto pulito dei workload saltato")
        return

    console.print("  [cyan]▶[/]  cordon di tutti i nodi")
    for n in cluster.nodes:
        _kubectl(kubeconfig, "cordon", n.hostname)

    rc, out = _kubectl(kubeconfig, "get", "ns", "-o", "jsonpath={.items[*].metadata.name}")
    app_ns = [ns for ns in out.split() if ns not in _SYSTEM_NAMESPACES] if rc == 0 else []
    if app_ns:
        console.print(f"  [cyan]▶[/]  arresto workload applicativi ({', '.join(app_ns)})")
        for ns in app_ns:
            # i pod dei DaemonSet ignorano il cordon e verrebbero ricreati subito, rimontando
            # i volumi (es. NPM su longhorn-rwx): si eliminano i DaemonSet stessi
            _kubectl(kubeconfig, "delete", "daemonsets", "--all", "-n", ns, "--cascade=foreground",
                     "--wait=true", "--timeout=180s", timeout=200)
            # gli altri controller ricreano i pod, ma restano Pending (nodi in cordon)
            _kubectl(kubeconfig, "delete", "pods", "--all", "-n", ns,
                     "--wait=true", "--timeout=180s", timeout=200)

    console.print("  [cyan]▶[/]  attesa smontaggio volumi (NFS/Longhorn) sui nodi")
    deadline = time.time() + 180
    pending: List[str] = []
    while time.time() < deadline:
        pending = []
        for n in cluster.nodes:
            rc, out = _node_cmd(cluster, n, _COUNT_VOLUME_MOUNTS, timeout=20)
            if rc == 0 and out.strip().isdigit() and int(out.strip()) > 0:
                pending.append(n.hostname)
        if not pending:
            console.print("  [green]✓[/]  volumi smontati")
            return
        time.sleep(10)
    console.print(f"  [yellow]⚠[/]  volumi ancora montati su: {', '.join(pending)} (proseguo)")


def reset_nodes(cluster: ClusterConfig) -> None:
    """Fase 2: kubeadm reset + pulizia su ogni nodo."""
    for node in cluster.nodes:
        console.print(f"  [cyan]▶[/]  reset {node.hostname} ({node.ip})...")
        rc, _ = _node_cmd(cluster, node, _NODE_RESET_SCRIPT, timeout=600, capture=False)
        if rc == 0:
            console.print(f"  [green]✓[/]  {node.hostname} pulito")
        else:
            console.print(f"  [yellow]⚠[/]  {node.hostname}: exit {rc} (non bloccante)")


def check_stuck_processes(cluster: ClusterConfig) -> List[str]:
    """Fase 3: nodi con processi bloccati in stato D (tipicamente I/O su NFS sparito)."""
    stuck = []
    for n in cluster.nodes:
        rc, out = _node_cmd(cluster, n, _LIST_D_STATE, timeout=20)
        procs = [l for l in out.splitlines() if l.strip()] if rc == 0 else []
        if procs:
            stuck.append(n.hostname)
            console.print(f"  [yellow]⚠[/]  {n.hostname}: {len(procs)} processi bloccati (stato D):")
            for p in procs[:5]:
                console.print(f"       {p}")
    return stuck


def reboot_nodes(cluster: ClusterConfig, nodes: List[str]) -> None:
    for n in cluster.nodes:
        if n.hostname not in nodes:
            continue
        if cluster.local_node:
            console.print(f"  [yellow]⚠[/]  {n.hostname}: modalità local-node, riavviare a mano")
            continue
        console.print(f"  [cyan]▶[/]  riavvio {n.hostname}")
        _node_cmd(cluster, n, "sudo systemctl reboot", timeout=20)
