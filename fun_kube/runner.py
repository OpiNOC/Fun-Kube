"""
Esecuzione dei playbook Ansible in sequenza.
Genera l'inventory da ClusterConfig e passa le variabili come --extra-vars.
"""
from __future__ import annotations

import datetime
import json
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path
from typing import List

from rich.console import Console

from .config import ClusterConfig

console = Console()


class RunnerError(Exception):
    pass


_ANSIBLE_DIR  = Path(__file__).parent.parent / "ansible"
_PLAYBOOK_DIR = _ANSIBLE_DIR / "playbooks"


# ---------------------------------------------------------------------------
# Entry point pubblico — Script 1: cluster core
# ---------------------------------------------------------------------------

def run_core(cluster: ClusterConfig, debug: bool = False) -> None:
    k8s_version_resolved = _resolve_k8s_version(cluster.k8s_version)
    inventory_path = _write_inventory(cluster)
    extra_vars = _build_extra_vars(cluster, k8s_version_resolved)

    playbooks = _build_playbook_sequence(cluster)

    # Syntax check su tutti i playbook prima di eseguire qualsiasi cosa
    console.print("  [cyan]▶[/]  syntax check playbooks...")
    _syntax_check_playbooks(playbooks, inventory_path, extra_vars)
    console.print("  [green]✓[/]  syntax check OK")

    for pb in playbooks:
        pb_path = _PLAYBOOK_DIR / pb
        if not pb_path.exists():
            raise RunnerError(f"Playbook non trovato: {pb_path}")
        console.print(f"  [cyan]▶[/]  {pb}")
        _run_playbook(pb_path, inventory_path, extra_vars, debug)

    _fetch_kubeconfig(cluster)
    _setup_bashrc(cluster)


def write_output(cluster: ClusterConfig) -> None:
    """Scrive output/cluster-info.txt e /root/<cluster>-manutenzione.txt."""
    cluster.output_dir.mkdir(parents=True, exist_ok=True)
    out = cluster.output_dir / "cluster-info.txt"

    cp_list = "  ".join(f"{n.hostname}({n.ip})" for n in cluster.control_planes)
    w_list = "  ".join(f"{n.hostname}({n.ip})" for n in cluster.workers) or "none"

    lines = [
        f"Fun-Kube — Cluster: {cluster.cluster_name}",
        "=" * 60,
        "",
        "# Kubeconfig primario (ServiceAccount, non scade)",
        f"export KUBECONFIG=/root/.kube/{cluster.cluster_name}",
        "# oppure: source ~/.bashrc  (configurato automaticamente)",
        "",
        "# Kubeconfig di emergenza (admin.conf, scade ~1 anno)",
        f"export KUBECONFIG=/root/.kube/{cluster.cluster_name}-admin",
        "# Usare solo se il cluster è parzialmente rotto o la SA è stata eliminata",
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
        f"Untaint CP:      {cluster.untaint_cp}",
    ]

    if cluster.topology == "single-node":
        lines += [
            "",
            "# Storage",
            "StorageClass:    local-path (default)",
            "kubectl get storageclass",
        ]

    if cluster.keepalived.enabled:
        lines += [
            "",
            "# Keepalived",
            f"VIP:             {cluster.keepalived.vip}",
            f"Interface:       {cluster.keepalived.interface}",
        ]

    lines += [
        "",
        "# Troubleshooting",
        "kubectl describe node <name>",
        "journalctl -u kubelet -f",
        "crictl ps",
        "",
        "# Aggiungere un nodo worker in seguito",
        "# (eseguire dal primo control-plane)",
        "kubeadm token create --print-join-command",
    ]

    out.write_text("\n".join(lines) + "\n")
    console.print(f"  [green]✓[/]  {out}")

    _write_maintenance_file(cluster)


