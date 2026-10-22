#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Установка GNOME Cloud Backup ==="

# 1. Ensure directories exist
mkdir -p "$HOME/.local/bin"
mkdir -p "$HOME/.local/share/applications"
mkdir -p "$HOME/.local/share/icons/hicolor/scalable/apps"

# 2. Make scripts executable
chmod +x "$SCRIPT_DIR/main.py"
chmod +x "$SCRIPT_DIR/backup_engine.py"
chmod +x "$SCRIPT_DIR/gdrive_sync.py"
chmod +x "$SCRIPT_DIR/app_gui.py"

# 3. Create symlink in ~/.local/bin
ln -sf "$SCRIPT_DIR/main.py" "$HOME/.local/bin/gnome-gdrive-backup"
echo "✓ Установлена ссылка в ~/.local/bin/gnome-gdrive-backup"

# 4. Copy icon
cp "$SCRIPT_DIR/gnome-gdrive-backup.svg" "$HOME/.local/share/icons/hicolor/scalable/apps/gnome-gdrive-backup.svg"
echo "✓ Установлена иконка в ~/.local/share/icons/..."

# 5. Copy desktop entry
cp "$SCRIPT_DIR/gnome-gdrive-backup.desktop" "$HOME/.local/share/applications/gnome-gdrive-backup.desktop"
echo "✓ Установлен ярлык приложения в ~/.local/share/applications/..."

# 6. Update caches if tools available
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

echo "🎉 Установка успешно завершена!"
echo "Приложение доступно в меню приложений GNOME (GNOME Cloud Backup)"
echo "Или командой в терминале: gnome-gdrive-backup"
