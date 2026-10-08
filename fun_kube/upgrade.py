"""
Upgrade di Kubernetes (kubeadm) — comando `fun-kube upgrade --to vX.Y.Z`.

Flusso:
  1. analisi del cluster e controlli preliminari (nessuna modifica)
  2. riepilogo + conferma (conferma esplicita se l'upgrade ferma i workload)
  3. keepalived.yml (HA: health check API sul VIP) + upgrade.yml
  4. aggiornamento di K8S_VERSION nel .env e di kubectl sulla bootstrap

Regole: solo patch o una minor version alla volta; nodi già alla versione target
saltati (rilanciare dopo un errore riprende da dove si era fermato).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from rich.console import Console

from . import deps, runner, versions
from .config import ClusterConfig

console = Console()

_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_LONGHORN_NS = "longhorn-system"


class UpgradeError(Exception):
    pass


@dataclass
class UpgradePlan:
    current: str                      # versione control plane (kubeadm-config)
    target: str                       # es. v1.36.5
    nodes_todo: List[str]             # nodi con kubelet != target
    resume: bool                      # control plane già al target, nodi da completare
    drain: bool                       # False = solo cordon (mononodo / fermo workload)
    downtime_reasons: List[str] = field(default_factory=list)
    longhorn_installed: bool = False
    longhorn_force_drain: bool = False   # node-drain-policy=always-allow durante l'upgrade
    addons: Dict[str, str] = field(default_factory=dict)   # addon → versione installata
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# kubectl verso il cluster (kubeconfig della bootstrap)
# ---------------------------------------------------------------------------

def _kubeconfig(cluster: ClusterConfig) -> Path:
    for p in (Path(f"/root/.kube/{cluster.cluster_name}"),
              Path(f"/root/.kube/{cluster.cluster_name}-admin")):
        if os.access(p, os.R_OK):
            return p
    raise UpgradeError(
        f"Kubeconfig del cluster non trovato (/root/.kube/{cluster.cluster_name}).\n"
        "  L'upgrade va lanciato come root dalla bootstrap che ha creato il cluster."
    )


def _kubectl(kubeconfig: Path, *args: str, timeout: int = 30) -> Tuple[int, str, str]:
    env = {**os.environ, "KUBECONFIG": str(kubeconfig)}
    try:
        r = subprocess.run(["kubectl", "--request-timeout=10s", *args],
                           capture_output=True, text=True, env=env, timeout=timeout)
        return r.returncode, r.stdout, r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"


def _kubectl_json(kubeconfig: Path, *args: str) -> dict:
    rc, out, err = _kubectl(kubeconfig, *args, "-o", "json")
    if rc != 0:
        raise UpgradeError(f"kubectl {' '.join(args)} fallito: {err}")
    return json.loads(out)


def _image_tag(kubeconfig: Path, kind: str, name: str, ns: str) -> Optional[str]:
    rc, out, _ = _kubectl(kubeconfig, "get", kind, name, "-n", ns,
                          "-o", "jsonpath={.spec.template.spec.containers[0].image}")
    if rc != 0 or not out:
        return None
    m = re.search(r":(v?\d+\.\d+\.\d+)", out)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Analisi + controlli preliminari
# ---------------------------------------------------------------------------

def _parse(v: str) -> Tuple[int, int, int]:
    m = _VERSION_RE.match(v.strip())
    if not m:
        raise UpgradeError(f"Versione non valida: '{v}' (formato atteso: v1.36.5)")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def _check_target_package(target: str) -> None:
    """Verifica che kubeadm <target> esista nel repo apt pkgs.k8s.io."""
    major, minor, _ = _parse(target)
    url = f"https://pkgs.k8s.io/core:/stable:/v{major}.{minor}/deb/Packages"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            packages = r.read().decode()
    except Exception as e:
        raise UpgradeError(f"Repo apt Kubernetes v{major}.{minor} non raggiungibile ({url}): {e}")
    wanted = f"{target.lstrip('v')}-1.1"
    if not re.search(rf"^Package: kubeadm\n(?:.+\n)*?Version: {re.escape(wanted)}$", packages, re.M):
        raise UpgradeError(f"kubeadm {wanted} non presente in {url}.")


def _installed_addons(kubeconfig: Path) -> Dict[str, Optional[str]]:
    """Versioni degli addon installati nel cluster (solo quelli presenti)."""
    found: Dict[str, Optional[str]] = {}
    rc, out, _ = _kubectl(kubeconfig, "get", "clusterinformation", "default",
                          "-o", "jsonpath={.spec.calicoVersion}")
    if rc == 0 and out:
        found["calico"] = out.strip()
    for name, kind, obj, ns in (
        ("cert-manager", "deployment", "cert-manager", "cert-manager"),
        ("metrics-server", "deployment", "metrics-server", "kube-system"),
        ("metallb", "deployment", "controller", "metallb-system"),
        ("local-path", "deployment", "local-path-provisioner", "local-path-storage"),
    ):
        tag = _image_tag(kubeconfig, kind, obj, ns)
        if tag:
            found[name] = tag
    rc, out, _ = _kubectl(kubeconfig, "get", "daemonset", "longhorn-manager", "-n", _LONGHORN_NS,
                          "-o", "jsonpath={.metadata.annotations.longhorn\\.io/version}")
    if rc == 0 and out:
        found["longhorn"] = out.strip()
    rc, _, _ = _kubectl(kubeconfig, "get", "daemonset", "traefik", "-n", "traefik")
    if rc == 0:
        found["traefik"] = None   # chart configurabile: solo check di compatibilità
    return found


_PINNED = {
    "calico": versions.CALICO_VERSION,
    "cert-manager": versions.CERT_MANAGER_VERSION,
    "metrics-server": versions.METRICS_SERVER_VERSION,
    "metallb": versions.METALLB_VERSION,
    "local-path": versions.LOCAL_PATH_VERSION,
    "longhorn": versions.LONGHORN_VERSION,
}


def analyze(cluster: ClusterConfig, target: str, ignore_addon_compat: bool = False) -> UpgradePlan:
    """Controlli preliminari. Nessuna modifica al cluster. Solleva UpgradeError."""
    if not target.startswith("v"):
        target = "v" + target
    tgt = _parse(target)
    kc = _kubeconfig(cluster)

    # --- Versione attuale del control plane ---
    rc, cc, err = _kubectl(kc, "get", "cm", "kubeadm-config", "-n", "kube-system",
                           "-o", "jsonpath={.data.ClusterConfiguration}")
    if rc != 0:
        raise UpgradeError(f"Cluster non raggiungibile ({err}).")
    m = re.search(r"^kubernetesVersion:\s*(\S+)", cc, re.M)
    if not m:
        raise UpgradeError("kubernetesVersion non trovata in kubeadm-config.")
    current = m.group(1)
    cur = _parse(current)

    # --- Nodi ---
    nodes = _kubectl_json(kc, "get", "nodes")["items"]
    live = {n["metadata"]["name"]: n for n in nodes}
    env_names = {n.hostname for n in cluster.nodes}
    if set(live) != env_names:
        raise UpgradeError(
            "I nodi del cluster non corrispondono al .env:\n"
            f"  solo nel cluster: {', '.join(sorted(set(live) - env_names)) or '-'}\n"
            f"  solo nel .env:    {', '.join(sorted(env_names - set(live))) or '-'}\n"
            "  Allineare prima (./fun-kube up per i nodi nuovi, drain/delete per quelli rimossi)."
        )
    kubelets = {name: n["status"]["nodeInfo"]["kubeletVersion"] for name, n in live.items()}

    # --- Salto di versione ---
    if tgt < cur:
        raise UpgradeError(f"Il control plane è già a {current}: downgrade non supportato.")
    resume = tgt == cur
    if resume and all(v == target for v in kubelets.values()):
        raise UpgradeError(f"Il cluster è già interamente a {target}: niente da fare.")
    if tgt[:2] != cur[:2] and (tgt[0] != cur[0] or tgt[1] != cur[1] + 1):
        raise UpgradeError(
            f"Da {current} si può passare solo a una patch della 1.{cur[1]} o alla 1.{cur[1] + 1}.x\n"
            f"  (una minor version alla volta: prima l'ultima patch della 1.{cur[1] + 1})."
        )
    # Minor ammesse per i kubelet: quella attuale e quella target; in ripresa
    # (control plane già al target) anche la precedente al target.
    allowed_minors = {cur[1], tgt[1]} | ({tgt[1] - 1} if resume else set())
    for name, v in kubelets.items():
        kv = _parse(v)
        if kv > tgt:
            raise UpgradeError(f"{name}: kubelet {v} più nuovo del target {target}.")
        if kv[0] != tgt[0] or kv[1] not in allowed_minors:
            raise UpgradeError(
                f"{name}: kubelet {v} troppo distante dal control plane {current}:\n"
                "  completare prima l'upgrade precedente di quel nodo."
            )

    _check_target_package(target)

    # --- Salute del cluster ---
    not_ready = [n for n, o in live.items()
                 if not any(c["type"] == "Ready" and c["status"] == "True"
                            for c in o["status"].get("conditions", []))]
    if not_ready:
        raise UpgradeError(f"Nodi non Ready: {', '.join(not_ready)}.")
    rc, out, err = _kubectl(kc, "get", "--raw", "/readyz")
    if rc != 0 or out.strip() != "ok":
        raise UpgradeError(f"API server non pronto (/readyz): {out.strip() or err}")
    pods = _kubectl_json(kc, "get", "pods", "-n", "kube-system")["items"]
    bad = [p["metadata"]["name"] for p in pods
           if p["status"].get("phase") not in ("Running", "Succeeded")
           or (p["status"].get("phase") == "Running"
               and not all(c.get("ready") for c in p["status"].get("containerStatuses", [])))]
    if bad:
        raise UpgradeError(f"Pod di kube-system non pronti: {', '.join(bad)}.")

    # --- Addon: allineati al repo e compatibili con il target ---
    installed = _installed_addons(kc)
    misaligned = [f"{a} {v} (repo: {_PINNED[a]})" for a, v in installed.items()
                  if a in _PINNED and v and v != _PINNED[a]]
    if misaligned:
        raise UpgradeError(
            "Addon non allineati alle versioni del repo: " + ", ".join(misaligned) + ".\n"
            "  Eseguire prima ./fun-kube up (aggiorna gli addon), poi l'upgrade."
        )
    incompatible = versions.incompatible_addons(target, [a for a in installed if a != "local-path"])
    warnings: List[str] = []
    if incompatible:
        msg = (f"Addon non testati/supportati con {target}: " + ", ".join(incompatible) + ".\n"
               "  Aggiornare prima gli addon (fun_kube/versions.py + ./fun-kube up).")
        if not ignore_addon_compat:
            raise UpgradeError(msg + "\n  (--ignore-addon-compat per procedere comunque)")
        warnings.append(msg)

    # --- Drain possibile? Longhorn ---
    downtime: List[str] = []
    schedulable = [n for n, o in live.items()
                   if not any(t.get("effect") == "NoSchedule" for t in o["spec"].get("taints", []) or [])]
    drain = len(live) > 1 and len(schedulable) > 1
    if not drain:
        downtime.append(
            "un solo nodo schedulabile: nessun posto dove spostare i pod, il nodo viene solo\n"
            "    messo in cordon (i container restano in esecuzione, API e workload subiscono\n"
            "    interruzioni durante il riavvio di control plane e kubelet)"
        )

    longhorn_installed = "longhorn" in installed
    longhorn_force_drain = False
    if longhorn_installed:
        vols = _kubectl_json(kc, "get", "volumes.longhorn.io", "-n", _LONGHORN_NS)["items"]
        degraded = [v["metadata"]["name"] for v in vols
                    if v.get("status", {}).get("robustness") not in ("healthy", "unknown", None, "")]
        if degraded:
            raise UpgradeError(
                f"Volumi Longhorn non healthy: {', '.join(degraded)}.\n"
                "  Attendere la ricostruzione delle repliche prima dell'upgrade."
            )
        single = [v["metadata"]["name"] for v in vols
                  if int(v.get("spec", {}).get("numberOfReplicas", 3)) < 2
                  and v.get("status", {}).get("state") == "attached"]
        if single and drain:
            longhorn_force_drain = True
            downtime.append(
                f"volumi Longhorn con una sola replica in uso ({', '.join(single)}):\n"
                "    il drain del nodo che li ospita ferma i workload che li usano finché il\n"
                "    nodo non torna disponibile (node-drain-policy=always-allow durante l'upgrade)"
            )

    return UpgradePlan(
        current=current, target=target,
        nodes_todo=sorted(n for n, v in kubelets.items() if v != target),
        resume=resume, drain=drain, downtime_reasons=downtime,
        longhorn_installed=longhorn_installed, longhorn_force_drain=longhorn_force_drain,
        addons={a: (v or "-") for a, v in installed.items()}, warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Esecuzione
# ---------------------------------------------------------------------------

def execute(cluster: ClusterConfig, plan: UpgradePlan, env_file: Path, debug: bool = False) -> Path:
    """Esegue l'upgrade. Ritorna la directory dei backup. Solleva RunnerError/UpgradeError."""
    kc = _kubeconfig(cluster)
    backup_dir = (cluster.output_dir / "backups").resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)

    inventory = runner._write_inventory(cluster)
    extra_vars = runner._build_extra_vars(cluster, plan.target)
    major, minor, _ = _parse(plan.target)
    extra_vars.update({
        "k8s_target": plan.target,
        "k8s_target_minor": f"{major}.{minor}",
        "upgrade_drain": plan.drain,
        "longhorn_installed": plan.longhorn_installed,
        "backup_dir": str(backup_dir),
    })

    playbooks = (["keepalived.yml"] if cluster.topology == "ha" else []) + ["upgrade.yml"]
    runner._syntax_check_playbooks(playbooks, inventory, extra_vars)

    previous_policy = None
    if plan.longhorn_force_drain:
        previous_policy = _longhorn_drain_policy(kc)
        _set_longhorn_drain_policy(kc, "always-allow")
    try:
        for pb in playbooks:
            console.print(f"  [cyan]▶[/]  {pb}")
            runner._run_playbook(runner._PLAYBOOK_DIR / pb, inventory, extra_vars, debug)
    finally:
        if previous_policy is not None:
            _set_longhorn_drain_policy(kc, previous_policy)

    _finalize(cluster, plan, env_file)
    return backup_dir


