"""
Parsing e validazione del file .env.
Unico punto di accesso alla configurazione del cluster.
"""
from __future__ import annotations

import os
import ipaddress
from dataclasses import dataclass
from pathlib import Path
from typing import List, Literal

from dotenv import dotenv_values


class ConfigError(Exception):
    pass


@dataclass
class NodeConfig:
    ip: str
    role: Literal["control-plane", "worker"]
    hostname: str


@dataclass
class KeepalivedConfig:
    enabled: bool
    vip: str
    interface: str


@dataclass
class MetalLBConfig:
    enabled: bool
    ip_pool: str  # es. "10.0.0.200-10.0.0.220"


@dataclass
class IngressConfig:
    enabled: bool
    type: Literal["traefik", "nginx-proxy-manager"]


@dataclass
class LonghornConfig:
    enabled: bool
    rwx: bool
    ui_nodeport: int  # 0 = disabilitato, altrimenti porta NodePort (es. 30080)
    version: str


Topology = Literal["single-node", "single-cp", "ha"]


@dataclass
class ClusterConfig:
    cluster_name: str
    nodes: List[NodeConfig]
    ssh_user: str
    ssh_key_path: Path
    k8s_version: str
    pod_cidr: str
    service_cidr: str
    cni: str
    keepalived: KeepalivedConfig
    metallb: MetalLBConfig
    ingress: IngressConfig
    longhorn: LonghornConfig
    topology: Topology
    output_dir: Path
    log_level: str
    cert_manager_version: str
    api_server_extra_sans: List[str]
    local_node: bool
    cluster_timezone: str

    @property
    def control_planes(self) -> List[NodeConfig]:
        return [n for n in self.nodes if n.role == "control-plane"]

    @property
    def workers(self) -> List[NodeConfig]:
        return [n for n in self.nodes if n.role == "worker"]

    @property
    def first_cp(self) -> NodeConfig:
        return self.control_planes[0]

    @property
    def untaint_cp(self) -> bool:
        """True se non ci sono worker dedicati: i nodi CP devono anche schedulare workload."""
        return len(self.workers) == 0

    @property
    def api_endpoint(self) -> str:
        """Endpoint usato da kubeadm come --control-plane-endpoint.
        In HA è il VIP keepalived; altrimenti è l'IP del primo CP."""
        if self.topology == "ha":
            return self.keepalived.vip
        return self.first_cp.ip


# ---------------------------------------------------------------------------
# Entry point pubblico
# ---------------------------------------------------------------------------

def load(env_file: Path) -> ClusterConfig:
    """Carica, parsifica e valida il file .env. Ritorna ClusterConfig."""
    if not env_file.exists():
        raise ConfigError(f"File .env non trovato: {env_file}")

    env = dotenv_values(env_file)

    nodes = _parse_nodes(env)
    topology = _detect_topology(nodes)
    keepalived = _parse_keepalived(env, topology)
    local_node = _bool(env, "LOCAL_NODE")

    # SSH credentials: obbligatori solo se non local_node
    if local_node:
        ssh_user = env.get("SSH_USER", "root").strip()
        ssh_key_path = Path("")
    else:
        ssh_user = _require(env, "SSH_USER")
        ssh_key_path = Path(os.path.expanduser(_require(env, "SSH_KEY_PATH")))

    extra_sans = _parse_extra_sans(env)
    # In local_node aggiungiamo 127.0.0.1 automaticamente per comodità
    if local_node and "127.0.0.1" not in extra_sans:
        extra_sans = extra_sans + ["127.0.0.1"]

    cfg = ClusterConfig(
        cluster_name=_require(env, "CLUSTER_NAME"),
        nodes=nodes,
        ssh_user=ssh_user,
        ssh_key_path=ssh_key_path,
        k8s_version=env.get("K8S_VERSION", "latest"),
        pod_cidr=_require(env, "POD_CIDR"),
        service_cidr=env.get("SERVICE_CIDR", "10.96.0.0/12"),
        cni=env.get("CNI", "calico").lower(),
        keepalived=keepalived,
        metallb=MetalLBConfig(
            enabled=_bool(env, "METALLB_ENABLED"),
            ip_pool=env.get("METALLB_IP_POOL", ""),
        ),
        ingress=_parse_ingress(env),
        longhorn=LonghornConfig(
            enabled=_bool(env, "LONGHORN_ENABLED"),
            rwx=_bool(env, "LONGHORN_RWX"),
            ui_nodeport=int(env.get("LONGHORN_UI_NODEPORT", "0") or "0"),
            version=env.get("LONGHORN_VERSION", "").strip(),
        ),
        topology=topology,
        output_dir=Path(env.get("OUTPUT_DIR", "./output")),
        log_level=env.get("LOG_LEVEL", "info").lower(),
        cert_manager_version=env.get("CERT_MANAGER_VERSION", "v1.17.2"),
        api_server_extra_sans=extra_sans,
        local_node=local_node,
        cluster_timezone=env.get("CLUSTER_TIMEZONE", "Europe/Rome"),
    )

    _validate(cfg)
    return cfg


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _require(env: dict, key: str) -> str:
    val = env.get(key, "").strip()
    if not val:
        raise ConfigError(f"Variabile obbligatoria mancante o vuota: {key}")
    return val


