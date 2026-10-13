#!/usr/bin/env python3
"""
Verifica che Longhorn possa prenotare lo spazio per i volumi del monitoring PRIMA
dell'installazione (altrimenti i pod restano in Init:0/1 con volumi faulted).

  longhorn_capacity.py <replicas> <namespace> <pvc>=<bytes> [<pvc>=<bytes> ...]

Considera solo i PVC non ancora esistenti. Regola di scheduling Longhorn per disco:
  storageScheduled + size <= (storageMaximum - storageReserved) * overProvisioning%
Le repliche di un volume vanno su nodi diversi (anti-affinity di default).
Exit 0 = spazio sufficiente, 1 = insufficiente (dettaglio su stdout).
"""
import json
import subprocess
import sys

GiB = 1024 ** 3


def kubectl_json(*args):
    out = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def main() -> int:
    replicas = int(sys.argv[1])
    namespace = sys.argv[2]
    wanted = {k: int(v) for k, v in (a.split("=", 1) for a in sys.argv[3:])}

    try:
        existing = {p["metadata"]["name"] for p in kubectl_json("get", "pvc", "-n", namespace)["items"]}
    except subprocess.CalledProcessError:
        existing = set()
    todo = sorted(((n, b) for n, b in wanted.items() if n not in existing), key=lambda x: -x[1])
    if not todo:
        print("Volumi del monitoring già presenti: nessuna nuova prenotazione su Longhorn.")
        return 0

    settings = {s["metadata"]["name"]: s.get("value")
                for s in kubectl_json("get", "settings.longhorn.io", "-n", "longhorn-system")["items"]}
    overprov = int(settings.get("storage-over-provisioning-percentage") or 100)

    nodes = {}
    for n in kubectl_json("get", "nodes.longhorn.io", "-n", "longhorn-system")["items"]:
        if not n.get("spec", {}).get("allowScheduling", True):
            continue
        free = 0
        for disk, st in (n.get("status", {}).get("diskStatus") or {}).items():
            spec = (n.get("spec", {}).get("disks") or {}).get(disk, {})
            if not spec.get("allowScheduling", True):
                continue
            maximum = int(st.get("storageMaximum", 0))
            reserved = int(spec.get("storageReserved", 0))
            scheduled = int(st.get("storageScheduled", 0))
            free = max(free, int((maximum - reserved) * overprov / 100) - scheduled)
        nodes[n["metadata"]["name"]] = free

    report = ", ".join(f"{k} {v / GiB:.1f}GiB" for k, v in sorted(nodes.items()))
    if len(nodes) < replicas:
        print(f"Nodi Longhorn schedulabili: {len(nodes)}, repliche richieste: {replicas}.")
        return 1
    for name, size in todo:          # greedy: ogni replica sul nodo con più spazio
        best = sorted(nodes, key=lambda k: -nodes[k])[:replicas]
        if any(nodes[k] < size for k in best):
            need = sum(b for _, b in todo) * replicas / GiB
            print(f"Spazio Longhorn insufficiente per {name} ({size / GiB:.0f}GiB x {replicas} repliche).\n"
                  f"  Totale richiesto: ~{need:.0f}GiB. Prenotabile per nodo (over-provisioning "
                  f"{overprov}%): {report}.\n"
                  f"  Aggiungere spazio ai dischi Longhorn o ridurre PROM_STORAGE / LOKI_STORAGE / "
                  f"GRAFANA_STORAGE / ALERTMANAGER_STORAGE nel .env.")
            return 1
        for k in best:
            nodes[k] -= size
    print(f"Spazio Longhorn sufficiente ({len(todo)} volumi x {replicas} repliche). Prenotabile: {report}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
