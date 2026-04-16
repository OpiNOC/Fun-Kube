Fun-Kube — Kubernetes Cluster Provisioning Tool

Obiettivo

Costruire un tool che permetta di creare cluster Kubernetes in modo automatizzato
partendo da una macchina bootstrap con accesso SSH ai nodi.

Il tool deve:
  • richiedere il minimo numero di comandi all'utente (idealmente 1 comando)
  • permettere di definire ruoli dei nodi (control-plane / worker)
  • installare sempre l'ultima versione stabile di Kubernetes (override possibile)
  • usare kubeadm come backend
  • installare Calico come CNI
  • supportare topologie flessibili: cluster mononodo, single control-plane, multi control-plane HA
  • supportare addon: MetalLB, Ingress (Traefik o Nginx Proxy Manager), Longhorn
  • eseguire controlli di prerequisiti prima del deploy
  • produrre output utile per troubleshooting e scaling

Sistema target nodi: Ubuntu 22.04 / 24.04
Macchina bootstrap: Ubuntu 22.04 / 24.04 (esterna al cluster, con accesso SSH ai nodi)

⸻

Stato del progetto

  Componente                     Stato
  ─────────────────────────────────────────────────────────
  .env.example (config template) ✓ completo
  fun_kube/config.py             ✓ completo (parsing, validazione, topologia)
  fun_kube/preflight.py          ✓ completo (SSH checks sui nodi)
  fun_kube/runner.py             ✓ completo (inventory, playbook sequence, kubeconfig)
  fun_kube/cli.py                ✓ completo (up, check-deps)
  fun_kube/deps.py               ✓ completo (verifica tool bootstrap machine)
  bootstrap-setup.sh             ✓ completo (setup bootstrap machine)
  ansible/ansible.cfg            ✓ completo
  ansible/playbooks/ (11)        ✓ scaffolding completo (generato, non testato)
  ansible/roles/ (10)            ✓ scaffolding completo (generato, non testato)
  README.md                      ✗ da fare
  Test reale su nodi             ✗ da fare

Prossimi passi (priorità):
  1. Scrivere README.md (quickstart, requisiti, esempi)
  2. Primo test reale — topologia mononodo su VM singola
  3. Fix bug emersi dal test (apt_key deprecato, run_once+register nel kubeadm role)
  4. Test topologia single control-plane
  5. Test topologia HA

⸻

Limitazioni note (v0.1.0, non bloccanti per ora)
  • apt_key module deprecato su Ubuntu 24.04: da migrare a get_url nei roles
    containerd e kubeadm
  • Join scripts salvati in /tmp/: se il primo CP si riavvia prima del join dei
    worker, vanno persi. Fix futuro: salvarli in /etc/kubernetes/
  • kubeadm role: run_once + register — da verificare il comportamento su play
    multi-host (la variabile potrebbe non propagarsi a tutti i nodi)

⸻

Filosofia
  • Configurazione dichiarativa (.env = source of truth)
  • Automazione idempotente (Ansible)
  • CLI semplice (Python + typer)
  • Modularità (addon attivabili/disattivabili)
  • Separazione tra cluster base e addon

⸻

Topologie supportate

  Topologia           CP    Worker   Keepalived
  ────────────────────────────────────────────
  Mononodo            1     0        no
  Single CP           1     N        no
  HA multi CP         3+    N        sì (VIP obbligatorio)

Rilevamento automatico dal numero di control-plane in .env:
  • 1 CP, 0 worker  → mononodo (taint NoSchedule rimosso automaticamente)
  • 1 CP, N worker  → single control-plane
  • 3+ CP           → HA con keepalived

⸻

Macchina bootstrap

Fun-Kube gira su una macchina esterna al cluster (laptop, jump host, VM di management)
che ha accesso SSH a tutti i nodi. NON è necessario installare nulla sui nodi prima
di eseguire fun-kube: ci pensa il tool.

Setup una tantum:

  git clone https://github.com/OpiNOC/Fun-Kube
  cd Fun-Kube
  bash bootstrap-setup.sh

Tool installati da bootstrap-setup.sh:

  Tool                 Usato per
  ───────────────────────────────────────────────────────
  Python 3.10+         runtime del CLI fun-kube
  pip                  gestione dipendenze Python
  ansible              esecuzione playbook sui nodi
  community.general    collection Ansible usata dai roles
  kubectl              verifica stato cluster post-deploy
  helm                 installazione Traefik (se abilitato)
  ssh / scp            accesso SSH ai nodi, fetch kubeconfig
  git                  clone del repo

Verifica in qualsiasi momento:

  ./fun-kube check-deps            # status sintetico
  ./fun-kube check-deps --verbose  # con versioni

fun-kube up esegue check-deps automaticamente come step 0.

⸻

Struttura del progetto