def _write_maintenance_file(cluster: ClusterConfig) -> None:
    """Scrive /root/<cluster>-manutenzione.txt con info di manutenzione permanenti."""
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cert_expiry = (datetime.datetime.now() + datetime.timedelta(days=365)).strftime("%Y-%m-%d")
    cp_list = "\n".join(
        f"    {n.hostname:20s}  {n.ip}" for n in cluster.control_planes
    )
    w_list = "\n".join(
        f"    {n.hostname:20s}  {n.ip}" for n in cluster.workers
    ) or "    (nessuno — nodi CP schedulano anche workload)"

    lines = [
        f"Fun-Kube — Manutenzione Cluster: {cluster.cluster_name}",
        "=" * 60,
        f"Generato il: {now}",
        "",
        "",
        "ACCESSO AL CLUSTER",
        "-" * 40,
        "",
        "Kubeconfig PRIMARIO (token ServiceAccount, non scade mai)",
        f"  File:    /root/.kube/{cluster.cluster_name}",
        f"  Comando: export KUBECONFIG=/root/.kube/{cluster.cluster_name}",
        "  Note:    Basato su ServiceAccount fun-kube-admin (kube-system).",
        "           Valido finché il cluster è in piedi e il CA non è scaduto (10 anni).",
        "",
        f"Kubeconfig di EMERGENZA (admin.conf, scade circa: {cert_expiry})",
        f"  File:    /root/.kube/{cluster.cluster_name}-admin",
        f"  Comando: export KUBECONFIG=/root/.kube/{cluster.cluster_name}-admin",
        "  Note:    Usare SOLO se il cluster è parzialmente rotto o la SA è stata eliminata.",
        "           Dopo il rinnovo certificati (vedi sotto) va riaggiornato manualmente.",
        "",
        "",
        "CERTIFICATI — RINNOVO AUTOMATICO",
        "-" * 40,
        "",
        "I certificati del cluster vengono rinnovati automaticamente ogni mese.",
        "Systemd timer attivo su tutti i nodi control-plane: k8s-cert-renew.timer",
    ]

    if cluster.topology == "ha":
        lines += [
            "In modalità HA i nodi rinnovano in finestre diverse (RandomizedDelaySec=3600)",
            "per evitare restart simultanei del kube-apiserver.",
        ]

    if cluster.local_node:
        lines += [
            "",
            "Verifica stato certificati (locale):",
            "  sudo kubeadm certs check-expiration",
            "",
            "Verifica timer attivo (locale):",
            "  systemctl status k8s-cert-renew.timer",
            "",
            "Rinnovo MANUALE (emergenza — se il timer non ha funzionato):",
            "  sudo kubeadm certs renew all",
            "  sudo mkdir -p /tmp/k8s-backup",
            "  sudo mv /etc/kubernetes/manifests/kube-apiserver.yaml \\",
            "          /etc/kubernetes/manifests/kube-controller-manager.yaml \\",
            "          /etc/kubernetes/manifests/kube-scheduler.yaml /tmp/k8s-backup/",
            "  sleep 20",
            "  sudo mv /tmp/k8s-backup/*.yaml /etc/kubernetes/manifests/",
            f"  sudo cp /etc/kubernetes/admin.conf /root/.kube/{cluster.cluster_name}-admin",
            "  NOTA: il kubeconfig primario (SA token) non richiede aggiornamenti.",
        ]
    else:
        lines += [
            "",
            "Verifica stato certificati (da qualsiasi CP):",
        ]
        for n in cluster.control_planes:
            lines.append(f"  ssh {cluster.ssh_user}@{n.ip} 'sudo kubeadm certs check-expiration'")

        lines += [
            "",
            "Verifica timer attivo (da qualsiasi CP):",
        ]
        for n in cluster.control_planes:
            lines.append(f"  ssh {cluster.ssh_user}@{n.ip} 'systemctl status k8s-cert-renew.timer'")

        lines += [
            "",
            "Rinnovo MANUALE (emergenza — se il timer non ha funzionato):",
            "  1. Accedere al nodo CP:",
            f"       ssh {cluster.ssh_user}@{cluster.first_cp.ip}",
            "  2. Rinnovare certificati:",
            "       sudo kubeadm certs renew all",
            "  3. Riavviare componenti control-plane:",
            "       sudo mkdir -p /tmp/k8s-backup",
            "       sudo mv /etc/kubernetes/manifests/kube-apiserver.yaml \\",
            "               /etc/kubernetes/manifests/kube-controller-manager.yaml \\",
            "               /etc/kubernetes/manifests/kube-scheduler.yaml /tmp/k8s-backup/",
            "       sleep 20",
            "       sudo mv /tmp/k8s-backup/*.yaml /etc/kubernetes/manifests/",
            "  4. Aggiornare il kubeconfig di emergenza sulla bootstrap:",
            f"       scp -i {cluster.ssh_key_path} {cluster.ssh_user}@{cluster.first_cp.ip}:",
            f"           /etc/kubernetes/admin.conf /root/.kube/{cluster.cluster_name}-admin",
            "  NOTA: il kubeconfig primario (SA token) non richiede aggiornamenti.",
        ]

        if cluster.topology == "ha":
            lines += [
                "",
                "  In HA: ripetere i passi 2-3 su ogni CP uno alla volta,",
                "  verificando che il cluster sia healthy prima di procedere al successivo.",
            ]

    lines += [
        "",
        "",
        "INFRASTRUTTURA",
        "-" * 40,
        "",
        f"Topologia:     {cluster.topology}",
        f"API endpoint:  https://{cluster.api_endpoint}:6443",
        f"K8s version:   {cluster.k8s_version}",
        f"Pod CIDR:      {cluster.pod_cidr}",
        f"Service CIDR:  {cluster.service_cidr}",
        "",
        "Control-plane:",
        cp_list,
        "",
        "Workers:",
        w_list,
    ]

    if cluster.keepalived.enabled:
        lines += [
            "",
            f"Keepalived VIP:       {cluster.keepalived.vip}",
            f"Keepalived interface: {cluster.keepalived.interface}",
        ]

    dest = Path(f"/root/{cluster.cluster_name}-manutenzione.txt")
    dest.write_text("\n".join(lines) + "\n")
    console.print(f"  [green]✓[/]  file manutenzione → {dest}")


