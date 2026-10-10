#!/usr/bin/env python3
"""Ambience beds: a continuous room tone under the blocks whose own effects are thin, built from the film's own
effects stem where the same place is well covered. No library sounds, no new sources.

Usage:
  bin/tool beds --effects DIR/effects.wav --speech DIR/speech.wav --spot audio/spotting-<ver>.tsv --plan audio/beds.tsv
                --out r3/audio/beds/beds-<ver>.wav [--report r3/audio/beds/beds-<ver>.tsv] [--no-land]

Text root: --spot, --plan. Media root: --effects, --speech, --out, --report, and the plan's clip paths.
--effects  the BandIt Plus effects stem of the cut's production sound, on the cut's own timeline
           (sep_chunks on `edl_cut --audio-only` output: the same stem mix reads).
--speech   the speech stem of the same separation, for the leak test.
--plan     tab-separated, '#' = comment. Three kinds of row:
             source  <place>  <block prefix>                  a block whose effects feed that place's tone
             clip    <place>  <path to CLIP-effects.wav>      an outtake of the same place, separated by devoice;
                                                              its -speech.wav beside it is the voice the leak test checks against
             bed     <place>  <span>  <lufs>  <fade s>  note  a bed; span = a block prefix, or IN-OUT in s
             method  loop | synth                         loop: the steady windows themselves, levelled and looped;
                                                          synth: noise shaped to the
                                                          place's spectrum (synth_tone)
           lufs is the bed's own integrated loudness in the FINISHED mix (mix --beds scales the file by the master
           gain it computes before the beds go in), so the number in the plan is the bed's own loudness in the
           film — not the block's effects figure in the QA table, which also holds the shot's own sound.
Writes a full-length 48 kHz stereo float bed and a report of which source windows made each place's tone, and lands
them. Deterministic: windows are chosen by measurement in a fixed order, no randomness (synth: a generator seeded by
the place's name; byte-reproducible).

How a place's tone is made (loop). Every source block's effects stem is cut into 0.5 s windows (hop 0.25 s). A window
qualifies when it is (1) at least 0.4 s clear of every named hit in the spotting sheet (a hit is an event, not a
tone); (2) free of speech leak: where the speech stem is above -50 dBFS in the window, the effects stem's 10 ms level
must not follow the speech's (Pearson r of the two dB envelopes under 0.3); (3) steady: its 50 ms RMS stays within
6 dB of the window's median either way; (4) not silent: above -75 dBFS RMS. Qualifying windows are merged into runs,
levelled to the place's median run level, joined in plan order with 120 ms equal-power crossfades, and looped.
Synth: generated effects stems rarely hold a steady half second, so the bed is
noise shaped to what the recordings share — per window a Welch spectrum, per frequency the median over the quieter
half of the windows, tones flattened by a running median over 2/3 octave, smoothed over 1/6 octave.
Ported from START's scripts/mix/beds.py (2026-09-25): the same maths.
"""
import os
import sys

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import resample_poly

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli, landing  # noqa: E402

SR = 48000
WIN, HOP, SUB = 0.5, 0.25, 0.05
CLEAR, STEP_DB, FLOOR_DB, XF, MIN_TONE, VOICE_DB, LEAK_R, ENV = 0.4, 6.0, -75.0, 0.12, 1.0, -50.0, 0.3, 0.01


def load(path):
    if not os.path.exists(path) and os.path.exists(os.path.splitext(path)[0] + '.flac'):
        path = os.path.splitext(path)[0] + '.flac'      # landed masters are flac; the plan may still say .wav
    x, sr = sf.read(path, always_2d=True, dtype='float32')
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    if sr != SR:
        from math import gcd
        g = gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g, axis=0).astype(np.float32)
    return x


def read_spot(path):
    rows = [l.rstrip('\n').split('\t') for l in open(path, encoding='utf-8') if not l.startswith('#') and l.strip()]
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def block_span(spot, prefix):
    m = [r for r in spot if r['kind'] == 'block' and r['block'].startswith(prefix)]
    if len(m) != 1:
        raise SystemExit(f'block {prefix!r} matches {len(m)} blocks')
    return float(m[0]['sec_in']), float(m[0]['sec_out'])


def span(spot, s):
    if '-' in s and s.replace('-', '').replace('.', '').isdigit():
        a, b = s.split('-')
        return float(a), float(b)
    return block_span(spot, s)


def db(v):
    return 20 * np.log10(v + 1e-12)


def xfade_join(parts):
    """Join pieces with equal-power crossfades of XF s."""
    f = int(XF * SR)
    out = parts[0]
    th = np.linspace(0, np.pi / 2, f, dtype=np.float32)[:, None]
    for p in parts[1:]:
        if len(out) < f or len(p) < f:
            out = np.concatenate([out, p])
            continue
        mid = out[-f:] * np.cos(th) + p[:f] * np.sin(th)
        out = np.concatenate([out[:-f], mid, p[f:]])
    return out


def env(x):
    """10 ms RMS envelope in dB of a (n, 2) buffer."""
    e = int(ENV * SR)
    m = x.mean(axis=1)
    k = len(m) // e
    return db(np.sqrt((m[:k * e].reshape(-1, e) ** 2).mean(axis=1)))


