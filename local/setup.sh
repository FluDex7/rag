#!/bin/bash
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1"; exit 1; }

# --- Ollama ---
if ! command -v ollama &>/dev/null; then
    info "Ollama не найдена — устанавливаю..."
    curl -fsSL https://ollama.com/install.sh | sh
fi
info "Ollama найдена: $(ollama --version)"

# Настраиваем Ollama слушать на 0.0.0.0
OVERRIDE_DIR="/etc/systemd/system/ollama.service.d"
OVERRIDE_FILE="$OVERRIDE_DIR/override.conf"

if ! grep -q "OLLAMA_HOST=0.0.0.0" "$OVERRIDE_FILE" 2>/dev/null; then
    info "Настраиваю Ollama для доступа из Docker..."
    sudo mkdir -p "$OVERRIDE_DIR"
    sudo tee "$OVERRIDE_FILE" > /dev/null <<EOF
[Service]
Environment="OLLAMA_HOST=0.0.0.0"
EOF
    sudo systemctl daemon-reload
    sudo systemctl restart ollama
    sleep 2
    info "Ollama перезапущена"
else
    info "Ollama уже настроена"
fi

# Проверяем доступность
if ! curl -sf http://localhost:11434/api/tags > /dev/null; then
    error "Ollama недоступна на порту 11434. Проверьте: sudo systemctl status ollama"
fi

# Скачиваем модель если её нет
MODEL="${OLLAMA_MODEL:-mistral:7b}"
if ollama list | grep -q "^${MODEL}"; then
    info "Модель $MODEL уже скачана"
else
    info "Скачиваю модель $MODEL (это займёт несколько минут)..."
    ollama pull "$MODEL"
fi

# --- .env ---
if [ ! -f .env ]; then
    cp .env.example .env
    warn "Создан .env из .env.example — укажите TELEGRAM_BOT_TOKEN"
    warn "Откройте .env и заполните токен, затем запустите: docker compose up -d"
    exit 0
fi

if grep -q "your_telegram_bot_token" .env; then
    warn ".env содержит токен-заглушку — замените TELEGRAM_BOT_TOKEN на реальный"
    warn "Затем запустите: docker compose up -d"
    exit 0
fi

# --- Запуск ---
info "Всё готово. Запускаю docker compose..."
docker compose up -d
info "Бот запущен. Логи: docker compose logs -f"
