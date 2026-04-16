"""
CLI entry point — fun-kube up [env_file]
"""
from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

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
) -> None:
    """Provisiona un cluster Kubernetes dal file .env."""

    console.print(Panel(
        "[bold cyan]Fun-Kube[/] — Kubernetes Cluster Provisioner",
        expand=False,
    ))

    # --- 0. Dipendenze bootstrap machine ---
    console.print("\n[bold]0/4  Verifica dipendenze bootstrap machine...[/]")
    try:
        deps.run(verbose=debug)
    except DepsError as e:
        err.print(f"\n[red]Dipendenze mancanti:[/]\n{e}")
        raise typer.Exit(1)

    # --- 1. Configurazione ---
    console.print("\n[bold]1/4  Caricamento configurazione...[/]")
    try:
        cluster = cfg_module.load(env_file)
    except ConfigError as e:
        err.print(f"\n[red]Errore di configurazione:[/]\n{e}")
        raise typer.Exit(1)

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

    console.print(f"  Cluster   : [cyan]{cluster.cluster_name}[/]")
    console.print(f"  Topologia : [cyan]{topology_label}[/]")
    console.print(f"  Nodi      : {len(cluster.control_planes)} control-plane, {len(cluster.workers)} worker")
    if cluster.local_node:
        console.print(f"  Modalità  : [yellow]local-node[/] (bootstrap = nodo)")
    console.print(f"  K8s       : {cluster.k8s_version}")
    console.print(f"  Addons    : {', '.join(addons) if addons else 'nessuno'}")

    if dry_run:
        console.print("\n[green]--dry-run: configurazione valida. Nessuna modifica effettuata.[/]")
        raise typer.Exit(0)

    # --- 2. Preflight ---
    if not skip_checks:
        console.print("\n[bold]2/4  Preflight checks...[/]")
        try:
            preflight.run(cluster, debug=debug)
        except preflight.PreflightError as e:
            err.print(f"\n[red]Preflight fallito:[/]\n{e}")
            raise typer.Exit(1)
    else:
        console.print("\n[yellow]  ⚠  Preflight checks saltati.[/]")

    # --- 3. Provisioning ---
    console.print("\n[bold]3/4  Provisioning cluster core...[/]")
    try:
        runner.run_core(cluster, debug=debug)
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
    """Raccoglie info diagnostiche da tutti i nodi del cluster."""
    from rich.table import Table
    import subprocess as sp

    console.print(Panel(
        "[bold cyan]Fun-Kube Diagnose[/] — Stato nodi",
        expand=False,
    ))

    try:
        cluster = cfg_module.load(env_file)
    except ConfigError as e:
        err.print(f"\n[red]Errore di configurazione:[/]\n{e}")
        raise typer.Exit(1)

    checks = [
        ("kubelet",     "systemctl is-active kubelet 2>/dev/null || echo inactive"),
        ("containerd",  "systemctl is-active containerd 2>/dev/null || echo inactive"),
        ("k8s node",    "kubectl get node $(hostname) --no-headers 2>/dev/null | awk '{print $2}' || echo n/a"),
        ("disk free",   "df -h / | awk 'NR==2{print $4\" free\"}'"),
        ("RAM free",    "free -h | awk '/^Mem/{print $7\" free\"}'"),
        ("load avg",    "cut -d' ' -f1-3 /proc/loadavg"),
        ("k8s version", "kubelet --version 2>/dev/null || echo n/a"),
    ]

    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Node", style="cyan")
    table.add_column("Role")
    for label, _ in checks:
        table.add_column(label, justify="center")

    for node in cluster.nodes:
        row = [node.hostname, node.role]
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
            color = "green" if val in ("active", "Ready") else "red" if val in ("inactive", "NotReady") else ""
            row.append(f"[{color}]{val}[/]" if color else val)
        table.add_row(*row)

    console.print(table)


def main() -> None:
    app()
