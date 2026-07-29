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

  # Diagnose a file that converts but plays back wrong
  python qsv2mp4.py --inspect video.qsv

Options
-------
  input            One or more .qsv files or directories
  -o, --output     Output directory (default: same dir as each input file)
  -r, --recursive  Scan directories recursively for .qsv files
  --skip-existing  Skip if the .mp4 output already exists
  --keep-ts        Keep the intermediate MPEG-TS file
  --dry-run        Print what would be done without converting
  --inspect        Dump container layout / TS sync / per-segment clocks
  --mux-mode       auto (default) | single | concat
  --salvage-clear  On a DRM-protected file, convert the unencrypted segments
  -v, --verbose    Show ffmpeg output

Troubleshooting
---------------
If the .mp4 is produced but plays back wrong, run --inspect.  It reports, per
segment, whether the index offsets are consistent, whether the descrambled data
starts on an MPEG-TS sync byte, whether the segments share one clock, and
whether a decoder can actually read each one.

Segments with independent clocks need --mux-mode concat, which muxes each one
separately instead of byte-splicing them into a single stream.

Some downloads are DRM-protected: the container unpacks perfectly, but the
video is encrypted under a key that lives on iQIYI's licence server and was
never written to the file.  Nothing here can decode those, so such files are
refused rather than converted into an unplayable result; --salvage-clear
converts whatever segments are in the clear.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
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
# MPEG-TS probing  (used for validation & diagnostics)
# ─────────────────────────────────────────────────────────────────────────────

TS_PACKET = 188


def find_ts_sync(buf: bytes, runs: int = 5) -> int:
    """
    Return the offset of the first byte that starts a run of *runs* MPEG-TS
    packets (0x47 every 188 bytes), or -1 if the buffer never locks on.
    """
    limit = min(len(buf), TS_PACKET * 2)
    for off in range(limit):
        if buf[off] != 0x47:
            continue
        if all(off + k * TS_PACKET < len(buf) and buf[off + k * TS_PACKET] == 0x47
               for k in range(runs)):
            return off
    return -1


def first_pcr(buf: bytes, sync: int = 0) -> float | None:
    """
    Scan TS packets starting at *sync* and return the first PCR found, in
    seconds.  Returns None if no packet in *buf* carries a PCR.

    The PCR is the segment's position on the stream clock; comparing it across
    segments tells us whether the shards share one timeline or each restart
    at zero.
    """
    for i in range(sync, len(buf) - TS_PACKET + 1, TS_PACKET):
        if buf[i] != 0x47:
            continue
        afc = (buf[i + 3] >> 4) & 0b11
        if afc not in (0b10, 0b11):        # no adaptation field -> no PCR
            continue
        af_len = buf[i + 4]
        if af_len < 7:
            continue
        if not (buf[i + 5] & 0x10):        # PCR_flag
            continue
        b = buf[i + 6:i + 12]
        base = ((b[0] << 25) | (b[1] << 17) | (b[2] << 9) |
                (b[3] << 1) | (b[4] >> 7))
        return base / 90000.0
    return None


def first_pts(buf: bytes, sync: int = 0) -> float | None:
    """
    Scan TS packets starting at *sync* and return the first PES presentation
    timestamp found, in seconds.

    The PCR is the transport clock; the PTS is what the muxer actually writes
    into the MP4.  They usually agree, but a container assembled from shards can
    have a tidy PCR and a broken PTS, so both are worth checking.
    """
    for i in range(sync, len(buf) - TS_PACKET + 1, TS_PACKET):
        if buf[i] != 0x47:
            continue
        if not (buf[i + 1] & 0x40):        # payload_unit_start_indicator
            continue
        afc = (buf[i + 3] >> 4) & 0b11
        if afc in (0b00, 0b10):            # no payload
            continue
        p = i + 4
        if afc == 0b11:                    # skip the adaptation field
            p += 1 + buf[i + 4]
        if p + 14 > i + TS_PACKET:
            continue
        if buf[p:p + 3] != b"\x00\x00\x01":
            continue
        if not (0xC0 <= buf[p + 3] <= 0xEF):   # audio or video PES only
            continue
        if not (buf[p + 7] & 0x80):            # PTS_DTS_flags
            continue
        b = buf[p + 9:p + 14]
        pts = (((b[0] >> 1) & 0x07) << 30 |
               b[1] << 22 |
               ((b[2] >> 1) & 0x7F) << 15 |
               b[3] << 7 |
               b[4] >> 1)
        return pts / 90000.0
    return None


