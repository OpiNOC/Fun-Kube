"""
CLI entry point — fun-kube up [env_file]
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import config as cfg_module
from .config import ConfigError
from . import preflight, runner, deps
from .deps import DepsError

app = typer.Typer(
    name="fun-kube",
    help="Kubernetes cluster provisioning tool",
    add_completion=False,
)
console = Console()
err = Console(stderr=True)


@app.command()
def up(
    env_file: Path = typer.Argument(
        Path(".env"),
        help="Percorso al file .env (default: .env nella directory corrente)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Solo validazione, nessuna modifica"),
    debug: bool = typer.Option(False, "--debug", help="Output verboso"),
    skip_checks: bool = typer.Option(False, "--skip-checks", help="Salta preflight checks"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Salta la conferma interattiva"),
) -> None:
    """Provisiona un cluster Kubernetes dal file .env."""

    console.print(Panel(
        "[bold cyan]Fun-Kube[/] — Kubernetes Cluster Provisioner",
        expand=False,
    ))

    # --- 0. Dipendenze bootstrap machine ---
    console.print("\n[bold]0/4  Verifica e installazione dipendenze bootstrap machine...[/]")
    try:
        deps.auto_install()
        deps.run(verbose=debug)
    except KeyboardInterrupt:
        err.print("\n[yellow]Installazione interrotta dall'utente (Ctrl+C).[/]")
        raise typer.Exit(130)
    except DepsError as e:
        err.print(f"\n[red]Dipendenze mancanti:[/]\n{e}")
        raise typer.Exit(1)
    except Exception as e:
        err.print(f"\n[red]Errore durante l'installazione delle dipendenze:[/]\n{e}")
        raise typer.Exit(1)

    # --- 1. Configurazione ---
    console.print("\n[bold]1/4  Caricamento configurazione...[/]")
    try:
        cluster = cfg_module.load(env_file)
    except ConfigError as e:
        err.print(f"\n[red]Errore di configurazione:[/]\n{e}")
        raise typer.Exit(1)

    # --- Riepilogo e conferma ---
    _print_cluster_summary(cluster)

    if dry_run:
        console.print("\n[green]--dry-run: configurazione valida. Nessuna modifica effettuata.[/]")
        raise typer.Exit(0)

    if not yes:
        console.print()
        confirm = typer.confirm("Procedere con il provisioning?", default=False)
        if not confirm:
            console.print("[yellow]Provisioning annullato.[/]")
            raise typer.Exit(0)

    # --- 2. Preflight ---
    if not skip_checks:
        console.print("\n[bold]2/4  Preflight checks...[/]")
        try:
            preflight.run(cluster, debug=debug)
        except KeyboardInterrupt:
            err.print("\n[yellow]Installazione interrotta dall'utente (Ctrl+C).[/]")
            raise typer.Exit(130)
        except preflight.PreflightError as e:
            err.print(f"\n[red]Preflight fallito:[/]\n{e}")
            raise typer.Exit(1)
    else:
        console.print("\n[yellow]  ⚠  Preflight checks saltati.[/]")

    # --- 3. Provisioning ---
    console.print("\n[bold]3/4  Provisioning cluster core...[/]")
    try:
        runner.run_core(cluster, debug=debug)
    except KeyboardInterrupt:
        err.print("\n[yellow]Installazione interrotta dall'utente (Ctrl+C).[/]")
        raise typer.Exit(130)
    except runner.RunnerError as e:
        err.print(f"\n[red]Provisioning fallito:[/]\n{e}")
        raise typer.Exit(1)

    # --- 4. Output ---
    console.print("\n[bold]4/4  Generazione output...[/]")
    runner.write_output(cluster)

    console.print(f"\n[bold green]✓ Cluster '{cluster.cluster_name}' pronto![/]")
    console.print(f"  Kubeconfig primario  : /root/.kube/{cluster.cluster_name}  [green](non scade)[/]")
    console.print(f"  Kubeconfig emergenza : /root/.kube/{cluster.cluster_name}-admin  [yellow](~1 anno)[/]")
    console.print(f"  Info manutenzione    : /root/{cluster.cluster_name}-manutenzione.txt")
    console.print(f"  Output progetto      : {cluster.output_dir}/cluster-info.txt")
    console.print(f"\n  [yellow]Applica alias e kubeconfig:[/]  source ~/.bashrc\n")


@app.command(name="check-deps")
def check_deps(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Mostra versioni"),
) -> None:
    """Verifica che tutti i tool necessari siano installati sulla macchina bootstrap."""
    console.print(Panel(
        "[bold cyan]Fun-Kube[/] — Bootstrap machine dependency check",
        expand=False,
    ))
    console.print()
    try:
        deps.run(verbose=verbose)
        console.print("\n[bold green]✓ Tutte le dipendenze sono soddisfatte.[/]")
    except DepsError as e:
        Console(stderr=True).print(f"\n[red]{e}[/]")
        raise typer.Exit(1)


@app.command()
def reset(
    env_file: Path = typer.Argument(Path(".env")),
    yes: bool = typer.Option(False, "--yes", "-y", help="Salta la conferma interattiva"),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Distrugge il cluster: kubeadm reset su tutti i nodi + pulizia."""

    console.print(Panel(
        "[bold red]Fun-Kube RESET[/] — Questa operazione è distruttiva e irreversibile",
        expand=False,
    ))

    try:
        cluster = cfg_module.load(env_file)
    except ConfigError as e:
        err.print(f"\n[red]Errore di configurazione:[/]\n{e}")
        raise typer.Exit(1)

    if not yes:
        console.print(f"\n  Cluster : [cyan]{cluster.cluster_name}[/]")
        console.print(f"  Nodi    : {', '.join(n.hostname for n in cluster.nodes)}")
        console.print("\n  [yellow]Verranno eseguiti kubeadm reset + pulizia su tutti i nodi.[/]")
        confirm = typer.prompt("\nDigita il nome del cluster per confermare", default="")
        if confirm != cluster.cluster_name:
            console.print("[yellow]Reset annullato.[/]")
            raise typer.Exit(0)

    reset_script = (
        # Stop kubelet first to release CSI/iSCSI/NFS mounts (e.g. Longhorn volumes)
        # that would otherwise cause `kubeadm reset` to hang indefinitely.
        "sudo systemctl stop kubelet 2>/dev/null || true; "
        "sudo systemctl stop iscsid 2>/dev/null || true; "
        "for m in $(grep -E 'kubelet|longhorn|csi' /proc/mounts 2>/dev/null | awk '{print $2}' | sort -r); do "
        "  sudo umount -f -l \"$m\" 2>/dev/null || true; "
        "done; "
        "sudo kubeadm reset -f 2>/dev/null || true; "
        "sudo rm -rf /etc/kubernetes /var/lib/etcd /var/lib/kubelet "
        "         /etc/cni/net.d /opt/cni/bin /root/.kube; "
        "sudo iptables -F && sudo iptables -t nat -F && "
        "sudo iptables -t mangle -F && sudo iptables -X 2>/dev/null || true; "
        "sudo ip link delete cni0 2>/dev/null || true; "
        "sudo ip link delete flannel.1 2>/dev/null || true; "
        "sudo ip link delete calico.1 2>/dev/null || true"
    )

    import subprocess as sp
    for node in cluster.nodes:
        console.print(f"  [cyan]▶[/]  reset {node.hostname} ({node.ip})...")
        if cluster.local_node:
            result = sp.run(["bash", "-c", reset_script], text=True)
        else:
            result = sp.run([
                "ssh", "-i", str(cluster.ssh_key_path),
                "-o", "StrictHostKeyChecking=no",
                f"{cluster.ssh_user}@{node.ip}",
                reset_script,
            ], text=True)
        if result.returncode == 0:
            console.print(f"  [green]✓[/]  {node.hostname} pulito")
        else:
            console.print(f"  [yellow]⚠[/]  {node.hostname}: exit {result.returncode} (non bloccante)")

    console.print("\n[bold green]Reset completato.[/] I nodi sono pronti per un nuovo provisioning.")


