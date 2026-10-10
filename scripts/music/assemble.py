#!/usr/bin/env python3
"""Join chunked music generations into one cue on the sketch's timeline.

Usage:
  bin/tool assemble OUT.wav LENGTH_S  CHUNK.wav:T0:SEAM[:gain=DB][:shift=MS]  ...  (the last chunk's SEAM = end)
                    [--ref SKETCH.wav]  (levels follow the sketch; see below)
                    [--lift T0:T1:DB]   (raise everything from T1 on by DB, ramped over T0..T1; e.g. quiet credit pings)
                    [--ringout RT60]    (a take cut short by the model's length cap rings out in a synthetic hall
                                         instead of stopping: its last 0.3 s convolved with decaying noise, -10 dB)
Paths are relative to the media root (r<N>/audio/music/…). Each chunk was rendered from the cue's notes starting at
T0 (cue seconds), so it is placed at T0. Between chunk i and i+1 the join is at chunk i's SEAM (a bar line inside
chunk i+1's pre-roll): chunk i+1 is gain-matched to chunk i over the shared pre-roll, then an equal-power crossfade
of 120 ms ends 10 ms before the downbeat, so the downbeat itself comes from the new chunk. A chunk's own lag against
the timeline is measured by onset cross-correlation over the pre-roll and corrected when it exceeds 5 ms.
gain=DB fixes a chunk's gain instead of matching it; shift=MS moves a chunk earlier (negative) or later against the
timeline and turns off the pre-roll lag match (measure it against the sketch with bin/tool lagscan).
--ref SKETCH.wav matches levels to the sketch instead of across the pre-roll: each chunk keeps the sketch's level
over its own stretch (previous seam to its seam), relative to the first chunk. Pre-roll matching can drift upward when
a model eases into every generation, and it cannot keep a quiet section quiet after a loud one.
Ported from START's scripts/music/assemble.py (2026-09-25): the same maths.
"""
import os
import sys

import numpy as np
import scipy.signal
import soundfile as sf

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli  # noqa: E402

SR = 48000


def load(p):
    x, sr = sf.read(p, dtype='float32', always_2d=True)
    assert sr == SR, (p, sr)
    return x


def env(x):
    e = np.abs(x.mean(1))
    k = int(0.01 * SR)
    return np.convolve(e, np.ones(k) / k, mode='same')


