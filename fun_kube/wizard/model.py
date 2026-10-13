"""
Wizard model — pure logic, no UI (testable on its own).

- the wizard state is a dict of .env keys → string values
- cascading rules: which choices are available given the previous ones
- reading existing .env files and writing a new one (unknown keys are preserved)
- final validation with config.load(), the same code path used by `up`

The wizard never duplicates validation rules: anything it lets through is checked
again by config.load() before saving.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import string
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import dotenv_values

from .. import versions
from ..config import ConfigError, load as load_config

# ---------------------------------------------------------------------------
# Defaults (same as config.py / .env.example)
# ---------------------------------------------------------------------------

DEFAULTS: Dict[str, str] = {
    "CLUSTER_NAME": "",
    "K8S_VERSION": versions.K8S_VERSION,
    "CLUSTER_TIMEZONE": "Europe/Rome",
    "LOCAL_NODE": "false",
    "SSH_USER": "root",
    "SSH_KEY_PATH": "",
    "POD_CIDR": "172.16.0.0/16",
    "SERVICE_CIDR": "10.96.0.0/12",
    "API_SERVER_EXTRA_SANS": "",
    "KEEPALIVED_ENABLED": "false",
    "KEEPALIVED_VIP": "",
    "KEEPALIVED_INTERFACE": "",
    "METALLB_ENABLED": "false",
    "METALLB_IP_POOL": "",
    "INGRESS_ENABLED": "false",
    "INGRESS_TYPE": "nginx-proxy-manager",
    "INGRESS_SERVICE_TYPE": "auto",
    "TRAEFIK_LB_IP": "",
    "TRAEFIK_HTTP_NODEPORT": "30080",
    "TRAEFIK_HTTPS_NODEPORT": "30443",
    "TRAEFIK_IS_DEFAULT_CLASS": "true",
    "TRAEFIK_DASHBOARD_HOST": "",
    "TRAEFIK_ACME_EMAIL": "",
    "NPM_LB_IP": "",
    "NPM_HTTP_NODEPORT": "30080",
    "NPM_HTTPS_NODEPORT": "30443",
    "NPM_ADMIN_NODEPORT": "30081",
    "NPM_DB_PASSWORD": "",
    "NPM_DB_STORAGE": "1Gi",
    "NPM_DATA_STORAGE": "2Gi",
    "LONGHORN_ENABLED": "false",
    "LONGHORN_RWX": "false",
    "LONGHORN_UI_NODEPORT": "31080",
    "DN_ESSENCE_ENABLED": "true",
    "DN_ESSENCE_UI_NODEPORT": "30880",
    "MONITORING_ENABLED": "false",
    "GRAFANA_EXPOSE": "auto",
    "GRAFANA_LB_IP": "auto",
    "GRAFANA_ALLOWED_CIDRS": "",
    "GRAFANA_NODEPORT": "30300",
    "GRAFANA_TRAEFIK_INGRESS": "false",
    "GRAFANA_DOMAIN": "",
    "GRAFANA_ADMIN_PASSWORD": "",
    "MONITORING_STORAGE_CLASS": "auto",
    "PROM_RETENTION": "15d",
    "PROM_RETENTION_SIZE": "8GB",
    "PROM_STORAGE": "10Gi",
    "LOKI_RETENTION": "168h",
    "LOKI_STORAGE": "10Gi",
    "GRAFANA_STORAGE": "1Gi",
    "ALERTMANAGER_STORAGE": "1Gi",
    "ALERTMANAGER_SLACK_WEBHOOK": "",
    "ALERTMANAGER_SLACK_CHANNEL": "#alert-k8s",
}

TOPOLOGIES = {
    "single": "Single node",
    "single-cp": "1 control-plane + workers",
    "ha": "High availability (3+ control-planes)",
}

# Keys written by the wizard, grouped in sections with a short English comment.
# New files: keys of disabled features are written only as "<FEATURE>_ENABLED=false";
# keys already in a loaded file are always kept.
SECTIONS: List[Tuple[str, str, List[str]]] = [
    ("Cluster", "", ["CLUSTER_NAME", "K8S_VERSION", "CLUSTER_TIMEZONE"]),
    ("Access", "SSH from this machine to the nodes (not used with LOCAL_NODE=true).",
     ["LOCAL_NODE", "SSH_USER", "SSH_KEY_PATH"]),
    ("Network", "", ["POD_CIDR", "SERVICE_CIDR", "API_SERVER_EXTRA_SANS"]),
    ("High availability", "keepalived VIP = API endpoint of the cluster.",
     ["KEEPALIVED_ENABLED", "KEEPALIVED_VIP", "KEEPALIVED_INTERFACE"]),
    ("MetalLB", "", ["METALLB_ENABLED", "METALLB_IP_POOL"]),
    ("Ingress", "", ["INGRESS_ENABLED", "INGRESS_TYPE", "INGRESS_SERVICE_TYPE",
                     "TRAEFIK_LB_IP", "TRAEFIK_HTTP_NODEPORT", "TRAEFIK_HTTPS_NODEPORT",
                     "TRAEFIK_IS_DEFAULT_CLASS", "TRAEFIK_DASHBOARD_HOST", "TRAEFIK_ACME_EMAIL",
                     "NPM_LB_IP", "NPM_HTTP_NODEPORT", "NPM_HTTPS_NODEPORT", "NPM_ADMIN_NODEPORT",
                     "NPM_DB_PASSWORD", "NPM_DB_STORAGE", "NPM_DATA_STORAGE"]),
    ("Longhorn", "", ["LONGHORN_ENABLED", "LONGHORN_RWX", "LONGHORN_UI_NODEPORT"]),
    ("DN-essence", "", ["DN_ESSENCE_ENABLED", "DN_ESSENCE_UI_NODEPORT"]),
    ("Monitoring", "", ["MONITORING_ENABLED", "GRAFANA_EXPOSE", "GRAFANA_LB_IP", "GRAFANA_ALLOWED_CIDRS",
                        "GRAFANA_NODEPORT", "GRAFANA_TRAEFIK_INGRESS", "GRAFANA_DOMAIN",
                        "GRAFANA_ADMIN_PASSWORD", "MONITORING_STORAGE_CLASS",
                        "PROM_RETENTION", "PROM_RETENTION_SIZE", "PROM_STORAGE", "LOKI_RETENTION",
                        "LOKI_STORAGE", "GRAFANA_STORAGE", "ALERTMANAGER_STORAGE",
                        "ALERTMANAGER_SLACK_WEBHOOK", "ALERTMANAGER_SLACK_CHANNEL"]),
]

MAX_NODES = 30


def _true(v: Optional[str]) -> bool:
    return (v or "").strip().lower() in ("true", "1", "yes")


def random_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class Node:
    ip: str = ""
    hostname: str = ""


class WizardState:
    """Wizard values: env keys + node list + topology. Rules live here, not in the UI."""

    def __init__(self, values: Optional[Dict[str, str]] = None, extra: Optional[Dict[str, str]] = None):
        self.values: Dict[str, str] = {**DEFAULTS, **(values or {})}
        self.extra: Dict[str, str] = extra or {}      # unknown keys, preserved on save
        self.from_file: set = set(values or {})       # keys of the loaded file: always written back
        self.cps: List[Node] = []
        self.workers: List[Node] = []
        self.topology = "single"
        self.existing_cluster = False                 # cluster reachable → some fields locked
        self.live: Optional[ClusterInfo] = None       # what the existing cluster runs
        self.upgrade_to = ""                          # Kubernetes upgrade target ("" = none)

    # --- nodes / topology -------------------------------------------------
    def load_nodes(self, env: Dict[str, str]) -> None:
        i = 1
        while env.get(f"NODE_{i}_IP"):
            node = Node(env.get(f"NODE_{i}_IP", ""), env.get(f"NODE_{i}_HOSTNAME", ""))
            role = (env.get(f"NODE_{i}_ROLE") or "").strip().lower()
            (self.cps if role == "control-plane" else self.workers).append(node)
            i += 1
        if len(self.cps) >= 3:
            self.topology = "ha"
        elif self.workers:
            self.topology = "single-cp"
        else:
            self.topology = "single"
        if not self.cps:
            self.cps = [Node()]

    def set_topology(self, topology: str, cp_count: Optional[int] = None,
                     worker_count: Optional[int] = None) -> None:
        self.topology = topology
        if topology == "single":
            cp_count, worker_count = 1, 0
        elif topology == "single-cp":
            cp_count = 1
            worker_count = max(1, worker_count if worker_count is not None else len(self.workers))
        else:
            cp_count = cp_count or max(3, len(self.cps))
            if cp_count < 3 or cp_count % 2 == 0:
                cp_count = 3
            worker_count = worker_count if worker_count is not None else len(self.workers)
        self.cps = (self.cps + [Node() for _ in range(cp_count)])[:cp_count]
        self.workers = (self.workers + [Node() for _ in range(worker_count)])[:worker_count]
        self.values["KEEPALIVED_ENABLED"] = "true" if topology == "ha" else "false"
        if topology != "single":
            self.values["LOCAL_NODE"] = "false"

    @property
    def multi_node(self) -> bool:
        return len(self.cps) + len(self.workers) > 1

    @property
    def schedulable_nodes(self) -> int:
        return len(self.workers) or len(self.cps)

    def v(self, key: str) -> str:
        return self.values.get(key, "")

    def on(self, key: str) -> bool:
        return _true(self.values.get(key))

    # --- cascading rules --------------------------------------------------
    def ingress_service_types(self) -> List[Tuple[str, str, bool]]:
        """(value, label, available)."""
        mlb = self.on("METALLB_ENABLED")
        return [
            ("auto", "Auto — LoadBalancer if MetalLB is enabled, otherwise NodePort", True),
            ("loadbalancer", "LoadBalancer — dedicated IP from MetalLB", mlb),
            ("nodeport", "NodePort — fixed ports on every node", True),
        ]

    def grafana_exposures(self) -> List[Tuple[str, str, bool]]:
        mlb = self.on("METALLB_ENABLED")
        return [
            ("auto", "Auto — LoadBalancer if MetalLB is enabled, otherwise NodePort", True),
            ("loadbalancer", "LoadBalancer — dedicated MetalLB IP on the nodes' network", mlb),
            ("nodeport", "NodePort — http://<node IP>:<port> on every node", True),
            ("clusterip", "ClusterIP — internal only, published by NPM or Traefik", True),
        ]

    def resolved_grafana_exposure(self) -> str:
        e = self.v("GRAFANA_EXPOSE") or "auto"
        if e == "auto":
            return "loadbalancer" if self.on("METALLB_ENABLED") else "nodeport"
        return e

    def longhorn_required(self) -> bool:
        """NPM on multi-node clusters needs RWX storage shared by its DaemonSet pods."""
        return (self.on("INGRESS_ENABLED") and self.v("INGRESS_TYPE") == "nginx-proxy-manager"
                and self.multi_node)

    def monitoring_storage_ok(self) -> bool:
        """Monitoring needs persistent storage: Longhorn, or local-path on a single node."""
        return self.on("LONGHORN_ENABLED") or not self.multi_node or \
            self.v("MONITORING_STORAGE_CLASS") not in ("", "auto")

    def visible(self, key: str) -> bool:
        """Is a field relevant given the current choices?"""
        v, on = self.v, self.on
        ingress = on("INGRESS_ENABLED")
        traefik = ingress and v("INGRESS_TYPE") == "traefik"
        npm = ingress and v("INGRESS_TYPE") == "nginx-proxy-manager"
        svc = v("INGRESS_SERVICE_TYPE")
        lb = svc == "loadbalancer" or (svc == "auto" and on("METALLB_ENABLED"))
        mon = on("MONITORING_ENABLED")
        gexp = self.resolved_grafana_exposure()
        rules = {
            "SSH_USER": not on("LOCAL_NODE"),
            "SSH_KEY_PATH": not on("LOCAL_NODE"),
            "LOCAL_NODE": self.topology == "single",
            "KEEPALIVED_VIP": self.topology == "ha",
            "KEEPALIVED_INTERFACE": self.topology == "ha",
            "METALLB_IP_POOL": on("METALLB_ENABLED"),
            "INGRESS_TYPE": ingress, "INGRESS_SERVICE_TYPE": ingress,
            "TRAEFIK_LB_IP": traefik and lb,
            "TRAEFIK_HTTP_NODEPORT": traefik and not lb, "TRAEFIK_HTTPS_NODEPORT": traefik and not lb,
            "TRAEFIK_IS_DEFAULT_CLASS": traefik, "TRAEFIK_DASHBOARD_HOST": traefik,
            "TRAEFIK_ACME_EMAIL": traefik,
            "NPM_LB_IP": npm and lb,
            "NPM_HTTP_NODEPORT": npm and not lb, "NPM_HTTPS_NODEPORT": npm and not lb,
            "NPM_ADMIN_NODEPORT": npm and not lb,
            "NPM_DB_PASSWORD": npm, "NPM_DB_STORAGE": npm, "NPM_DATA_STORAGE": npm,
            "LONGHORN_RWX": on("LONGHORN_ENABLED"), "LONGHORN_UI_NODEPORT": on("LONGHORN_ENABLED"),
            "DN_ESSENCE_UI_NODEPORT": on("DN_ESSENCE_ENABLED"),
            "GRAFANA_EXPOSE": mon,
            "GRAFANA_LB_IP": mon and gexp == "loadbalancer",
            "GRAFANA_ALLOWED_CIDRS": mon and gexp == "loadbalancer",
            "GRAFANA_NODEPORT": mon and gexp == "nodeport",
            "GRAFANA_TRAEFIK_INGRESS": mon and gexp == "clusterip" and traefik,
            "GRAFANA_DOMAIN": mon, "GRAFANA_ADMIN_PASSWORD": mon, "MONITORING_STORAGE_CLASS": mon,
        }
        if key.startswith(("PROM_", "LOKI_", "GRAFANA_STORAGE", "ALERTMANAGER_")):
            return mon
        return rules.get(key, True)

    def normalize(self) -> None:
        """Apply forced choices (e.g. Longhorn required by NPM on multi-node)."""
        if self.longhorn_required():
            self.values["LONGHORN_ENABLED"] = "true"
            self.values["LONGHORN_RWX"] = "true"
        if self.v("INGRESS_SERVICE_TYPE") == "loadbalancer" and not self.on("METALLB_ENABLED"):
            self.values["INGRESS_SERVICE_TYPE"] = "auto"
        if self.v("GRAFANA_EXPOSE") == "loadbalancer" and not self.on("METALLB_ENABLED"):
            self.values["GRAFANA_EXPOSE"] = "auto"
        if not (self.on("INGRESS_ENABLED") and self.v("INGRESS_TYPE") == "traefik"):
            self.values["GRAFANA_TRAEFIK_INGRESS"] = "false"
        if self.on("INGRESS_ENABLED") and self.v("INGRESS_TYPE") == "nginx-proxy-manager" \
                and not self.v("NPM_DB_PASSWORD"):
            self.values["NPM_DB_PASSWORD"] = random_password()

    def installed_addons(self) -> List[str]:
        """Add-ons (names in versions.K8S_COMPAT) the configuration installs."""
        out = ["calico", "cert-manager", "metrics-server"]
        if self.on("METALLB_ENABLED"):
            out.append("metallb")
        if self.on("LONGHORN_ENABLED"):
            out.append("longhorn")
        if self.on("INGRESS_ENABLED") and self.v("INGRESS_TYPE") == "traefik":
            out.append("traefik")
        if self.on("MONITORING_ENABLED"):
            out.append("monitoring")
        return out

    def upgrade_node_mismatch(self) -> str:
        """`upgrade` needs the .env nodes to match the cluster nodes exactly."""
        if not self.live:
            return ""
        env = {n.hostname.strip() for n in self.cps + self.workers}
        added, removed = sorted(env - set(self.live.nodes)), sorted(set(self.live.nodes) - env)
        if not (added or removed):
            return ""
        parts = ([f"new in the file: {', '.join(added)}"] if added else []) + \
                ([f"missing from the file: {', '.join(removed)}"] if removed else [])
        return "; ".join(parts)

    # --- .env output ------------------------------------------------------
    def to_env(self) -> Dict[str, str]:
        self.normalize()
        out: Dict[str, str] = {}
        for _, _, keys in SECTIONS:
            for k in keys:
                if self._written(k):
                    out[k] = self.v(k)
        nodes = [(n, "control-plane") for n in self.cps] + [(n, "worker") for n in self.workers]
        for i, (n, role) in enumerate(nodes, 1):
            out[f"NODE_{i}_IP"] = n.ip.strip()
            out[f"NODE_{i}_ROLE"] = role
            out[f"NODE_{i}_HOSTNAME"] = n.hostname.strip()
        return out

    def _written(self, key: str) -> bool:
        if key.endswith("_ENABLED") or key in ("CLUSTER_NAME", "K8S_VERSION", "CLUSTER_TIMEZONE",
                                               "POD_CIDR", "SERVICE_CIDR"):
            return True
        if key == "LOCAL_NODE":
            return self.topology == "single"
        if key in self.from_file:
            return True       # settings of a disabled feature stay in the file
        return self.visible(key) and (self.v(key) != "" or key in ("KEEPALIVED_INTERFACE",
                                                                  "GRAFANA_DOMAIN", "GRAFANA_ADMIN_PASSWORD",
                                                                  "API_SERVER_EXTRA_SANS"))

    def render(self) -> str:
        env = self.to_env()
        lines = [
            "# Fun-Kube cluster configuration — generated by `fun-kube --wizard`.",
            "# Can be edited by hand; reference for every variable: .env.example",
            "",
        ]
        for title, comment, keys in SECTIONS:
            present = [k for k in keys if k in env]
            if not present:
                continue
            lines.append(f"# --- {title} " + "-" * max(3, 60 - len(title)))
            if comment:
                lines.append(f"# {comment}")
            lines += [f"{k}={env[k]}" for k in present]
            lines.append("")
            if title == "Access":
                lines.append("# --- Nodes " + "-" * 56)
                i = 1
                while f"NODE_{i}_IP" in env:
                    lines += [f"NODE_{i}_IP={env[f'NODE_{i}_IP']}", f"NODE_{i}_ROLE={env[f'NODE_{i}_ROLE']}",
                              f"NODE_{i}_HOSTNAME={env[f'NODE_{i}_HOSTNAME']}", ""]
                    i += 1
        managed = set(DEFAULTS) | {k for k in self.extra if k.startswith("NODE_")}
        others = {k: v for k, v in self.extra.items() if k not in managed and not k.startswith("NODE_")}
        if others:
            lines.append("# --- Other settings (preserved, not managed by the wizard) " + "-" * 8)
            lines += [f"{k}={v}" for k, v in others.items()]
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

@dataclass
class EnvFile:
    path: Path
    cluster: str
    nodes: int


def list_env_files(directory: Path) -> List[EnvFile]:
    """.env and .env.<name> in the project directory (not .env.example)."""
    out = []
    for p in sorted(directory.glob(".env*")):
        if p.name == ".env.example" or not p.is_file() or p.suffix in (".bak", ".swp"):
            continue
        env = dotenv_values(p)
        n = 0
        while env.get(f"NODE_{n + 1}_IP"):
            n += 1
        out.append(EnvFile(p, (env.get("CLUSTER_NAME") or "").strip() or "?", n))
    return out


def load_state(path: Optional[Path]) -> WizardState:
    if path is None or not path.exists():
        st = WizardState()
        st.set_topology("single")
        return st
    raw = {k: (v or "").strip() for k, v in dotenv_values(path).items()}
    known = {k: v for k, v in raw.items() if k in DEFAULTS}
    st = WizardState(known, extra={k: v for k, v in raw.items() if k not in DEFAULTS})
    if st.v("GRAFANA_LB_IP") == "":
        st.values["GRAFANA_LB_IP"] = "auto"
    st.load_nodes(raw)
    st.live = cluster_info(st.v("CLUSTER_NAME"))
    st.existing_cluster = st.live is not None
    return st


# ---------------------------------------------------------------------------
# Existing cluster: version, nodes, upgrade targets
# ---------------------------------------------------------------------------

@dataclass
class ClusterInfo:
    version: str                                          # control plane (API server), e.g. v1.35.9
    nodes: Dict[str, str] = field(default_factory=dict)   # node name → kubelet version

    @property
    def partial_upgrade(self) -> bool:
        """Some kubelets are behind the control plane (interrupted upgrade)."""
        return any(v != self.version for v in self.nodes.values())


def _kubectl(name: str, *args: str) -> Optional[str]:
    kubeconfig = Path(f"/root/.kube/{name}")
    try:
        if not os.access(kubeconfig, os.R_OK):
            return None
        r = subprocess.run(["kubectl", "--request-timeout=4s", *args], capture_output=True, text=True,
                           timeout=8, env={**os.environ, "KUBECONFIG": str(kubeconfig)})
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def cluster_info(name: str) -> Optional[ClusterInfo]:
    """Version and nodes of the cluster with this name (bootstrap kubeconfig), None if unreachable."""
    if not name:
        return None
    raw = _kubectl(name, "get", "--raw", "/version")
    if raw is None:
        return None
    try:
        info = ClusterInfo(json.loads(raw)["gitVersion"])
        for n in json.loads(_kubectl(name, "get", "nodes", "-o", "json") or "{}").get("items", []):
            info.nodes[n["metadata"]["name"]] = n["status"]["nodeInfo"]["kubeletVersion"]
        return info
    except (ValueError, KeyError):
        return None


def latest_patch(minor: int) -> Optional[str]:
    """Newest kubeadm v1.<minor>.x in pkgs.k8s.io, None if the minor is not released / unreachable."""
    url = f"https://pkgs.k8s.io/core:/stable:/v1.{minor}/deb/Packages"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            packages = r.read().decode()
    except Exception:
        return None
    found = re.findall(r"^Package: kubeadm\n(?:.+\n)*?Version: (\d+)\.(\d+)\.(\d+)-", packages, re.M)
    if not found:
        return None
    return "v%s.%s.%s" % max(tuple(map(int, v)) for v in found)


def upgrade_targets(state: WizardState) -> List[Tuple[str, str, bool]]:
    """(version, label, available) for `fun-kube upgrade`: same-minor patch and next minor.
    Needs network access to pkgs.k8s.io; call it off the UI thread."""
    live = state.live
    if not live:
        return []
    try:
        cur_minor = versions.k8s_minor(live.version)
    except ValueError:
        return []
    out: List[Tuple[str, str, bool]] = []
    if live.partial_upgrade:
        out.append((live.version, f"{live.version} — resume the interrupted upgrade (some nodes are behind)", True))
    cur = tuple(int(x) for x in live.version.lstrip("v").split(".")[:3])
    for minor, kind in ((cur_minor, "patch"), (cur_minor + 1, "minor")):
        target = latest_patch(minor)
        if not target or tuple(int(x) for x in target.lstrip("v").split(".")) <= cur:
            continue
        bad = versions.incompatible_addons(target, state.installed_addons())
        if bad:
            names = ", ".join(bad).replace("supporta", "supports")
            out.append((target, f"{target} — {kind} upgrade, not tested with: {names}", False))
        else:
            tested = " (tested by Fun-Kube)" if target == versions.K8S_VERSION else ""
            out.append((target, f"{target} — {kind} upgrade{tested}", True))
    return out


# Fields `up` cannot change on an existing cluster (see preflight.check_existing_cluster)
LOCKED_ON_EXISTING = {"CLUSTER_NAME", "POD_CIDR", "SERVICE_CIDR", "KEEPALIVED_VIP", "K8S_VERSION"}


def validate(state: WizardState) -> Tuple[List[str], List[str]]:
    """Validate with config.load() — the same code path used by `up`. Returns (errors, warnings)."""
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write(state.render())
        tmp = Path(f.name)
    try:
        cfg = load_config(tmp)
        return [], list(cfg.config_warnings)
    except ConfigError as e:
        msg = str(e).replace("Errori di configurazione:", "").strip()
        return [line.strip(" •") for line in msg.split("\n  • ") if line.strip()], []
    finally:
        tmp.unlink(missing_ok=True)


def save(state: WizardState, path: Path) -> Path:
    """Write the .env (backup of the previous version as <file>.bak)."""
    if path.exists():
        path.with_name(path.name + ".bak").write_text(path.read_text())
    path.write_text(state.render())
    path.chmod(0o600)
    return path


def ssh_keys() -> List[str]:
    """Private keys in ~/.ssh that have a matching .pub."""
    home = Path.home() / ".ssh"
    return sorted(str(p.with_suffix("")) for p in home.glob("*.pub") if p.with_suffix("").exists())


# ---------------------------------------------------------------------------
# Per-step checks (English messages for the wizard; config.load stays the final check)
# ---------------------------------------------------------------------------

STEPS = ["Cluster", "Nodes", "Access & network", "Add-ons", "Monitoring", "Review"]


def step_errors(state: WizardState) -> Dict[str, List[str]]:
    import ipaddress
    import re
    from ..config import _parse_ip_pool

    errs: Dict[str, List[str]] = {s: [] for s in STEPS}
    v, on = state.v, state.on

    name = v("CLUSTER_NAME")
    if not name:
        errs["Cluster"].append("Cluster name is required.")
    elif not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", name):
        errs["Cluster"].append("Cluster name: lowercase letters, digits and '-' only (e.g. prod-k8s-01).")

    nodes = state.cps + state.workers
    ips, hosts = [], []
    for i, n in enumerate(nodes, 1):
        role = "control-plane" if i <= len(state.cps) else "worker"
        try:
            ipaddress.ip_address(n.ip.strip())
        except ValueError:
            errs["Nodes"].append(f"Node {i} ({role}): invalid or missing IP address.")
        if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", n.hostname.strip()):
            errs["Nodes"].append(f"Node {i} ({role}): hostname required (lowercase, digits, '-').")
        ips.append(n.ip.strip())
        hosts.append(n.hostname.strip())
    if len(set(ips)) != len(ips):
        errs["Nodes"].append("Duplicate IP addresses.")
    if len(set(hosts)) != len(hosts):
        errs["Nodes"].append("Duplicate hostnames.")

    if not on("LOCAL_NODE"):
        key = os.path.expanduser(v("SSH_KEY_PATH"))
        if not key:
            errs["Access & network"].append("SSH private key path is required.")
        elif not Path(key).exists():
            errs["Access & network"].append(f"SSH key not found: {key}")
    for key in ("POD_CIDR", "SERVICE_CIDR"):
        try:
            ipaddress.ip_network(v(key), strict=False)
        except ValueError:
            errs["Access & network"].append(f"{key}: invalid CIDR (e.g. 172.16.0.0/16).")
    if state.topology == "ha":
        try:
            vip = str(ipaddress.ip_address(v("KEEPALIVED_VIP")))
            if vip in ips:
                errs["Access & network"].append("The VIP must be a free IP, not a node address.")
        except ValueError:
            errs["Access & network"].append("Virtual IP (VIP) is required for high availability.")

    if on("METALLB_ENABLED"):
        try:
            _parse_ip_pool(v("METALLB_IP_POOL"))
        except ValueError:
            errs["Add-ons"].append("MetalLB pool: use a range (10.0.0.200-10.0.0.220) or a CIDR (10.0.0.192/28).")
    if on("INGRESS_ENABLED") and v("INGRESS_TYPE") == "nginx-proxy-manager" and not v("NPM_DB_PASSWORD"):
        errs["Add-ons"].append("Nginx Proxy Manager database password is required.")

    if state.upgrade_to:
        mismatch = state.upgrade_node_mismatch()
        if mismatch:
            errs["Cluster"].append(f"The upgrade needs the node list to match the cluster ({mismatch}). "
                                   "Add nodes with provision first, then upgrade.")

    if on("MONITORING_ENABLED"):
        if not state.monitoring_storage_ok():
            errs["Monitoring"].append("Monitoring needs persistent storage: enable Longhorn (Add-ons) "
                                      "or set a StorageClass in the advanced options.")
        if on("GRAFANA_TRAEFIK_INGRESS") and not v("GRAFANA_DOMAIN"):
            errs["Monitoring"].append("The Traefik virtual host needs a Grafana domain.")
    return errs
