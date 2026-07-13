#!/usr/bin/env python3
"""
qsv2mp4.py  –  Convert iQIYI .qsv files to .mp4

iQIYI's QSV v2 container embeds MPEG-TS video segments with the first 1024
bytes of each segment scrambled by a proprietary shuffle cipher.  This script
reverses that cipher, reassembles the clean MPEG-TS stream, and hands it to
ffmpeg for a lossless stream-copy to MP4.

Usage examples
--------------
  # Single file
  python qsv2mp4.py video.qsv
  python qsv2mp4.py -o ~/Movies  video.qsv

  # Whole directory (non-recursive)
  python qsv2mp4.py /path/to/dir

  # Recursive scan (finds .qsv anywhere under the tree)
  python qsv2mp4.py -r /path/to/dir

  # Skip files that already have an .mp4 next to them
  python qsv2mp4.py -r --skip-existing /path/to/dir

  # Keep the intermediate .ts file as well
  python qsv2mp4.py --keep-ts video.qsv

  # Dry-run: list what would be converted, do nothing
  python qsv2mp4.py --dry-run -r /path/to/dir

Options
-------
  input            One or more .qsv files or directories
  -o, --output     Output directory (default: same dir as each input file)
  -r, --recursive  Scan directories recursively for .qsv files
  --skip-existing  Skip if the .mp4 output already exists
  --keep-ts        Keep the intermediate MPEG-TS file
  --dry-run        Print what would be done without converting
  -v, --verbose    Show ffmpeg output
"""

import argparse
import os
import struct
import subprocess
import sys
import tempfile
import time


# ─────────────────────────────────────────────────────────────────────────────
# Decryption  (reverse-engineered iQIYI shuffle cipher, key 0x62677079)
# ─────────────────────────────────────────────────────────────────────────────