Fun-Kube/
├── bootstrap-setup.sh        # setup una tantum macchina bootstrap
├── fun-kube                  # CLI entry point (eseguibile diretto)
├── pyproject.toml            # installabile via pip install -e .
├── fun_kube/
│   ├── __init__.py
│   ├── cli.py                # comandi: up, check-deps
│   ├── config.py             # parsing .env, validazione, topologia
│   ├── deps.py               # verifica tool sulla bootstrap machine
│   ├── preflight.py          # preflight checks SSH sui nodi
│   └── runner.py             # inventory generation + esecuzione Ansible
├── .env.example              # template configurazione (committato)
├── .env                      # configurazione locale (gitignored)
├── ansible/
│   ├── ansible.cfg           # roles_path, pipelining, forks, timeout
│   ├── playbooks/
│   │   ├── bootstrap.yml           # common + containerd + kubeadm su tutti i nodi
│   │   ├── keepalived.yml          # HA only
│   │   ├── kubeadm-init.yml        # init primo CP, salva join scripts
│   │   ├── control-plane-join.yml  # join CP aggiuntivi (HA only)
│   │   ├── worker-join.yml         # join workers
│   │   ├── calico.yml
│   │   ├── untaint-single-node.yml # mononodo only
│   │   ├── metallb.yml
│   │   ├── ingress.yml             # traefik o nginx-proxy-manager
│   │   └── longhorn.yml
│   └── roles/
│       ├── common/           # sysctl, moduli kernel, swap, nfs-common
│       ├── containerd/       # containerd.io + SystemdCgroup
│       ├── kubeadm/          # kubelet + kubeadm + kubectl (versione pinned o latest)
│       ├── keepalived/       # VIP MASTER/BACKUP
│       ├── calico/
│       ├── metallb/          # IPAddressPool + L2Advertisement
│       ├── traefik/          # Helm install
│       ├── nginx-proxy-manager/
│       └── longhorn/
├── output/                   # gitignored — generato da fun-kube up
│   ├── inventory.ini
│   ├── kubeconfig
│   └── cluster-info.txt
└── KIMI-PROMPTS/             # gitignored — prompt per Kimi AI

⸻

Configurazione cluster (.env)

  cp .env.example .env
  # editare .env con i propri nodi e parametri

Variabili principali (vedere .env.example per la lista completa):

  CLUSTER_NAME, NODE_N_IP/ROLE/HOSTNAME, SSH_USER, SSH_KEY_PATH
  K8S_VERSION, POD_CIDR, SERVICE_CIDR, CNI
  KEEPALIVED_ENABLED, KEEPALIVED_VIP, KEEPALIVED_INTERFACE
  METALLB_ENABLED, METALLB_IP_POOL
  INGRESS_ENABLED, INGRESS_TYPE
  LONGHORN_ENABLED, LONGHORN_RWX

CIDR da tenere non sovrapposti:
  POD_CIDR, SERVICE_CIDR, METALLB_IP_POOL — config.py lo verifica all'avvio.

⸻

CLI

  fun-kube up [.env]           # provisiona il cluster
    --dry-run                  # solo validazione, nessuna modifica
    --debug                    # output verboso (ansible -vv)
    --skip-checks              # salta preflight SSH (non consigliato)

  fun-kube check-deps          # verifica tool sulla bootstrap machine
    --verbose                  # mostra versioni

⸻

Flusso operativo (fun-kube up)

  0. check-deps          verifica tool sulla bootstrap machine
  1. Configurazione      parsing .env, validazione, rilevamento topologia
  2. Preflight SSH       checks su tutti i nodi (swap, moduli, porte, disco, ping)
  3. Provisioning        playbook Ansible in sequenza:
       bootstrap → [keepalived] → kubeadm-init → [cp-join] → [worker-join]
       → calico → [untaint] → [metallb] → [ingress] → [longhorn]
  4. Output              inventory.ini, kubeconfig, cluster-info.txt

⸻

Requisiti nodi
  • Ubuntu 22.04 o 24.04
  • CPU >= 2 core
  • RAM >= 2 GB (worker), >= 4 GB (control-plane raccomandato)
  • Disco >= 20 GB
  • swap disabilitato (o disabilitabile)
  • accesso SSH con chiave
  • sudo senza password

⸻

Considerazioni importanti
  • Idempotente: ogni run controlla lo stato prima di agire (kubeadm già init → skip)
  • K8s version "latest" risolta da dl.k8s.io/release/stable.txt e salvata in output
  • Logging su stdout con rich; output persistente in output/cluster-info.txt
  • MetalLB pool deve essere sulla subnet dei nodi, fuori da POD_CIDR e SERVICE_CIDR
  • Keepalived VIP = IP libero sulla subnet dei nodi (non assegnato ad alcun nodo)
  • Ansible gira dalla dir ansible/ così ansible.cfg viene rilevato automaticamente