def main(argv):
    if not argv or '--help' in argv or '-h' in argv:
        print(__doc__.strip())
        return 0 if argv else 2
    ns, args = cli.split_roots(argv)
    r = cli.roots(ns)
    r.say('assemble')
    os.chdir(r.media)
    lift = ref = ring = None
    if '--ringout' in args:
        i = args.index('--ringout')
        ring = float(args[i + 1])
        del args[i:i + 2]
    if '--ref' in args:
        i = args.index('--ref')
        ref = load(args[i + 1])
        del args[i:i + 2]
    if '--lift' in args:
        i = args.index('--lift')
        lift = [float(v) for v in args[i + 1].split(':')]
        del args[i:i + 2]
    out_path, L = args[0], float(args[1])
    chunks = []
    for x in args[2:]:
        f = x.split(':')
        p, t0, seam = f[0], float(f[1]), float(f[2])
        opt = dict(kv.split('=') for kv in f[3:])
        chunks.append((p, t0, seam, {k: float(v) for k, v in opt.items()}))
    n = int(round(L * SR))
    out = np.zeros((n, 2), np.float32)
    prev = None
    db = lambda v: 10 * np.log10(np.mean(v ** 2) + 1e-12)  # noqa: E731

    def own(x, t0, a, b):                                # a chunk's level over timeline stretch a..b, minus the sketch's
        s0, s1 = int(a * SR), int(b * SR)
        x = x[max(0, s0 - int(t0 * SR)): s1 - int(t0 * SR)]
        return db(x) - db(ref[s0: s0 + len(x)])
    d0 = own(load(chunks[0][0]), chunks[0][1], chunks[0][1], chunks[0][2]) if ref is not None else 0.0
    for i, (p, t0, seam, opt) in enumerate(chunks):
        x = load(p)
        a0 = int(round(t0 * SR))
        sh = int(round(opt.get('shift', 0) * SR / 1000))
        if sh < 0:
            x = x[-sh:]
        elif sh > 0:
            x = np.vstack([np.zeros((sh, 2), np.float32), x])
        if prev is not None:
            ps = int(round(prev * SR))                    # the seam, in timeline samples
            o0, o1 = a0, ps                                # shared pre-roll
            ov = x[: o1 - a0]
            cur = out[o0:o1]
            # lag of the new chunk against what is already laid (onset envelopes, +-60 ms)
            e1, e2 = env(cur), env(ov)
            m = int(0.06 * SR)
            c = scipy.signal.correlate(e2 - e2.mean(), e1 - e1.mean(), mode='full', method='fft')
            mid = len(e1) - 1
            lag = int(np.argmax(c[mid - m: mid + m + 1]) - m)
            if 'gain' in opt or 'shift' in opt:
                lag = 0   # measured against the sketch instead
            if abs(lag) > int(0.005 * SR):
                x = x[lag:] if lag > 0 else np.vstack([np.zeros((-lag, 2), np.float32), x])
                ov = x[: o1 - a0]
            g = np.sqrt(np.mean(cur ** 2) / (np.mean(ov ** 2) + 1e-12))
            g = float(np.clip(g, 0.25, 4.0))
            if ref is not None:
                g = 10 ** ((d0 - own(x, t0, prev, seam)) / 20)
            if 'gain' in opt:
                g = 10 ** (opt['gain'] / 20)
            x = x * g
            f1 = ps - int(0.01 * SR)
            f0 = f1 - int(0.12 * SR)
            rr = np.linspace(0, 1, f1 - f0, dtype=np.float32)[:, None]
            fo, fi = np.cos(rr * np.pi / 2), np.sin(rr * np.pi / 2)
            out[f0:f1] *= fo
            out[f1:] = 0
            seg = x[f0 - a0:]
            seg = seg[: n - f0]
            seg[: f1 - f0] *= fi
            out[f0: f0 + len(seg)] += seg
            print(f'{p}: lag {lag / SR * 1000:+.1f} ms, gain {20 * np.log10(g):+.1f} dB, seam {prev:.3f} s')
        else:
            seg = x[: n - a0]
            out[a0: a0 + len(seg)] += seg
            print(f'{p}: placed at {t0} s, shift {opt.get("shift", 0):+.0f} ms')
        prev = seam
    if lift:
        l0, l1 = int(lift[0] * SR), int(lift[1] * SR)
        gl = 10 ** (lift[2] / 20)
        out[l0:l1] *= np.linspace(1, gl, l1 - l0, dtype=np.float32)[:, None]
        out[l1:] *= gl
        print(f'lift {lift[2]:+.1f} dB from {lift[1]} s')
    # a take that stops short of LENGTH gets a 0.3 s fade at its end instead of a cliff
    last = np.max(np.nonzero(np.abs(out).max(1) > 1e-4)[0]) if np.any(np.abs(out) > 1e-4) else n
    k = int((0.03 if ring else 0.3) * SR)
    s0 = max(0, last - k)
    out[s0:last] *= np.linspace(1, 0, last - s0, dtype=np.float32)[:, None]
    if ring:
        w = int(0.3 * SR)
        src = out[last - w: last] * np.hanning(2 * w)[:w, None].astype(np.float32)
        m = int(ring * SR)
        t = np.arange(m) / SR
        rng = np.random.default_rng(7)
        ir = rng.standard_normal((m, 2)).astype(np.float32) * np.exp(-6.91 * t / ring)[:, None].astype(np.float32)
        b, a = scipy.signal.butter(1, 5000 / (SR / 2))
        ir = scipy.signal.lfilter(b, a, ir, axis=0).astype(np.float32)
        wet = np.stack([scipy.signal.fftconvolve(src[:, c], ir[:, c]) for c in range(2)], 1)
        ref_lvl = np.sqrt(np.mean(out[last - int(0.13 * SR): last - k] ** 2))
        wl = np.sqrt(np.mean(wet[w: w + int(0.05 * SR)] ** 2)) + 1e-12
        wet *= ref_lvl / wl * 10 ** (-10 / 20)
        wet = wet[: n - (last - w)]
        out[last - w: last - w + len(wet)] += wet
        last = min(n, last - w + len(wet))
        print(f'ring-out {ring} s from {(last - len(wet) + w) / SR:.2f} s')
    sf.write(out_path, out, SR, subtype='FLOAT')
    print(out_path, f'{n / SR:.2f} s, audio to {last / SR:.2f} s, peak {20 * np.log10(np.abs(out).max() + 1e-9):.1f} dBFS')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
