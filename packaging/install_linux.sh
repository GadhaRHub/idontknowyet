#!/bin/sh
# Adds PiSES to the application menu / dock of the current user (Linux).
# Run from anywhere:  sh packaging/install_linux.sh
set -e
HERE=$(cd "$(dirname "$0")/.." && pwd)
ICONS="$HOME/.local/share/icons/hicolor"
for s in 16 24 32 48 64 128 256 512; do
    mkdir -p "$ICONS/${s}x${s}/apps"
    cp "$HERE/assets/pises_${s}.png" "$ICONS/${s}x${s}/apps/pises.png"
done
mkdir -p "$ICONS/scalable/apps" "$HOME/.local/share/applications"
cp "$HERE/assets/pises.svg" "$ICONS/scalable/apps/pises.svg"
sed "s|/path/to/idontknowyet|$HERE|g" "$HERE/packaging/pises.desktop" \
    > "$HOME/.local/share/applications/pises.desktop"
command -v update-desktop-database >/dev/null && \
    update-desktop-database "$HOME/.local/share/applications" || true
command -v gtk-update-icon-cache >/dev/null && \
    gtk-update-icon-cache -q "$ICONS" 2>/dev/null || true
echo "PiSES added to the application menu (log out and in if it doesn't show)."