def _bool(env: dict, key: str, default: bool = False) -> bool:
    val = env.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("true", "1", "yes")


def _parse_nodes(env: dict) -> List[NodeConfig]:
    nodes = []
    i = 1
    while True:
        ip = env.get(f"NODE_{i}_IP", "").strip()
        if not ip:
            # Controlla se ci sono nodi definiti DOPO questo gap (es. NODE_3 mancante ma NODE_4 presente)
            for j in range(i + 1, i + 20):
                if env.get(f"NODE_{j}_IP", "").strip():
                    raise ConfigError(
                        f"Gap nella sequenza nodi: NODE_{i}_IP non è definito ma NODE_{j}_IP sì.\n"
                        f"  I nodi vengono letti in sequenza — rinumera i nodi senza buchi."
                    )
            break

        role = env.get(f"NODE_{i}_ROLE", "").strip().lower()
        if not role:
            raise ConfigError(
                f"NODE_{i}_ROLE non definito (NODE_{i}_IP={ip}).\n"
                f"  Valori validi: control-plane | worker"
            )
        if role not in ("control-plane", "worker"):
            raise ConfigError(
                f"NODE_{i}_ROLE deve essere 'control-plane' o 'worker', trovato: '{role}'"
            )
        hostname = env.get(f"NODE_{i}_HOSTNAME", "").strip()
        if not hostname:
            raise ConfigError(
                f"NODE_{i}_HOSTNAME non definito (NODE_{i}_IP={ip}).\n"
                f"  Ogni nodo deve avere un hostname univoco."
            )
        nodes.append(NodeConfig(ip=ip, role=role, hostname=hostname))
        i += 1
    if not nodes:
        raise ConfigError(
            "Nessun nodo definito. Aggiungi almeno NODE_1_IP, NODE_1_ROLE, NODE_1_HOSTNAME."
        )
    return nodes


def _detect_topology(nodes: List[NodeConfig]) -> Topology:
    cps = [n for n in nodes if n.role == "control-plane"]
    workers = [n for n in nodes if n.role == "worker"]

    if len(cps) == 0:
        raise ConfigError("Nessun nodo control-plane definito.")
    if len(cps) == 2:
        raise ConfigError(
            "2 control-plane non è una topologia supportata. Usa 1 (single/mononodo) o 3+ (HA)."
        )
    if len(cps) == 1 and len(workers) == 0:
        return "single-node"
    if len(cps) == 1:
        return "single-cp"
    return "ha"


def _parse_keepalived(env: dict, topology: Topology) -> KeepalivedConfig:
    enabled = _bool(env, "KEEPALIVED_ENABLED")
    if topology == "ha" and not enabled:
        raise ConfigError(
            "La topologia HA richiede KEEPALIVED_ENABLED=true e KEEPALIVED_VIP."
        )
    return KeepalivedConfig(
        enabled=enabled,
        vip=env.get("KEEPALIVED_VIP", "").strip(),
        interface=env.get("KEEPALIVED_INTERFACE", "eth0").strip(),
    )