def leaks(fxw, spw):
    """True when the window carries voice and the effects follow it."""
    es = env(spw)
    if es.max() <= VOICE_DB:
        return False
    ef = env(fxw)
    es = np.maximum(es, VOICE_DB - 30)
    if ef.std() < 1e-6 or es.std() < 1e-6:
        return False
    return np.corrcoef(ef, es)[0, 1] >= LEAK_R


def tone_for(fx, speech, spot, blocks, avoid):
    """(tone, windows used) for a place, from its source blocks and outtake clips."""
    runs, used = [], []
    w, h, s = int(WIN * SR), int(HOP * SR), int(SUB * SR)
    for pref in blocks:
        if pref.endswith('.wav'):            # an outtake: its own effects stem, its own voice spans
            x = load(pref)
            sp = load(pref.replace('-effects.wav', '-speech.wav'))
            a, b, src, ssrc, av = 0.0, len(x) / SR, x, sp, []
        else:
            a, b = block_span(spot, pref)
            av, src, ssrc = avoid, fx, speech
        got = 0.0
        i0, i1 = int(a * SR), int(b * SR)
        ok = []
        for p in range(i0, i1 - w + 1, h):
            t0, t1 = p / SR, (p + w) / SR
            if any(t0 < y + CLEAR and t1 > x - CLEAR for x, y in av):
                ok.append(False)
                continue
            seg = src[p:p + w]
            mono = seg.mean(axis=1)
            if leaks(seg, ssrc[p:p + w]):
                ok.append(False)
                continue
            sub = np.sqrt((mono[:len(mono) // s * s].reshape(-1, s) ** 2).mean(axis=1))
            rms = np.sqrt((mono ** 2).mean())
            ok.append(db(rms) > FLOOR_DB and np.abs(db(sub) - db(np.median(sub))).max() <= STEP_DB)
        # merge qualifying windows into runs
        k = 0
        while k < len(ok):
            if not ok[k]:
                k += 1
                continue
            j = k
            while j + 1 < len(ok) and ok[j + 1]:
                j += 1
            p0, p1 = i0 + k * h, i0 + j * h + w
            runs.append(src[p0:p1].copy())
            used.append((os.path.basename(pref), round(p0 / SR, 3), round(p1 / SR, 3)))
            got += (p1 - p0) / SR
            k = j + 1
        if got < MIN_TONE:
            print(f'  source {pref!r}: only {got:.2f} s of steady tone - skipped')
            while used and used[-1][0] == os.path.basename(pref):
                used.pop()
                runs.pop()
    if not runs:
        raise SystemExit(f'no steady tone in {blocks}')
    lv = [db(np.sqrt((rn ** 2).mean())) for rn in runs]
    ref = float(np.median(lv))
    runs = [rn * 10 ** ((ref - l) / 20) for rn, l in zip(runs, lv)]
    return xfade_join(runs), used


def loop_to(tone, n):
    """Loop a tone to n samples, each seam crossfaded."""
    parts, have = [], 0
    while have < n + int(XF * SR) * (len(parts) + 1):
        parts.append(tone)
        have += len(tone)
    return xfade_join(parts)[:n]


def spectrum_windows(fx, speech, spot, blocks, avoid):
    """Every 0.5 s window of a place's sources that passes the hit, leak and floor tests, as (name, t0, window)."""
    out = []
    w, h = int(WIN * SR), int(HOP * SR)
    for pref in blocks:
        if pref.endswith('.wav'):
            x = load(pref)
            sp = load(pref.replace('-effects.wav', '-speech.wav'))
            a, b, av = 0.0, len(x) / SR, []
        else:
            (a, b), x, sp, av = block_span(spot, pref), fx, speech, avoid
        for p in range(int(a * SR), int(b * SR) - w + 1, h):
            t0, t1 = p / SR, (p + w) / SR
            if any(t0 < y + CLEAR and t1 > q - CLEAR for q, y in av):
                continue
            seg = x[p:p + w]
            if leaks(seg, sp[p:p + w]) or db(np.sqrt((seg ** 2).mean())) <= FLOOR_DB:
                continue
            out.append((os.path.basename(pref), round(t0, 3), seg))
    return out


def synth_tone(place, wins, seconds=30.0):
    """A steady tone with the place's own spectrum: no event of any source survives into it (see the header)."""
    import zlib
    from scipy.signal import welch
    from scipy.ndimage import median_filter
    spread = [np.percentile(env(w), 98) - np.median(env(w)) for _, _, w in wins]
    keep = [w for (_, _, w), s in zip(wins, spread) if s <= np.median(spread)]
    f, P = welch(np.stack([w.mean(axis=1) for w in keep]), SR, nperseg=4096, axis=-1)
    med = np.median(10 * np.log10(P + 1e-20), axis=0)
    lf = np.log2(np.maximum(f, 20.0))
    grid = np.arange(lf.min(), lf.max(), 1 / 48)       # 1/48 octave grid
    g = np.interp(grid, lf, med)
    g = median_filter(g, size=32, mode='nearest')                                        # 2/3 octave: tones out
    k = np.hanning(9)
    g = np.convolve(g, k / k.sum(), mode='same')                                         # 1/6 octave smoothing
    n = int(seconds * SR)
    rng = np.random.default_rng(zlib.crc32(place.encode()))
    z = rng.standard_normal((3, n))
    ch = np.stack([np.sqrt(0.6) * z[0] + np.sqrt(0.4) * z[1], np.sqrt(0.6) * z[0] + np.sqrt(0.4) * z[2]])
    F = np.fft.rfftfreq(n, 1 / SR)
    H = 10 ** (np.interp(np.log2(np.maximum(F, 20.0)), grid, g) / 20)
    H[F < 40] *= (F[F < 40] / 40) ** 2                                                   # no DC or sub rumble
    y = np.fft.irfft(np.fft.rfft(ch, axis=1) * H, n=n, axis=1).T
    y = (y * 10 ** (-30 / 20) / np.sqrt((y ** 2).mean())).astype(np.float32)   # -30 dBFS RMS: above the meter's gate
    print(f'  {place}: spectrum from {len(keep)} of {len(wins)} windows')
    return y


def main(argv):
    ap = cli.parser('beds', __doc__)
    ap.add_argument('--effects', required=True, metavar='FILE')
    ap.add_argument('--speech', required=True, metavar='FILE')
    ap.add_argument('--spot', required=True, metavar='FILE')
    ap.add_argument('--plan', required=True, metavar='FILE')
    ap.add_argument('--out', required=True, metavar='FILE')
    ap.add_argument('--report', metavar='FILE')
    ap.add_argument('--no-land', action='store_true')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('beds')
    os.chdir(r.media)
    spot = read_spot(r.t(a.spot))
    fx = load(r.m(a.effects))
    speech = load(r.m(a.speech))
    avoid = [(float(x['sec_in']), float(x['sec_out'])) for x in spot if x['kind'] == 'hit']
    sources, beds, method = {}, [], 'loop'
    for line in open(r.t(a.plan), encoding='utf-8'):
        if line.startswith('#') or not line.strip():
            continue
        f = line.rstrip('\n').split('\t')
        if f[0] in ('source', 'clip'):
            sources.setdefault(f[1], []).append(r.m(f[2]) if f[0] == 'clip' else f[2])
        elif f[0] == 'method':
            method = f[1]
        elif f[0] == 'bed':
            beds.append(dict(place=f[1], span=f[2], lufs=float(f[3]), fade=float(f[4]), note=f[5] if len(f) > 5 else ''))
    n = len(fx)
    out = np.zeros((n, 2), np.float32)
    meter = pyln.Meter(SR)
    tones, rep = {}, []
    for place, blocks in sources.items():
        if method == 'synth':
            wins = spectrum_windows(fx, speech, spot, blocks, avoid)
            tones[place] = synth_tone(place, wins)
            for nm, t0, _ in wins:
                rep.append(('window', place, nm, t0))
            continue
        tones[place], used = tone_for(fx, speech, spot, blocks, avoid)
        print(f'{place}: {len(tones[place]) / SR:.2f} s of tone from {len(used)} runs')
        for u in used:
            rep.append(('source', place, *u))
    for bd in beds:
        a0, b0 = span(spot, bd['span'])
        i0, i1 = int(round(a0 * SR)), int(round(b0 * SR))
        x = loop_to(tones[bd['place']], i1 - i0).copy()
        L = meter.integrated_loudness(x.astype(np.float64))
        x *= 10 ** ((bd['lufs'] - L) / 20)
        fd = int(bd['fade'] * SR)
        if fd:
            rr = np.sin(np.linspace(0, np.pi / 2, fd, dtype=np.float32))[:, None] ** 2
            x[:fd] *= rr
            x[-fd:] *= rr[::-1]
        out[i0:i1] += x
        print(f"bed {bd['place']:14s} {a0:8.3f}-{b0:8.3f} s  {bd['lufs']:.1f} LUFS  ({bd['note']})")
        rep.append(('bed', bd['place'], bd['span'], round(a0, 3), round(b0, 3), bd['lufs']))
    o = r.m(a.out)
    os.makedirs(os.path.dirname(o) or '.', exist_ok=True)
    # scipy's writer, not soundfile's: libsndfile adds a PEAK chunk with a timestamp, so two identical
    # runs would differ in the header. This file is byte-reproducible.
    from scipy.io import wavfile
    wavfile.write(o, SR, out.astype(np.float32))
    made = [o]
    if a.report:
        rp = r.m(a.report)
        with open(rp, 'w', encoding='utf-8') as fo:
            fo.write('# written by bin/tool beds; source rows: place, block, sec_in, sec_out of a run used\n')
            for x in rep:
                fo.write('\t'.join(map(str, x)) + '\n')
        made.append(rp)
    print('wrote', o, f'{n / SR:.3f} s')
    if a.no_land:
        print('NOT LANDED (--no-land): in the Mac shell, run: bin/tool land ' + ' '.join(made))
        return 0
    return landing.land(r, made, tool='beds')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
