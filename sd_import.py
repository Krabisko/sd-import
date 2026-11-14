#!/usr/bin/env python3
"""Import photos, videos and other files from SD cards into a photo/video library.

Layout created:
    <library>/<category>/by year/<YYYY>/<YYYY-MM-DD[ name]>/...

Inside the import folder:
    Photo/RAW, Photo/JPEG   (split only when the card holds both formats)
    Photo/                  (otherwise)
    Video/
    Other/                  (drone telemetry, proxies, sidecars, anything unknown)

Nothing is ever deleted from the card. Re-running an import into the same folder
skips files that were already copied.

The first run asks for your terminal, file manager, library folder and categories
and saves them to ~/.config/sd-import/config.json. Run `sd-import --setup` to change them.

Usage:
    sd-import                  # auto-detect inserted cards
    sd-import /run/media/...   # import a specific mount point / folder
    sd-import --dry-run        # show what would happen, copy nothing
    sd-import --setup          # (re)run the first-time setup
"""

import argparse
import atexit
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("SD_IMPORT_CONFIG") or
                   Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "sd-import" / "config.json")
DEFAULT_CATEGORIES = ["01_personal", "02_clients", "03_events"]
YEAR_FOLDER = "by year"
YEAR_FOLDER_ALIASES = {"by year", "yearly"}  # compared after _ and - become spaces

# Terminal -> (command prefix, whether the command must be passed as one quoted string).
TERMINALS = {
    "cosmic-term": (["cosmic-term", "-e"], False),
    "gnome-terminal": (["gnome-terminal", "--"], False),
    "ptyxis": (["ptyxis", "--"], False),
    "kgx": (["kgx", "--"], False),
    "konsole": (["konsole", "-e"], False),
    "xfce4-terminal": (["xfce4-terminal", "-x"], False),
    "mate-terminal": (["mate-terminal", "-x"], False),
    "tilix": (["tilix", "-e"], True),
    "terminator": (["terminator", "-x"], False),
    "lxterminal": (["lxterminal", "-e"], True),
    "qterminal": (["qterminal", "-e"], True),
    "alacritty": (["alacritty", "-e"], False),
    "kitty": (["kitty"], False),
    "foot": (["foot"], False),
    "wezterm": (["wezterm", "start", "--"], False),
    "ghostty": (["ghostty", "-e"], False),
    "xterm": (["xterm", "-e"], False),
    "xdg-terminal-exec": (["xdg-terminal-exec"], False),
}
FILE_MANAGERS = {
    "xdg-open": ["xdg-open"],
    "cosmic-files": ["cosmic-files"],
    "nautilus": ["nautilus"],
    "dolphin": ["dolphin"],
    "nemo": ["nemo"],
    "thunar": ["thunar"],
    "caja": ["caja"],
    "pcmanfm": ["pcmanfm"],
    "pcmanfm-qt": ["pcmanfm-qt"],
}
# XDG_CURRENT_DESKTOP fragment -> (preferred terminals, preferred file managers).
DESKTOP_APPS = {
    "cosmic": (["cosmic-term"], ["cosmic-files"]),
    "gnome": (["ptyxis", "kgx", "gnome-terminal"], ["nautilus"]),
    "ubuntu": (["ptyxis", "gnome-terminal"], ["nautilus"]),
    "pop": (["cosmic-term", "gnome-terminal"], ["cosmic-files", "nautilus"]),
    "kde": (["konsole"], ["dolphin"]),
    "xfce": (["xfce4-terminal"], ["thunar"]),
    "cinnamon": (["gnome-terminal"], ["nemo"]),
    "mate": (["mate-terminal"], ["caja"]),
    "budgie": (["tilix", "gnome-terminal"], ["nautilus", "nemo"]),
    "lxqt": (["qterminal"], ["pcmanfm-qt"]),
    "lxde": (["lxterminal"], ["pcmanfm"]),
    "hyprland": (["kitty", "foot", "alacritty"], []),
    "sway": (["foot", "alacritty", "kitty"], []),
    "niri": (["alacritty", "foot", "kitty"], []),
}

