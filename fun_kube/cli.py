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
    console.print("\n[bold]3/4  Provisioning cluster...[/]")
    try:
        runner.run(cluster, debug=debug)
    except runner.RunnerError as e:
        err.print(f"\n[red]Provisioning fallito:[/]\n{e}")
        raise typer.Exit(1)

    # --- 4. Output ---
    console.print("\n[bold]4/4  Generazione output...[/]")
    runner.write_output(cluster)

    console.print(f"\n[bold green]✓ Cluster '{cluster.cluster_name}' pronto![/]")
    console.print(f"  Output   : {cluster.output_dir}/cluster-info.txt")
    console.print(f"  Kubeconfig: export KUBECONFIG={cluster.output_dir}/kubeconfig\n")


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


def main() -> None:
    app()
