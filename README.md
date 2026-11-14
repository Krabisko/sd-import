# SD Import

Imports photos, videos and other files from SD cards (drones, photo and video cameras)
into a photo/video library, sorted by category, year and date. Works on any Linux desktop
(GNOME, KDE Plasma, COSMIC, XFCE, Cinnamon, MATE, LXQt, Hyprland, Sway, …).

```
<library>/<category>/by year/<YYYY>/<YYYY-MM-DD[ name]>/
    Photo/RAW, Photo/JPEG   (split only when the card has both formats)
    Photo/                  (otherwise)
    Video/
    Other/                  (drone telemetry, proxies, unknown files)
```

- Asks which category to import into, plus an optional shoot / client / event name
- Reuses existing year folders and an existing `by year` or `yearly` folder, never duplicates them
- Never deletes anything from the card; re-runs skip already-imported files
- Name clashes get a `_1` suffix instead of overwriting
- Checks free space, verifies copied sizes, notifies when done, offers to eject and open the folder

## Install

```bash
git clone https://github.com/Krabisko/sd-import.git
cd sd-import
./install.sh
```

This links `sd-import` into `~/.local/bin` and adds an **SD Import** app-menu entry.
On KDE Plasma it also adds an action to the device notifier that appears when a card is inserted.

Requires Python 3.8+ and `lsblk` (part of util-linux). Optional: `udisksctl` (eject),
`notify-send` (notifications).

## First launch

The first time it runs, SD Import asks:

1. **Terminal**: the app opens itself in this terminal when started from the app menu.
2. **File manager**: used for "Open the import folder".
3. **Library folder**: where imports go, e.g. `~/Pictures` or `/mnt/data/Photo & Video`.
4. **Categories**: defaults to `01_personal`, `02_clients`, `03_events`. Existing folders
   are recognised even with a different number or case (`Personal` matches `01_personal`).
   You can add, rename or remove categories.

Each category gets a `by year` folder. If it already has `yearly` or `by year`, that is used.
Year folders sitting directly in a category (e.g. `02_clients/2026`) are offered for moving
into `by year/`. That's a rename on the same drive, and nothing moves unless you say yes.

Settings are stored in `~/.config/sd-import/config.json`. Run `sd-import --setup` to change them.

Terminals offered when installed: COSMIC Terminal, GNOME Terminal, Ptyxis, GNOME Console,
Konsole, Xfce Terminal, MATE Terminal, Tilix, Terminator, LXTerminal, QTerminal, Alacritty,
kitty, foot, WezTerm, Ghostty, xterm, `xdg-terminal-exec`, or any custom command
(`{cmd}` marks where the command goes).

File managers: system default (`xdg-open`), COSMIC Files, Nautilus, Dolphin, Nemo, Thunar,
Caja, PCManFM, PCManFM-Qt, or a custom command (`{path}` marks the folder).

## Usage

```bash
sd-import                     # auto-detect inserted cards
sd-import /run/media/$USER/X  # import a specific folder
sd-import --dry-run           # show the plan, copy nothing
sd-import --date 2026-11-09   # override the import date
sd-import --setup             # change terminal, file manager, library, categories
```

`SD_IMPORT_CONFIG=/path/config.json` uses a different config file, which is handy for testing.
