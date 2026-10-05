#!/usr/bin/env bash

set -e

RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'
CYAN='\033[0;36m'; DIM='\033[2m'; BOLD='\033[1m'; RESET='\033[0m'

info()  { echo -e "${CYAN}▸ $*${RESET}"; }
ok()    { echo -e "${GREEN}✓ $*${RESET}"; }
warn()  { echo -e "${YELLOW}⚠ $*${RESET}"; }
nota()  { echo -e "${DIM}  $*${RESET}"; }
die()   { echo -e "${RED}✗ ERRO: $*${RESET}"; exit 1; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════╗"
echo "║      De Lá Pra Cá — Dev Mode         ║"
echo "║      Media Composer                  ║"
echo "╚══════════════════════════════════════╝"
echo -e "${RESET}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
info "Diretório: $SCRIPT_DIR"

PACK=0; LIMPO=0
for arg in "$@"; do
    case "$arg" in
        --pack)  PACK=1 ;;
        --limpo) LIMPO=1 ;;
        -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "opção desconhecida: $arg  (use --pack, --limpo ou --help)" ;;
    esac
done

PYTHON=""
for cmd in python3.12 python3.13 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        MAJOR=${VER%%.*}; MINOR=${VER##*.}
        if [ "$MAJOR" -ge 3 ] && [ "$MINOR" -ge 12 ]; then
            PYTHON="$cmd"; ok "Python $VER ($cmd)"; break
        fi
    fi
done
[ -z "$PYTHON" ] && die "Python 3.12+ não encontrado.  brew install python@3.12"

if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg $(ffmpeg -version 2>&1 | head -1 | awk '{print $3}')"
else
    warn "ffmpeg não está no PATH."
    nota "O relink não precisa dele hoje (o matcher não abre mídia), mas os"
    nota "writers de saída precisam.  brew install ffmpeg"
fi

VENV=".venv"
if [ "$LIMPO" = "1" ] && [ -d "$VENV" ]; then
    info "Removendo o venv antigo…"; rm -rf "$VENV"
fi
if [ -d "$VENV" ] && ! "$VENV/bin/python" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
    warn "O venv existente não é Python 3.12+ (ou não executa) — recriando."
    rm -rf "$VENV"
fi
if [ ! -d "$VENV" ]; then
    info "Criando venv (--copies, o volume é ExFAT)…"
    "$PYTHON" -m venv --copies "$VENV"
    ok "venv criado"
    NOVO_VENV=1
fi

CARIMBO="$VENV/.deps-instaladas"
if [ "${NOVO_VENV:-0}" = "1" ] || [ ! -f "$CARIMBO" ] || [ requirements.lock -nt "$CARIMBO" ]; then
    info "Instalando dependências Python…"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r requirements.lock
    touch "$CARIMBO"
    ok "Dependências OK"
else
    ok "Dependências já instaladas"
fi

AVPI="/Library/Application Support/Avid/PanelSDKPlugins/com.ciclomedia.delapraca.mc.avpi"
if [ "$PACK" = "1" ]; then
    info "Empacotando e instalando o painel…"
    bash tools/pack.sh >/dev/null
    ok "Painel instalado — v$(cat VERSION)"
    nota "Painel já aberto? botão direito na área vazia → Reload"
    nota "Painel novo? feche o MC, rode 'bash tools/reload.sh', reabra"
elif [ -f "$AVPI" ]; then
    ok "Painel instalado (v$(cat VERSION 2>/dev/null || echo '?'))"
else
    warn "O painel .avpi ainda não está instalado."
    nota "Rode: bash dev.sh --pack"
fi

PORTA=7823
if lsof -nP -iTCP:$PORTA -sTCP:LISTEN >/dev/null 2>&1; then
    PID=$(lsof -nP -tiTCP:$PORTA -sTCP:LISTEN | head -1)
    warn "A porta $PORTA já está ocupada (pid $PID) — outra instância do serviço?"
    read -r -p "$(echo -e "${YELLOW}  Encerrar aquele processo e continuar? [s/N] ${RESET}")" R
    case "$R" in
        [sSyY]*) kill "$PID" 2>/dev/null || true; sleep 1; ok "Encerrado" ;;
        *) die "porta ocupada — encerre o outro serviço e tente de novo" ;;
    esac
fi

if pgrep -f "MacOS/AvidMediaComposer" >/dev/null 2>&1; then
    ok "Media Composer está aberto"
else
    warn "Media Composer fechado — o painel vai abrir, mas sem nada para dirigir."
fi

if pgrep -f "avid-api-gateway" >/dev/null 2>&1 && ! pgrep -f "MacOS/AvidMediaComposer" >/dev/null 2>&1; then
    warn "Há um avid-api-gateway ÓRFÃO rodando (o MC está fechado)."
    nota "Ele segura as portas do Panel SDK e faz o menu Extensions sumir."
    nota "Rode: bash tools/reload.sh"
fi

echo ""
echo -e "${BOLD}${GREEN}Tudo pronto. Subindo o serviço…${RESET}"
echo -e "${CYAN}  painel:  http://127.0.0.1:$PORTA/painel${RESET}"
echo -e "${CYAN}  no MC:   Tools ▸ De Lá Pra Cá${RESET}"
echo -e "${DIM}  (Ctrl+C para encerrar)${RESET}"
echo ""

export PYTHONPATH="motor:plugins/media-composer/servico"
exec "$VENV/bin/python" plugins/media-composer/servico/main.py --bancada --log "${DLPC_LOG:-info}"
