#!/usr/bin/env python3
"""The prop recordings, cut into every event a person could hear, and a labelling reel for the reviewer's ear.

Usage:
  bin/tool toys_takes [--dir r3/audio/toys] [--reel] [--no-land]

Media root: --dir (default r<N>/audio/toys), which holds source/<prefix>-src-*.m4a and, optionally, the older cut's
MANIFEST.tsv (whose takes are matched by overlap into the `was` column).
The first
cut kept 45 events and rejected 31 below -25 dBFS peak, and grouped them by measurement. This pass keeps every event
and leaves the naming to the reviewer:
- An event is a run of 5 ms frames whose peak is 15 dB over the file's own floor (the 20th percentile of its frame
  peaks), or over -60 dBFS if the floor is lower; runs closer than 100 ms join; runs under 40 ms are handling clicks
  and are dropped. Nothing else is judged.
- Each take: DC removed, 60 Hz high-pass, 30 ms pre-roll, 150 ms tail, 5 ms fade in, 30 ms fade out, source gain
  kept, 48 kHz / 24-bit WAV, named t<NN>-<MM>.wav (NN = the recording, MM = its order in it), in takes-v2/.
- takes-v2/MANIFEST.tsv: one row per take — source, in-point, length, peak, the older take it overlaps (`was`), its
  position in the reel, and an empty `label` column for the reviewer.
- --reel writes takes-v2/<label_prefix>-toys-labels-v2.mp4: every take in manifest order, peak-normalised to -3 dBFS
  for listening only, 1.0 s apart, on black with its id, source and in-point burned in, so a label can be written
  against what is on screen. The takes and the reel are landed.
Deterministic: same sources in, same takes and manifest out.
Ported from START's scripts/mix/toys_takes.py (2026-09-25): the same maths.
"""
import csv
import glob
import os
import subprocess
import sys

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfiltfilt

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli, fonts, landing, production  # noqa: E402

SR = 48000
HOP, OVER_FLOOR, ABS_MIN, GAP, MIN_LEN = 0.005, 15.0, -60.0, 0.10, 0.04
PRE, TAIL, FIN, FOUT = 0.030, 0.150, 0.005, 0.030
LEAD, SPACE = 0.6, 1.0


def read(path):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-f', 'f32le', '-ac', '1', '-ar', str(SR), '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).astype(np.float64)


def events(x):
    h = int(SR * HOP)
    n = len(x) // h
    db = 20 * np.log10(np.abs(x[:n * h]).reshape(n, h).max(1) + 1e-12)
    thr = max(np.percentile(db, 20) + OVER_FLOOR, ABS_MIN)
    on = np.nonzero(db > thr)[0]
    out = []
    for i in on:
        if out and i - out[-1][1] <= GAP / HOP:
            out[-1][1] = i + 1
        else:
            out.append([i, i + 1])
    return [(a * HOP, b * HOP) for a, b in out if (b - a) * HOP >= MIN_LEN]


def cut(x, t0, t1):
    a = max(0, int((t0 - PRE) * SR))
    b = min(len(x), int((t1 + TAIL) * SR))
    y = x[a:b].copy()
    y -= y.mean()
    y = sosfiltfilt(butter(2, 60, 'highpass', fs=SR, output='sos'), y)
    fi, fo = int(FIN * SR), int(FOUT * SR)
    y[:fi] *= np.linspace(0, 1, fi)
    y[-fo:] *= np.linspace(1, 0, fo)
    return a / SR, y


def ts(s):
    return f'{int(s // 3600)}:{int(s % 3600 // 60):02d}:{s % 60:05.2f}'


