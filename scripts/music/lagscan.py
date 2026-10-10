#!/usr/bin/env python3
"""Onset-envelope lag of a take against the sketch in sliding windows (ms, + = the take is late).

Usage:  bin/tool lagscan SKETCH.wav TAKE.wav [window s, default 8]
Paths relative to the media root. One line per half-window step: the window, the lag and the correlation. This is
how a chunk's `shift=` for bin/tool assemble is measured against the sketch rather than against the previous chunk
(chained placement drifts).
Ported from START's scripts/music/lagscan.py (2026-09-25): the same measurement.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli  # noqa: E402


def main(argv):
    if len(argv) < 2 or '--help' in argv or '-h' in argv:
        print(__doc__.strip())
        return 0 if '--help' in argv or '-h' in argv else 2
    ns, args = cli.split_roots(argv)
    r = cli.roots(ns)
    os.chdir(r.media)
    import scipy.signal
    import scipy.signal.windows
    scipy.signal.hann = scipy.signal.windows.hann
    import librosa
    import numpy as np
    sr, hop = 22050, 64
    a, _ = librosa.load(args[0], sr=sr)
    b, _ = librosa.load(args[1], sr=sr)
    W = float(args[2]) if len(args) > 2 else 8.0
    oa = librosa.onset.onset_strength(y=a, sr=sr, hop_length=hop)
    ob = librosa.onset.onset_strength(y=b, sr=sr, hop_length=hop)
    n = min(len(oa), len(ob))
    fps = sr / hop
    m = int(0.08 * fps)
    for t in np.arange(0, n / fps - W + 0.01, W / 2):
        i0, i1 = int(t * fps), int((t + W) * fps)
        x, y = oa[i0:i1] - oa[i0:i1].mean(), ob[i0:i1] - ob[i0:i1].mean()
        c = np.correlate(y, x, 'full')
        mid = len(x) - 1
        seg = c[mid - m: mid + m + 1]
        k = int(np.argmax(seg)) - m
        cc = seg.max() / (np.linalg.norm(x) * np.linalg.norm(y) + 1e-9)
        print(f'{t:6.1f}-{t+W:6.1f}  lag {k / fps * 1000:+6.1f} ms  r {cc:.2f}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
