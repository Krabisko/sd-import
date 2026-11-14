#!/usr/bin/env python3
"""Import photos, videos and other files from SD cards into the Photo & Video library.

Layout created:
    <LIBRARY>/01_personal/by year/<YYYY>/<YYYY-MM-DD[ name]>/...
    <LIBRARY>/02_clients/<YYYY>/<YYYY-MM-DD[ name]>/...

Inside the import folder:
    Photo/RAW, Photo/JPEG   (split only when the card holds both formats)
    Photo/                  (otherwise)
    Video/
    Other/                  (drone telemetry, proxies, sidecars, anything unknown)

Nothing is ever deleted from the card. Re-running an import into the same folder
skips files that were already copied (same name and size).

Usage:
    sd-import                  # auto-detect inserted cards
    sd-import /run/media/...   # import a specific mount point / folder
    sd-import --dry-run        # show what would happen, copy nothing
"""

import argparse
import atexit
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LIBRARY = Path(os.environ.get("SD_IMPORT_LIBRARY", "/mnt/8tbhdd/8tbhdd/Photo & Video"))
PERSONAL = LIBRARY / "01_personal" / "by year"
CLIENTS = LIBRARY / "02_clients"

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


def choose(prompt, options):
    """Numbered menu; returns the chosen index."""
    print(c(prompt, "1"))
    for i, label in enumerate(options, 1):
        print(f"  {c(i, '36')}) {label}")
    while True:
        answer = ask("Choose")
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


# ------------------------------------------------------------ card detection

def detect_cards():
    """Mounted removable / hot-plugged filesystems (SD readers, USB card readers)."""
    try:
        out = subprocess.run(
            ["lsblk", "-J", "-o", "NAME,PATH,RM,HOTPLUG,TRAN,MOUNTPOINTS,LABEL,SIZE"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return []

    cards = []

    def walk(dev, removable):
        removable = removable or dev.get("rm") or dev.get("hotplug") or dev.get("tran") in ("usb", "mmc")
        for mp in dev.get("mountpoints") or []:
            if mp and removable and not mp.startswith(("/boot", "/mnt/8tbhdd")) and mp != "/":
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


def run_import(sources, base, dry_run):
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

    LIBRARY.mkdir(exist_ok=True)
    free = shutil.disk_usage(LIBRARY).free
    if total > free:
        print(c(f"\nNot enough space: need {human(total)}, only {human(free)} free.", "31"))
        sys.exit(1)

    if dry_run:
        print(c("\nDry run - nothing copied.", "33"))
        return 0, 0, []
    if ask("\nStart import? (Y/n)", "y").lower() not in ("y", "yes"):
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

def pick_sources(args):
    if args.source:
        sources = [Path(s) for s in args.source]
        for s in sources:
            if not s.is_dir():
                sys.exit(f"Not a folder: {s}")
        return sources, []

    cards = detect_cards()
    if not cards:
        print(c("No SD card / removable drive found.", "33"))
        path = ask("Insert a card and press Enter to rescan, or type a folder path")
        if path:
            return pick_sources(argparse.Namespace(source=[path]))
        return pick_sources(args)

    labels = [f"{cd['label']}  ({cd['size']}, {cd['mount']})" for cd in cards]
    if len(cards) == 1:
        print(f"Found card: {c(labels[0], '36')}")
        return [cards[0]["mount"]], cards
    idx = choose("Which card?", labels + ["All of them"])
    chosen = cards if idx == len(cards) else [cards[idx]]
    return [cd["mount"] for cd in chosen], chosen


def main():
    parser = argparse.ArgumentParser(description="Import SD card media into the Photo & Video library.")
    parser.add_argument("source", nargs="*", help="card mount point(s) or folder(s); auto-detected if omitted")
    parser.add_argument("--dry-run", action="store_true", help="show the plan without copying")
    parser.add_argument("--date", help="import date YYYY-MM-DD (default: today)")
    parser.add_argument("--pause", action="store_true",
                        help="wait for Enter before exiting (keeps a launcher's terminal window open)")
    args = parser.parse_args()
    if args.pause:
        atexit.register(pause)

    if not LIBRARY.parent.is_dir():
        sys.exit(f"Library drive not mounted: {LIBRARY.parent}")

    print(c("SD Import", "1;35"))
    sources, cards = pick_sources(args)

    today = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    year = str(today.year)

    which = choose("Import into:", ["01_personal", "02_clients"])
    name = ask("Shoot / client name (optional, Enter to skip)")
    folder = today.isoformat() + (f" {name}" if name else "")
    root = (PERSONAL if which == 0 else CLIENTS) / year  # reused if it already exists
    base = root / folder

    copied, skipped, errors = run_import(sources, base, args.dry_run)
    if args.dry_run:
        return

    notify("Import finished" if not errors else "Import finished with errors",
           f"{copied} copied, {skipped} skipped\n{base}")

    if cards and not errors and ask("\nEject the card(s)? (Y/n)", "y").lower() in ("y", "yes"):
        for cd in cards:
            r = subprocess.run(["udisksctl", "unmount", "-b", cd["device"]], capture_output=True, text=True)
            print(r.stdout.strip() or r.stderr.strip())
            subprocess.run(["udisksctl", "power-off", "-b", cd["device"]], capture_output=True)

    if shutil.which("xdg-open") and ask("Open the import folder? (y/N)", "n").lower() in ("y", "yes"):
        # Own session: otherwise the terminal's hangup on exit kills xdg-open before the
        # file manager starts (seen on COSMIC, where xdg-open takes the slower generic path).
        subprocess.Popen(["xdg-open", str(base)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(c("\nCancelled. Partially copied files end in .part and can be deleted.", "33"))
        sys.exit(130)
