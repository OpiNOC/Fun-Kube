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
    longhorn_version_resolved = _resolve_longhorn_version(cluster.longhorn.version) if cluster.longhorn.enabled else ""
    metallb_version_resolved = _resolve_metallb_version(cluster.metallb.version) if cluster.metallb.enabled else ""
    inventory_path = _write_inventory(cluster)
    extra_vars = _build_extra_vars(cluster, k8s_version_resolved, longhorn_version_resolved, metallb_version_resolved)

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

    if cluster.metallb.enabled:
        lines += [
            "",
            "# MetalLB",
            f"IP pool:         {cluster.metallb.ip_pool}",
            "kubectl get ipaddresspool -n metallb-system",
            "kubectl get svc -A | grep LoadBalancer",
        ]

    if cluster.longhorn.enabled:
        lh_ui = (
            f"http://{cluster.api_endpoint}:{cluster.longhorn.ui_nodeport}"
            if cluster.longhorn.ui_nodeport else "NodePort disabilitato"
        )
        lines += [
            "",
            "# Longhorn",
            f"Namespace:       longhorn-system",
            f"UI:              {lh_ui}",
            f"RWX:             {'abilitato (StorageClass: longhorn-rwx)' if cluster.longhorn.rwx else 'disabilitato'}",
            "kubectl get storageclass",
            "kubectl get pods -n longhorn-system",
        ]

    if cluster.ingress.enabled:
        lines += _ingress_output_lines(cluster)

    if cluster.dn_essence.enabled:
        lines += _dn_essence_output_lines(cluster)

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

    if cluster.ingress.enabled:
        _write_ingress_extra_files(cluster)

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

    if cluster.metallb.enabled:
        lines += [
            "",
            "ADDON — MetalLB",
            "-" * 40,
            "",
            f"IP pool:  {cluster.metallb.ip_pool}",
            f"Versione: {cluster.metallb.version or 'risolto da GitHub API'}",
            "",
            "Verifica:",
            "  kubectl get ipaddresspool -n metallb-system",
            "  kubectl get l2advertisement -n metallb-system",
            "  kubectl get svc -A | grep LoadBalancer",
        ]

    if cluster.longhorn.enabled:
        lh_ui = (
            f"http://{cluster.api_endpoint}:{cluster.longhorn.ui_nodeport}"
            if cluster.longhorn.ui_nodeport else "NodePort disabilitato"
        )
        lines += [
            "",
            "ADDON — Longhorn",
            "-" * 40,
            "",
            f"Namespace:  longhorn-system",
            f"Versione:   {cluster.longhorn.version or 'risolto da GitHub API'}",
            f"UI:         {lh_ui}",
            f"RWX:        {'abilitato (StorageClass: longhorn-rwx)' if cluster.longhorn.rwx else 'disabilitato'}",
            "",
            "Verifica:",
            "  kubectl get pods -n longhorn-system",
            "  kubectl get storageclass",
        ]

    if cluster.ingress.enabled:
        lines += ["", ""] + _ingress_maintenance_lines(cluster)

    if cluster.dn_essence.enabled:
        lines += ["", ""] + _dn_essence_maintenance_lines(cluster)

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

    if cluster.metallb.enabled:
        playbooks.append("metallb.yml")

    if cluster.longhorn.enabled:
        playbooks.append("longhorn.yml")

    if cluster.ingress.enabled:
        playbooks.append("ingress.yml")

    if cluster.dn_essence.enabled:
        playbooks.append("dn-essence.yml")

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