def _parse_extra_sans(env: dict) -> List[str]:
    raw = env.get("API_SERVER_EXTRA_SANS", "").strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _parse_ingress(env: dict) -> IngressConfig:
    enabled = _bool(env, "INGRESS_ENABLED")
    ingress_type = env.get("INGRESS_TYPE", "traefik").strip().lower()
    if ingress_type not in ("traefik", "nginx-proxy-manager"):
        raise ConfigError(
            f"INGRESS_TYPE deve essere 'traefik' o 'nginx-proxy-manager', trovato: '{ingress_type}'"
        )
    return IngressConfig(enabled=enabled, type=ingress_type)


# ---------------------------------------------------------------------------
# Validazione
# ---------------------------------------------------------------------------

def _validate(cfg: ClusterConfig) -> None:
    errors: List[str] = []

    # local_node: solo single-node
    if cfg.local_node and cfg.topology != "single-node":
        errors.append(
            "LOCAL_NODE=true è compatibile solo con topologia single-node "
            "(1 control-plane, nessun worker)."
        )

    # IP duplicati
    ips = [n.ip for n in cfg.nodes]
    if len(ips) != len(set(ips)):
        errors.append("IP duplicati tra i nodi.")

    # Hostname duplicati
    hostnames = [n.hostname for n in cfg.nodes]
    if len(hostnames) != len(set(hostnames)):
        errors.append("Hostname duplicati tra i nodi.")

    # SSH key (non richiesta in local_node)
    if not cfg.local_node and not cfg.ssh_key_path.exists():
        errors.append(f"SSH_KEY_PATH non trovato: {cfg.ssh_key_path}")

    # Keepalived VIP obbligatorio in HA
    if cfg.topology == "ha" and not cfg.keepalived.vip:
        errors.append("KEEPALIVED_VIP è obbligatorio per topologia HA.")

    # MetalLB IP pool obbligatorio se abilitato
    if cfg.metallb.enabled and not cfg.metallb.ip_pool:
        errors.append("METALLB_IP_POOL è obbligatorio se METALLB_ENABLED=true.")

    # Controlli CIDR
    try:
        pod_net = ipaddress.ip_network(cfg.pod_cidr, strict=False)
        svc_net = ipaddress.ip_network(cfg.service_cidr, strict=False)

        if pod_net.overlaps(svc_net):
            errors.append(
                f"POD_CIDR {cfg.pod_cidr} si sovrappone a SERVICE_CIDR {cfg.service_cidr}."
            )

        if cfg.metallb.enabled and cfg.metallb.ip_pool:
            for ip in _expand_ip_pool(cfg.metallb.ip_pool):
                if ip in pod_net:
                    errors.append(
                        f"METALLB_IP_POOL contiene {ip} che è dentro POD_CIDR {cfg.pod_cidr}."
                    )
                    break
                if ip in svc_net:
                    errors.append(
                        f"METALLB_IP_POOL contiene {ip} che è dentro SERVICE_CIDR {cfg.service_cidr}."
                    )
                    break

    except ValueError as e:
        errors.append(f"CIDR non valido: {e}")

    if errors:
        raise ConfigError(
            "Errori di configurazione:\n" + "\n".join(f"  • {e}" for e in errors)
        )


def _expand_ip_pool(pool: str) -> List[ipaddress.IPv4Address]:
    """Espande un range tipo '10.0.0.200-10.0.0.220' in lista di indirizzi."""
    if "-" not in pool:
        return [ipaddress.ip_address(pool)]
    start_str, end_str = pool.split("-", 1)
    start = ipaddress.ip_address(start_str.strip())
    end = ipaddress.ip_address(end_str.strip())
    result = []
    current = start
    while current <= end:
        result.append(current)
        current += 1
    return result