def _longhorn_drain_policy(kc: Path) -> str:
    rc, out, err = _kubectl(kc, "get", "settings.longhorn.io", "node-drain-policy", "-n", _LONGHORN_NS,
                            "-o", "jsonpath={.value}")
    if rc != 0 or not out:
        raise UpgradeError(f"Impossibile leggere node-drain-policy di Longhorn: {err}")
    return out.strip()


def _set_longhorn_drain_policy(kc: Path, value: str) -> None:
    rc, _, err = _kubectl(kc, "patch", "settings.longhorn.io", "node-drain-policy", "-n", _LONGHORN_NS,
                          "--type=merge", "-p", json.dumps({"value": value}))
    if rc != 0:
        console.print(f"  [red]✗[/]  node-drain-policy Longhorn non impostata a '{value}': {err}")
    else:
        console.print(f"  [green]✓[/]  Longhorn node-drain-policy = {value}")


def _finalize(cluster: ClusterConfig, plan: UpgradePlan, env_file: Path) -> None:
    kc = _kubeconfig(cluster)
    nodes = _kubectl_json(kc, "get", "nodes")["items"]
    lagging = [n["metadata"]["name"] for n in nodes
               if n["status"]["nodeInfo"]["kubeletVersion"] != plan.target]
    if lagging:
        raise UpgradeError(f"Upgrade incompleto, nodi non a {plan.target}: {', '.join(lagging)}.")

    update_env_k8s_version(env_file, plan.target)
    console.print(f"  [green]✓[/]  {env_file}: K8S_VERSION={plan.target}")

    client_minor = deps.kubectl_client_minor()
    if client_minor != _parse(plan.target)[1]:
        deps.install_kubectl(plan.target)


def update_env_k8s_version(env_file: Path, version: str) -> None:
    """Imposta K8S_VERSION nel .env preservando il resto del file."""
    text = env_file.read_text()
    line = f"K8S_VERSION={version}"
    if re.search(r"^K8S_VERSION=.*$", text, re.M):
        text = re.sub(r"^K8S_VERSION=.*$", line, text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n{line}\n"
    env_file.write_text(text)