RAW_EXT = {
    ".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".raf",
    ".orf", ".rw2", ".pef", ".dng", ".3fr", ".iiq", ".rwl", ".srw", ".x3f",
    ".gpr", ".erf", ".kdc", ".mrw", ".mos",
}
JPEG_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".hif", ".png", ".tif", ".tiff", ".webp"}
VIDEO_EXT = {
    ".mp4", ".mov", ".mts", ".m2ts", ".avi", ".mxf", ".mkv", ".3gp", ".m4v",
    ".insv", ".360", ".braw", ".r3d", ".crm", ".wmv", ".mpg", ".mpeg",
}
# Card housekeeping that is never worth copying.
SKIP_DIRS = {"system volume information", "$recycle.bin", ".trashes", ".spotlight-v100", ".fseventsd"}
SKIP_FILES = {".ds_store", "thumbs.db", "desktop.ini", "indexervolumeguid"}

COPY_CHUNK = 8 * 1024 * 1024


# ---------------------------------------------------------------- terminal ui

def c(text, code):
    return f"\033[{code}m{text}\033[0m" if sys.stdout.isatty() else text


def ask(prompt, default=None):
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        sys.exit(1)
    return answer or (default or "")


def yes(prompt, default=True):
    return ask(f"{prompt} ({'Y/n' if default else 'y/N'})", "y" if default else "n").lower() in ("y", "yes")


def choose(prompt, options, default=None):
    """Numbered menu; returns the chosen index. Enter picks `default` (an index) if given."""
    print(c(prompt, "1"))
    for i, label in enumerate(options, 1):
        print(f"  {c(i, '36')}) {label}")
    while True:
        answer = ask("Choose", str(default + 1) if default is not None else None)
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print(c("  Please type one of the numbers above.", "33"))


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def pause():
    try:
        input("\nPress Enter to close.")
    except (EOFError, KeyboardInterrupt):
        pass


def notify(summary, body=""):
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "SD Import", "-i", "camera-photo", summary, body],
                       check=False, stderr=subprocess.DEVNULL)


# ------------------------------------------------------- desktop integration

def desktop_prefs():
    """Preferred (terminals, file managers) for the running desktop."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
    terms, fms = [], []
    for key, (t, f) in DESKTOP_APPS.items():
        if key in desktop:
            terms += t
            fms += f
    return terms, fms


def ordered_installed(table, preferred):
    """Installed entries of `table`, the desktop's preferred ones first."""
    def binary(entry):
        return entry[0][0] if isinstance(entry, tuple) else entry[0]

    names = list(dict.fromkeys(preferred + list(table)))
    return [n for n in names if n in table and shutil.which(binary(table[n]))]


def custom_argv(template, placeholder, args):
    """Expand a custom command like 'myterm --run {cmd}' with the given argv."""
    parts = shlex.split(template)
    if placeholder not in template:
        return parts + args
    out = []
    for p in parts:
        if p == placeholder:
            out += args
        else:
            out.append(p.replace(placeholder, shlex.join(args)))
    return out


def terminal_argv(choice, cmd):
    if choice in TERMINALS:
        prefix, joined = TERMINALS[choice]
        return prefix + ([shlex.join(cmd)] if joined else cmd)
    return custom_argv(choice, "{cmd}", cmd)


def pick_terminal(config):
    """Configured terminal if usable, else xdg-terminal-exec, the desktop's own, any installed."""
    choice = (config or {}).get("terminal")
    if choice and (choice not in TERMINALS or shutil.which(TERMINALS[choice][0][0])):
        return choice
    terms, _ = desktop_prefs()
    installed = ordered_installed(TERMINALS, ["xdg-terminal-exec"] + terms)
    return installed[0] if installed else None


