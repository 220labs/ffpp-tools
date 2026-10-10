#!/usr/bin/env python3
"""The prop sounds: recordings of the real toys, split into takes, placed on the spotting sheet's hits.

Usage:
  bin/tool toys --spot audio/spotting-<ver>.tsv --plan audio/toys.tsv [--dir r3/audio/toys]
                --out r3/audio/toys/toys-bed-<ver>.wav [--list] [--no-land]

Text root: --spot, --plan. Media root: --dir (default r<N>/audio/toys), --out, and the plan's file paths.
--list        split every recording in --dir into takes and print them (index, start, length, peak, RMS) without
              writing anything. This is how a take gets picked, and the pick is then written into the plan with a reason.
--plan FILE   tab-separated, '#' = comment:  hit  file  take  gain_db  offset_ms  note
              hit      : a substring of the note of a `hit` row in the spotting sheet, e.g. 'вуп'
              file     : a recording in --dir (any format ffmpeg reads); file@S stops the take S s after its onset,
                         so a sound can hand over to the next one (the unfold that the fold interrupts)
              take     : 1-based index from --list, or 0 for the whole file (a take that was already cut to one
                         sound, as takes/ holds them, or a click cut by bin/tool film_clicks)
              gain_db  : level relative to the recording, applied before the mix's own balance
              offset_ms: + = later than the hit frame. The take is aligned on its own onset (first sample above
                         -35 dBFS, backed off 5 ms), not on the file's start.
Writes a full-length 48 kHz stereo bed that mix adds into the effects (`--toys FILE`), so the toys ride the same
balance and the same loudness pass as the generator's own effects; lands it.
Ported from START's scripts/mix/toys.py (2026-09-25): the same maths.
"""
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli, landing  # noqa: E402

SR = 48000
ON_DB, SPLIT_DB, MIN_TAKE, GAP = -35.0, -45.0, 0.08, 0.25
AUDIO = ('.m4a', '.wav', '.mp3', '.aif', '.aiff', '.caf', '.flac')


def read(path):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '2', '-ar', str(SR), '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2).copy()


def takes(x, hop=0.005):
    """Split a recording into takes on silence: spans above SPLIT_DB, joined across gaps under GAP s."""
    h = int(SR * hop)
    m = np.abs(x).max(axis=1)
    e = np.array([20 * np.log10(m[i * h:(i + 1) * h].max() + 1e-12) for i in range(len(m) // h)])
    on = e > SPLIT_DB
    out, i = [], 0
    while i < len(on):
        if not on[i]:
            i += 1
            continue
        j = i
        while j < len(on):
            if on[j]:
                j += 1
                continue
            k = j
            while k < len(on) and not on[k]:
                k += 1
            if (k - j) * hop > GAP or k >= len(on):
                break
            j = k
        if (j - i) * hop >= MIN_TAKE:
            out.append((i * h, min(len(x), j * h)))
        i = j + 1
    return out


def onset(seg):
    m = np.abs(seg).max(axis=1)
    idx = np.where(m > 10 ** (ON_DB / 20))[0]
    return max(0, (idx[0] if len(idx) else 0) - int(0.005 * SR))


def main(argv):
    ap = cli.parser('toys', __doc__)
    ap.add_argument('--spot', metavar='FILE')
    ap.add_argument('--plan', metavar='FILE')
    ap.add_argument('--dir', metavar='DIR', help='the recordings (default: r<N>/audio/toys)')
    ap.add_argument('--out', metavar='FILE')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--round', metavar='rN')
    ap.add_argument('--no-land', action='store_true')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('toys')
    os.chdir(r.media)
    d = r.m(a.dir) if a.dir else os.path.join(a.round or cli.latest_round(r.media) or '', 'audio', 'toys')
    files = sorted(f for f in os.listdir(d) if not f.startswith('.') and os.path.splitext(f)[1].lower() in AUDIO)
    if a.list:
        for f in files:
            x = read(os.path.join(d, f))
            print(f'\n{f}  {len(x)/SR:.2f} s')
            print('  take  start      length   peak dBFS   RMS dBFS')
            for i, (s0, s1) in enumerate(takes(x), 1):
                seg = x[s0:s1]
                print(f'  {i:>4}  {s0/SR:>7.3f} s  {(s1-s0)/SR:>6.3f} s  {20*np.log10(np.abs(seg).max()+1e-12):>9.1f}'
                      f'  {20*np.log10(np.sqrt((seg**2).mean())+1e-12):>9.1f}')
        return 0
    for k in ('spot', 'plan', 'out'):
        if not getattr(a, k):
            sys.exit(f'toys: --{k} is needed (or --list)')
    spot = [l.rstrip('\n').split('\t') for l in open(r.t(a.spot), encoding='utf-8') if not l.startswith('#') and l.strip()]
    head = spot[0]
    rows = [dict(zip(head, x)) for x in spot[1:]]
    hits = {x['note']: float(x['sec_in']) for x in rows if x['kind'] == 'hit'}
    n = max(int(float(x['sec_out']) * SR) for x in rows) + SR
    bed = np.zeros((n, 2), np.float32)
    placed = []
    for line in open(r.t(a.plan), encoding='utf-8'):
        if line.startswith('#') or not line.strip():
            continue
        f = line.rstrip('\n').split('\t')
        key, fn, tk, gdb, off = f[0], f[1], int(f[2]), float(f[3]), float(f[4])
        where = [v for k, v in hits.items() if key in k]
        if len(where) != 1:
            raise SystemExit(f'hit {key!r} matches {len(where)} rows in the spotting sheet')
        fn, _, cap = fn.partition('@')          # file@SECONDS: the take stops SECONDS after its onset (20 ms fade)
        x = read(os.path.join(d, fn))
        s0, s1 = (0, len(x)) if tk == 0 else takes(x)[tk - 1]
        seg = x[s0 + onset(x[s0:s1]):s1] * 10 ** (gdb / 20)
        if cap:
            seg = seg[:int(float(cap) * SR)]
        t0 = where[0] + off / 1000.0
        p = int(round(t0 * SR))
        m = min(len(seg), n - p)
        rr = int(0.005 * SR)                                  # 5 ms in, 20 ms out, so nothing clicks
        seg = seg.copy()
        seg[:rr] *= np.linspace(0, 1, rr, dtype=np.float32)[:, None]
        o = int(0.02 * SR)
        seg[-o:] *= np.linspace(1, 0, o, dtype=np.float32)[:, None]
        bed[p:p + m] += seg[:m]
        placed.append((key, fn, tk, round(t0, 3), round(len(seg) / SR, 3), gdb))
    out = r.m(a.out)
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    sf.write(out, bed, SR, subtype='FLOAT')
    for p in placed:
        print('placed', *p)
    print('wrote', out, f'{len(bed)/SR:.3f} s')
    if a.no_land:
        print('NOT LANDED (--no-land): in the Mac shell, run: bin/tool land ' + out)
        return 0
    return landing.land(r, [out], tool='toys')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