# ---------------------------------------------------------------------------
# Sequenza playbook — dinamica per topologia
# ---------------------------------------------------------------------------

def _build_playbook_sequence(cluster: ClusterConfig) -> List[str]:
    playbooks = ["bootstrap.yml"]

    if cluster.topology == "ha":
        playbooks.append("keepalived.yml")

    playbooks.append("kubeadm-init.yml")

    if cluster.topology == "ha":
        playbooks.append("control-plane-join.yml")

    if cluster.workers:
        playbooks.append("worker-join.yml")

    playbooks.append("calico.yml")

    if cluster.untaint_cp:
        playbooks.append("untaint-cp.yml")

    playbooks.append("metrics-server.yml")
    playbooks.append("cert-manager.yml")
    playbooks.append("cert-renewal.yml")

    # Su mononodo (local-node incluso) installa local-path-provisioner
    # come StorageClass di default — leggero, zero configurazione.
    if cluster.topology == "single-node":
        playbooks.append("local-path-provisioner.yml")

    playbooks.append("bootstrap-kubeconfig.yml")

    return playbooks


# ---------------------------------------------------------------------------
# Versione k8s — risolta una volta sola in Python
# ---------------------------------------------------------------------------

def _resolve_k8s_version(k8s_version: str) -> str:
    if k8s_version != "latest":
        v = k8s_version if k8s_version.startswith("v") else f"v{k8s_version}"
        console.print(f"  [green]✓[/]  k8s version: {v}")
        return v
    console.print("  [cyan]▶[/]  resolving latest k8s version...")
    with urllib.request.urlopen(
        "https://dl.k8s.io/release/stable.txt", timeout=10
    ) as resp:
        version = resp.read().decode().strip()
    console.print(f"  [green]✓[/]  k8s version: {version}")
    return version


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def _write_inventory(cluster: ClusterConfig) -> Path:
    lines = []

    lines.append("[control_plane]")
    for n in cluster.control_planes:
        if cluster.local_node:
            lines.append(f"{n.hostname} ansible_connection=local")
        else:
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

    lines += ["", "[all:vars]"]
    if not cluster.local_node:
        lines.append("ansible_ssh_common_args='-o StrictHostKeyChecking=no'")

    inv_path = (cluster.output_dir / "inventory.ini").resolve()
    inv_path.parent.mkdir(parents=True, exist_ok=True)
    inv_path.write_text("\n".join(lines) + "\n")
    return inv_path


# ---------------------------------------------------------------------------
# Extra vars per Ansible
# ---------------------------------------------------------------------------

def _build_extra_vars(cluster: ClusterConfig, k8s_version_resolved: str) -> dict:
    return {
        "cluster_name": cluster.cluster_name,
        "topology": cluster.topology,
        "k8s_version": cluster.k8s_version,
        "k8s_version_resolved": k8s_version_resolved,
        "pod_cidr": cluster.pod_cidr,
        "service_cidr": cluster.service_cidr,
        "cni": cluster.cni,
        "api_endpoint": cluster.api_endpoint,
        "first_cp_ip": cluster.first_cp.ip,
        "first_cp_hostname": cluster.first_cp.hostname,
        "untaint_cp": cluster.untaint_cp,
        "cert_manager_version": cluster.cert_manager_version,
        "local_node": cluster.local_node,
        "all_cp_ips": [n.ip for n in cluster.control_planes],
        "api_server_extra_sans": cluster.api_server_extra_sans,
        # Keepalived
        "keepalived_enabled": cluster.keepalived.enabled,
        "keepalived_vip": cluster.keepalived.vip,
        "keepalived_interface": cluster.keepalived.interface,
        # Timezone
        "cluster_timezone": cluster.cluster_timezone,
    }


