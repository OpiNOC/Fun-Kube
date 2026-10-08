#!/usr/bin/env python3
"""
Riscarica i manifest vendored in ansible/roles/*/files/ secondo le versioni
definite in fun_kube/versions.py.

Uso:  ./scripts/vendor-manifests.py
Poi verificare con  git diff --stat  e committare insieme a versions.py.
"""
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fun_kube import versions  # noqa: E402

ROLES = ROOT / "ansible" / "roles"


def _metrics_server_insecure_tls(body: str) -> str:
    # Su kubeadm i kubelet usano certificati self-signed: senza questo flag
    # kubectl top non funziona. Va nel manifest (non in un patch post-apply,
    # che verrebbe annullato dall'apply al run successivo).
    out = re.sub(r"^(\s+)- --secure-port=10250$",
                 r"\1- --secure-port=10250\n\1- --kubelet-insecure-tls",
                 body, count=1, flags=re.M)
    if "--kubelet-insecure-tls" not in out:
        raise ValueError("riga --secure-port=10250 non trovata: aggiornare il patch")
    return out


def _longhorn_check_replicas(body: str) -> str:
    # ansible/playbooks/longhorn.yml sostituisce questa riga (ConfigMap
    # longhorn-storageclass) con le repliche calcolate per il cluster.
    if body.count('      numberOfReplicas: "3"') != 1:
        raise ValueError('riga numberOfReplicas: "3" della ConfigMap longhorn-storageclass '
                         'non trovata (o duplicata): aggiornare longhorn.yml')
    return body


# Modifiche/verifiche applicate ai manifest dopo il download
PATCHES = {
    "metrics-server/files/components.yaml": _metrics_server_insecure_tls,
    "longhorn/files/longhorn.yaml": _longhorn_check_replicas,
}


def main() -> int:
    failed = 0
    for rel, url in versions.VENDORED_MANIFESTS:
        dest = ROLES / rel
        try:
            with urllib.request.urlopen(url, timeout=60) as r:
                body = r.read().decode()
            if rel in PATCHES:
                body = PATCHES[rel](body)
        except Exception as e:
            print(f"  ✗ {rel}: {e}")
            failed += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"# Vendored da scripts/vendor-manifests.py — {url}\n" + body)
        print(f"  ✓ {rel}  ({len(body.splitlines())} righe)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