def _fmt_clock(sec) -> str:
    if sec is None:
        return "     —      "
    h, rem = divmod(int(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}.{int((sec % 1) * 100):02d}"


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
    meta : dict            – header fields + file layout facts
    """
    file_size = os.path.getsize(path)

    with open(path, "rb") as f:
        (magic, version, vid, u1, u2, u3, u4,
         xml_offset, xml_size, nb_indices) = _parse_header(f)

        # Between the fixed header and the index table there is a small
        # bitmap whose size depends on nb_indices.
        flag_bitmap_size = (nb_indices + 7) >> 3
        f.seek(flag_bitmap_size, 1)
        index_table_offset = f.tell()

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

    # Layout sanity: are the segments actually back-to-back?  The old
    # extractor assumed they were and copied one contiguous run of bytes;
    # anything non-contiguous silently produced a corrupt stream.
    gaps = []
    overruns = []
    prev_end = None
    for n, (_, off, size) in enumerate(indices, 1):
        if prev_end is not None and off != prev_end:
            gaps.append((n, off - prev_end))
        if off + size > file_size:
            overruns.append((n, off + size - file_size))
        prev_end = off + size

    meta = {
        "file_size":          file_size,
        "version":            version,
        "vid":                vid.hex(),
        "xml_offset":         xml_offset,
        "xml_size":           xml_size,
        "nb_indices":         nb_indices,
        "index_table_offset": index_table_offset,
        "gaps":               gaps,
        "overruns":           overruns,
        "contiguous":         not gaps,
    }
    return indices, total, meta


def read_metadata(path: str, meta: dict):
    """
    Decrypt and parse the JSON descriptor iQIYI stores between the index table
    and the first segment.  Returns the ``qsv_info`` object, or None.

    Everything read here is informational, so any failure returns None rather
    than raising — a container whose descriptor moved or changed shape must
    still convert exactly as it did before.
    """
    off, size = meta.get("xml_offset") or 0, meta.get("xml_size") or 0
    if off <= 0 or not 0 < size <= (16 << 20):
        return None
    try:
        with open(path, "rb") as f:
            f.seek(off)
            blob = bytearray(f.read(size))
        _decrypt_1(blob)
        start = blob.find(b"{")
        if start < 0:
            return None
        obj, _ = json.JSONDecoder().raw_decode(
            blob[start:].decode("utf-8", "replace"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    info = obj.get("qsv_info")
    return info if isinstance(info, dict) else None


def drm_info(qsv_info):
    """
    Describe the DRM declared in the descriptor, or None if it declares none.

    The licence ticket is the decisive signal.  It is the blob a player trades
    with iQIYI's licence server for the content key, so its presence means the
    elementary stream is encrypted under a key that is deliberately *not* in
    this file — no amount of work on the container will produce clear video.
    """
    if not isinstance(qsv_info, dict):
        return None
    ticket = qsv_info.get("drmticket")
    ticket = ticket if isinstance(ticket, dict) else {}
    data = ticket.get("ticketdata")
    version = qsv_info.get("drmversion")
    if not data and not version:
        return None

    bits = []
    if version:
        bits.append(f"version {version}")
    if qsv_info.get("dr") is not None:
        bits.append(f"dr={qsv_info['dr']}")
    if qsv_info.get("edrtype"):
        bits.append(f"edrtype={qsv_info['edrtype']}")
    if data:
        bits.append(f"licence ticket {len(data)} chars")
    return {
        "version": version,
        "ticket":  bool(data),
        "label":   "iQIYI DRM (" + ", ".join(bits) + ")",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Extraction
# ─────────────────────────────────────────────────────────────────────────────

READ_CHUNK = 1 << 20            # 1 MiB
HEAD_LEN   = 1024               # scrambled prefix of every segment


def _copy_segment(src, dst, n: int, seg_offset: int, seg_size: int,
                  file_size: int, warnings: list) -> int:
    """
    Seek to *seg_offset*, descramble the segment's 1024-byte head, and copy the
    whole segment to *dst*.  Returns the number of bytes written.

    Every segment is located by its own index offset — segments are NOT assumed
    to sit back-to-back in the container.
    """
    if seg_offset + seg_size > file_size:
        short = seg_offset + seg_size - file_size
        warnings.append(
            f"segment {n}: index says {seg_size:,} bytes at {seg_offset:,}, "
            f"but that runs {short:,} bytes past EOF — file is truncated"
        )
        seg_size = max(0, file_size - seg_offset)
    if seg_size <= 0:
        warnings.append(f"segment {n}: empty, skipped")
        return 0

    src.seek(seg_offset)
    head_len = min(HEAD_LEN, seg_size)
    head = bytearray(src.read(head_len))
    if len(head) < head_len:
        warnings.append(f"segment {n}: short read on head")
        head_len = len(head)
    _decrypt_2(head)

    sync = find_ts_sync(head)
    if sync != 0:
        got = f"0x{head[0]:02X}" if head else "nothing"
        warnings.append(
            f"segment {n}: no MPEG-TS sync byte at the start after decryption "
            f"(got {got}, lock-on at {sync}) — cipher or format mismatch"
        )

    dst.write(head)
    written = head_len

    remaining = seg_size - head_len
    while remaining > 0:
        chunk = src.read(min(READ_CHUNK, remaining))
        if not chunk:
            warnings.append(f"segment {n}: unexpected EOF, {remaining:,} bytes short")
            break
        dst.write(chunk)
        written   += len(chunk)
        remaining -= len(chunk)

    return written


def _progress(written: int, total: int, start: float) -> None:
    pct     = written / total * 100 if total else 100.0
    elapsed = time.time() - start
    mb_s    = (written / (1 << 20)) / max(elapsed, 0.001)
    print(f"\r  Extracting … {pct:5.1f}%  {mb_s:6.1f} MB/s", end="", flush=True)


def extract_ts(qsv_path: str, ts_path: str, indices, total: int,
               verbose: bool = False):
    """
    Copy every MPEG-TS segment out of *qsv_path* into a single *ts_path*,
    descrambling each segment's head as it goes.

    Returns (bytes_written, warnings).
    """
    warnings = []
    written  = 0
    start    = time.time()

    with open(qsv_path, "rb") as src, open(ts_path, "wb") as dst:
        file_size = os.fstat(src.fileno()).st_size
        for n, (_, seg_offset, seg_size) in enumerate(indices, 1):
            written += _copy_segment(src, dst, n, seg_offset, seg_size,
                                     file_size, warnings)
            if verbose:
                _progress(written, total, start)

    if verbose:
        print()
    return written, warnings


def extract_segments(qsv_path: str, out_dir: str, indices, total: int,
                     verbose: bool = False):
    """
    Same as :func:`extract_ts`, but writes one .ts file per segment.

    Used when the segments carry independent clocks: ffmpeg's concat demuxer
    re-bases each input's timestamps, which a single spliced file cannot do.

    Returns (paths, warnings).
    """
    warnings = []
    paths    = []
    written  = 0
    start    = time.time()

    with open(qsv_path, "rb") as src:
        file_size = os.fstat(src.fileno()).st_size
        for n, (_, seg_offset, seg_size) in enumerate(indices, 1):
            path = os.path.join(out_dir, f"seg_{n:04d}.ts")
            with open(path, "wb") as dst:
                got = _copy_segment(src, dst, n, seg_offset, seg_size,
                                    file_size, warnings)
            if got:
                paths.append(path)
                written += got
            if verbose:
                _progress(written, total, start)

    if verbose:
        print()
    return paths, warnings


def probe_segments(qsv_path: str, indices, probe_bytes: int = 1 << 16):
    """
    Read the first *probe_bytes* of every segment (descrambling the head) and
    report where TS sync locks on and what the first PCR is.

    Returns a list of dicts, one per segment.
    """
    out = []
    with open(qsv_path, "rb") as src:
        file_size = os.fstat(src.fileno()).st_size
        for n, (fid, seg_offset, seg_size) in enumerate(indices, 1):
            avail = max(0, min(seg_size, file_size - seg_offset))
            src.seek(seg_offset)
            buf = bytearray(src.read(min(probe_bytes, avail)))
            head_len = min(HEAD_LEN, len(buf))
            if head_len:
                head = bytearray(buf[:head_len])
                _decrypt_2(head)
                buf[:head_len] = head
            sync = find_ts_sync(buf)
            out.append({
                "n":      n,
                "fid":    fid,
                "offset": seg_offset,
                "size":   seg_size,
                "sync":   sync,
                "pcr":    first_pcr(buf, sync) if sync >= 0 else None,
                "pts":    first_pts(buf, sync) if sync >= 0 else None,
            })
    return out


# Byte-value lookup tables so the hot loops stay at C speed.
_TEI_TABLE = bytes(1 if (i & 0x80) else 0 for i in range(256))

def scan_segment(src, seg_offset: int, seg_size: int, file_size: int,
                 pid_sample: int = 40000) -> dict:
    """
    Walk one segment packet-by-packet and report transport-level health.

    This is the test that separates "the container was unpacked wrong" from
    "the payload itself is not clear MPEG-TS".  A segment whose 188-byte sync
    grid holds from end to end was extracted correctly, whatever the picture
    looks like afterwards.  A segment that locks on at offset 0 and then loses
    sync partway through is still encrypted past that point.
    """
    CHUNK = TS_PACKET * 8192            # ~1.5 MiB, always whole packets

    avail     = max(0, min(seg_size, file_size - seg_offset))
    packets    = 0
    bad_sync   = 0
    first_bad  = None
    tei        = 0
    pids       = {}

    src.seek(seg_offset)
    remaining = avail
    consumed  = 0
    first     = True

    while remaining >= TS_PACKET:
        want = min(CHUNK, remaining - (remaining % TS_PACKET))
        if want < TS_PACKET:
            break
        buf = src.read(want)
        if not buf:
            break
        if len(buf) % TS_PACKET:
            buf = buf[:len(buf) - (len(buf) % TS_PACKET)]
            if not buf:
                break

        if first:
            head = bytearray(buf[:min(HEAD_LEN, len(buf))])
            _decrypt_2(head)
            buf = bytes(head) + buf[len(head):]
            first = False

        syncs = buf[0::TS_PACKET]
        good  = syncs.count(0x47)
        n     = len(syncs)
        packets  += n
        if good != n:
            bad = n - good
            bad_sync += bad
            if first_bad is None:
                for k in range(n):
                    if syncs[k] != 0x47:
                        first_bad = consumed + k * TS_PACKET
                        break

        tei += buf[1::TS_PACKET].translate(_TEI_TABLE).count(1)

        if len(pids) < 64 and consumed // TS_PACKET < pid_sample:
            b1 = buf[1::TS_PACKET]
            b2 = buf[2::TS_PACKET]
            for hi, lo in zip(b1[:pid_sample], b2[:pid_sample]):
                pid = ((hi & 0x1F) << 8) | lo
                pids[pid] = pids.get(pid, 0) + 1

        consumed  += len(buf)
        remaining -= len(buf)

    return {
        "packets":    packets,
        "bad_sync":   bad_sync,
        "first_bad":  first_bad,
        "tei":        tei,
        "pids":       pids,
        "scanned":    consumed,
        "size":       seg_size,
    }


def probe_decodable(qsv_path: str, indices, sample: int = 12 << 20,
                    verbose: bool = False) -> list:
    """
    Ask a decoder whether each segment stands on its own.

    Byte patterns can only say that two segments *differ*.  What actually
    decides whether a segment survives being muxed on its own is whether a
    decoder can find codec parameters in it, so this extracts the first
    *sample* bytes of each segment and puts ffprobe on them.

    Returns one dict per segment with 'codec', 'width', 'height' and 'ok'.
    """
    import json

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return []

    out = []
    work = tempfile.mkdtemp(prefix="qsv2mp4-probe-")
    try:
        with open(qsv_path, "rb") as src:
            file_size = os.fstat(src.fileno()).st_size
            for n, (_, off, size) in enumerate(indices, 1):
                part = os.path.join(work, f"probe_{n}.ts")
                with open(part, "wb") as dst:
                    _copy_segment(src, dst, n, off, min(size, sample),
                                  file_size, [])
                res = subprocess.run(
                    [ffprobe, "-v", "error", "-show_entries",
                     "stream=codec_type,codec_name,width,height",
                     "-of", "json", part],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                info = {"n": n, "codec": None, "width": None,
                        "height": None, "ok": False}
                try:
                    streams = json.loads(res.stdout or b"{}").get("streams", [])
                except ValueError:
                    streams = []
                for s in streams:
                    if s.get("codec_type") == "video":
                        info["codec"]  = s.get("codec_name")
                        info["width"]  = s.get("width")
                        info["height"] = s.get("height")
                        info["ok"]     = bool(s.get("width"))
                        break
                out.append(info)
                os.remove(part)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def detect_clock_resets(probes, key: str = "pcr") -> list:
    """
    Return the segment numbers whose clock fails to advance past the previous
    segment's.  On one shared timeline every segment starts strictly later than
    the one before it, so a clock that stalls or moves backwards means the
    shards are independent transport streams — splicing those byte-wise yields
    non-monotonic timestamps.

    *key* selects which clock to test: "pcr" (transport) or "pts" (what the
    muxer writes).  They can disagree, and a clean PCR does not imply a clean
    PTS, so both are checked.
    """
    resets = []
    prev = None
    for p in probes:
        t = p.get(key)
        if t is None:
            continue
        if prev is not None and t <= prev:
            resets.append(p["n"])
        prev = t
    return resets


def detect_any_resets(probes) -> tuple:
    """Return (segments, which_clock) for whichever clock looks discontinuous."""
    pcr = detect_clock_resets(probes, "pcr")
    if pcr:
        return pcr, "PCR"
    pts = detect_clock_resets(probes, "pts")
    if pts:
        return pts, "PTS"
    return [], None


# ─────────────────────────────────────────────────────────────────────────────
# ffmpeg wrapper
# ─────────────────────────────────────────────────────────────────────────────

def _find_ffmpeg() -> str:
    """Return the path to ffmpeg, or raise if not found."""
    ff = shutil.which("ffmpeg")
    if not ff:
        raise RuntimeError(
            "ffmpeg not found.  Install it with:  brew install ffmpeg"
        )
    return ff


# ffmpeg exits 0 on all of these.  "severe" ones mean the MP4 will not play
# back correctly; the rest are expected wherever two TS shards are spliced
# (continuity counters restart at every segment boundary) and are reported for
# information only.
_FFMPEG_RED_FLAGS = (
    # needle,                            label,                      severe
    ("Non-monotonous DTS",               "backwards timestamps",     True),
    ("Non-monotonic DTS",                "backwards timestamps",     True),
    ("Invalid timestamps",               "invalid timestamps",       True),
    ("Application provided invalid",     "invalid input timestamps", True),
    ("could not find corresponding trex", "broken stream header",    True),
    ("Packet corrupt",                   "corrupt packets",          False),
    ("corrupt input packet",             "corrupt packets",          False),
)

_SEVERE_LABELS = {label for _, label, severe in _FFMPEG_RED_FLAGS if severe}


def _generalise(line: str) -> str:
    """Collapse a log line to a shape, so repeats with different numbers group."""
    out = []
    prev_digit = False
    for ch in line:
        if ch.isdigit():
            if not prev_digit:
                out.append("#")
            prev_digit = True
        else:
            out.append(ch)
            prev_digit = False
    return "".join(out)


def _summarise_ffmpeg(stderr: str) -> dict:
    """Count the stderr lines that mean 'exit 0, but look closer'."""
    counts = {}
    for line in stderr.splitlines():
        for needle, label, _severe in _FFMPEG_RED_FLAGS:
            if needle in line:
                counts[label] = counts.get(label, 0) + 1
                break
    return counts


def _run_ffmpeg(cmd, verbose: bool):
    """Run ffmpeg; return (ok, red_flag_counts)."""
    if verbose:
        print(f"  $ {' '.join(cmd)}")

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    err = result.stderr.decode(errors="replace")

    if result.returncode != 0:
        print("\n  [ffmpeg error output]")
        for line in err.strip().splitlines()[-40:]:
            print("  ", line)
        return False, {}

    flags = _summarise_ffmpeg(err)
    if verbose:
        # Showing only the tail hides the decoder complaints that explain a bad
        # picture, so collapse repeats instead of truncating: one line per
        # distinct message, with a count.
        seen = {}
        for line in err.strip().splitlines():
            key = _generalise(line)
            entry = seen.setdefault(key, [0, line])
            entry[0] += 1
        for count, sample in seen.values():
            suffix = f"   (×{count:,})" if count > 1 else ""
            print("  ", sample.strip() + suffix)
    return True, flags


def ts_to_mp4(ts_path: str, mp4_path: str, verbose: bool = False):
    """
    Stream-copy *ts_path* → *mp4_path* via ffmpeg.
    Returns (ok, red_flag_counts).
    """
    cmd = [
        _find_ffmpeg(), "-y",
        "-i",      ts_path,
        "-c",      "copy",
        "-bsf:a",  "aac_adtstoasc",   # reframe ADTS AAC → MP4-style
        mp4_path,
    ]
    return _run_ffmpeg(cmd, verbose)


def segments_to_mp4(seg_paths, mp4_path: str, work_dir: str,
                    verbose: bool = False):
    """
    Join the per-segment .ts files via ffmpeg's concat demuxer.

    Unlike byte-splicing the segments into one file, the concat demuxer re-bases
    each input's timestamps onto the running output clock, so segments that each
    start near PCR 0 still produce a monotonic MP4.

    Returns (ok, red_flag_counts).
    """
    list_path = os.path.join(work_dir, "concat.txt")
    with open(list_path, "w", encoding="utf-8") as fh:
        for p in seg_paths:
            escaped = p.replace("'", r"'\''")
            fh.write(f"file '{escaped}'\n")

    cmd = [
        _find_ffmpeg(), "-y",
        "-f",      "concat",
        "-safe",   "0",
        "-i",      list_path,
        "-c",      "copy",
        "-bsf:a",  "aac_adtstoasc",
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        mp4_path,
    ]
    return _run_ffmpeg(cmd, verbose)


# ─────────────────────────────────────────────────────────────────────────────
# High-level convert_one
# ─────────────────────────────────────────────────────────────────────────────

def convert_one(qsv_path: str,
                output_dir = None,
                keep_ts: bool = False,
                skip_existing: bool = False,
                dry_run: bool = False,
                mux_mode: str = "auto",
                salvage_clear: bool = False,
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
    salvage_clear: on a DRM-protected file, convert the unencrypted segments
                   instead of refusing outright
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
        indices, total, meta = parse_qsv(qsv_path)
    except QsvParseError as exc:
        print(f"  [SKIP] {exc}")
        return False
    except Exception as exc:
        print(f"  [ERROR] Unexpected parse error: {exc}")
        return False

    seg_count = len(indices)
    total_mb  = total / (1 << 20)
    print(f"  Segments: {seg_count}   TS payload: {total_mb:.1f} MB")

    for n, gap in meta["gaps"]:
        print(f"  [note] {gap:+,} byte gap before segment {n} "
              f"— segments are not contiguous")
    for n, over in meta["overruns"]:
        print(f"  [WARN] segment {n} runs {over:,} bytes past EOF "
              f"— the .qsv download is incomplete")

    # ── DRM ──────────────────────────────────────────────────────────────────
    # Only files that actually declare a licence ticket pay for the decode
    # probe, so an ordinary .qsv converts on exactly the path it always did.
    drm = drm_info(read_metadata(qsv_path, meta))
    if drm:
        print(f"  [note] {drm['label']}")
        decodes = probe_decodable(qsv_path, indices, verbose=verbose)
        dead  = [d["n"] for d in decodes if not d["ok"]]
        clear = [d["n"] for d in decodes if d["ok"]]
        if dead:
            print(f"  [WARN] segment(s) {dead} carry encrypted video — a "
                  f"decoder finds no codec parameters in them.")
            print(f"         The container unpacks fine; the elementary stream "
                  f"is locked to a key held by")
            print(f"         iQIYI's licence server and is not present in the "
                  f"file. Converting those segments")
            print(f"         yields a file that plays as macroblock soup.")
            if not clear:
                print("  ✗ Nothing in this file is convertible.")
                return False
            if not salvage_clear:
                print(f"  ✗ Refusing to write a mostly-unplayable file. "
                      f"Segment(s) {clear} are in the clear —")
                print(f"    re-run with --salvage-clear to convert just those, "
                      f"or --inspect for the full report.")
                return False
            print(f"  [note] --salvage-clear: converting segment(s) {clear} "
                  f"only, dropping {len(dead)} encrypted segment(s)")
            keep = set(clear)
            indices = [ix for n, ix in enumerate(indices, 1) if n in keep]
            total = sum(sz for _, _, sz in indices)
            seg_count = len(indices)
            # A partial rip gets a distinct name: nobody should mistake a
            # six-minute file for the feature they asked to convert.
            mp4_path = os.path.join(dest_dir, stem + ".clear.mp4")
            ts_path = os.path.join(dest_dir, stem + ".clear.ts") if keep_ts else None
            print(f"  Output : {mp4_path}  (revised)")

    # ── Decide how to mux ────────────────────────────────────────────────────
    resets, clock = [], None
    if seg_count > 1:
        probes = probe_segments(qsv_path, indices)
        resets, clock = detect_any_resets(probes)
        bad_sync = [p["n"] for p in probes if p["sync"] != 0]
        if bad_sync:
            print(f"  [WARN] segment(s) {bad_sync} do not start with an MPEG-TS "
                  f"sync byte after decryption — run --inspect for details")

    use_concat = mux_mode == "concat" or (mux_mode == "auto" and bool(resets))
    if resets:
        print(f"  [note] {clock} resets at segment(s) {resets} "
              f"— each shard has its own timeline")
    if use_concat and seg_count > 1:
        print("  Strategy: per-segment extract + ffmpeg concat demuxer")

    os.makedirs(dest_dir, exist_ok=True)

    # ── Extract ──────────────────────────────────────────────────────────────
    work_dir = tempfile.mkdtemp(prefix="qsv2mp4-")
    if ts_path:
        work_ts = ts_path
    else:
        work_ts = os.path.join(work_dir, "payload.ts")

    t0 = time.time()
    seg_paths = None
    try:
        print("  Extracting & decrypting MPEG-TS …", flush=True)

        if use_concat and seg_count > 1:
            seg_paths, warns = extract_segments(qsv_path, work_dir, indices,
                                                total, verbose=verbose)
        else:
            seg_paths = None
            _, warns = extract_ts(qsv_path, work_ts, indices, total,
                                  verbose=verbose)

        for w in warns:
            print(f"  [WARN] {w}")
        print(f"  Extracted in {time.time() - t0:.1f}s")

        # ── Convert ────────────────────────────────────────────────────────────
        print("  Muxing TS → MP4 via ffmpeg …", flush=True)
        t1 = time.time()
        written_paths = []
        flags = {}
        ok = True

        if seg_paths is not None:
            ok, flags = segments_to_mp4(seg_paths, mp4_path, work_dir,
                                        verbose=verbose)
            written_paths = [mp4_path] if ok else []
        else:
            ok, flags = ts_to_mp4(work_ts, mp4_path, verbose=verbose)
            written_paths = [mp4_path] if ok else []

        if ok:
            elapsed2 = time.time() - t1
            out_mb = sum(os.path.getsize(p) for p in written_paths) / (1 << 20)
            print(f"  Muxed in {elapsed2:.1f}s  |  Output: {out_mb:.1f} MB")

            severe = {l: c for l, c in flags.items() if l in _SEVERE_LABELS}
            minor  = {l: c for l, c in flags.items() if l not in _SEVERE_LABELS}

            # A handful of corrupt packets per segment join is expected — the
            # continuity counters restart at every splice.  Orders of magnitude
            # more than that means the payload itself is damaged, so promote it.
            join_budget = max(32, seg_count * 8)
            for label, count in sorted(minor.items()):
                if count > join_budget:
                    severe[label] = count
                    print(f"  [WARN] ffmpeg reported {count:,}× {label} "
                          f"— far more than the ~{join_budget} expected from "
                          f"{seg_count} segment joins; the payload is damaged")
                else:
                    print(f"  [info] ffmpeg reported {count:,}× {label} "
                          f"(normal at segment joins)")
            for label, count in sorted(severe.items()):
                if label not in minor:
                    print(f"  [WARN] ffmpeg reported {count:,}× {label}")

            if severe:
                hint = ("" if use_concat else
                        " Try re-running with --mux-mode concat.")
                print(f"  ⚠ Output written, but playback is likely corrupt.{hint}"
                      f" Run --inspect and open an issue if it persists.")
            elif warns:
                print(f"  ⚠ Done with warnings — some source data was missing.")
            else:
                print(f"  ✓ Done!")
            for p in written_paths:
                print(f"      {p}")
        else:
            print("  ✗ ffmpeg failed.")
        return ok

    except Exception as exc:
        print(f"  [ERROR] {exc}")
        return False

    finally:
        # --keep-ts promises the intermediate TS next to the output, so the
        # per-segment files get copied out rather than left in a temp dir that
        # nothing ever cleans up.
        if keep_ts and seg_paths:
            kept = os.path.join(dest_dir, stem + ".ts.d")
            os.makedirs(kept, exist_ok=True)
            for p in seg_paths:
                shutil.copy2(p, os.path.join(kept, os.path.basename(p)))
            print(f"  [note] --keep-ts: {len(seg_paths)} segment .ts file(s) in {kept}")
        shutil.rmtree(work_dir, ignore_errors=True)


# ─────────────────────────────────────────────────────────────────────────────
# Diagnostics
# ─────────────────────────────────────────────────────────────────────────────

def inspect_qsv(qsv_path: str, deep: bool = True) -> bool:
    """
    Dump the container layout and per-segment TS state without converting
    anything.  This is what to paste into a bug report — it says whether the
    problem is the download, the cipher, the layout, or the timeline.
    """
    qsv_path = os.path.abspath(qsv_path)
    print(f"\n{'─' * 72}")
    print(f"  {qsv_path}")

    try:
        indices, total, meta = parse_qsv(qsv_path)
    except QsvParseError as exc:
        print(f"  [SKIP] {exc}")
        return False
    except Exception as exc:
        print(f"  [ERROR] Unexpected parse error: {exc}")
        return False

    print(f"  file size    : {meta['file_size']:,} bytes")
    print(f"  qsv version  : {meta['version']}")
    print(f"  vid          : {meta['vid']}")
    print(f"  xml          : offset {meta['xml_offset']:,}  size {meta['xml_size']:,}")
    print(f"  index table  : offset {meta['index_table_offset']:,}  "
          f"entries {meta['nb_indices']}")
    print(f"  TS payload   : {total:,} bytes ({total / (1 << 20):.1f} MB)")

    qsv_info = read_metadata(qsv_path, meta)
    drm = drm_info(qsv_info)
    if drm:
        print(f"  DRM          : {drm['label']}")
    elif qsv_info is not None:
        print(f"  DRM          : none declared")

    payload_end = max(off + sz for _, off, sz in indices)
    trailing = meta["file_size"] - payload_end
    print(f"  accounted    : {total / meta['file_size'] * 100:.1f}% of the file"
          f"   (trailing bytes after last segment: {trailing:,})")

    probes = probe_segments(qsv_path, indices)

    print(f"\n  {'#':>3}  {'offset':>14}  {'size':>15}  {'gap':>10}  "
          f"{'sync':>6}  {'first PCR':>13}  {'first PTS':>13}")
    print(f"  {'-' * 3}  {'-' * 14}  {'-' * 15}  {'-' * 10}  {'-' * 6}  "
          f"{'-' * 13}  {'-' * 13}")

    prev_end = None
    for p in probes:
        gap = "—" if prev_end is None else f"{p['offset'] - prev_end:+,}"
        prev_end = p["offset"] + p["size"]
        sync = "ok" if p["sync"] == 0 else (
            f"@{p['sync']}" if p["sync"] > 0 else "LOST")
        print(f"  {p['n']:>3}  {p['offset']:>14,}  {p['size']:>15,}  {gap:>10}  "
              f"{sync:>6}  {_fmt_clock(p['pcr'])}  {_fmt_clock(p['pts'])}")

    # ── Deep scan ────────────────────────────────────────────────────────────
    scans = []
    if deep:
        print(f"\n  Deep scan (walking the 188-byte TS grid of every segment) …",
              flush=True)
        t0 = time.time()
        with open(qsv_path, "rb") as src:
            file_size = os.fstat(src.fileno()).st_size
            for _, off, size in indices:
                scans.append(scan_segment(src, off, size, file_size))
        print(f"  scanned {sum(s['scanned'] for s in scans) / (1 << 20):.0f} MB "
              f"in {time.time() - t0:.1f}s\n")

        print(f"  {'#':>3}  {'packets':>12}  {'sync errors':>13}  "
              f"{'first bad @':>14}  {'TEI':>8}  {'PIDs':>5}")
        print(f"  {'-' * 3}  {'-' * 12}  {'-' * 13}  {'-' * 14}  "
              f"{'-' * 8}  {'-' * 5}")
        for p, s in zip(probes, scans):
            pct = (s["bad_sync"] / s["packets"] * 100) if s["packets"] else 0
            errs = ("clean" if not s["bad_sync"]
                    else f"{s['bad_sync']:,} ({pct:.1f}%)")
            fb = "—" if s["first_bad"] is None else f"{s['first_bad']:,}"
            print(f"  {p['n']:>3}  {s['packets']:>12,}  {errs:>13}  "
                  f"{fb:>14}  {s['tei']:>8,}  {len(s['pids']):>5}")

    # ── Can a decoder actually read each segment? ────────────────────────────
    decodes = probe_decodable(qsv_path, indices) if deep else []
    if decodes:
        print(f"\n  Per-segment decode check (ffprobe on the first 12 MB):")
        print(f"  {'#':>3}  {'codec':>10}  {'dimensions':>14}  {'verdict':>28}")
        print(f"  {'-' * 3}  {'-' * 10}  {'-' * 14}  {'-' * 28}")
        for d in decodes:
            dims = (f"{d['width']}x{d['height']}" if d["ok"] else "—")
            verdict = ("ok" if d["ok"]
                       else "no codec parameters found")
            print(f"  {d['n']:>3}  {str(d['codec'] or '—'):>10}  {dims:>14}  "
                  f"{verdict:>28}")

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("\n  Findings:")
    findings = []

    if scans:
        broken = [p["n"] for p, s in zip(probes, scans) if s["bad_sync"]]
        clean  = [p["n"] for p, s in zip(probes, scans) if not s["bad_sync"]]
        if broken and clean:
            findings.append(
                f"segment(s) {clean} keep TS sync end-to-end but segment(s) "
                f"{broken} lose it partway — those segments are still "
                f"encrypted/damaged beyond their 1024-byte head, which no "
                f"amount of re-muxing can fix"
            )
        elif broken:
            findings.append(
                f"every segment loses TS sync partway through — the payload is "
                f"not clear MPEG-TS past the descrambled head"
            )
        else:
            findings.append(
                "all segments hold TS sync end-to-end — the 188-byte framing "
                "survived extraction.  Note this proves the container was "
                "unpacked right, not that the payload inside it is clear; see "
                "the decode check for that"
            )

        # PID layout drift makes the demuxer follow the wrong program.  Only
        # trust it where the TS grid held: in a segment that lost sync the PID
        # bytes are ciphertext, so they always "differ" and would bury the real
        # finding under a spurious one.
        if not broken:
            pid_sets = [frozenset(s["pids"]) for s in scans]
            if len(set(pid_sets)) > 1:
                findings.append(
                    "the segments do not all carry the same PID set — the demuxer "
                    "locks onto the first segment's PMT and will ignore streams "
                    "that move to a different PID later"
                )

        tei_total = sum(s["tei"] for s in scans)
        if tei_total:
            findings.append(f"{tei_total:,} packets have the transport_error "
                            f"flag set — the source data is damaged")

    if decodes:
        dead  = [d["n"] for d in decodes if not d["ok"]]
        clear = [d["n"] for d in decodes if d["ok"]]
        if dead and drm:
            # The descriptor named a licence server, and the decoder confirms
            # the stream needs it.  That pair is conclusive, so say so plainly
            # instead of inviting a bug report this tool can never act on.
            findings.append(
                f"this file is DRM-protected and segment(s) {dead} are "
                f"encrypted video: {drm['label']}. The container unpacks "
                f"correctly — TS framing, PIDs, PCR and PTS are all intact — "
                f"but the elementary stream inside those segments is encrypted "
                f"under a key held by iQIYI's licence server, not stored in the "
                f"file. No unpacker can recover it"
                + (f". Segment(s) {clear} are in the clear and can still be "
                   f"converted with --salvage-clear" if clear else "")
            )
        elif dead and len(dead) < len(decodes):
            findings.append(
                f"a decoder finds codec parameters in segment(s) {clear} but "
                f"none in {dead}, even though the TS framing of all of them is "
                f"intact. The container is being unpacked correctly and the "
                f"elementary stream inside {dead} is not readable — the payload "
                f"past the 1024-byte head is still scrambled, or is scrambled a "
                f"way this tool does not undo. Muxing those segments on their "
                f"own cannot work; please report this output"
            )
        elif dead:
            findings.append(
                "no segment yields codec parameters — the payload is not clear "
                "video anywhere, so the cipher or layout does not match this file"
            )

    if meta["overruns"]:
        findings.append("segment indices point past EOF — the .qsv is "
                        "incomplete; re-download it")
    if meta["gaps"]:
        total_gap = sum(g for _, g in meta["gaps"])
        findings.append(f"{len(meta['gaps'])} gap(s), {total_gap:,} bytes total "
                        "— segments are not contiguous")
    bad_sync = [p["n"] for p in probes if p["sync"] != 0]
    if bad_sync:
        findings.append(f"segment(s) {bad_sync} have no TS sync at offset 0 "
                        "after decryption — cipher/format mismatch")
    no_pcr = [p["n"] for p in probes if p["pcr"] is None]
    if no_pcr:
        findings.append(f"segment(s) {no_pcr} carry no PCR in their first 64 KB "
                        "— cannot judge their timeline")
    resets, clock = detect_any_resets(probes)
    if resets:
        findings.append(f"{clock} resets at segment(s) {resets} — each shard has "
                        "its own timeline; splicing them produces non-monotonic "
                        "timestamps. Use --mux-mode concat")

    if not findings:
        findings.append("layout, sync and timeline all look consistent — "
                        "if playback is still broken the problem is downstream "
                        "of extraction")

    for f in findings:
        print(f"    • {f}")
    print()
    return True


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
        "--inspect", action="store_true",
        help="Dump container layout, TS sync and per-segment clocks, then exit. "
             "Paste this output into a bug report.",
    )
    p.add_argument(
        "--no-deep", action="store_true",
        help="With --inspect, skip the full per-segment TS grid walk "
             "(faster, but cannot tell a mis-unpacked segment from an "
             "encrypted one)",
    )
    p.add_argument(
        "--mux-mode", choices=("auto", "single", "concat"),
        default="auto",
        help="How to join segments: 'single' splices them into one TS (fast, "
             "assumes one shared clock), 'concat' muxes each segment separately "
             "and joins via ffmpeg's concat demuxer (handles segments with "
             "independent clocks). 'auto' (default) picks concat when the "
             "clock resets.",
    )
    p.add_argument(
        "--salvage-clear", action="store_true",
        help="If the file is DRM-protected, convert the segments that are not "
             "encrypted instead of refusing, writing NAME.clear.mp4. The "
             "encrypted segments are dropped — they cannot be decoded without "
             "iQIYI's licence key.",
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

    if args.inspect:
        print(f"Inspecting {len(files)} .qsv file(s).")
        ok = sum(inspect_qsv(q, deep=not args.no_deep) for q in files)
        sys.exit(0 if ok == len(files) else 1)

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
            mux_mode=args.mux_mode,
            salvage_clear=args.salvage_clear,
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