# ---------------------------------------------------------------------------
# Ansible runner
# ---------------------------------------------------------------------------

def _syntax_check_playbooks(playbooks: List[str], inventory: Path, extra_vars: dict) -> None:
    """Verifica la sintassi di tutti i playbook prima di eseguirli."""
    errors = []
    for pb in playbooks:
        pb_path = _PLAYBOOK_DIR / pb
        if not pb_path.exists():
            errors.append(f"Playbook non trovato: {pb_path}")
            continue
        cmd = [
            "ansible-playbook",
            "--syntax-check",
            str(pb_path),
            "-i", str(inventory),
            "--extra-vars", json.dumps(extra_vars),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(_ANSIBLE_DIR))
        if result.returncode != 0:
            errors.append(f"{pb}:\n{result.stdout}\n{result.stderr}")
    if errors:
        raise RunnerError("Syntax check fallito:\n" + "\n".join(errors))


def _run_playbook(pb: Path, inventory: Path, extra_vars: dict, debug: bool) -> None:
    cmd = [
        "ansible-playbook",
        str(pb),
        "-i", str(inventory),
        "--extra-vars", json.dumps(extra_vars),
    ]
    if debug:
        cmd.append("-vv")

    # Esegue dalla directory ansible/ così ansible.cfg viene rilevato automaticamente
    proc = subprocess.Popen(cmd, text=True, cwd=str(_ANSIBLE_DIR))
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        raise
    if proc.returncode != 0:
        raise RunnerError(
            f"Il playbook {pb.name} è terminato con codice {proc.returncode}"
        )


# ---------------------------------------------------------------------------
# Kubeconfig
# ---------------------------------------------------------------------------

def _fetch_kubeconfig(cluster: ClusterConfig) -> None:
    """Recupera admin.conf come kubeconfig di emergenza (<cluster>-admin)."""
    console.print("  [cyan]▶[/]  fetching admin kubeconfig (emergency backup)...")

    kube_dir = Path("/root/.kube")
    kube_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = kube_dir / f"{cluster.cluster_name}-admin"

    if cluster.local_node:
        src = Path("/etc/kubernetes/admin.conf")
        if not src.exists():
            console.print("  [yellow]⚠[/]  admin.conf non trovato in /etc/kubernetes/")
            return
        shutil.copy2(src, dest)
    else:
        cmd = [
            "scp",
            "-i", str(cluster.ssh_key_path),
            "-o", "StrictHostKeyChecking=no",
            f"{cluster.ssh_user}@{cluster.first_cp.ip}:/etc/kubernetes/admin.conf",
            str(dest),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            console.print(f"  [yellow]⚠[/]  admin kubeconfig non recuperato: {result.stderr.strip()}")
            return

    dest.chmod(0o600)
    console.print(f"  [green]✓[/]  admin kubeconfig (backup) → {dest}")

    cluster.output_dir.mkdir(parents=True, exist_ok=True)
    (cluster.output_dir / "kubeconfig-admin").write_bytes(dest.read_bytes())


def _setup_bashrc(cluster: ClusterConfig) -> None:
    """Aggiunge/aggiorna il blocco Fun-Kube in ~/.bashrc (idempotente)."""
    kubeconfig_path = Path("/root/.kube") / cluster.cluster_name
    bashrc = Path("/root/.bashrc")

    marker_start = f"# >>> fun-kube:{cluster.cluster_name} >>>"
    marker_end   = f"# <<< fun-kube:{cluster.cluster_name} <<<"

    block = "\n".join([
        marker_start,
        f"export KUBECONFIG={kubeconfig_path}",
        "alias k=kubectl",
        "source <(kubectl completion bash)",
        "complete -F __start_kubectl k",
        marker_end,
    ]) + "\n"

    current = bashrc.read_text() if bashrc.exists() else ""

    # Rimuove blocco precedente (se presente) e riscrive
    pattern = re.compile(
        rf"{re.escape(marker_start)}.*?{re.escape(marker_end)}\n?",
        re.DOTALL,
    )
    cleaned = pattern.sub("", current).rstrip("\n")
    bashrc.write_text(cleaned + "\n\n" + block)
    console.print(f"  [green]✓[/]  ~/.bashrc aggiornato (cluster: {cluster.cluster_name})")
