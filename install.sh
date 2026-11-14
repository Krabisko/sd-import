#!/usr/bin/env bash
# Installs the sd-import command, app launcher and KDE "device inserted" action.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.local/bin ~/.local/share/applications ~/.local/share/solid/actions
ln -sf "$here/sd_import.py" ~/.local/bin/sd-import
ln -sf "$here/sd-import-launch" ~/.local/bin/sd-import-launch
cp "$here/sd-import.desktop" ~/.local/share/applications/
cp "$here/sd-import-solid.desktop" ~/.local/share/solid/actions/sd-import.desktop
echo "Installed. Run 'sd-import', open 'SD Import' from the app menu, or pick it in the device notifier when a card is inserted."