def relaunch_in_terminal(config):
    """Started from an app menu without a terminal: reopen ourselves inside one."""
    term = pick_terminal(config)
    if not term:
        notify("SD Import needs a terminal", "No terminal emulator found. Run sd-import --setup in one.")
        sys.exit(1)
    cmd = [sys.executable, os.path.realpath(sys.argv[0]), *sys.argv[1:]]
    if "--pause" not in cmd:
        cmd.append("--pause")
    argv = terminal_argv(term, cmd)
    try:
        os.execvp(argv[0], argv)
    except OSError as e:
        notify("SD Import could not open a terminal", f"{argv[0]}: {e}")
        sys.exit(1)


def open_folder(config, path):
    choice = config.get("file_manager", "xdg-open")
    argv = FILE_MANAGERS[choice] + [str(path)] if choice in FILE_MANAGERS else custom_argv(choice, "{path}", [str(path)])
    if not shutil.which(argv[0]):
        argv = ["xdg-open", str(path)]
    # Own session: otherwise the terminal's hangup on exit kills the opener before the
    # file manager starts (seen on COSMIC, where xdg-open takes the slower generic path).
    try:
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError as e:
        print(c(f"Could not open the folder: {e}", "31"))


# ------------------------------------------------------------- config/setup

def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return None


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")


def norm(name):
    """'01_Personal' -> 'personal', for recognising existing folders."""
    return re.sub(r"^[\d\s._-]+", "", name).replace("_", " ").replace("-", " ").strip().lower()


def year_root(category_dir):
    """Existing 'by year' / 'yearly' folder in a category, else '<category>/by year'."""
    if category_dir.is_dir():
        for d in sorted(category_dir.iterdir()):
            if d.is_dir() and d.name.lower().replace("_", " ").replace("-", " ") in YEAR_FOLDER_ALIASES:
                return d
    return category_dir / YEAR_FOLDER


def match_categories(library, wanted):
    """Map each wanted category to an existing top-level folder with the same normalised name."""
    existing = [d.name for d in library.iterdir() if d.is_dir()] if library.is_dir() else []
    result = []
    for cat in wanted:
        if cat in existing:
            result.append(cat)
        else:
            result.append(next((e for e in existing if norm(e) == norm(cat)), cat))
    return result, [e for e in existing if e not in result]


def choose_app(kind, table, preferred, current, labels=None):
    installed = ordered_installed(table, preferred)
    options = [(labels or {}).get(n, n) + ("  (suggested)" if n in preferred[:1] else "") for n in installed]
    options.append("Other (type a command)")
    default = installed.index(current) if current in installed else 0
    idx = choose(f"Which {kind}?", options, default)
    if idx < len(installed):
        return installed[idx]
    hint = "{cmd}" if kind == "terminal" else "{path}"
    return ask(f"Command (use {hint} where the {'command' if hint == '{cmd}' else 'folder'} goes)",
               current if current and current not in table else None) or installed[0]


def edit_categories(library, cats):
    while True:
        print(c("\nCategories", "1"))
        for cat in cats:
            state = "found" if (library / cat).is_dir() else "will be created"
            print(f"  - {cat}  ({state})")
        idx = choose("Categories OK?", ["Use these", "Add a category", "Rename one", "Remove one"], 0)
        if idx == 0 and cats:
            return cats
        if idx == 0:
            print(c("  You need at least one category.", "33"))
        elif idx == 1:
            _, others = match_categories(library, cats)
            if others:
                print(f"  Other folders in the library: {', '.join(others)}")
            name = ask("Folder name").strip("/")
            if name and name not in cats:
                cats.append(name)
        elif idx == 2:
            i = choose("Rename which?", cats)
            new = ask("New name", cats[i]).strip("/")
            old_dir, new_dir = library / cats[i], library / new
            if new and new != cats[i]:
                if old_dir.is_dir() and not new_dir.exists() and yes(f"Also rename the folder on disk to '{new}'?", False):
                    old_dir.rename(new_dir)
                cats[i] = new
        elif idx == 3 and cats:
            cats.pop(choose("Remove which? (the folder itself is kept)", cats))