@app.command()
def diagnose(
    env_file: Path = typer.Argument(Path(".env")),
    debug: bool = typer.Option(False, "--debug"),
) -> None:
    """Raccoglie info diagnostiche da tutti i nodi e stato degli addon."""
    import subprocess as sp
    import os

    console.print(Panel(
        "[bold cyan]Fun-Kube Diagnose[/] — Stato nodi e addon",
        expand=False,
    ))

    try:
        cluster = cfg_module.load(env_file)
    except ConfigError as e:
        err.print(f"\n[red]Errore di configurazione:[/]\n{e}")
        raise typer.Exit(1)

    kubeconfig = Path(f"/root/.kube/{cluster.cluster_name}")
    kube_env = {**os.environ, "KUBECONFIG": str(kubeconfig)} if kubeconfig.exists() else None

    # Stato k8s dei nodi: una sola chiamata kubectl dalla bootstrap (evita il problema
    # dei worker senza kubeconfig)
    node_statuses = _get_node_statuses(kube_env)

    checks = [
        ("kubelet",    "systemctl is-active kubelet 2>/dev/null || echo inactive"),
        ("containerd", "systemctl is-active containerd 2>/dev/null || echo inactive"),
        ("disk",       "df -h / | awk 'NR==2{print $4}'"),
        ("RAM",        "free -h | awk '/^Mem/{print $7}'"),
        ("load",       "cut -d' ' -f1-3 /proc/loadavg | tr ' ' '/'"),
        ("k8s",        "kubelet --version 2>/dev/null | awk '{print $2}' || echo n/a"),
    ]

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Node", style="cyan")
    table.add_column("Role")
    table.add_column("status", justify="center")
    for label, _ in checks:
        table.add_column(label, justify="center")

    for node in cluster.nodes:
        k8s_st = node_statuses.get(node.hostname, "?")
        if k8s_st == "Ready":
            k8s_cell = "[green]Ready[/]"
        elif k8s_st in ("NotReady", "Unknown"):
            k8s_cell = f"[red]{k8s_st}[/]"
        else:
            k8s_cell = k8s_st

        row = [node.hostname, node.role, k8s_cell]
        for _, cmd in checks:
            if cluster.local_node:
                result = sp.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=10)
            else:
                result = sp.run([
                    "ssh", "-i", str(cluster.ssh_key_path),
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=5",
                    f"{cluster.ssh_user}@{node.ip}", cmd,
                ], capture_output=True, text=True, timeout=15)
            val = result.stdout.strip() or result.stderr.strip()[:30] or "?"
            color = "green" if val == "active" else "red" if val == "inactive" else ""
            row.append(f"[{color}]{val}[/]" if color else val)
        table.add_row(*row)

    console.print(table)

    if kube_env:
        _print_addon_status(kube_env, cluster)


