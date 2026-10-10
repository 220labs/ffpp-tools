#!/usr/bin/env python3
"""The mix: dialogue, effects and the score, conformed to a cut, ducked, and set to the film's loudness.

Usage (needs numpy, scipy, soundfile, pyloudnorm, ffmpeg):
  bin/tool mix --edl edl/film-rough-v37.edl.tsv --stems DIR [--stems-edl edl/film-rough-v32.edl.tsv]
               --prod r3/audio/film-rough-v37-production.wav --cues DIR --spot audio/spotting-film-rough-v37.tsv
               --out r3/audio/mix/film-mix-v8 [--beds FILE] [--fx-mute audio/fx-mute.tsv] [--toys FILE]
               [--config audio/mix.yaml] [--conform-only DIR] [--no-land]

Text root: --edl, --stems-edl, --spot, --fx-mute, --config. Media root: --stems, --prod, --cues, --out, --beds, --toys.
--stems  speech.wav / music.wav / effects.wav from BandIt Plus (any rate; .flac beside .wav is taken), cut to
         --stems-edl. They are conformed to --edl segment by segment (same clip, same source time); anything the
         old cut did not have is taken from --prod (the new cut's own production sound) into the effects.
--beds   an optional ambience bed from bin/tool beds, levels in finished-mix LUFS; added into the effects after
         the balance, scaled by the master gain.
--fx-mute FILE  spans of the cut's own effects to take out (audio/fx-mute.tsv: in_s out_s fade_s note), before toys and beds.
--toys   an optional bed from bin/tool toys (the prop recordings, already placed on the spotting sheet's hits);
         added into the effects before the balance, so it rides the same gain.
--cues   <cue>.wav per cue in the config's cue sheet, placed at its anchor block in the spotting sheet.
The production's facts are the config (default audio/mix.yaml):
  cue_sheet: [[cue1, opening, 0.0, "02 "], …]   cue, anchor block (a prefix of the block's note in the spotting
             sheet), offset in s, and the block the cue must be gone by (null = it plays out)
  keep_prod_music: [05-v1-t1.mp4]   clips whose own baked music stays (e.g. a drum baked into a clip); Flow's other music goes
  lufs: -16   true_peak: -1.5   dialogue_lufs: -21   duck: {db: 13.0, pre: 0.15, rel: 0.6}   (the defaults)
Writes OUT.wav (48 kHz, 24-bit) and OUT-dialogue.wav, OUT-music.wav, OUT-effects.wav (the same gains, before the
final limiter), plus OUT-qa.tsv (per-block loudness and true peak), and lands them (--no-land in the Desktop VM).
Ported from START's scripts/mix/mix.py (2026-09-25): CUE_SHEET and KEEP_PROD_MUSIC became the
config; the same maths and the same ffmpeg filters.
"""
import math
import os
import subprocess
import sys

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import butter, resample_poly, sosfilt

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import spotting  # noqa: E402
from spotting import FPS, audio_source, segments  # noqa: E402
from lib import cli, landing, production  # noqa: E402

SR = 48000
DUCK_DB, DUCK_PRE, DUCK_REL = 13.0, 0.15, 0.6       # music under every spoken line (the config's `duck` overrides)


