# Fun-Kube

Tool per installare cluster Kubernetes in modo completamente automatizzato, partendo da macchine Ubuntu 24.04 pulite.

Un solo comando. Nessun prerequisito sui nodi oltre a Ubuntu e SSH.

**Versione 1.0.0** — novità e limitazioni note in [CHANGELOG.md](CHANGELOG.md).

```bash
git clone https://github.com/OpiNOC/Fun-Kube /root/Fun-Kube
cd /root/Fun-Kube
cp .env.example .env
# modifica .env
./fun-kube up
```

Oppure, con il wizard interattivo:

```bash
./fun-kube --wizard
```

---

## Wizard interattivo

`./fun-kube --wizard` (o `./fun-kube wizard`) apre un'interfaccia a terminale, stile k9s, usabile
solo da tastiera (anche via SSH). Compila il `.env` passo per passo e alla fine può lanciare `up`.

- **Più cluster**: all'avvio si sceglie un `.env` esistente da modificare o se ne crea uno nuovo
  (`.env` per il cluster principale, `.env.<nome>` per gli altri).
- **Passi**: Cluster → Nodes → Access & network → Add-ons → Monitoring → Review. Ogni passo ha una
  breve spiegazione, ogni campo un suggerimento; il pannello in basso spiega il campo selezionato.
