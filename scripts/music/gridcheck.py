#!/usr/bin/env python3
"""Does a performance keep the sketch's grid?

Usage:  bin/tool gridcheck SKETCH.wav TAKE.wav [hit seconds ...]
Paths relative to the media root. Prints the take's tempo, frame-for-frame chroma similarity against the sketch
(and a shuffled baseline), the onset cross-correlation lag, and each hit's nearest onset in frames (24 fps). A cover
that keeps the grid reads the sketch's tempo, chroma well above the shuffled figure and a lag near 0.
Ported from START's scripts/music/gridcheck.py (2026-09-25): the same measurements.
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
    sr, hop = 22050, 256
    a, _ = librosa.load(args[0], sr=sr)
    b, _ = librosa.load(args[1], sr=sr)
    hits = [float(x) for x in args[2:]]
    ob = librosa.onset.onset_strength(y=b, sr=sr, hop_length=hop)
    oa = librosa.onset.onset_strength(y=a, sr=sr, hop_length=hop)
    ac = librosa.autocorrelate(ob, max_size=int(4 * sr / hop))
    lags = np.arange(len(ac)) * hop / sr
    pk = scipy.signal.find_peaks(ac)[0]
    tempo = sorted([(round(60 / lags[i], 1), round(float(ac[i] / ac[0]), 3)) for i in pk if 0.25 < lags[i] < 2.1], key=lambda x: -x[1])[:3]
    ca = librosa.feature.chroma_cqt(y=a, sr=sr, hop_length=hop)
    cb = librosa.feature.chroma_cqt(y=b, sr=sr, hop_length=hop)
    nrm = lambda c: c / (np.linalg.norm(c, axis=0) + 1e-9)  # noqa: E731
    na, nb = nrm(ca), nrm(cb)
    m = min(na.shape[1], nb.shape[1])
    ident = float(np.mean(np.sum(na[:, :m] * nb[:, :m], 0)))
    shuf = float(np.mean(np.sum(na[:, :m] * nb[:, np.random.default_rng(0).permutation(m)], 0)))
    n = min(len(oa), len(ob))
    x = np.correlate(ob[:n] - ob[:n].mean(), oa[:n] - oa[:n].mean(), 'full')
    lag = (np.argmax(x) - (n - 1)) * hop / sr
    cc = float(x.max() / (np.linalg.norm(ob[:n] - ob[:n].mean()) * np.linalg.norm(oa[:n] - oa[:n].mean())))
    on = librosa.onset.onset_detect(y=b, sr=sr, units='time', hop_length=128)
    print(f'take {len(b)/sr:.2f} s; tempo peaks {tempo}; chroma frame-for-frame {ident:.3f} (shuffled {shuf:.3f}); '
          f'onset xcorr {cc:.3f} at lag {lag:+.3f} s')
    print('hits (s, nearest onset in frames):', [(h, round(float((on[np.argmin(abs(on - h))] - h) * 24), 1)) for h in hits])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