def load(path):
    if not os.path.exists(path) and os.path.exists(os.path.splitext(path)[0] + '.flac'):
        path = os.path.splitext(path)[0] + '.flac'
    x, sr = sf.read(path, always_2d=True, dtype='float32')
    if x.shape[1] == 1:
        x = np.repeat(x, 2, axis=1)
    if sr != SR:
        g = math.gcd(SR, sr)
        x = resample_poly(x, SR // g, sr // g, axis=0).astype(np.float32)
    return x


def conform(old_edl, new_edl, stems, prod):
    """Rebuild each stem on the new cut from the old cut's timeline, segment by segment."""
    olds = list(segments(old_edl))
    news = list(segments(new_edl))
    n = (news[-1]['f0'] + news[-1]['n']) * SR // FPS
    out = {k: np.zeros((n, 2), np.float32) for k in stems}
    patched = []
    for s in news:
        if s['src'] in ('BLACK', 'CARD') or s['audio'] == 'mute':
            continue   # mute: no sound of its own (edl_cut)
        t0 = s['f0'] / FPS
        L = s['n'] / FPS
        clip, st = audio_source(s, t0)
        # candidate old segments with the same sound source
        cover = np.zeros(int(round(L * SR)), bool)
        vo = s['audio'] == 'voice-out'
        cands = [o for o in olds if o['src'] not in ('BLACK', 'CARD') and abs(o['sp'] - s['sp']) < 1e-6
                 and (o['audio'] == 'voice-out') == vo and audio_source(o, o['f0'] / FPS)[0] == clip]
        # the same sound can sit in several old segments: the one nearest in the timeline is written last and wins
        cands.sort(key=lambda o: -abs(o['f0'] - s['f0']))
        for o in cands:
            oc, ost = audio_source(o, o['f0'] / FPS)
            oL = o['n'] / FPS
            a = max(st, ost)
            b = min(st + L * s['sp'], ost + oL * o['sp'])
            if b - a <= 1e-3:
                continue
            nd0 = int(round((t0 + (a - st) / s['sp']) * SR))
            od0 = int(round((o['f0'] / FPS + (a - ost) / o['sp']) * SR))
            m = int(round((b - a) / s['sp'] * SR))
            m = min(m, n - nd0, len(next(iter(stems.values()))) - od0)
            for k in stems:
                out[k][nd0:nd0 + m] = stems[k][od0:od0 + m]
            c0 = nd0 - int(round(t0 * SR))
            cover[max(0, c0):c0 + m] = True
        if not cover.all():
            idx = np.where(~cover)[0]
            a0 = int(round(t0 * SR))
            out['effects'][a0 + idx] = prod[np.minimum(a0 + idx, len(prod) - 1)]
            patched.append((os.path.basename(s['src']), round(len(idx) / SR, 3)))
        # 10 ms fades at every join, as the renderer does 40 ms
        f = int(0.01 * SR)
        a0 = int(round(t0 * SR))
        a1 = a0 + int(round(L * SR))
        rr = np.linspace(0, 1, f, dtype=np.float32)[:, None]
        for k in out:
            out[k][a0:a0 + f] *= rr
            out[k][max(a0, a1 - f):a1] *= rr[::-1][:a1 - max(a0, a1 - f)]
    return out, patched, news


def read_spot(path):
    rows = [l.rstrip('\n').split('\t') for l in open(path, encoding='utf-8') if not l.startswith('#')]
    hdr = rows[0]
    return [dict(zip(hdr, r)) for r in rows[1:]]


def block_start(spot, prefix):
    for r in spot:
        if r['kind'] == 'block' and r['block'].startswith(prefix):
            return float(r['sec_in'])
    raise KeyError(prefix)


def duck_curve(n, lines, db=None, pre=None, rel=None):
    db, pre, rel = DUCK_DB if db is None else db, DUCK_PRE if pre is None else pre, DUCK_REL if rel is None else rel
    g = np.zeros(n, np.float32)
    for a, b in lines:
        i0, i1, i2, i3 = [int(max(0, min(n, x * SR))) for x in (a - pre, a, b, b + rel)]
        if i1 > i0:
            g[i0:i1] = np.maximum(g[i0:i1], np.linspace(0, db, i1 - i0))
        g[i1:i2] = db
        if i3 > i2:
            g[i2:i3] = np.maximum(g[i2:i3], np.linspace(db, 0, i3 - i2))
    return (10 ** (-g / 20)).astype(np.float32)


def ffx(x, af):
    """Run an ffmpeg audio filter over a buffer (float in, float out, same length)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        sf.write(f'{d}/i.wav', x, SR, subtype='FLOAT')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', f'{d}/i.wav', '-af', af, '-c:a', 'pcm_f32le', f'{d}/o.wav'], check=True)
        y, _ = sf.read(f'{d}/o.wav', dtype='float32', always_2d=True)
    return y[:len(x)] if len(y) >= len(x) else np.pad(y, ((0, len(x) - len(y)), (0, 0)))


def level_lines(dia, lines, target=-21.0, lo=-3.0, hi=6.0, ramp=0.08):
    """Dialogue leveling: each spoken line toward the target loudness, within [lo, hi] dB, ramped."""
    n = len(dia)
    g = np.zeros(n, np.float32)
    m = pyln.Meter(SR, block_size=0.2)
    for a, b in lines:
        i0, i1 = int(a * SR), int(b * SR)
        try:
            L = m.integrated_loudness(dia[i0:i1].astype(np.float64))
        except Exception:
            continue
        if not np.isfinite(L):
            continue
        d = float(np.clip(target - L, lo, hi))
        rr = int(ramp * SR)
        j0, j1 = max(0, i0 - rr), min(n, i1 + rr)
        env = np.ones(j1 - j0, np.float32)
        env[:i0 - j0] = np.linspace(0, 1, i0 - j0)
        env[len(env) - (j1 - i1):] = np.linspace(1, 0, j1 - i1)
        g[j0:j1] = np.where(np.abs(env * d) > np.abs(g[j0:j1]), env * d, g[j0:j1])
    return (10 ** (g / 20)).astype(np.float32)


def lufs(x):
    m = pyln.Meter(SR)
    try:
        return m.integrated_loudness(x.astype(np.float64))
    except Exception:
        return -99.0


def main(argv):
    ap = cli.parser('mix', __doc__)
    ap.add_argument('--edl', required=True)
    ap.add_argument('--stems', required=True, metavar='DIR')
    ap.add_argument('--stems-edl', metavar='FILE', help='the cut the stems were separated on (default: --edl, nothing conformed)')
    ap.add_argument('--prod', metavar='FILE', help='the new cut\'s own production sound (needed when --stems-edl differs)')
    ap.add_argument('--cues', metavar='DIR')
    ap.add_argument('--spot', metavar='FILE')
    ap.add_argument('--out', metavar='PATH', help='r<N>/audio/mix/<mix> (without .wav)')
    ap.add_argument('--beds', metavar='FILE')
    ap.add_argument('--fx-mute', metavar='FILE')
    ap.add_argument('--toys', metavar='FILE')
    ap.add_argument('--config', metavar='FILE', help='the cue sheet and the rest (default: audio/mix.yaml in the text root)')
    ap.add_argument('--conform-only', metavar='DIR', help='write the conformed stems there and stop')
    ap.add_argument('--no-land', action='store_true')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('mix')
    cfgp = r.t(a.config or os.path.join('audio', 'mix.yaml'))
    c = (production.load_yaml(cfgp) if os.path.isfile(cfgp) else None) or {}
    spotting.edl_cut.TEXT = r.text
    edl = r.t(a.edl)
    old_edl = r.t(a.stems_edl) if a.stems_edl else edl
    stems_dir = r.m(a.stems)
    stems = {k: load(f'{stems_dir}/{k}.wav') for k in ('speech', 'music', 'effects')}
    if old_edl != edl:
        if not a.prod:
            sys.exit('mix: --prod is needed to conform the stems from --stems-edl to --edl')
        st, patched, segs = conform(old_edl, edl, stems, load(r.m(a.prod)))
    else:
        st, patched, segs = stems, [], list(segments(edl))
    n = len(st['speech'])
    print('conformed', n / SR, 's; patched from production:', patched)
    if a.conform_only:
        d = r.m(a.conform_only)
        os.makedirs(d, exist_ok=True)
        for k, v in st.items():
            sf.write(f'{d}/{k}.wav', v, SR, subtype='FLOAT')
        print('wrote conformed stems to', d)
        return 0
    for k in ('spot', 'out', 'cues'):
        if not getattr(a, k):
            sys.exit(f'mix: --{k} is needed')
    if not c.get('cue_sheet'):
        sys.exit(f'mix: {cfgp} has no `cue_sheet` — audio/mix.yaml names each cue, its anchor block, offset and the block it is gone by')
    spot = read_spot(r.t(a.spot))
    out = r.m(a.out)
    cues = r.m(a.cues)
    target = float(c.get('lufs', -16))
    tp_max = float(c.get('true_peak', -1.5))
    dia_target = float(c.get('dialogue_lufs', -21))
    duck = c.get('duck') or {}
    keep_music = set(c.get('keep_prod_music') or [])
    # production music: only where its source is kept (keep_prod_music)
    keep = np.zeros(n, np.float32)
    for s in segs:
        clip, _ = audio_source(s, s['f0'] / FPS) if s['src'] not in ('BLACK', 'CARD') else ('', 0)
        if clip in keep_music:
            keep[int(s['f0'] / FPS * SR):int((s['f0'] + s['n']) / FPS * SR)] = 1.0
    k = int(0.05 * SR)
    keep = np.convolve(keep, np.ones(k) / k, mode='same').astype(np.float32)
    hp = butter(2, 70, 'highpass', fs=SR, output='sos')
    dia = sosfilt(hp, st['speech'], axis=0).astype(np.float32)
    fx = st['effects'] + st['music'] * keep[:, None]
    if a.fx_mute:
        # spans of the cut's own effects taken out (audio/fx-mute.tsv: in_s out_s fade_s note), before the toys and
        # the beds go in, so a toy or a bed can stand where Flow's sound was
        g = np.ones(n, np.float32)
        for line in open(r.t(a.fx_mute), encoding='utf-8'):
            if line.startswith('#') or not line.strip():
                continue
            t0, t1, fd = (float(v) for v in line.split('\t')[:3])
            a0, a1, kk = int(t0 * SR), int(t1 * SR), max(1, int(fd * SR))
            g[a0:a1] = 0
            g[max(0, a0 - kk):a0] = np.minimum(g[max(0, a0 - kk):a0], np.linspace(1, 0, a0 - max(0, a0 - kk), dtype=np.float32))
            g[a1:a1 + kk] = np.minimum(g[a1:a1 + kk], np.linspace(0, 1, len(g[a1:a1 + kk]), dtype=np.float32))
            print(f'effects muted {t0:.3f}-{t1:.3f} s (fades {fd} s)')
        fx *= g[:, None]
    if a.toys:
        t = load(r.m(a.toys))
        m = min(len(t), n)
        fx[:m] += t[:m]
        print('toys bed added:', a.toys, f'{len(t) / SR:.3f} s')
    # the score
    mus = np.zeros((n, 2), np.float32)
    lines = [(float(x['sec_in']), float(x['sec_out'])) for x in spot if x['kind'] == 'line']
    for name, anchor, off, gone in c['cue_sheet']:
        p = f'{cues}/{name}.wav'
        if not os.path.exists(p):
            print('no', p)
            continue
        cue = load(p)
        t0 = block_start(spot, anchor) + float(off)
        a0 = int(round(t0 * SR))
        m = min(len(cue), n - a0)
        if gone:
            tg = block_start(spot, gone)
            e = int(round(tg * SR)) - a0
            if e < m:
                f = int(1.0 * SR)
                cue = cue.copy()
                cue[max(0, e - f):e] *= np.linspace(1, 0, e - max(0, e - f), dtype=np.float32)[:, None]
                cue[e:] = 0
        mus[a0:a0 + m] += cue[:m]
    # balance: dialogue to its target (speech-gated by the meter), effects at the production balance,
    # the score so that its own integrated loudness sits at the same target before ducking
    gd = 10 ** ((dia_target - lufs(dia)) / 20)
    dia *= gd
    fx *= gd
    dia *= level_lines(dia, lines, target=dia_target)[:, None]
    mus = ffx(mus, 'acompressor=threshold=-30dB:ratio=2.5:attack=30:release=400:knee=6')
    gm = 10 ** ((dia_target - lufs(mus)) / 20)
    mus *= gm
    mus *= duck_curve(n, lines, duck.get('db'), duck.get('pre'), duck.get('rel'))[:, None]
    if a.beds:
        # beds writes each bed at its loudness in the FINISHED mix; undo the master gain it will get.
        # The gain is measured without the beds: they sit 20 dB and more under the program, so the
        # final gain moves by hundredths of a dB and each bed lands at the loudness its plan row gives.
        b = load(r.m(a.beds))
        m = min(len(b), n)
        g0 = target - lufs(dia + fx + mus)
        fx[:m] += b[:m] * 10 ** (-g0 / 20)
        print('beds added:', a.beds, f'(master gain before beds {g0:+.2f} dB)')
    mix = dia + fx + mus
    # final: gain to the target, then a true-peak-safe limiter (4x oversampled) at -1.8 dBFS so the
    # result reads -1.5 dBTP or lower; one trim pass if the limiter cost loudness. Linear, no AGC.
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    raw = out + '-premaster.wav'

    def master(g_db, lim_db=-1.8):
        sf.write(raw, mix * 10 ** (g_db / 20), SR, subtype='FLOAT')
        subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', raw, '-af',
                        f'aresample=192000,alimiter=limit={10 ** (lim_db / 20):.4f}:attack=1:release=80:level=disabled,aresample=48000',
                        '-c:a', 'pcm_s24le', out + '.wav'], check=True)
        e = subprocess.run(['ffmpeg', '-hide_banner', '-nostats', '-i', out + '.wav', '-af', 'ebur128=peak=true', '-f', 'null', '-'],
                           capture_output=True, text=True).stderr
        return float(e[e.rindex('I:'):].split()[1]), float(e[e.rindex('Peak:'):].split()[1])
    gain = target - lufs(mix)
    I, TP = master(gain)
    if abs(I - target) > 0.1:
        gain += target - I
        I, TP = master(gain)
    if TP > tp_max:
        I, TP = master(gain, -1.8 - (TP - tp_max) - 0.1)
    print(f'master: gain {gain:+.2f} dB, integrated {I} LUFS, true peak {TP} dBTP')
    gain = 10 ** (gain / 20)
    made = [out + '.wav']
    for name, x in (('dialogue', dia), ('music', mus), ('effects', fx)):
        sf.write(f'{out}-{name}.wav', np.clip(x * gain, -1, 1), SR, subtype='PCM_24')
        made.append(f'{out}-{name}.wav')
    os.remove(raw)
    # QA per block
    final, _ = sf.read(out + '.wav', always_2d=True)
    with open(out + '-qa.tsv', 'w', encoding='utf-8') as fo:
        fo.write('block\ttc_in\tLUFS\tpeak_dBFS\tdialogue_LUFS\tmusic_LUFS\teffects_LUFS\n')
        for x in spot:
            if x['kind'] != 'block':
                continue
            a0, b0 = int(float(x['sec_in']) * SR), int(float(x['sec_out']) * SR)
            seg = final[a0:b0]
            pk = 20 * np.log10(np.abs(seg).max() + 1e-9)
            fo.write(f"{x['block'][:40]}\t{x['tc_in']}\t{lufs(seg):.1f}\t{pk:.1f}\t{lufs(dia[a0:b0] * gain):.1f}\t"
                     f"{lufs(mus[a0:b0] * gain):.1f}\t{lufs(fx[a0:b0] * gain):.1f}\n")
    made.append(out + '-qa.tsv')
    print('wrote', out + '.wav')
    if a.no_land:
        print('NOT LANDED (--no-land): in the Mac shell, run: bin/tool land ' + ' '.join(made))
        return 0
    return landing.land(r, made, tool='mix')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
