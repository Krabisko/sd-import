# SD Import

Imports photos, videos and other files from SD cards (drones, photo and video cameras)
into the Photo & Video library on Linux/KDE.

```
Photo & Video/01_personal/by year/<YYYY>/<YYYY-MM-DD[ name]>/
Photo & Video/02_clients/<YYYY>/<YYYY-MM-DD[ name]>/
    Photo/RAW, Photo/JPEG   (split only when the card has both formats)
    Photo/                  (otherwise)
    Video/
    Other/                  (drone telemetry, proxies, unknown files)
```

- Asks personal vs. clients and an optional shoot / client name
- Reuses existing year folders, never duplicates them
- Never deletes anything from the card; re-runs skip already-imported files
- Name clashes get a `_1` suffix instead of overwriting
- Checks free space, verifies copied sizes, notifies when done, offers to eject

## Install

```bash
./install.sh
```

This links `sd-import` and `sd-import-launch` into `~/.local/bin`, adds an **SD Import** app-menu entry (opens in `xdg-terminal-exec` if present, e.g. COSMIC Terminal, else Konsole) and a
KDE device-notifier action that appears when a card is inserted.

## Usage

```bash
sd-import                     # auto-detect inserted cards
sd-import /run/media/$USER/X  # import a specific folder
sd-import --dry-run           # show the plan, copy nothing
sd-import --date 2026-11-09   # override the import date
```

The library location defaults to `/mnt/8tbhdd/8tbhdd/Photo & Video` and can be overridden
with the `SD_IMPORT_LIBRARY` environment variable.

Requires Python 3.8+, `lsblk`, and optionally `udisksctl`, `notify-send`, `konsole`.
