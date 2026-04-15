"""
Esecuzione dei playbook Ansible in sequenza.
Genera l'inventory da ClusterConfig e passa le variabili come --extra-vars.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import List

from rich.console import Console

from .config import ClusterConfig, Topology

console = Console()


class RunnerError(Exception):
    pass


# Sequenza playbook per topologia (base cluster, senza addon)
_PLAYBOOK_SEQUENCE: dict[Topology, List[str]] = {
    "single-node": [
        "bootstrap.yml",
        "kubeadm-init.yml",
        "calico.yml",
        "untaint-single-node.yml",
    ],
    "single-cp": [
        "bootstrap.yml",
        "kubeadm-init.yml",
        "worker-join.yml",
        "calico.yml",
    ],
    "ha": [
        "bootstrap.yml",
        "keepalived.yml",
        "kubeadm-init.yml",
        "control-plane-join.yml",
        "worker-join.yml",
        "calico.yml",
    ],
}

_PLAYBOOK_DIR = Path(__file__).parent.parent / "ansible" / "playbooks"


# ---------------------------------------------------------------------------
# Entry point pubblico
# ---------------------------------------------------------------------------

def run(cluster: ClusterConfig, debug: bool = False) -> None:
    inventory_path = _write_inventory(cluster)
    extra_vars = _build_extra_vars(cluster)

    playbooks = list(_PLAYBOOK_SEQUENCE[cluster.topology])

    if cluster.metallb.enabled:
        playbooks.append("metallb.yml")
    if cluster.ingress.enabled:
        playbooks.append("ingress.yml")
    if cluster.longhorn.enabled:
        playbooks.append("longhorn.yml")

    for pb in playbooks:
        pb_path = _PLAYBOOK_DIR / pb
        if not pb_path.exists():
            raise RunnerError(f"Playbook non trovato: {pb_path}")
        console.print(f"  [cyan]▶[/]  {pb}")
        _run_playbook(pb_path, inventory_path, extra_vars, debug)

    _fetch_kubeconfig(cluster)


def write_output(cluster: ClusterConfig) -> None:
    """Scrive output/cluster-info.txt con riepilogo del cluster."""
    cluster.output_dir.mkdir(parents=True, exist_ok=True)
    out = cluster.output_dir / "cluster-info.txt"

    cp_list = "  ".join(f"{n.hostname}({n.ip})" for n in cluster.control_planes)
    w_list = "  ".join(f"{n.hostname}({n.ip})" for n in cluster.workers) or "none"

    lines = [
        f"Fun-Kube — Cluster: {cluster.cluster_name}",
        "=" * 60,
        "",
        "# Kubeconfig",
        f"export KUBECONFIG=$(pwd)/output/kubeconfig",
        "",
        "# Stato cluster",
        "kubectl get nodes",
        "kubectl get pods -A",
        "",
        "# Topologia",
        f"Topology:        {cluster.topology}",
        f"Control-planes:  {cp_list}",
        f"Workers:         {w_list}",
        f"API endpoint:    {cluster.api_endpoint}",
        f"K8s version:     {cluster.k8s_version}",
    ]

    if cluster.keepalived.enabled:
        lines += [
            "",
            "# Keepalived",
            f"VIP:             {cluster.keepalived.vip}",
            f"Interface:       {cluster.keepalived.interface}",
        ]

    if cluster.metallb.enabled:
        lines += [
            "",
            "# MetalLB",
            f"IP pool:         {cluster.metallb.ip_pool}",
        ]

    if cluster.ingress.enabled:
        lines += [
            "",
            "# Ingress",
            f"Type:            {cluster.ingress.type}",
            "URL:             http://<metallb-ip>  (vedi: kubectl get svc -A)",
        ]

    if cluster.longhorn.enabled:
        lines += [
            "",
            "# Longhorn",
            "UI:              http://<metallb-ip>  (vedi: kubectl get svc -n longhorn-system)",
            f"RWX:             {cluster.longhorn.rwx}",
        ]

    lines += [
        "",
        "# Join commands (eseguire dal primo control-plane)",
        "kubeadm token create --print-join-command",
        "",
        "# Troubleshooting",
        "kubectl describe node <name>",
        "journalctl -u kubelet -f",
        "crictl ps",
    ]

    out.write_text("\n".join(lines) + "\n")
    console.print(f"  [green]✓[/]  {out}")


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def _write_inventory(cluster: ClusterConfig) -> Path:
    lines = []

    lines.append("[control_plane]")
    for n in cluster.control_planes:
        lines.append(
            f"{n.hostname} ansible_host={n.ip} "
            f"ansible_user={cluster.ssh_user} "
            f"ansible_ssh_private_key_file={cluster.ssh_key_path}"
        )

    if cluster.workers:
        lines += ["", "[workers]"]
        for n in cluster.workers:
            lines.append(
                f"{n.hostname} ansible_host={n.ip} "
                f"ansible_user={cluster.ssh_user} "
                f"ansible_ssh_private_key_file={cluster.ssh_key_path}"
            )

    lines += [
        "",
        "[all:vars]",
        "ansible_ssh_common_args='-o StrictHostKeyChecking=no'",
    ]

    inv_path = cluster.output_dir / "inventory.ini"
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text("\n".join(lines) + "\n")
    return inv_path


# ---------------------------------------------------------------------------
# Extra vars per Ansible
# ---------------------------------------------------------------------------

def _build_extra_vars(cluster: ClusterConfig) -> dict:
    return {
        "cluster_name": cluster.cluster_name,
        "topology": cluster.topology,
        "k8s_version": cluster.k8s_version,
        "pod_cidr": cluster.pod_cidr,
        "service_cidr": cluster.service_cidr,
        "cni": cluster.cni,
        "api_endpoint": cluster.api_endpoint,
        "first_cp_ip": cluster.first_cp.ip,
        "first_cp_hostname": cluster.first_cp.hostname,
        # Keepalived
        "keepalived_enabled": cluster.keepalived.enabled,
        "keepalived_vip": cluster.keepalived.vip,
        "keepalived_interface": cluster.keepalived.interface,
        # MetalLB
        "metallb_enabled": cluster.metallb.enabled,
        "metallb_ip_pool": cluster.metallb.ip_pool,
        # Ingress
        "ingress_enabled": cluster.ingress.enabled,
        "ingress_type": cluster.ingress.type,
        # Longhorn
        "longhorn_enabled": cluster.longhorn.enabled,
        "longhorn_rwx": cluster.longhorn.rwx,
    }


# ---------------------------------------------------------------------------
# Ansible runner
# ---------------------------------------------------------------------------

def _run_playbook(pb: Path, inventory: Path, extra_vars: dict, debug: bool) -> None:
    cmd = [
        "ansible-playbook",
        str(pb),
        "-i", str(inventory),
        "--extra-vars", json.dumps(extra_vars),
    ]
    if debug:
        cmd.append("-vv")

    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        raise RunnerError(
            f"Il playbook {pb.name} è terminato con codice {result.returncode}"
        )


# ---------------------------------------------------------------------------
# Kubeconfig
# ---------------------------------------------------------------------------

def _fetch_kubeconfig(cluster: ClusterConfig) -> None:
    console.print("  [cyan]▶[/]  fetching kubeconfig...")
    dest = cluster.output_dir / "kubeconfig"

    cmd = [
        "scp",
        "-i", str(cluster.ssh_key_path),
        "-o", "StrictHostKeyChecking=no",
        f"{cluster.ssh_user}@{cluster.first_cp.ip}:/etc/kubernetes/admin.conf",
        str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        console.print(f"  [yellow]⚠[/]  kubeconfig non recuperato: {result.stderr.strip()}")
    else:
        console.print(f"  [green]✓[/]  kubeconfig → {dest}")