- **Scelte a cascata**: compaiono solo le opzioni sensate date le scelte precedenti (es.
  LoadBalancer solo con MetalLB, Longhorn obbligatorio con Nginx Proxy Manager su più nodi,
  opzioni di Grafana secondo l'esposizione).
- **Opzioni avanzate** (porte, volumi, retention, CIDR…) nascoste finché non si preme `F2`:
  restano ai default.
- **Cluster già installato**: i campi che `up` non può cambiare (nome, CIDR, VIP, versione,
  topologia) sono bloccati; si possono aggiungere worker e addon.
- **Upgrade di Kubernetes** (cluster già installato): nel passo *Cluster* il wizard legge la
  versione in esecuzione e propone l'ultima patch della stessa minor e l'ultima della minor
  successiva (da pkgs.k8s.io), disabilitando quelle non compatibili con gli addon; se un upgrade
  è stato interrotto propone di riprenderlo. Alla fine lancia `fun-kube upgrade --to …`
  (dry-run o reale). Richiede che i nodi del `.env` coincidano con quelli del cluster: i nodi
  nuovi si aggiungono prima con *provision*.
- **Validazione** con le stesse regole di `up` prima di salvare; il `.env` precedente viene
  salvato come `<file>.bak`. Le variabili non gestite dal wizard e quelle già presenti nel file
  (anche di addon disattivati) vengono conservate.
- **Fine**: anteprima completa del `.env` (scorrevole: `Tab` fino all'anteprima, poi frecce e
  PgUp/PgDn), poi *Save*, *Save + dry-run* o *Save + provision* — oppure, con un upgrade
  selezionato, *Save + upgrade dry-run* / *Save + upgrade*.
- Test della connessione SSH a tutti i nodi direttamente dal passo *Access & network*.

Tasti: `Tab`/`Shift+Tab` campi · `Ctrl+N`/`Ctrl+B` passo successivo/precedente · `F2` avanzate ·
`Ctrl+S` riepilogo · `Ctrl+Q` esci. L'interfaccia del wizard è in inglese.

---

## Topologie supportate

| Topologia | Control-plane | Worker | Note |
|---|---|---|---|
| **Local-node** | 1 (la bootstrap stessa) | 0 | Tutto gira sulla stessa macchina |
| **Mononodo** | 1 remoto | 0 | VM singola, fa tutto |
| **Single CP** | 1 | 1+ | CP dedicato + worker separati |
| **HA** | 3+ | 0+ | Alta disponibilità con keepalived VIP |

> **2 control-plane non è supportato.** Usa 1 (mononodo/single-cp) oppure 3+ (HA).

Se non definisci worker, i nodi control-plane vengono detaintati automaticamente e schedulano anche i workload applicativi.

---

## Cosa installa

**Cluster core** (sempre):
- containerd, kubelet, kubeadm, kubectl
- Calico CNI (con API server Calico: `kubectl get tiers`, risorse `projectcalico.org/v3`)
- metrics-server (`kubectl top nodes/pods` funziona subito)
- cert-manager
- Rinnovo automatico certificati (systemd timer mensile su ogni CP)
- keepalived (solo HA)
- local-path-provisioner + StorageClass default (solo mononodo)
- `alias k=kubectl` e autocompletamento kubectl configurati in `~/.bashrc` sulla bootstrap machine

**Addon opzionali** (abilitati via `.env`):

| Addon | Variabile | Note |
|---|---|---|
| MetalLB | `METALLB_ENABLED=true` | Load balancer per IP on-premise |
| Traefik | `INGRESS_TYPE=traefik` | Ingress controller, DaemonSet, LB o NodePort |
| Nginx Proxy Manager | `INGRESS_TYPE=nginx-proxy-manager` | Ingress con UI web, DaemonSet, LB o NodePort |
| Longhorn | `LONGHORN_ENABLED=true` | Storage distribuito, RWO e RWX |
| DN-essence | `DN_ESSENCE_ENABLED=true` *(default)* | DNS rewrite manager per CoreDNS, UI web inclusa |
| Monitoring | `MONITORING_ENABLED=true` | Prometheus, Alertmanager, Grafana, Loki (log + eventi), alert pronti |

> Solo uno tra Traefik e Nginx Proxy Manager può essere attivo alla volta.

---

## Versioni

Le versioni sono fissate in [`fun_kube/versions.py`](fun_kube/versions.py) (unica fonte di verità).
I manifest applicati con kubectl sono vendored nel repo (`ansible/roles/*/files/`): l'installazione
è riproducibile e non dipende da download dei manifest a runtime.

| Componente | Versione |
|---|---|
| Kubernetes (default, `K8S_VERSION`) | v1.35.9 |
| containerd.io (repo Docker) | 2.3.6 |
| Calico | v3.32.2 |
| cert-manager | v1.21.2 |
| metrics-server | v0.9.0 |
| local-path-provisioner | v0.0.37 |
| MetalLB | v0.16.0 |
| Longhorn | v1.12.1 |
| Traefik chart (default, `TRAEFIK_CHART_VERSION`) | 41.6.1 (Traefik v3.7) |
| Nginx Proxy Manager / MariaDB | 2.16.0 / 11.4 |
| Monitoring: kube-prometheus-stack / Loki / Alloy (chart) | 92.3.0 / 18.15.1 / 1.13.1 |
| Helm sulla bootstrap | 4.x |

Solo `K8S_VERSION` (`latest` = ultima stabile upstream), `TRAEFIK_CHART_VERSION` (chart >= 41) e
`DN_ESSENCE_VERSION` sono configurabili in `.env`.

Per aggiornare un componente: cambiare la versione in `versions.py`, eseguire
`./scripts/vendor-manifests.py` e committare `versions.py` insieme ai manifest aggiornati.

---

## Requisiti

### Bootstrap machine
Ubuntu 24.04 LTS. Può essere il tuo laptop, una VM di management, o il nodo stesso (modalità local-node).
Fun-Kube va eseguito come **root** (kubeconfig in `/root/.kube/`, `~/.bashrc` di root).

Tutte le dipendenze (Python, Ansible, kubectl, Helm) vengono **installate automaticamente** al primo `./fun-kube up`.

### Nodi del cluster
- Ubuntu 24.04 LTS (verificato dal preflight)
- CPU ≥ 2 core
- RAM ≥ 2 GB (worker), ≥ 4 GB (control-plane raccomandato)
- Disco ≥ 20 GB
- Accesso SSH con chiave dalla bootstrap machine
- Sudo senza password per l'utente SSH
- Orologio sincronizzato (NTP) — il preflight blocca scarti oltre 60s rispetto alla bootstrap
- Accesso a Internet (repository apt, registry delle immagini, GitHub/Helm)

**Non serve installare nulla sui nodi.** Fun-Kube ci pensa.

---

## Accesso SSH ai nodi

**1. Genera la chiave sulla bootstrap machine** (se non l'hai già):

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_rsa -N ""
```

**2. Aggiungi la chiave pubblica su ogni nodo.**

Copia il contenuto di `~/.ssh/id_rsa.pub` e appendilo a `/root/.ssh/authorized_keys` su ciascun nodo. Puoi farlo in fase di creazione della VM tramite console (Proxmox, ecc.), cloud-init, o qualsiasi sistema di provisioning — senza bisogno di accesso root interattivo.

**3. Verifica che la connessione funzioni:**

```bash
ssh -i ~/.ssh/id_rsa root@<ip-nodo> 'id'
```

**4. Imposta nel `.env`:**

```ini
SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa
```

---

## Configurazione per topologia

### Local-node (bootstrap = nodo)

```ini
CLUSTER_NAME=mylocal
LOCAL_NODE=true

NODE_1_IP=192.168.1.10
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=mylocal

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12
```

SSH_USER e SSH_KEY_PATH non servono. Ansible gira in locale.

---

### Mononodo (VM singola remota)

```ini
CLUSTER_NAME=mynode
NODE_1_IP=10.0.0.10
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=mynode

SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12
```

---

### Single CP + worker

```ini
CLUSTER_NAME=mycluster
NODE_1_IP=10.0.0.1
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=cp1

NODE_2_IP=10.0.0.11
NODE_2_ROLE=worker
NODE_2_HOSTNAME=worker1

NODE_3_IP=10.0.0.12
NODE_3_ROLE=worker
NODE_3_HOSTNAME=worker2

SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12
```

---

### HA (3 CP + worker)

```ini
CLUSTER_NAME=mycluster-ha
NODE_1_IP=10.0.0.1
NODE_1_ROLE=control-plane
NODE_1_HOSTNAME=cp1

NODE_2_IP=10.0.0.2
NODE_2_ROLE=control-plane
NODE_2_HOSTNAME=cp2

NODE_3_IP=10.0.0.3
NODE_3_ROLE=control-plane
NODE_3_HOSTNAME=cp3

NODE_4_IP=10.0.0.11
NODE_4_ROLE=worker
NODE_4_HOSTNAME=worker1

SSH_USER=root
SSH_KEY_PATH=~/.ssh/id_rsa

POD_CIDR=172.16.0.0/16
SERVICE_CIDR=10.96.0.0/12

KEEPALIVED_ENABLED=true
KEEPALIVED_VIP=10.0.0.100        # IP libero sulla subnet, non assegnato ad alcun nodo
KEEPALIVED_INTERFACE=           # vuoto = auto-detect (interfaccia con l'IP del nodo)
```

Il `KEEPALIVED_VIP` diventa l'**endpoint del cluster**: è l'IP usato da kubeadm come `--control-plane-endpoint` e presente nel kubeconfig. In caso di failover del CP attivo, il VIP si sposta automaticamente su un altro nodo e il cluster rimane raggiungibile.

```bash
export KUBECONFIG=/root/.kube/mycluster-ha
kubectl get nodes   # raggiungibile tramite VIP 10.0.0.100
```

Per verificare quale CP detiene il VIP in un dato momento:
```bash
./fun-kube diagnose   # sezione Keepalived mostra MASTER/BACKUP per ogni CP
```

---

## Configurazione addon

### MetalLB

```ini
METALLB_ENABLED=true
METALLB_IP_POOL=10.0.0.200-10.0.0.220   # range IP liberi sulla subnet dei nodi
```

Il pool deve essere sulla stessa subnet dei nodi (L2) e non sovrapporsi a `POD_CIDR` o `SERVICE_CIDR`. Fun-Kube verifica automaticamente le sovrapposizioni.

---

### Ingress — Traefik

```ini
INGRESS_ENABLED=true
INGRESS_TYPE=traefik
INGRESS_SERVICE_TYPE=auto         # auto = LoadBalancer se MetalLB attivo, altrimenti NodePort

TRAEFIK_LB_IP=                    # IP specifico dal pool MetalLB (vuoto = auto-assign)
TRAEFIK_HTTP_NODEPORT=30080       # usato solo se service type = NodePort
TRAEFIK_HTTPS_NODEPORT=30443
TRAEFIK_IS_DEFAULT_CLASS=true
TRAEFIK_DASHBOARD_HOST=           # es. traefik.miodominio.com — vuoto = solo port-forward
TRAEFIK_ACME_EMAIL=               # es. admin@miodominio.com — vuoto = guida in output/
```

Traefik viene installato come **DaemonSet** (un pod per nodo worker). Se `TRAEFIK_ACME_EMAIL` è impostato, vengono creati automaticamente i ClusterIssuer `letsencrypt-staging` e `letsencrypt-prod`. Altrimenti la guida per configurarli manualmente viene scritta in `output/traefik-letsencrypt-guide.txt`.

Dashboard: se `TRAEFIK_DASHBOARD_HOST` non è impostato, la dashboard è accessibile via:
```bash
kubectl port-forward -n traefik svc/traefik 9000:9000
# poi: http://localhost:9000/dashboard/
```

---

### Ingress — Nginx Proxy Manager

```ini
INGRESS_ENABLED=true
INGRESS_TYPE=nginx-proxy-manager
INGRESS_SERVICE_TYPE=auto

NPM_LB_IP=                        # IP specifico dal pool MetalLB (vuoto = auto-assign)
NPM_HTTP_NODEPORT=30080
NPM_HTTPS_NODEPORT=30443
NPM_ADMIN_NODEPORT=30081
NPM_DB_PASSWORD=cambia-questa-password
```

NPM viene installato come **DaemonSet** (un pod per nodo). Volumi: `NPM_DB_STORAGE=1Gi` (MariaDB)
e `NPM_DATA_STORAGE=2Gi` (configurazioni, certificati e log di accesso; da aumentare per proxy
con molto traffico). I volumi esistenti non vengono mai ridotti. La UI di amministrazione è accessibile sulla porta 81 (LB) o sulla `NPM_ADMIN_NODEPORT` (NodePort).

Login di default UI: `admin@example.com` / `changeme` — **cambiare al primo accesso.**

Su cluster multi-nodo richiede `LONGHORN_ENABLED=true` per lo storage RWX condiviso tra i pod. Su mononodo usa local-path.

---

### Longhorn

```ini
LONGHORN_ENABLED=true
LONGHORN_RWX=true                 # abilita StorageClass ReadWriteMany (longhorn-rwx)
LONGHORN_UI_NODEPORT=31080        # 0 = UI non esposta
```

Funziona su qualsiasi numero di nodi. Il numero di repliche viene calcolato automaticamente in base ai nodi schedulabili del cluster:

| Nodi schedulabili | Repliche Longhorn | Ridondanza |
|---|---|---|
| 1 (mononodo o CP untainted) | 1 | Nessuna — se il nodo va giù, i dati non sono accessibili |
| 2 | 2 | Parziale — tolera la perdita di 1 nodo |
| 3+ | 3 | Piena — tolera la perdita di 1 nodo senza interruzioni |

Fun-Kube mostra un avviso durante `up` se le repliche sono inferiori a 3.

**Mononodo**: local-path è sempre installato ed è la StorageClass default. Longhorn su un solo
nodo ha 1 replica (nessuna ridondanza in più di local-path) e circa 20 pod di overhead: ha
senso per backup/snapshot dei volumi, volumi RWX o se prevedi di aggiungere worker. Se lo
abiliti, la StorageClass default diventa `longhorn` (local-path resta disponibile).

Prerequisiti (`nfs-common`, `open-iscsi`) installati automaticamente su tutti i nodi.

### DN-essence

```ini
DN_ESSENCE_ENABLED=true              # abilitato di default
DN_ESSENCE_UI_NODEPORT=30880         # 0 = UI non esposta (accesso solo via port-forward)
# DN_ESSENCE_VERSION=                # vuoto = ultima versione dal registry OCI
```

DN-essence gestisce le regole di riscrittura DNS in CoreDNS in modo dichiarativo, tramite Custom Resources Kubernetes (`DNSRewrite`). È utile per risolvere problemi di **hairpin NAT** (domini pubblici che devono puntare a IP interni) e split-horizon DNS.

Offre tre interfacce:
- **UI web** — accessibile su `http://<node-ip>:<DN_ESSENCE_UI_NODEPORT>`
- **REST API** — endpoint CRUD per automazione
- **kubectl** — `kubectl get dnsrewrite -A`

Se `DN_ESSENCE_UI_NODEPORT=0`, la UI è raggiungibile solo tramite port-forward:
```bash
kubectl port-forward svc/dn-essence 8080:80 -n dn-essence
# poi: http://localhost:8080
```

Il controller sincronizza automaticamente CoreDNS (plugin `reload`) senza restart dei pod.

Per disabilitare completamente: `DN_ESSENCE_ENABLED=false`.

---

### Monitoring

```ini
MONITORING_ENABLED=true
GRAFANA_EXPOSE=auto        # loadbalancer (MetalLB) o nodeport
GRAFANA_LB_IP=auto
```

Installa nel namespace `monitoring` (versioni in `fun_kube/versions.py`):

| Componente | Ruolo |
|---|---|
| kube-prometheus-stack | Prometheus, Alertmanager, Grafana, kube-state-metrics, node-exporter, alert Kubernetes |
| Loki (monolitico) | archivio dei log su volume persistente |
| Alloy (DaemonSet) | log di tutti i pod, control-plane compresi |
| Alloy (1 replica) | eventi Kubernetes (Kubernetes li conserva solo ~1 ora) |
| ServiceMonitor Longhorn | metriche di volumi e dischi (se Longhorn è abilitato) |
| Dashboard "Log dei pod" | log per namespace/pod/container con ricerca, errori ed eventi |

**Esposizione di Grafana** — per default resta sulla rete dei nodi, non pubblicata:

| `GRAFANA_EXPOSE` | Accesso | A fine `up` viene mostrato |
|---|---|---|
| `auto` *(default)* | `loadbalancer` se c'è MetalLB, altrimenti `nodeport` | — |
| `loadbalancer` | IP MetalLB (`GRAFANA_LB_IP=auto` o un IP del pool), limitabile con `GRAFANA_ALLOWED_CIDRS` | `http://<IP assegnato>` |
| `nodeport` | `http://<IP nodo>:30300` (`GRAFANA_NODEPORT`) | URL con VIP o IP del CP |
| `clusterip` | solo dentro il cluster, da pubblicare con un proxy su `GRAFANA_DOMAIN` | nome interno per NPM: `kube-prometheus-stack-grafana.monitoring.svc.cluster.local:80` |
| `clusterip` + `GRAFANA_TRAEFIK_INGRESS=true` | virtual host Traefik su `GRAFANA_DOMAIN` (Let's Encrypt se `TRAEFIK_ACME_EMAIL`) | `https://<dominio>` |

`clusterip` senza dominio: solo `kubectl port-forward -n monitoring svc/kube-prometheus-stack-grafana 3000:80`.
`GRAFANA_DOMAIN` va impostato solo se un proxy pubblica Grafana con quel nome (è il `root_url`).
Prometheus e Alertmanager non vengono mai pubblicati (nessuna autenticazione): si usano con
`kubectl port-forward`.

**Storage**: con Longhorn crea la StorageClass `longhorn-monitoring` (max 2 repliche, `Retain`) e
verifica prima che ci sia spazio prenotabile. I default sono per un cluster piccolo/medio:
Prometheus 10Gi (retention 15 giorni / 8GB), Loki 10Gi (7 giorni), Grafana 1Gi, Alertmanager 1Gi
= 22Gi, cioè ~44 GiB su Longhorn con 2 repliche (valori per cluster grandi nel `.env.example`).
Sul mononodo usa local-path. Le dimensioni non si possono cambiare dopo l'installazione.
Lo spazio viene verificato già da `up --dry-run`: su un cluster nuovo con una stima (70% del
filesystem di `/var/lib` dei nodi, cioè il default di Longhorn), su un cluster esistente con i
dati reali di Longhorn.

**Metriche del control plane**: kubeadm le espone solo su `127.0.0.1`; con il monitoring
abilitato Fun-Kube le porta su `0.0.0.0` (scheduler, controller-manager, etcd, kube-proxy),
anche nella configurazione di kubeadm, così restano dopo `upgrade` e nei nuovi CP. Senza,
`KubeSchedulerDown`, `etcdMembersDown` e simili scatterebbero sempre.

**Installazione esistente** fatta a mano con la guida: viene adottata (stessi nomi di
release, namespace e StorageClass). `up` si ferma se le dimensioni dei volumi nel `.env` non
corrispondono a quelle esistenti, e rilegge la password di Grafana dal cluster.

`MONITORING_ENABLED=false` non disinstalla: la procedura è nel file di manutenzione.

---

## CIDR — regole di non sovrapposizione

| Variabile | Uso | Esempio |
|---|---|---|
| `POD_CIDR` | Indirizzi dei pod | `172.16.0.0/16` |
| `SERVICE_CIDR` | ClusterIP dei service | `10.96.0.0/12` |
| `METALLB_IP_POOL` | IP esterni LoadBalancer | `10.0.0.200-10.0.0.220` |

Fun-Kube verifica le sovrapposizioni all'avvio e blocca con un errore chiaro se le trova.

---

## Comandi

```bash
./fun-kube up [.env]          # provisiona il cluster
  --dry-run                   # valida configurazione e preflight (SSH, sola lettura): nessuna modifica
  --debug                     # output verboso (Ansible -vv)
  --skip-checks               # salta i preflight check SSH
  --yes / -y                  # salta la conferma interattiva

./fun-kube reset [.env]       # distrugge il cluster: arresto pulito dei workload, kubeadm reset
  --yes                       # salta la conferma interattiva
  --reboot                    # riavvia i nodi a fine reset (stato pulito garantito)
                              # Nota: agisce solo sui nodi. Sulla bootstrap machine
                              # rimangono kubeconfig, output/ e venv (non interferiscono con un nuovo up)

./fun-kube diagnose [.env]    # stato nodi (kubelet, containerd, disco, RAM, versioni) e addon,
                              # con gli alert attivi se il monitoring è installato

./fun-kube upgrade --to v1.36.5 [.env]   # aggiorna Kubernetes (kubeadm upgrade)
  --dry-run                   # solo controlli e piano, nessuna modifica
  --yes / -y                  # salta la conferma (non basta se l'upgrade ferma i workload)
  --allow-downtime            # accetta il fermo dei workload senza conferma interattiva
  --ignore-addon-compat       # procede anche con addon non testati sulla versione target

./fun-kube check-deps         # verifica tool sulla bootstrap machine
  --verbose                   # mostra le versioni

./fun-kube --version          # versione di Fun-Kube
./fun-kube --wizard           # wizard interattivo: crea/modifica un .env e lancia up
```

Ogni sottocomando accetta `--help` per la lista completa delle opzioni.

---

## Output generato

Al termine dell'installazione:

| File | Contenuto |
|---|---|
| `/root/.kube/<cluster_name>` | Kubeconfig **primario** — ServiceAccount token, non scade |
| `/root/.kube/<cluster_name>-admin` | Kubeconfig di **emergenza** — admin.conf, scade ~1 anno |
| `/root/<cluster_name>-manutenzione.txt` | Guida manutenzione (nodi, certificati, addon, comandi) |
| `output/cluster-info.txt` | Riepilogo rapido del cluster |
| `output/inventory.ini` | Inventory Ansible generato |
| `output/traefik-letsencrypt-guide.txt` | Guida LE (solo se Traefik senza ACME email) |
| `output/backups/` | Snapshot etcd presi da `fun-kube upgrade` prima di ogni upgrade |

`~/.bashrc` viene aggiornato automaticamente con `KUBECONFIG`, `alias k=kubectl` e autocompletamento.

---

## Modifiche dopo l'installazione

Rieseguire `./fun-kube up` dopo aver cambiato il `.env` applica le modifiche supportate
(pool MetalLB, parametri ingress, NodePort, nuovi nodi, nuovi addon). Prima di toccare i
nodi confronta il `.env` con il cluster e si ferma se cambiano endpoint/VIP, `POD_CIDR`,
`SERVICE_CIDR`, `NPM_DB_PASSWORD` o `K8S_VERSION` (per quest'ultima: `fun-kube upgrade`).
Un addon messo a `false` **non** viene disinstallato. Dettagli in `PROJECT.md`.

---

## Upgrade di Kubernetes

```bash
./fun-kube upgrade --to v1.36.5 --dry-run   # controlli + piano
./fun-kube upgrade --to v1.36.5
```

- Solo **patch** o **una minor version** alla volta (1.35.x → 1.36.y): per salti più ampi
  ripetere il comando.
- Controlli prima di toccare il cluster: nodi Ready, API server pronto, pod di
  kube-system sani, nodi del cluster = nodi del `.env`, pacchetto disponibile nel repo
  apt, addon allineati alle versioni del repo e compatibili con il target, volumi
  Longhorn healthy. Se un controllo fallisce l'upgrade non parte.
- Snapshot di etcd copiato sulla bootstrap (`output/backups/`) prima di iniziare.
- Primo control-plane (`kubeadm upgrade apply`), poi gli altri CP e i worker
  (`kubeadm upgrade node`), **un nodo alla volta**: drain → kubelet → Ready → uncordon,
  con controlli di salute (e attesa dei volumi Longhorn) prima del nodo successivo.
- In HA, keepalived sposta il VIP se l'API server del nodo che lo detiene non risponde.
- Se si ferma a metà, rilanciare lo stesso comando: i nodi già aggiornati vengono saltati.
- A fine upgrade aggiorna `K8S_VERSION` nel `.env` e kubectl sulla bootstrap.
- Mononodo o volumi Longhorn con una sola replica: l'upgrade comporta un fermo dei
  workload e richiede conferma esplicita (nome del cluster) o `--allow-downtime`.
- L'upgrade di una minor version **non è reversibile**: lo snapshot etcd serve per il
  disaster recovery, non come "annulla".

`up` non aggiorna mai Kubernetes: se `K8S_VERSION` differisce dalla versione dei nodi
già nel cluster si ferma e indica di usare `upgrade`.

---

## Certificati — rinnovo automatico

Fun-Kube installa un systemd timer su ogni nodo control-plane che rinnova i certificati ogni mese con `kubeadm certs renew all` e riavvia automaticamente i componenti del control-plane.

In modalità HA i nodi rinnovano in finestre diverse (fino a 1 ora di spread) per evitare restart simultanei.

Il kubeconfig primario (ServiceAccount token) **non è soggetto alla scadenza annuale** dei certificati kubeadm. Solo il kubeconfig di emergenza (`-admin`) richiede un aggiornamento manuale dopo ogni rinnovo.

---

## Struttura del progetto

```
Fun-Kube/
├── fun-kube                      # CLI (eseguibile diretto, auto-bootstrap venv)
├── .env.example                  # template configurazione
├── fun_kube/
│   ├── cli.py                    # comandi: up, upgrade, reset, diagnose, check-deps
│   ├── config.py                 # parsing .env, validazione, topologia
│   ├── preflight.py              # check sui nodi + confronto .env ↔ cluster esistente
│   ├── runner.py                 # inventory + sequenza playbook + output
│   ├── deps.py                   # verifica e auto-install tool bootstrap
│   ├── upgrade.py                # comando upgrade (controlli + esecuzione)
│   ├── reset.py                  # comando reset (arresto workload, reset nodi)
│   ├── wizard/                   # wizard TUI (Textual): model.py regole, app.py UI, texts.py testi
│   └── versions.py               # versioni di tutti i componenti
├── scripts/
│   └── vendor-manifests.py       # riscarica i manifest vendored
└── ansible/
    ├── playbooks/
    │   ├── bootstrap.yml              # common + containerd + kubeadm su tutti i nodi
    │   ├── keepalived.yml             # VIP keepalived (HA only)
    │   ├── kubeadm-init.yml           # init primo control-plane
    │   ├── control-plane-join.yml     # join CP aggiuntivi (HA only)
    │   ├── worker-join.yml            # join worker nodes
    │   ├── calico.yml                 # CNI
    │   ├── calico-apiserver.yml       # API server Calico
    │   ├── untaint-cp.yml             # rimozione taint (se nessun worker)
    │   ├── metrics-server.yml
    │   ├── cert-manager.yml
    │   ├── cert-renewal.yml           # systemd timer rinnovo certificati
    │   ├── local-path-provisioner.yml # StorageClass default (mononodo only)
    │   ├── bootstrap-kubeconfig.yml   # kubeconfig SA non-scadente
    │   ├── control-plane-metrics.yml  # metriche control plane su 0.0.0.0 (monitoring)
    │   ├── monitoring.yml             # Prometheus, Grafana, Loki, Alloy
    │   ├── metallb.yml
    │   ├── longhorn.yml
    │   ├── upgrade.yml                # upgrade kubeadm (+ tasks/upgrade-node.yml)
    │   ├── ingress.yml                # Traefik o Nginx Proxy Manager
    │   └── dn-essence.yml
    └── roles/
        ├── common/                    # sysctl, kernel, swap, chrony
        ├── containerd/                # container runtime
        ├── kubeadm/                   # kubelet + kubeadm + kubectl
        ├── calico/                    # CNI
        ├── metrics-server/
        ├── cert-manager/
        ├── cert-renewal/              # script + timer rinnovo certificati
        ├── local-path-provisioner/
        ├── keepalived/
        ├── metallb/
        ├── traefik/                   # DaemonSet, Helm, LB/NodePort, LE
        ├── nginx-proxy-manager/       # DaemonSet, LB/NodePort, multi/single-node
        ├── longhorn/                  # storage distribuito, RWO + RWX
        ├── dn-essence/                # DNS rewrite manager per CoreDNS, UI web
        ├── control-plane-metrics/     # metriche scheduler/controller-manager/etcd/kube-proxy
        └── monitoring/                # kube-prometheus-stack, Loki, Alloy, dashboard
```

---

## Troubleshooting

**Panoramica rapida di tutti i nodi:**
```bash
./fun-kube diagnose
```

**Verifica dipendenze bootstrap:**
```bash
./fun-kube check-deps --verbose
```

**Stato del cluster:**
```bash
kubectl get nodes
kubectl get pods -A
```

**Log kubelet su un nodo:**
```bash
ssh root@<node-ip> 'journalctl -u kubelet -f'
```

**Stato certificati:**
```bash
ssh root@<cp-ip> 'sudo kubeadm certs check-expiration'
```

**Verifica completa senza modifiche** (configurazione + preflight via SSH):
```bash
./fun-kube up --dry-run
```

**Orologio dei nodi sfasato** (es. VM ripristinate da snapshot con RAM): il preflight lo segnala;
apt rifiuta i repository ("Release file is not valid yet") e i certificati risultano non validi.
```bash
ssh root@<node-ip> 'timedatectl set-ntp true; systemctl restart chrony 2>/dev/null; chronyc makestep'
```

**Spazio Longhorn insufficiente** per il monitoring: il controllo (anche in `--dry-run`) indica lo
spazio prenotabile per nodo; ridurre `PROM_STORAGE` / `LOKI_STORAGE` o ingrandire i dischi.

**Reinstallare da zero:**
```bash
./fun-kube reset --yes --reboot
./fun-kube up
```

`reset` arresta prima i workload (i volumi Longhorn/NFS si staccano in modo pulito) e segnala i
nodi con processi bloccati; `--reboot` riavvia i nodi e garantisce uno stato pulito.