def migrate_years(library, cats):
    """Offer to move year folders sitting directly in a category into its 'by year' folder."""
    moves, conflicts = [], []
    for cat in cats:
        cat_dir = library / cat
        root = year_root(cat_dir)
        for d in sorted(cat_dir.iterdir()):
            if d.is_dir() and re.fullmatch(r"\d{4}", d.name):
                (conflicts if (root / d.name).exists() else moves).append((d, root / d.name))
    if not moves and not conflicts:
        return
    print(c("\nYear folders outside 'by year'", "1"))
    for src, dst in moves:
        print(f"  {src.relative_to(library)}  ->  {dst.relative_to(library)}")
    for src, dst in conflicts:
        print(c(f"  {src.relative_to(library)}: skipped, {dst.relative_to(library)} already exists "
                f"(merge them by hand)", "33"))
    if moves and yes("Move these? Nothing is copied or deleted, the folders are only renamed.", False):
        for src, dst in moves:
            try:
                src.rename(dst)
                print(f"  moved {src.relative_to(library)}")
            except OSError as e:
                print(c(f"  ! {src}: {e}", "31"))


def setup(config):
    config = dict(config or {})
    print(c("SD Import setup", "1;35"))
    terms, fms = desktop_prefs()

    config["terminal"] = choose_app("terminal", TERMINALS, terms, config.get("terminal"))
    config["file_manager"] = choose_app("file manager", FILE_MANAGERS, fms + ["xdg-open"],
                                        config.get("file_manager"),
                                        {"xdg-open": "System default (xdg-open)"})

    while True:
        raw = ask("\nWhere should imports be saved? (library folder)",
                  config.get("library") or str(Path.home() / "Pictures"))
        library = Path(raw).expanduser().absolute()
        if library.is_dir():
            break
        if yes(f"{library} doesn't exist. Create it?"):
            library.mkdir(parents=True)
            break
    config["library"] = str(library)

    cats, _ = match_categories(library, config.get("categories") or DEFAULT_CATEGORIES)
    cats = edit_categories(library, cats)
    for cat in cats:
        root = year_root(library / cat)
        root.mkdir(parents=True, exist_ok=True)
        print(f"  {c('ok', '32')} {root.relative_to(library)}")
    config["categories"] = cats
    migrate_years(library, cats)

    save_config(config)
    print(c(f"\nSaved to {CONFIG_PATH}. Run 'sd-import --setup' to change it.\n", "32"))
    return config


# ------------------------------------------------------------ card detection

def detect_cards(library):
    """Mounted removable / hot-plugged filesystems (SD readers, USB card readers)."""
    try:
        out = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,PATH,RM,HOTPLUG,TRAN,MOUNTPOINTS,LABEL,SIZE"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []

    cards = []

    def is_library_drive(mp):
        mp = Path(mp)
        return library == mp or mp in library.parents or library in mp.parents

    def walk(dev, removable):
        removable = removable or dev.get("rm") or dev.get("hotplug") or dev.get("tran") in ("usb", "mmc")
        for mp in dev.get("mountpoints") or []:
            if mp and removable and mp != "/" and not mp.startswith("/boot") and not is_library_drive(mp):
                cards.append({"mount": Path(mp), "label": dev.get("label") or dev["name"],
                              "size": dev.get("size", ""), "device": dev["path"]})
        for child in dev.get("children") or []:
            walk(child, removable)

    for dev in json.loads(out).get("blockdevices", []):
        walk(dev, False)
    return cards


# ----------------------------------------------------------------- scanning

def classify(path):
    ext = path.suffix.lower()
    if ext in RAW_EXT:
        return "raw"
    if ext in JPEG_EXT:
        return "jpeg"
    if ext in VIDEO_EXT:
        return "video"
    return "other"


def scan(source):
    files = []
    for root, dirs, names in os.walk(source):
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS and not d.startswith(".")]
        for name in names:
            if name.startswith(".") or name.lower() in SKIP_FILES:
                continue
            p = Path(root) / name
            try:
                size = p.stat().st_size
            except OSError:
                continue
            files.append((p, size, classify(p)))
    files.sort(key=lambda f: str(f[0]))
    return files


