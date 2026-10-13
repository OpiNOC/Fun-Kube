#!/usr/bin/env python3
"""
Imposta le opzioni che espongono le metriche del control plane fuori da 127.0.0.1.

  metrics_config.py clusterconfig <in.yaml> <patch.json>   # ClusterConfiguration (kubeadm-config)
  metrics_config.py kubeproxy     <in.yaml> <patch.json>   # config.conf (ConfigMap kube-proxy)

Scrive in <patch.json> una merge patch per il ConfigMap e stampa "changed" o "ok".
Eseguito sulla bootstrap con l'interprete di Ansible (PyYAML disponibile).
"""
import json
import sys

import yaml

CONTROL_PLANE_ARGS = {
    "controllerManager": {"bind-address": "0.0.0.0"},
    "scheduler": {"bind-address": "0.0.0.0"},
}
ETCD_ARGS = {"listen-metrics-urls": "http://0.0.0.0:2381"}
KUBE_PROXY_METRICS = "0.0.0.0:10249"


def set_args(section: dict, wanted: dict) -> bool:
    """extraArgs: dict (kubeadm v1beta3) o lista di {name, value} (v1beta4)."""
    changed = False
    args = section.get("extraArgs")
    if isinstance(args, dict):
        for k, v in wanted.items():
            if args.get(k) != v:
                args[k] = v
                changed = True
        return changed
    args = args or []
    for k, v in wanted.items():
        entry = next((a for a in args if a.get("name") == k), None)
        if entry is None:
            args.append({"name": k, "value": v})
            changed = True
        elif entry.get("value") != v:
            entry["value"] = v
            changed = True
    section["extraArgs"] = args
    return changed


def main() -> int:
    mode, src, out = sys.argv[1:4]
    doc = yaml.safe_load(open(src)) or {}
    changed = False
    if mode == "clusterconfig":
        for comp, wanted in CONTROL_PLANE_ARGS.items():
            sec = doc.get(comp) or {}
            changed |= set_args(sec, wanted)
            doc[comp] = sec
        local = (doc.setdefault("etcd", {}) or {}).setdefault("local", {})
        if local is None:
            local = doc["etcd"]["local"] = {}
        changed |= set_args(local, ETCD_ARGS)
        key = "ClusterConfiguration"
    elif mode == "kubeproxy":
        if doc.get("metricsBindAddress") != KUBE_PROXY_METRICS:
            doc["metricsBindAddress"] = KUBE_PROXY_METRICS
            changed = True
        key = "config.conf"
    else:
        raise SystemExit(f"modo sconosciuto: {mode}")
    json.dump({"data": {key: yaml.safe_dump(doc, default_flow_style=False, sort_keys=False)}},
              open(out, "w"))
    print("changed" if changed else "ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
