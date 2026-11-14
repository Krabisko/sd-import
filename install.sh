#!/usr/bin/env bash
# Installs the sd-import command and app-menu entry (plus a device-notifier action on KDE Plasma).
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
  echo "sd-import needs Python 3.8 or newer (python3)." >&2
  exit 1
fi
command -v lsblk >/dev/null || echo "Note: lsblk not found, cards won't be detected automatically."

mkdir -p ~/.local/bin ~/.local/share/applications
chmod +x "$here/sd_import.py"
ln -sf "$here/sd_import.py" ~/.local/bin/sd-import
rm -f ~/.local/bin/sd-import-launch  # replaced by sd-import opening its own terminal
cp "$here/sd-import.desktop" ~/.local/share/applications/

# KDE Plasma shows these actions when a card is inserted; other desktops ignore them.
if command -v plasmashell >/dev/null || [[ -d ~/.local/share/solid/actions ]]; then
  mkdir -p ~/.local/share/solid/actions
  cp "$here/sd-import-solid.desktop" ~/.local/share/solid/actions/sd-import.desktop
fi

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Note: add ~/.local/bin to your PATH to run 'sd-import' from a terminal." ;;
esac
echo "Installed. Open 'SD Import' from the app menu or run 'sd-import'."
echo "The first launch asks for your terminal, file manager and library folder."
