#!/usr/bin/env python3
"""Single sounds cut out of the film's own effects stem, to be placed again elsewhere (by bin/tool toys, like a take).

Usage:
  bin/tool film_clicks --effects DIR/effects.wav --plan audio/film-clicks.tsv --dir r3/audio/fx/clicks [--no-land]

Media root: --effects (the BandIt Plus effects stem of the cut's production sound, on the cut's own timeline — the
one mix reads), --dir. Text root: --plan, tab-separated, '#' = comment:  name  in_s  dur_s  note  (in_s on that
timeline). Each clip: 5 ms fade in, 20 ms fade out, source level kept, 48 kHz / 24-bit WAV, DIR/<name>.wav, landed.
toys places it (audio/toys.tsv, file ../fx/clicks/<name>.wav, take 0) at the effects' own balance, so it sounds as
loud as it did where it came from. Deterministic.
Why: a shot with no sound of its own can borrow a click from a neighbouring scene.
Ported from START's scripts/mix/film_clicks.py (2026-09-25): the same maths.
"""
import os
import sys

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli, landing  # noqa: E402


def main(argv):
    ap = cli.parser('film_clicks', __doc__)
    ap.add_argument('--effects', required=True, metavar='FILE')
    ap.add_argument('--plan', required=True, metavar='FILE')
    ap.add_argument('--dir', required=True, metavar='DIR')
    ap.add_argument('--no-land', action='store_true')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('film_clicks')
    x, sr = sf.read(r.m(a.effects), always_2d=True, dtype='float32')
    assert sr in (44100, 48000), sr
    out = r.m(a.dir)
    os.makedirs(out, exist_ok=True)
    made = []
    for line in open(r.t(a.plan), encoding='utf-8'):
        if line.startswith('#') or not line.strip():
            continue
        name, t0, d = line.rstrip('\n').split('\t')[:3]
        t0, d = float(t0), float(d)
        y = x[int(round(t0 * sr)):int(round((t0 + d) * sr))].astype(np.float64)
        if sr != 48000:
            from scipy.signal import resample_poly
            y = resample_poly(y, 160, 147, axis=0)
        fi, fo = int(0.005 * 48000), int(0.02 * 48000)
        y[:fi] *= np.linspace(0, 1, fi)[:, None]
        y[-fo:] *= np.linspace(1, 0, fo)[:, None]
        p = os.path.join(out, name + '.wav')
        sf.write(p, y.astype(np.float32), 48000, subtype='PCM_24')
        made.append(p)
        print(f'{name}: {t0:.3f} s + {d:.3f} s, peak {20 * np.log10(np.abs(y).max() + 1e-12):.1f} dBFS')
    if a.no_land:
        print('NOT LANDED (--no-land): in the Mac shell, run: bin/tool land ' + ' '.join(made))
        return 0
    return landing.land(r, made, tool='film_clicks')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
