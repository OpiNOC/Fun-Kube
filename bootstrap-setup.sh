#!/usr/bin/env bash
# =============================================================================
# bootstrap-setup.sh
# Prepara la macchina bootstrap per eseguire Fun-Kube.
# Eseguire UNA VOLTA sulla macchina da cui si lancerà fun-kube up.
# Idempotente: non reinstalla tool già presenti.
#
# Requisiti minimi: Ubuntu 22.04 / 24.04, accesso sudo
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC}  $*"; }
skip() { echo -e "${YELLOW}  ↷${NC}  $* (già installato)"; }
info() { echo -e "  →  $*"; }
fail() { echo -e "${RED}  ✗${NC}  $*"; exit 1; }

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║  Fun-Kube — Bootstrap machine setup      ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# -----------------------------------------------------------------------------
# 1. Dipendenze di sistema
# -----------------------------------------------------------------------------
echo "▶  Dipendenze di sistema..."

sudo apt-get update -qq

for pkg in python3 python3-pip python3-venv git curl openssh-client; do
  if dpkg -s "$pkg" &>/dev/null; then
    skip "$pkg"
  else
    info "Installazione $pkg..."
    sudo apt-get install -y -qq "$pkg"
    ok "$pkg"
  fi
done

# -----------------------------------------------------------------------------
# 2. Fun-Kube Python package (typer, rich, python-dotenv)
# -----------------------------------------------------------------------------
echo ""
echo "▶  Fun-Kube Python dependencies..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if python3 -c "import fun_kube" &>/dev/null 2>&1; then
  skip "fun_kube package"
else
  info "pip install -e $SCRIPT_DIR"
  pip3 install --quiet -e "$SCRIPT_DIR"
  ok "fun_kube package"
fi

# Rendi eseguibile l'entry point
chmod +x "$SCRIPT_DIR/fun-kube"

# -----------------------------------------------------------------------------
# 3. Ansible
# -----------------------------------------------------------------------------
echo ""
echo "▶  Ansible..."

if command -v ansible-playbook &>/dev/null; then
  skip "ansible ($(ansible --version | head -1))"
else
  info "Installazione Ansible via pip..."
  pip3 install --quiet "ansible>=9.0"
  ok "ansible"
fi

# Collections necessarie
echo ""
echo "▶  Ansible collections..."

for collection in community.general; do
  if ansible-galaxy collection list 2>/dev/null | grep -q "^$collection"; then
    skip "$collection"
  else
    info "ansible-galaxy collection install $collection"
    ansible-galaxy collection install "$collection" --quiet
    ok "$collection"
  fi
done

# -----------------------------------------------------------------------------
# 4. kubectl
# -----------------------------------------------------------------------------
echo ""
echo "▶  kubectl..."

if command -v kubectl &>/dev/null; then
  skip "kubectl ($(kubectl version --client --short 2>/dev/null || kubectl version --client 2>/dev/null | head -1))"
else
  info "Download kubectl (versione stabile)..."
  KUBECTL_VERSION=$(curl -sL https://dl.k8s.io/release/stable.txt)
  ARCH=$(dpkg --print-architecture)
  curl -sLO "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${ARCH}/kubectl"
  sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl
  rm -f kubectl
  ok "kubectl ${KUBECTL_VERSION}"
fi

# -----------------------------------------------------------------------------
# 5. Helm
# -----------------------------------------------------------------------------
echo ""
echo "▶  Helm..."

if command -v helm &>/dev/null; then
  skip "helm ($(helm version --short 2>/dev/null))"
else
  info "Installazione Helm (get-helm-3)..."
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash -s -- --no-sudo 2>/dev/null \
    || curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
  ok "helm"
fi

# -----------------------------------------------------------------------------
# 6. Verifica finale
# -----------------------------------------------------------------------------
echo ""
echo "▶  Verifica dipendenze..."

MISSING=()
for cmd in python3 pip3 ansible ansible-playbook kubectl helm ssh git; do
  if command -v "$cmd" &>/dev/null; then
    ok "$cmd → $(command -v "$cmd")"
  else
    MISSING+=("$cmd")
    fail "$cmd non trovato"
  fi
done

echo ""
if [ ${#MISSING[@]} -eq 0 ]; then
  echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
  echo -e "${GREEN}║  Setup completato. Macchina bootstrap    ║${NC}"
  echo -e "${GREEN}║  pronta per fun-kube up                  ║${NC}"
  echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
  echo ""
  echo "  Prossimi passi:"
  echo "    cp .env.example .env"
  echo "    # modifica .env con i tuoi nodi e parametri"
  echo "    ./fun-kube up"
else
  echo -e "${RED}Setup incompleto. Mancano: ${MISSING[*]}${NC}"
  exit 1
fi