def destination_dir(kind, base, split_photos):
    if kind == "video":
        return base / "Video"
    if kind == "other":
        return base / "Other"
    if split_photos:
        return base / "Photo" / ("RAW" if kind == "raw" else "JPEG")
    return base / "Photo"


# ------------------------------------------------------------------ copying

def same_file(a, b, size):
    """Cheap identity check: equal size plus identical first and last MB."""
    sample = 1024 * 1024
    with open(a, "rb") as fa, open(b, "rb") as fb:
        if fa.read(sample) != fb.read(sample):
            return False
        if size > sample:
            fa.seek(-sample, os.SEEK_END)
            fb.seek(-sample, os.SEEK_END)
            return fa.read(sample) == fb.read(sample)
    return True


def unique_target(src, target, size, claimed):
    """Return (path, already_there). Identical file on disk => already imported."""
    candidate, n = target, 1
    while True:
        if candidate in claimed:
            pass
        elif candidate.exists():
            if candidate.stat().st_size == size and same_file(src, candidate, size):
                return candidate, True
        else:
            return candidate, False
        candidate = target.with_name(f"{target.stem}_{n}{target.suffix}")
        n += 1


def copy_file(src, dst, progress):
    tmp = dst.with_name(f".{dst.name}.part")
    with open(src, "rb") as fi, open(tmp, "wb") as fo:
        while chunk := fi.read(COPY_CHUNK):
            fo.write(chunk)
            progress(len(chunk))
        fo.flush()
        os.fsync(fo.fileno())
    shutil.copystat(src, tmp)
    if tmp.stat().st_size != src.stat().st_size:
        tmp.unlink()
        raise OSError("size mismatch after copy")
    tmp.rename(dst)


def run_import(sources, base, library, dry_run):
    all_files = []
    for src in sources:
        print(f"\nScanning {c(src, '36')} ...")
        found = scan(src)
        all_files += found
        print(f"  {len(found)} files, {human(sum(f[1] for f in found))}")

    if not all_files:
        print(c("Nothing to import.", "33"))
        return 0, 0, []

    kinds = {k for _, _, k in all_files}
    split = "raw" in kinds and "jpeg" in kinds
    counts = {k: sum(1 for *_, kk in all_files if kk == k) for k in ("raw", "jpeg", "video", "other")}
    total = sum(s for _, s, _ in all_files)

    print(c("\nPlan", "1"))
    print(f"  Destination: {c(base, '32')}")
    if split:
        print(f"  Photo/RAW   {counts['raw']:>6} files")
        print(f"  Photo/JPEG  {counts['jpeg']:>6} files")
    elif counts["raw"] or counts["jpeg"]:
        print(f"  Photo       {counts['raw'] + counts['jpeg']:>6} files")
    if counts["video"]:
        print(f"  Video       {counts['video']:>6} files")
    if counts["other"]:
        print(f"  Other       {counts['other']:>6} files")
    print(f"  Total       {human(total)}")

    free = shutil.disk_usage(library).free
    if total > free:
        print(c(f"\nNot enough space: need {human(total)}, only {human(free)} free.", "31"))
        sys.exit(1)

    if dry_run:
        print(c("\nDry run - nothing copied.", "33"))
        return 0, 0, []
    if not yes("\nStart import?"):
        sys.exit(0)

    done_bytes, copied, skipped, errors = 0, 0, 0, []
    claimed = set()
    start = time.monotonic()

    def show(name):
        elapsed = max(time.monotonic() - start, 0.001)
        pct = done_bytes / total * 100 if total else 100
        speed = done_bytes / elapsed
        line = f"\r  {pct:5.1f}%  {human(done_bytes)}/{human(total)}  {human(speed)}/s  {name[:40]:<40}"
        print(line, end="", flush=True)

    for src, size, kind in all_files:
        dest_dir = destination_dir(kind, base, split)
        dest_dir.mkdir(parents=True, exist_ok=True)
        target, exists = unique_target(src, dest_dir / src.name, size, claimed)
        claimed.add(target)
        if exists:
            skipped += 1
            done_bytes += size
            show(src.name)
            continue

        def progress(n):
            nonlocal done_bytes
            done_bytes += n
            show(src.name)

        try:
            copy_file(src, target, progress)
            copied += 1
        except OSError as e:
            errors.append(f"{src}: {e}")

    print()
    elapsed = time.monotonic() - start
    print(c(f"\nDone in {elapsed:.0f}s: {copied} copied, {skipped} already present, {len(errors)} errors.",
            "32" if not errors else "33"))
    for e in errors:
        print(c(f"  ! {e}", "31"))
    return copied, skipped, errors