def reel(out, clips, total, name):
    total += 0.5
    a = np.zeros(int(total * SR), np.float32)
    ass = ['[Script Info]', 'ScriptType: v4.00+', 'PlayResX: 1280', 'PlayResY: 720', '',
           '[V4+ Styles]', 'Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BorderStyle, Outline, Shadow, Alignment, MarginV',
           'Style: Id,DejaVu Sans,120,&H00FFFFFF,&H00000000,1,0,0,5,0',
           'Style: Src,DejaVu Sans,40,&H00B0B0B0,&H00000000,1,0,0,2,120', '',
           '[Events]', 'Format: Layer, Start, End, Style, Text']
    for i, (tid, src, s0, pk, t, y) in enumerate(clips):
        k = int(t * SR)
        z = (y / (np.abs(y).max() + 1e-12) * 10 ** (-3 / 20)).astype(np.float32)
        a[k:k + len(z)] += z[:len(a) - k]
        e = clips[i + 1][4] - 0.05 if i + 1 < len(clips) else total
        quiet = f'   (quiet: peak {pk:.0f} dBFS)' if pk < -25 else ''
        ass.append(f'Dialogue: 0,{ts(t - 0.3)},{ts(e)},Id,{tid}')
        ass.append(f'Dialogue: 0,{ts(t - 0.3)},{ts(e)},Src,{src} @ {s0:.2f} s   ·   {i + 1} of {len(clips)}{quiet}')
    wav = os.path.join(out, '_reel.wav')
    sub = os.path.join(out, '_reel.ass')
    sf.write(wav, np.repeat(a[:, None], 2, 1), SR, subtype='PCM_24')
    open(sub, 'w', encoding='utf-8').write('\n'.join(ass) + '\n')
    mp4 = os.path.join(out, name)
    fontsdir = os.path.dirname(fonts.path('sans-bold'))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=black:s=1280x720:r=24:d={total:.3f}',
                    '-i', wav, '-vf', f"ass={sub}:fontsdir='{fontsdir}'", '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-shortest', '-movflags', '+faststart', mp4], check=True)
    os.remove(wav)
    os.remove(sub)
    print(mp4, f'{total:.1f} s')
    return mp4


def main(argv):
    ap = cli.parser('toys_takes', __doc__)
    ap.add_argument('--dir', metavar='DIR', help='the recordings folder (default: r<N>/audio/toys)')
    ap.add_argument('--reel', action='store_true')
    ap.add_argument('--round', metavar='rN')
    ap.add_argument('--no-land', action='store_true')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('toys_takes')
    p = production.load(r.text, r.media)
    os.chdir(r.media)
    d = r.m(a.dir) if a.dir else os.path.join(a.round or cli.latest_round(r.media) or '', 'audio', 'toys')
    out = os.path.join(d, 'takes-v2')
    os.makedirs(out, exist_ok=True)
    old = []
    mf = os.path.join(d, 'MANIFEST.tsv')
    if os.path.exists(mf):
        with open(mf, encoding='utf-8') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                old.append((row['source'], float(row['src_in']), float(row['src_in']) + float(row['dur']), row['take'][:-4]))
    rows, clips, t = [], [], LEAD
    srcs = sorted(glob.glob(os.path.join(d, 'source', '*-src-*.m4a')))
    if not srcs:
        sys.exit(f'toys_takes: no recordings at {os.path.join(d, "source")}/<prefix>-src-*.m4a')
    for pth in srcs:
        src = os.path.basename(pth)
        nn = src.split('-src-', 1)[1][:-4]
        x = read(pth)
        for m, (t0, t1) in enumerate(events(x), 1):
            s0, y = cut(x, t0, t1)
            tid = f't{nn}-{m:02d}'
            sf.write(os.path.join(out, tid + '.wav'), y.astype(np.float32), SR, subtype='PCM_24')
            s1 = s0 + len(y) / SR
            was = [o[3] for o in old if o[0] == src and o[1] < s1 and o[2] > s0]
            pk = 20 * np.log10(np.abs(y).max() + 1e-12)
            rows.append({'take': tid + '.wav', 'source': src, 'src_in': f'{s0:.3f}', 'dur': f'{len(y) / SR:.3f}',
                         'peak_db': f'{pk:.1f}', 'was': ','.join(was), 'reel_in': f'{int(t // 60):02d}:{t % 60:05.2f}',
                         'label': ''})
            clips.append((tid, src, s0, pk, t, y))
            t += len(y) / SR + SPACE
    with open(os.path.join(out, 'MANIFEST.tsv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter='\t', lineterminator='\n')
        w.writeheader()
        w.writerows(rows)
    print(f'{len(rows)} takes from {len({x["source"] for x in rows})} recordings -> {out}')
    made = [out]
    if a.reel:
        made.append(reel(out, clips, t, f'{p.label_prefix}-toys-labels-v2.mp4'))
    if a.no_land:
        print('NOT LANDED (--no-land): in the Mac shell, run: bin/tool land ' + out)
        return 0
    return landing.land(r, [out], tool='toys_takes')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