def _decrypt_1(buf: bytearray) -> None:
    """XOR each byte with the rotating 4-byte key [0x62, 0x67, 0x70, 0x79].
    Used only for the embedded XML metadata."""
    key = (0x79, 0x70, 0x67, 0x62)          # ~i & 3  gives reverse order
    for i in range(len(buf) // 4 * 4):
        buf[i] ^= key[i & 3]


def _decrypt_2(buf: bytearray) -> None:
    """Reverse the proprietary shuffle cipher applied to:
      - each 28-byte segment-index entry
      - the first 1024 bytes of every MPEG-TS segment
    """
    n = len(buf)
    x = 0x62677079
    for i in range(n - 1, 0, -1):
        x = ((x << 1) | (x >> 31)) & 0xFFFFFFFF
        x ^= buf[i]
    for i in range(1, n):
        x ^= buf[i]
        x = ((x >> 1) | (x << 31)) & 0xFFFFFFFF
        j = x % i
        tmp        = buf[j]
        buf[j]     = tmp ^ (~buf[i] & 0xFF)
        buf[i]     = tmp


# ─────────────────────────────────────────────────────────────────────────────
# QSV binary format
# ─────────────────────────────────────────────────────────────────────────────

# Fixed 90-byte file header
#   "<" = little-endian
#   10s = ASCII magic "QIYI VIDEO"
#   I   = format version  (must be 2)
#   16s = vid  (opaque video-id bytes)
#   I   = unknown1
#   32s = unknown2
#   I   = unknown3
#   I   = unknown4
#   Q   = xml_offset  (absolute, uint64)
#   I   = xml_size
#   I   = nb_indices  (number of MPEG-TS segments)
_HEADER = struct.Struct("<10s I 16s I 32s I I Q I I")

# Each 28-byte segment-index entry (after decrypt_2)
#   16s = segment file-hash (identifies the original CDN .ts shard)
#   Q   = absolute byte offset of this segment inside the QSV file
#   I   = byte length of this segment
_INDEX = struct.Struct("<16s Q I")

QSV_MAGIC = b"QIYI VIDEO"


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

class QsvParseError(Exception):
    pass


def _parse_header(f):
    """Read and validate the fixed header.  Returns the unpacked tuple."""
    raw = f.read(_HEADER.size)
    if len(raw) < _HEADER.size:
        raise QsvParseError("File too small to contain a valid QSV header")
    fields = _HEADER.unpack_from(raw)
    magic, version = fields[0], fields[1]
    if magic != QSV_MAGIC:
        raise QsvParseError(f"Not a QSV file (bad magic: {magic!r})")
    if version != 2:
        raise QsvParseError(f"Unsupported QSV version {version} (only v2 supported)")
    return fields


def parse_qsv(path: str):
    """
    Open and parse *path*.

    Returns
    -------
    indices : list of (file_id_hex, abs_offset, byte_size)
    total_ts_bytes : int   – sum of all segment sizes
    """
    with open(path, "rb") as f:
        (magic, version, vid, u1, u2, u3, u4,
         xml_offset, xml_size, nb_indices) = _parse_header(f)

        # Between the fixed header and the index table there is a small
        # bitmap whose size depends on nb_indices.
        flag_bitmap_size = (nb_indices + 7) >> 3
        f.seek(flag_bitmap_size, 1)

        indices = []
        for _ in range(nb_indices):
            raw = bytearray(f.read(_INDEX.size))
            if len(raw) < _INDEX.size:
                break
            _decrypt_2(raw)
            file_id_bytes, offset, size = _INDEX.unpack_from(raw)
            indices.append((file_id_bytes.hex(), offset, size))

    if not indices:
        raise QsvParseError("No segment indices found")

    total = sum(sz for _, _, sz in indices)
    return indices, total


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────

def extract_ts(qsv_path: str, ts_path: str, indices, total: int,
               verbose: bool = False) -> None:
    """
    Copy the concatenated MPEG-TS payload out of *qsv_path* into *ts_path*,
    then decrypt the first 1024 bytes of each segment in-place.
    """
    READ_CHUNK = 1 << 17        # 128 KiB
    base_offset = indices[0][1]

    start = time.time()

    with open(qsv_path, "rb") as src, open(ts_path, "w+b") as dst:

        # ── Phase 1 : stream copy ────────────────────────────────────────────
        src.seek(base_offset)
        written = 0
        while written < total:
            want  = min(READ_CHUNK, total - written)
            chunk = src.read(want)
            if not chunk:
                break
            dst.write(chunk)
            written += len(chunk)
            if verbose:
                pct = written / total * 100
                elapsed = time.time() - start
                mb_s = (written / (1 << 20)) / max(elapsed, 0.001)
                print(f"\r  Extracting … {pct:5.1f}%  {mb_s:6.1f} MB/s", end="")

        if verbose:
            print()

        # ── Phase 2 : per-segment decryption (in-place) ──────────────────────
        for _, seg_offset, _ in indices:
            rel = seg_offset - base_offset
            dst.seek(rel)
            head = bytearray(dst.read(1024))
            _decrypt_2(head)
            dst.seek(rel)
            dst.write(head)


# ─────────────────────────────────────────────────────────────────────────────
# ffmpeg wrapper
# ─────────────────────────────────────────────────────────────────────────────

def _find_ffmpeg() -> str:
    """Return the path to ffmpeg, or raise if not found."""
    import shutil
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError(
            "ffmpeg not found.  Install it with:  brew install ffmpeg"
        )
    return ff


def ts_to_mp4(ts_path: str, mp4_path: str, verbose: bool = False) -> bool:
    """
    Stream-copy *ts_path* → *mp4_path* via ffmpeg.
    Returns True on success.
    """
    ffmpeg = _find_ffmpeg()
    cmd = [
        ffmpeg, "-y",
        "-i",      ts_path,
        "-c",      "copy",
        "-bsf:a",  "aac_adtstoasc",   # reframe ADTS AAC → MP4-style
        mp4_path,
    ]
    if verbose:
        print(f"  $ {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        err = result.stderr.decode(errors="replace")
        # Show last 40 lines of ffmpeg output on failure
        lines = err.strip().splitlines()
        print("\n  [ffmpeg error output]")
        for line in lines[-40:]:
            print("  ", line)
        return False

    if verbose:
        # Print ffmpeg's progress summary (last few lines)
        err = result.stderr.decode(errors="replace")
        for line in err.strip().splitlines()[-5:]:
            print("  ", line)

    return True


# ─────────────────────────────────────────────────────────────────────────────
# High-level convert_one
# ─────────────────────────────────────────────────────────────────────────────

def convert_one(qsv_path: str,
                output_dir = None,
                keep_ts: bool = False,
                skip_existing: bool = False,
                dry_run: bool = False,
                verbose: bool = False) -> bool:
    """
    Convert a single QSV file to MP4.

    Parameters
    ----------
    qsv_path     : absolute or relative path to the .qsv file
    output_dir   : where to write the .mp4 (None = same directory as input)
    keep_ts      : keep the intermediate .ts file
    skip_existing: skip if a .mp4 already exists at the destination
    dry_run      : print plan only, do not write anything
    verbose      : extra logging

    Returns True on success.
    """
    qsv_path  = os.path.abspath(qsv_path)
    stem      = os.path.splitext(os.path.basename(qsv_path))[0]
    dest_dir  = output_dir or os.path.dirname(qsv_path)
    mp4_path  = os.path.join(dest_dir, stem + ".mp4")
    ts_path   = os.path.join(dest_dir, stem + ".ts") if keep_ts else None

    SEP = "─" * 60
    print(f"\n{SEP}")
    print(f"  Input  : {qsv_path}")
    print(f"  Output : {mp4_path}")

    if skip_existing and os.path.exists(mp4_path):
        print("  Skipping – output already exists.")
        return True

    if dry_run:
        print("  [dry-run] Would convert.")
        return True

    # ── Parse ─────────────────────────────────────────────────────────────────
    try:
        indices, total = parse_qsv(qsv_path)
    except QsvParseError as exc:
        print(f"  [SKIP] {exc}")
        return False
    except Exception as exc:
        print(f"  [ERROR] Unexpected parse error: {exc}")
        return False

    seg_count = len(indices)
    total_mb  = total / (1 << 20)
    print(f"  Segments: {seg_count}   TS payload: {total_mb:.1f} MB")

    os.makedirs(dest_dir, exist_ok=True)

    # ── Extract ──────────────────────────────────────────────────────────────
    if ts_path:
        work_ts = ts_path
        cleanup = False
    else:
        fd, work_ts = tempfile.mkstemp(suffix=".ts")
        os.close(fd)
        cleanup = True

    t0 = time.time()
    try:
        print("  Extracting & decrypting MPEG-TS …", flush=True)
        extract_ts(qsv_path, work_ts, indices, total, verbose=verbose)
        elapsed = time.time() - t0
        print(f"  Extracted in {elapsed:.1f}s")

        # ── Convert ────────────────────────────────────────────────────────────
        print("  Muxing TS → MP4 via ffmpeg …", flush=True)
        t1 = time.time()
        ok = ts_to_mp4(work_ts, mp4_path, verbose=verbose)
        if ok:
            elapsed2 = time.time() - t1
            out_mb = os.path.getsize(mp4_path) / (1 << 20)
            print(f"  Muxed in {elapsed2:.1f}s  |  Output: {out_mb:.1f} MB")
            print(f"  ✓ Done!  {mp4_path}")
        else:
            print("  ✗ ffmpeg failed.")
        return ok

    except Exception as exc:
        print(f"  [ERROR] {exc}")
        return False

    finally:
        if cleanup:
            try:
                os.remove(work_ts)
            except OSError:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# File discovery
# ─────────────────────────────────────────────────────────────────────────────

def _find_qsv_files(paths, recursive: bool) -> list[str]:
    """
    Expand *paths* into a flat, sorted list of .qsv file paths.
    Directories are scanned (recursively if *recursive* is True).
    Plain file arguments are included as-is.
    """
    found = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            if p.lower().endswith(".qsv"):
                found.append(p)
            else:
                print(f"[warn] '{p}' does not look like a .qsv file – skipping",
                      file=sys.stderr)
        elif os.path.isdir(p):
            if recursive:
                for root, _, files in os.walk(p):
                    for fn in files:
                        if fn.lower().endswith(".qsv"):
                            found.append(os.path.join(root, fn))
            else:
                for fn in os.listdir(p):
                    if fn.lower().endswith(".qsv"):
                        found.append(os.path.join(p, fn))
        else:
            print(f"[warn] '{p}' not found – skipping", file=sys.stderr)

    return sorted(found)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qsv2mp4",
        description="Convert iQIYI .qsv files to .mp4  (lossless stream copy via ffmpeg)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument(
        "inputs", nargs="*", metavar="INPUT",
        help=".qsv file(s) or director(ies) to convert  (default: current directory)",
    )
    p.add_argument(
        "-o", "--output", metavar="DIR",
        help="Write .mp4 files here instead of alongside each input",
    )
    p.add_argument(
        "-r", "--recursive", action="store_true",
        help="Scan directories recursively for .qsv files",
    )
    p.add_argument(
        "--skip-existing", action="store_true",
        help="Skip conversion if a .mp4 already exists at the destination",
    )
    p.add_argument(
        "--keep-ts", action="store_true",
        help="Keep the intermediate MPEG-TS file alongside the .mp4",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be converted without doing anything",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show extraction progress and full ffmpeg output",
    )
    return p


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    # Default to current directory if nothing given
    inputs = args.inputs or ["."]

    files = _find_qsv_files(inputs, recursive=args.recursive)

    if not files:
        print("No .qsv files found.")
        sys.exit(0)

    print(f"Found {len(files)} .qsv file(s) to convert.")
    if args.dry_run:
        print("[dry-run mode – no files will be written]\n")

    success = 0
    for i, qsv in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}]", end="")
        if convert_one(
            qsv,
            output_dir=args.output,
            keep_ts=args.keep_ts,
            skip_existing=args.skip_existing,
            dry_run=args.dry_run,
            verbose=args.verbose,
        ):
            success += 1

    SEP = "═" * 60
    print(f"\n{SEP}")
    print(f"  Result: {success}/{len(files)} converted successfully.")
    if success < len(files):
        print(f"  Failed: {len(files) - success} file(s) — check output above for details.")
    print(SEP)

    sys.exit(0 if success == len(files) else 1)


if __name__ == "__main__":
    main()