# --------------------------------------------------------------------- main

def pick_sources(args, library):
    if args.source:
        sources = [Path(s) for s in args.source]
        for s in sources:
            if not s.is_dir():
                sys.exit(f"Not a folder: {s}")
        return sources, []

    cards = detect_cards(library)
    if not cards:
        print(c("No SD card / removable drive found.", "33"))
        path = ask("Insert a card and press Enter to rescan, or type a folder path")
        if path:
            args.source = [path]
        return pick_sources(args, library)

    labels = [f"{cd['label']}  ({cd['size']}, {cd['mount']})" for cd in cards]
    if len(cards) == 1:
        print(f"Found card: {c(labels[0], '36')}")
        return [cards[0]["mount"]], cards
    idx = choose("Which card?", labels + ["All of them"])
    chosen = cards if idx == len(cards) else [cards[idx]]
    return [cd["mount"] for cd in chosen], chosen


def eject(cards):
    if not shutil.which("udisksctl"):
        print("Unmount the card in your file manager before removing it.")
        return
    if not yes("\nEject the card(s)?"):
        return
    for cd in cards:
        r = subprocess.run(["udisksctl", "unmount", "-b", cd["device"]], capture_output=True, text=True)
        print(r.stdout.strip() or r.stderr.strip())
        subprocess.run(["udisksctl", "power-off", "-b", cd["device"]], capture_output=True)


def main():
    parser = argparse.ArgumentParser(description="Import SD card media into a photo/video library.")
    parser.add_argument("source", nargs="*", help="card mount point(s) or folder(s); auto-detected if omitted")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without copying")
    parser.add_argument("--date", help="import date YYYY-MM-DD (default: today)")
    parser.add_argument("--setup", action="store_true", help="choose terminal, file manager, library and categories")
    parser.add_argument("--pause", action="store_true",
                        help="wait for Enter before exiting (keeps a launcher's terminal window open)")
    parser.add_argument("--no-terminal", action="store_true",
                        help="never reopen inside a terminal window, even without one")
    args = parser.parse_args()

    config = load_config()
    has_display = os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")
    if not args.no_terminal and has_display and not sys.stdin.isatty() and not sys.stdout.isatty():
        relaunch_in_terminal(config)
    if args.pause:
        atexit.register(pause)

    if args.setup or not config:
        config = setup(config)
        if args.setup:
            return

    library = Path(config["library"])
    if not library.is_dir():
        sys.exit(f"Library folder not found (is the drive mounted?): {library}")

    print(c("SD Import", "1;35"))
    sources, cards = pick_sources(args, library)

    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    cats = config["categories"]
    category = cats[choose("Import into:", cats)]
    name = ask("Shoot / client / event name (optional, Enter to skip)")
    folder = today.isoformat() + (f" {name}" if name else "")
    base = year_root(library / category) / str(today.year) / folder  # existing year folder is reused

    copied, skipped, errors = run_import(sources, base, library, args.dry_run)
    if args.dry_run:
        return

    notify("Import finished" if not errors else "Import finished with errors",
           f"{copied} copied, {skipped} skipped\n{base}")

    if cards and not errors:
        eject(cards)

    if base.is_dir() and yes("Open the import folder?", False):
        open_folder(config, base)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(c("\nCancelled. Partially copied files end in .part and can be deleted.", "33"))
        sys.exit(130)