# ---------------------------------------------------------------------------
# Diagnose helpers
# ---------------------------------------------------------------------------

def _get_node_statuses(kube_env) -> dict:
    """Ritorna {hostname: status} interrogando kubectl dalla bootstrap."""
    import subprocess as sp
    if not kube_env:
        return {}
    r = sp.run(["kubectl", "get", "nodes", "--no-headers"],
               capture_output=True, text=True, env=kube_env, timeout=10)
    statuses = {}
    if r.returncode == 0:
        for line in r.stdout.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2:
                statuses[parts[0]] = parts[1]
    return statuses


def _kctl(args: list, kube_env: dict, timeout: int = 10):
    """Esegue kubectl e ritorna (rc, stdout, stderr)."""
    import subprocess as sp
    r = sp.run(["kubectl"] + args, capture_output=True, text=True,
               env=kube_env, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _bytes_human(b: int) -> str:
    for unit in ("B", "Ki", "Mi", "Gi", "Ti"):
        if b < 1024:
            return f"{b:.0f}{unit}"
        b //= 1024
    return f"{b:.0f}Pi"


def _print_addon_status(kube_env: dict, cluster) -> None:
    rc, out, _ = _kctl(["get", "ns", "--no-headers",
                         "-o", "custom-columns=NAME:.metadata.name"], kube_env)
    if rc != 0:
        return
    namespaces = set(out.splitlines())

    sections = []
    if cluster.keepalived.enabled:
        sections.append(_keepalived_section(cluster))
    if "metallb-system" in namespaces:
        sections.append(_metallb_section(kube_env))
    if "traefik" in namespaces:
        sections.append(_traefik_section(kube_env))
    if "npm-system" in namespaces:
        sections.append(_npm_section(kube_env))
    if "longhorn-system" in namespaces:
        sections.append(_longhorn_section(kube_env))

    if sections:
        console.print()
        console.rule("[bold]Addon installati")
        for s in sections:
            console.print(s)
            console.print()


def _pod_summary(kube_env: dict, namespace: str) -> str:
    """Ritorna 'N/T Running' per i pod di un namespace."""
    rc, out, _ = _kctl(["get", "pods", "-n", namespace, "--no-headers"], kube_env)
    if rc != 0 or not out:
        return "nessun pod"
    lines = out.splitlines()
    total = len(lines)
    running = sum(1 for l in lines if "Running" in l)
    color = "green" if running == total else "yellow" if running > 0 else "red"
    return f"[{color}]{running}/{total} Running[/]"


def _keepalived_section(cluster) -> str:
    import subprocess as sp
    lines = [f"[bold]Keepalived[/]  VIP: [cyan]{cluster.keepalived.vip}[/]  iface: {cluster.keepalived.interface}"]
    master = None
    for node in cluster.control_planes:
        try:
            cmd = f"ip addr show | grep -q '{cluster.keepalived.vip}' && echo MASTER || echo BACKUP"
            r = sp.run(
                ["ssh", "-i", str(cluster.ssh_key_path), "-o", "StrictHostKeyChecking=no",
                 "-o", "ConnectTimeout=5", f"{cluster.ssh_user}@{node.ip}", cmd],
                capture_output=True, text=True, timeout=8,
            )
            role = r.stdout.strip()
            if role == "MASTER":
                master = node.hostname
                lines.append(f"  MASTER: [green]{node.hostname}[/] ({node.ip})")
            else:
                lines.append(f"  backup: {node.hostname} ({node.ip})")
        except Exception:
            lines.append(f"  [yellow]?[/] {node.hostname} ({node.ip})")
    if master is None:
        lines.append("  [red]Nessun nodo MASTER rilevato — VIP non assegnato[/]")
    return "\n".join(lines)


def _metallb_section(kube_env: dict) -> str:
    import ipaddress
    lines = ["[bold]MetalLB[/]"]

    # Pool e IP allocati
    rc, out, _ = _kctl([
        "get", "ipaddresspool", "-n", "metallb-system",
        "-o", "jsonpath={range .items[*]}{.metadata.name}{'\\t'}{.spec.addresses[0]}{'\\n'}{end}",
    ], kube_env)

    rc2, svc_out, _ = _kctl([
        "get", "svc", "-A", "--no-headers",
        "-o", "custom-columns=NS:.metadata.namespace,NAME:.metadata.name,"
              "TYPE:.spec.type,IP:.status.loadBalancer.ingress[0].ip,"
              "PORTS:.spec.ports[*].port",
    ], kube_env)

    lb_services = []
    lb_ips: set = set()
    if rc2 == 0:
        for line in svc_out.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[2] == "LoadBalancer":
                ip = parts[3] if parts[3] not in ("<none>", "<no", "") else ""
                ports = parts[4] if len(parts) > 4 else ""
                if ip:
                    lb_ips.add(ip)
                lb_services.append((parts[0], parts[1], ip or "pending", ports))

    if rc == 0 and out:
        for pool_line in out.splitlines():
            if "\t" not in pool_line:
                continue
            pool_name, pool_range = pool_line.split("\t", 1)
            if "-" in pool_range:
                try:
                    s, e = pool_range.split("-")
                    total = int(ipaddress.ip_address(e.strip())) - int(ipaddress.ip_address(s.strip())) + 1
                    alloc = len(lb_ips)
                    free = total - alloc
                    free_color = "green" if free > 0 else "red"
                    lines.append(f"  Pool [cyan]{pool_name}[/]: {pool_range}  "
                                 f"({total} IP — {alloc} allocati, [{free_color}]{free} liberi[/])")
                except Exception:
                    lines.append(f"  Pool [cyan]{pool_name}[/]: {pool_range}")
            else:
                lines.append(f"  Pool [cyan]{pool_name}[/]: {pool_range}")

    if lb_services:
        lines.append("  Servizi LoadBalancer:")
        for ns, name, ip, ports in lb_services:
            lines.append(f"    [cyan]{ns}/{name}[/]  {ip}  :{ports}")

    lines.append(f"  Pods: {_pod_summary(kube_env, 'metallb-system')}")
    return "\n".join(lines)


def _traefik_section(kube_env: dict) -> str:
    lines = ["[bold]Traefik[/]"]

    rc, out, _ = _kctl(["get", "svc", "traefik", "-n", "traefik",
                         "--no-headers", "-o",
                         "custom-columns=TYPE:.spec.type,IP:.status.loadBalancer.ingress[0].ip,"
                         "NP_HTTP:.spec.ports[?(@.name==\"web\")].nodePort,"
                         "NP_HTTPS:.spec.ports[?(@.name==\"websecure\")].nodePort"], kube_env)
    if rc == 0 and out:
        parts = out.split()
        svc_type = parts[0] if parts else "?"
        if svc_type == "LoadBalancer":
            ip = parts[1] if len(parts) > 1 else "pending"
            lines.append(f"  Endpoint: [cyan]http://{ip}[/]  https://{ip}")
        else:
            np_http  = parts[2] if len(parts) > 2 else "?"
            np_https = parts[3] if len(parts) > 3 else "?"
            lines.append(f"  NodePort HTTP: [cyan]{np_http}[/]  HTTPS: {np_https}")

    rc, out, _ = _kctl(["get", "ingressclass", "--no-headers",
                         "-o", "custom-columns=NAME:.metadata.name,"
                               "DEFAULT:.metadata.annotations.ingressclass\\.kubernetes\\.io/is-default-class"],
                        kube_env)
    if rc == 0 and out:
        for line in out.splitlines():
            parts = line.split()
            name = parts[0] if parts else "?"
            default = "(default)" if len(parts) > 1 and parts[1] == "true" else ""
            lines.append(f"  IngressClass: [cyan]{name}[/] {default}")

    lines.append(f"  Pods: {_pod_summary(kube_env, 'traefik')}")
    return "\n".join(lines)


def _npm_section(kube_env: dict) -> str:
    lines = ["[bold]Nginx Proxy Manager[/]"]

    rc, out, _ = _kctl(["get", "svc", "nginx-proxy-manager", "-n", "npm-system",
                         "--no-headers", "-o",
                         "custom-columns=TYPE:.spec.type,IP:.status.loadBalancer.ingress[0].ip,"
                         "NP_HTTP:.spec.ports[?(@.name==\"http\")].nodePort,"
                         "NP_ADMIN:.spec.ports[?(@.name==\"admin\")].nodePort"], kube_env)
    if rc == 0 and out:
        parts = out.split()
        svc_type = parts[0] if parts else "?"
        if svc_type == "LoadBalancer":
            ip = parts[1] if len(parts) > 1 else "pending"
            lines.append(f"  HTTP:      [cyan]http://{ip}[/]")
            lines.append(f"  Admin UI:  [cyan]http://{ip}:81[/]")
        else:
            np_http  = parts[2] if len(parts) > 2 else "?"
            np_admin = parts[3] if len(parts) > 3 else "?"
            lines.append(f"  NodePort HTTP: [cyan]{np_http}[/]  Admin: {np_admin}")

    rc, out, _ = _kctl(["get", "pvc", "-n", "npm-system", "--no-headers",
                         "-o", "custom-columns=NAME:.metadata.name,"
                               "STATUS:.status.phase,CAP:.status.capacity.storage,"
                               "SC:.spec.storageClassName"], kube_env)
    if rc == 0 and out:
        lines.append("  PVC:")
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 4:
                status_color = "green" if parts[1] == "Bound" else "yellow"
                lines.append(f"    [cyan]{parts[0]}[/]  [{status_color}]{parts[1]}[/]  {parts[2]}  ({parts[3]})")

    lines.append(f"  Pods: {_pod_summary(kube_env, 'npm-system')}")
    return "\n".join(lines)


def _longhorn_section(kube_env: dict) -> str:
    import json as _json
    lines = ["[bold]Longhorn[/]"]

    # StorageClass
    rc, out, _ = _kctl(["get", "storageclass", "--no-headers",
                         "-o", "custom-columns=NAME:.metadata.name,"
                               "PROVISIONER:.provisioner,"
                               "DEFAULT:.metadata.annotations.storageclass\\.kubernetes\\.io/is-default-class"],
                        kube_env)
    if rc == 0 and out:
        lh_classes = [l for l in out.splitlines() if "longhorn" in l.lower()]
        if lh_classes:
            lines.append("  StorageClass:")
            for line in lh_classes:
                parts = line.split()
                name = parts[0] if parts else "?"
                default = " [dim](default)[/]" if len(parts) > 2 and parts[2] == "true" else ""
                lines.append(f"    [cyan]{name}[/]{default}")

    # PVC aggregate
    rc, out, _ = _kctl(["get", "pvc", "-A", "--no-headers",
                         "-o", "custom-columns=SC:.spec.storageClassName,CAP:.status.capacity.storage,STATUS:.status.phase"],
                        kube_env)
    if rc == 0 and out:
        lh_pvcs = [l for l in out.splitlines() if l.startswith("longhorn")]
        if lh_pvcs:
            total_gi = 0
            bound = 0
            for line in lh_pvcs:
                parts = line.split()
                if len(parts) >= 3 and parts[2] == "Bound":
                    bound += 1
                    cap = parts[1]
                    try:
                        if cap.endswith("Gi"):
                            total_gi += int(cap[:-2])
                        elif cap.endswith("Mi"):
                            total_gi += int(cap[:-2]) // 1024
                    except Exception:
                        pass
            lines.append(f"  Volumi: {bound} PVC bound ({total_gi}Gi allocati)")

    # Nodi storage via CRD
    rc, out, _ = _kctl(["get", "nodes.longhorn.io", "-n", "longhorn-system", "-o", "json"], kube_env, timeout=15)
    if rc == 0 and out:
        try:
            data = _json.loads(out)
            items = data.get("items", [])
            if items:
                lines.append("  Nodi storage:")
                for item in items:
                    name = item["metadata"]["name"]
                    schedulable = item.get("spec", {}).get("allowScheduling", True)
                    disk_statuses = item.get("status", {}).get("diskStatus", {})
                    avail_total = sum(int(d.get("storageAvailable", 0)) for d in disk_statuses.values())
                    max_total = sum(int(d.get("storageMaximum", 0)) for d in disk_statuses.values())
                    sched_str = "[green]schedulable[/]" if schedulable else "[yellow]unschedulable[/]"
                    if max_total > 0:
                        avail_h = _bytes_human(avail_total)
                        max_h = _bytes_human(max_total)
                        lines.append(f"    [cyan]{name}[/]  {sched_str}  {avail_h} liberi / {max_h} totali")
                    else:
                        lines.append(f"    [cyan]{name}[/]  {sched_str}")
        except Exception:
            pass

    lines.append(f"  Pods: {_pod_summary(kube_env, 'longhorn-system')}")
    return "\n".join(lines)


def _print_cluster_summary(cluster: "cfg_module.ClusterConfig") -> None:
    from .config import ClusterConfig

    topology_label = {
        "single-node": "Single-node (mononodo)",
        "single-cp":   "Single control-plane",
        "ha":          "HA multi control-plane",
    }[cluster.topology]

    addons = [
        name
        for name, enabled in [
            ("MetalLB", cluster.metallb.enabled),
            (f"Ingress/{cluster.ingress.type}", cluster.ingress.enabled),
            ("Longhorn", cluster.longhorn.enabled),
        ]
        if enabled
    ]

    console.print()
    console.print(Panel(
        f"[bold]Cluster:[/] [cyan]{cluster.cluster_name}[/]  |  "
        f"[bold]Topologia:[/] [cyan]{topology_label}[/]  |  "
        f"[bold]K8s:[/] {cluster.k8s_version}",
        title="Riepilogo configurazione",
        expand=False,
    ))

    # Tabella nodi
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", style="dim", justify="right")
    table.add_column("Hostname", style="cyan")
    table.add_column("IP")
    table.add_column("Ruolo")

    for i, node in enumerate(cluster.nodes, 1):
        role_color = "blue" if node.role == "control-plane" else "green"
        table.add_row(
            str(i),
            node.hostname,
            node.ip,
            f"[{role_color}]{node.role}[/]",
        )

    console.print(table)
    console.print()

    # Dettagli rete
    console.print(f"  Pod CIDR     : {cluster.pod_cidr}")
    console.print(f"  Service CIDR : {cluster.service_cidr}")
    if cluster.topology == "ha":
        console.print(f"  VIP          : {cluster.keepalived.vip}  (iface: {cluster.keepalived.interface})")
    if cluster.local_node:
        console.print(f"  Modalità     : [yellow]local-node[/] (bootstrap = nodo)")
    else:
        console.print(f"  SSH          : {cluster.ssh_user}@... (key: {cluster.ssh_key_path})")

    # Addons
    if addons:
        console.print(f"  Addon        : {', '.join(addons)}")
        if cluster.metallb.enabled:
            mlb_ver = cluster.metallb.version or "latest (GitHub API)"
            console.print(f"    MetalLB versione  : {mlb_ver}")
            console.print(f"    MetalLB IP pool   : {cluster.metallb.ip_pool}")
        if cluster.ingress.enabled:
            ing = cluster.ingress
            svc = cluster.effective_ingress_service_type
            ep = cluster.api_endpoint
            if ing.type == "traefik":
                if svc == "loadbalancer":
                    svc_detail = f"loadbalancer (IP: {ing.traefik_lb_ip or 'MetalLB auto-assign'})"
                else:
                    svc_detail = f"nodeport (http:{ing.traefik_http_nodeport} https:{ing.traefik_https_nodeport})"
                console.print(f"    Ingress type      : traefik")
                console.print(f"    Ingress service   : {svc_detail}")
                if ing.traefik_dashboard_host:
                    console.print(f"    Dashboard         : http://{ing.traefik_dashboard_host}/dashboard/")
                if ing.traefik_acme_email:
                    console.print(f"    Let's Encrypt     : {ing.traefik_acme_email}")
                else:
                    console.print(f"    Let's Encrypt     : [yellow]non configurato (guida in output/)[/]")
            else:  # nginx-proxy-manager
                if svc == "loadbalancer":
                    svc_detail = f"loadbalancer (IP: {ing.npm_lb_ip or 'MetalLB auto-assign'})"
                    admin_url = f"http://{ing.npm_lb_ip or '<lb-ip>'}:81"
                else:
                    svc_detail = f"nodeport (http:{ing.npm_http_nodeport} https:{ing.npm_https_nodeport} admin:{ing.npm_admin_nodeport})"
                    admin_url = f"http://{ep}:{ing.npm_admin_nodeport}"
                console.print(f"    Ingress type      : nginx-proxy-manager")
                console.print(f"    Ingress service   : {svc_detail}")
                console.print(f"    Admin UI          : {admin_url}")
                if ing.npm_db_password == "T1sh-PwD-Sh0ulD-B3-Ch4nGeD-NOW":
                    console.print(f"    [yellow]⚠  NPM_DB_PASSWORD è il valore di default — cambiarlo in .env![/]")
        if cluster.longhorn.enabled:
            lh_ver = cluster.longhorn.version or "latest (GitHub API)"
            lh_rwx = "sì" if cluster.longhorn.rwx else "no"
            if cluster.longhorn.ui_nodeport:
                lh_ui = f"http://{cluster.api_endpoint}:{cluster.longhorn.ui_nodeport}"
            else:
                lh_ui = "disabilitata"
            console.print(f"    Longhorn versione : {lh_ver}")
            console.print(f"    Longhorn RWX      : {lh_rwx}")
            console.print(f"    Longhorn UI       : {lh_ui}")
    else:
        console.print("  Addon        : nessuno")


def main() -> None:
    app()