def _resolve_metallb_version(metallb_version: str) -> str:
    if metallb_version:
        v = metallb_version if metallb_version.startswith("v") else f"v{metallb_version}"
        console.print(f"  [green]✓[/]  metallb version: {v}")
        return v
    console.print("  [cyan]▶[/]  resolving latest metallb version...")
    import json as _json
    req = urllib.request.Request(
        "https://api.github.com/repos/metallb/metallb/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = _json.loads(resp.read().decode())
    version = data["tag_name"]
    console.print(f"  [green]✓[/]  metallb version: {version}")
    return version


def _resolve_longhorn_version(longhorn_version: str) -> str:
    if longhorn_version:
        v = longhorn_version if longhorn_version.startswith("v") else f"v{longhorn_version}"
        console.print(f"  [green]✓[/]  longhorn version: {v}")
        return v
    console.print("  [cyan]▶[/]  resolving latest longhorn version...")
    import json as _json
    req = urllib.request.Request(
        "https://api.github.com/repos/longhorn/longhorn/releases/latest",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = _json.loads(resp.read().decode())
    version = data["tag_name"]
    console.print(f"  [green]✓[/]  longhorn version: {version}")
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

def _build_extra_vars(cluster: ClusterConfig, k8s_version_resolved: str, longhorn_version_resolved: str = "", metallb_version_resolved: str = "") -> dict:
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
        "local_path_provisioner_version": cluster.local_path_version or "v0.0.30",
        "local_node": cluster.local_node,
        "all_cp_ips": [n.ip for n in cluster.control_planes],
        "api_server_extra_sans": cluster.api_server_extra_sans,
        # Keepalived
        "keepalived_enabled": cluster.keepalived.enabled,
        "keepalived_vip": cluster.keepalived.vip,
        "keepalived_interface": cluster.keepalived.interface,
        # Timezone
        "cluster_timezone": cluster.cluster_timezone,
        # MetalLB
        "metallb_enabled": cluster.metallb.enabled,
        "metallb_ip_pool": cluster.metallb.ip_pool,
        "metallb_version": metallb_version_resolved,
        # Longhorn
        "longhorn_enabled": cluster.longhorn.enabled,
        "longhorn_replicas": cluster.longhorn_replicas,
        "longhorn_rwx": cluster.longhorn.rwx,
        "longhorn_ui_nodeport": cluster.longhorn.ui_nodeport,
        "longhorn_namespace": "longhorn-system",
        "longhorn_version": longhorn_version_resolved,
        # Ingress
        "ingress_enabled": cluster.ingress.enabled,
        "ingress_type": cluster.ingress.type,
        "ingress_service_type": cluster.effective_ingress_service_type,
        "traefik_namespace": "traefik",
        "traefik_chart_version": cluster.ingress.traefik_chart_version,
        "traefik_lb_ip": cluster.ingress.traefik_lb_ip,
        "traefik_http_nodeport": cluster.ingress.traefik_http_nodeport,
        "traefik_https_nodeport": cluster.ingress.traefik_https_nodeport,
        "traefik_is_default_class": cluster.ingress.traefik_is_default_class,
        "traefik_dashboard_host": cluster.ingress.traefik_dashboard_host,
        "traefik_acme_email": cluster.ingress.traefik_acme_email,
        "npm_namespace": "npm-system",
        "npm_lb_ip": cluster.ingress.npm_lb_ip,
        "npm_http_nodeport": cluster.ingress.npm_http_nodeport,
        "npm_https_nodeport": cluster.ingress.npm_https_nodeport,
        "npm_admin_nodeport": cluster.ingress.npm_admin_nodeport,
        "npm_db_password": cluster.ingress.npm_db_password,
        # DN-essence
        "dn_essence_enabled": cluster.dn_essence.enabled,
        "dn_essence_ui_nodeport": cluster.dn_essence.ui_nodeport,
        "dn_essence_version": cluster.dn_essence.version,
        "dn_essence_namespace": "dn-essence",
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
# Ingress output helpers
# ---------------------------------------------------------------------------

def _ingress_urls(cluster: "ClusterConfig"):
    """Ritorna (http_url, https_url, admin_url) per l'ingress configurato."""
    svc = cluster.effective_ingress_service_type
    ep = cluster.api_endpoint
    ing = cluster.ingress
    if ing.type == "traefik":
        if svc == "loadbalancer":
            ip = ing.traefik_lb_ip or "<MetalLB auto-assign — esegui: kubectl get svc -n traefik traefik>"
            return f"http://{ip}", f"https://{ip}", None
        else:
            return (
                f"http://{ep}:{ing.traefik_http_nodeport}",
                f"https://{ep}:{ing.traefik_https_nodeport}",
                None,
            )
    else:  # nginx-proxy-manager
        if svc == "loadbalancer":
            ip = ing.npm_lb_ip or "<MetalLB auto-assign — esegui: kubectl get svc -n npm-system nginx-proxy-manager>"
            return f"http://{ip}", f"https://{ip}", f"http://{ip}:81"
        else:
            return (
                f"http://{ep}:{ing.npm_http_nodeport}",
                f"https://{ep}:{ing.npm_https_nodeport}",
                f"http://{ep}:{ing.npm_admin_nodeport}",
            )


def _ingress_output_lines(cluster: "ClusterConfig") -> list:
    svc = cluster.effective_ingress_service_type
    http_url, https_url, admin_url = _ingress_urls(cluster)
    ing = cluster.ingress

    if ing.type == "traefik":
        if ing.traefik_dashboard_host:
            dash = f"http://{ing.traefik_dashboard_host}/dashboard/"
        else:
            dash = "kubectl port-forward -n traefik svc/traefik 9000:9000  →  http://localhost:9000/dashboard/"
        lines = [
            "",
            "# Ingress — Traefik",
            f"Namespace:       traefik",
            f"Service type:    {svc}",
            f"HTTP:            {http_url}",
            f"HTTPS:           {https_url}",
            f"Dashboard:       {dash}",
            f"IngressClass:    traefik (default: {'sì' if ing.traefik_is_default_class else 'no'})",
            "kubectl -n traefik get pods",
            "kubectl -n traefik get svc",
            "kubectl -n traefik logs -l app.kubernetes.io/name=traefik",
        ]
        if ing.traefik_acme_email:
            lines += [
                f"Let's Encrypt:   letsencrypt-staging + letsencrypt-prod",
                f"Email ACME:      {ing.traefik_acme_email}",
                "kubectl get clusterissuer",
            ]
        else:
            lines += [
                "Let's Encrypt:   non configurato",
                "Guida setup LE:  output/traefik-letsencrypt-guide.txt",
            ]
    else:  # nginx-proxy-manager
        lines = [
            "",
            "# Ingress — Nginx Proxy Manager",
            f"Namespace:       npm-system",
            f"Service type:    {svc}",
            f"HTTP:            {http_url}",
            f"HTTPS:           {https_url}",
            f"Admin UI:        {admin_url}",
            "Default login:   admin@example.com  /  changeme",
            "IMPORTANTE: cambiare la password al primo accesso!",
            "kubectl -n npm-system get pods",
            "kubectl -n npm-system get svc",
        ]
        if ing.npm_db_password == "T1sh-PwD-Sh0ulD-B3-Ch4nGeD-NOW":
            lines.append("ATTENZIONE: NPM_DB_PASSWORD è ancora il valore di default — cambiarlo in .env!")
    return lines


def _ingress_maintenance_lines(cluster: "ClusterConfig") -> list:
    svc = cluster.effective_ingress_service_type
    http_url, https_url, admin_url = _ingress_urls(cluster)
    ing = cluster.ingress
    ep = cluster.api_endpoint

    if ing.type == "traefik":
        if ing.traefik_dashboard_host:
            dash = f"http://{ing.traefik_dashboard_host}/dashboard/"
        else:
            dash = "kubectl port-forward -n traefik svc/traefik 9000:9000  (poi http://localhost:9000/dashboard/)"
        lines = [
            "ADDON — Ingress (Traefik)",
            "-" * 40,
            "",
            f"Namespace:    traefik",
            f"Service type: {svc}",
            f"HTTP:         {http_url}",
            f"HTTPS:        {https_url}",
            f"Dashboard:    {dash}",
            f"IngressClass: traefik (isDefaultClass: {ing.traefik_is_default_class})",
            "",
            "Verifica:",
            "  kubectl -n traefik get pods -o wide",
            "  kubectl -n traefik get svc",
            "  kubectl -n traefik logs -l app.kubernetes.io/name=traefik --tail=50",
            "  kubectl get ingressclass",
        ]
        if ing.traefik_acme_email:
            lines += [
                "",
                "Let's Encrypt:",
                f"  Email:        {ing.traefik_acme_email}",
                "  ClusterIssuer: letsencrypt-staging, letsencrypt-prod",
                "  kubectl get clusterissuer",
                "  kubectl get certificate -A",
            ]
        else:
            lines += [
                "",
                "Let's Encrypt: NON configurato automaticamente.",
                "  Guida post-install: output/traefik-letsencrypt-guide.txt",
            ]
        lines += [
            "",
            "Esempio Ingress TLS (richiede ClusterIssuer letsencrypt-prod):",
            "  apiVersion: networking.k8s.io/v1",
            "  kind: Ingress",
            "  metadata:",
            "    annotations:",
            "      cert-manager.io/cluster-issuer: letsencrypt-prod",
            "  spec:",
            "    ingressClassName: traefik",
            "    tls:",
            "    - hosts: [mioapp.miodominio.com]",
            "      secretName: mioapp-tls",
            "    rules:",
            "    - host: mioapp.miodominio.com",
        ]
    else:  # nginx-proxy-manager
        storage = "local-path (RWO)" if cluster.topology == "single-node" else "longhorn / longhorn-rwx"
        lines = [
            "ADDON — Ingress (Nginx Proxy Manager)",
            "-" * 40,
            "",
            f"Namespace:    npm-system",
            f"Service type: {svc}",
            f"HTTP:         {http_url}",
            f"HTTPS:        {https_url}",
            f"Admin UI:     {admin_url}",
            f"Storage:      {storage}",
            "",
            "Accesso admin UI:",
            "  URL:      " + admin_url,
            "  Login:    admin@example.com  /  changeme  (cambiare subito!)",
            "",
            "Verifica:",
            "  kubectl -n npm-system get pods -o wide",
            "  kubectl -n npm-system get svc",
            "  kubectl -n npm-system get pvc",
            "  kubectl -n npm-system logs -l app=nginx-proxy-manager --tail=50",
        ]
        if ing.npm_db_password == "T1sh-PwD-Sh0ulD-B3-Ch4nGeD-NOW":
            lines += [
                "",
                "ATTENZIONE: NPM_DB_PASSWORD è il valore di default.",
                "  Aggiornare NPM_DB_PASSWORD in .env e rieseguire fun-kube up.",
            ]
    return lines


def _dn_essence_output_lines(cluster: "ClusterConfig") -> list:
    ep = cluster.api_endpoint
    np = cluster.dn_essence.ui_nodeport
    ui = f"http://{ep}:{np}" if np else "kubectl port-forward svc/dn-essence 8080:80 -n dn-essence"
    return [
        "",
        "# DN-essence (DNS rewrite manager)",
        f"Namespace:       dn-essence",
        f"UI:              {ui}",
        "kubectl get dnsrewrite -A",
        "kubectl -n dn-essence get pods",
    ]


def _dn_essence_maintenance_lines(cluster: "ClusterConfig") -> list:
    ep = cluster.api_endpoint
    np = cluster.dn_essence.ui_nodeport
    ui = f"http://{ep}:{np}" if np else "kubectl port-forward svc/dn-essence 8080:80 -n dn-essence  (poi http://localhost:8080)"
    return [
        "ADDON — DN-essence (DNS rewrite manager per CoreDNS)",
        "-" * 40,
        "",
        f"Namespace:  dn-essence",
        f"UI:         {ui}",
        "",
        "Gestisce regole DNS rewrite in CoreDNS via Custom Resources.",
        "Utile per hairpin NAT (domini pubblici → IP interni).",
        "",
        "Verifica:",
        "  kubectl -n dn-essence get pods",
        "  kubectl -n dn-essence get svc",
        "  kubectl get dnsrewrite -A",
        "  kubectl describe configmap coredns -n kube-system",
    ]


def _write_ingress_extra_files(cluster: "ClusterConfig") -> None:
    """Scrive file aggiuntivi in output/ per l'ingress (guide LE, template)."""
    if cluster.ingress.type != "traefik" or cluster.ingress.traefik_acme_email:
        return

    ep = cluster.api_endpoint
    ing = cluster.ingress
    svc = cluster.effective_ingress_service_type
    if svc == "loadbalancer":
        lb_ref = ing.traefik_lb_ip or "<TRAEFIK-LB-IP>"
    else:
        lb_ref = f"{ep}:{ing.traefik_http_nodeport}"

    guide = cluster.output_dir / "traefik-letsencrypt-guide.txt"
    lines = [
        "Traefik — Guida configurazione Let's Encrypt",
        "=" * 60,
        "",
        "Questa guida spiega come configurare TLS automatico con cert-manager",
        "e Let's Encrypt dopo aver installato Traefik senza TRAEFIK_ACME_EMAIL.",
        "",
        "PREREQUISITI",
        "-" * 40,
        "  - DNS pubblico che risolve verso l'IP di Traefik",
        f"  - Porta 80 aperta verso {lb_ref} (necessaria per HTTP-01 challenge)",
        "  - cert-manager installato (già incluso da fun-kube)",
        "",
        "STEP 1 — Crea i ClusterIssuer",
        "-" * 40,
        "",
        "Salva il seguente YAML in clusterissuer.yaml e applica con:",
        "  kubectl apply -f clusterissuer.yaml",
        "",
        "---",
        "apiVersion: cert-manager.io/v1",
        "kind: ClusterIssuer",
        "metadata:",
        "  name: letsencrypt-staging",
        "spec:",
        "  acme:",
        "    email: TUA-EMAIL@DOMINIO.TLD",
        "    server: https://acme-staging-v02.api.letsencrypt.org/directory",
        "    privateKeySecretRef:",
        "      name: letsencrypt-staging-account-key",
        "    solvers:",
        "    - http01:",
        "        ingress:",
        "          ingressClassName: traefik",
        "---",
        "apiVersion: cert-manager.io/v1",
        "kind: ClusterIssuer",
        "metadata:",
        "  name: letsencrypt-prod",
        "spec:",
        "  acme:",
        "    email: TUA-EMAIL@DOMINIO.TLD",
        "    server: https://acme-v02.api.letsencrypt.org/directory",
        "    privateKeySecretRef:",
        "      name: letsencrypt-prod-account-key",
        "    solvers:",
        "    - http01:",
        "        ingress:",
        "          ingressClassName: traefik",
        "",
        "STEP 2 — Verifica ClusterIssuer",
        "-" * 40,
        "  kubectl get clusterissuer",
        "  kubectl describe clusterissuer letsencrypt-prod",
        "",
        "STEP 3 — Crea un Ingress con TLS",
        "-" * 40,
        "",
        "Salva il seguente YAML in ingress-mioapp.yaml e applica:",
        "  kubectl apply -f ingress-mioapp.yaml",
        "",
        "apiVersion: networking.k8s.io/v1",
        "kind: Ingress",
        "metadata:",
        "  name: mioapp",
        "  namespace: MIONAMESPACE",
        "  annotations:",
        "    cert-manager.io/cluster-issuer: letsencrypt-prod",
        "    acme.cert-manager.io/http01-edit-in-place: \"true\"",
        "spec:",
        "  ingressClassName: traefik",
        "  tls:",
        "  - hosts:",
        "    - mioapp.miodominio.com",
        "    secretName: mioapp-tls",
        "  rules:",
        "  - host: mioapp.miodominio.com",
        "    http:",
        "      paths:",
        "      - path: /",
        "        pathType: Prefix",
        "        backend:",
        "          service:",
        "            name: MIOSERVIZIO",
        "            port:",
        "              number: 80",
        "",
        "STEP 4 — Verifica certificato",
        "-" * 40,
        "  kubectl -n MIONAMESPACE get certificate",
        "  kubectl -n MIONAMESPACE describe certificate mioapp-tls",
        "  kubectl -n MIONAMESPACE get order,challenge",
        "",
        "  Quando READY=True il certificato è valido.",
        "",
        "TROUBLESHOOTING",
        "-" * 40,
        "  # Traefik",
        "  kubectl -n traefik get pods",
        "  kubectl -n traefik logs -l app.kubernetes.io/name=traefik",
        "  kubectl -n traefik get svc",
        "",
        "  # cert-manager",
        "  kubectl -n cert-manager get pods",
        "  kubectl -n cert-manager logs deploy/cert-manager",
        "",
        "  # Challenge HTTP-01 in corso",
        "  kubectl get challenge -A",
        "  kubectl describe challenge -A",
        "",
        "  # Test raggiungibilità porta 80",
        f"  curl -I http://mioapp.miodominio.com",
        "",
        "NOTE",
        "-" * 40,
        "  - Usare letsencrypt-staging per i test (nessun rate limit).",
        "  - Passare a letsencrypt-prod solo quando il setup funziona.",
        "  - Se c'è hairpin NAT, aggiungere DNS interno che risolve",
        "    il dominio direttamente verso l'IP di Traefik.",
    ]
    guide.write_text("\n".join(lines) + "\n")
    console.print(f"  [green]✓[/]  guida Let's Encrypt → {guide}")


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
