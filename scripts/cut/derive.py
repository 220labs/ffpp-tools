#!/usr/bin/env python3
"""Rebuild derived clips from another set of source clips (e.g. Flow's 720p upscales).

Usage:  bin/tool derive r3/clips-720 [--only NAME] [--config FILE] [--round rN] [--no-land]

A derived clip is a take re-timed or re-voiced once, at 360p, into r3/clips/<derived>.mp4.
This rebuilds its picture from DIR/<source>.mp4 and copies its sound untouched from the 360p
derived clip (sound edits do not depend on resolution). Output: DIR/<derived>.mp4, same name,
so `bin/tool edl_cut --clips DIR` picks it up. A source found only as DIR/<name>.mov (Topaz
exports 10-bit ProRes) gives DIR/<derived>.mov in ProRes 422 HQ 10-bit instead.

The recipes are the production's, in --config (default: edl/derive.yaml in the text root),
one entry per derived clip, in the order they are built:
  <derived>:  {from: <source take>, select: {first: F, step: S, count: N}}
                  frames F + ceil(S * i), i = 0..N-1 (a speed-up read off by frame match)
  <derived>:  {from: <source take>, copy: true}
                  the picture unchanged, video stream-copied (the derived clip re-placed sound only)
  <derived>:  {from: <source take>, filter: "<ffmpeg video filter chain>"}
                  e.g. half speed, motion-interpolated, with a fade
Every output is checked against the 360p derived clip: same frame count, and each frame,
scaled down to its size, matches it (PSNR printed; worst frame > 25 dB). The outputs are
masters: the run ends by landing them (--no-land in the Desktop VM, said out loud).
Ported from START's derive.py (2026-09-23): the Python table
became the config; the same ffmpeg commands.
"""
import math
import shutil
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from lib import cli, landing, production  # noqa: E402

ENC = ['-c:v', 'libx264', '-crf', '14', '-preset', 'slow', '-pix_fmt', 'yuv420p']


def run(args):
    subprocess.run(['ffmpeg', '-y', '-v', 'error', *args], check=True)


def frames(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-select_streams', 'v:0', '-show_entries',
                          'stream=nb_read_frames,width,height', '-of', 'csv=p=0', path], capture_output=True, text=True).stdout
    w, h, n = out.strip().split(',')
    return int(w), int(h), int(n)


def psnr(new, ref):
    # new scaled to ref's size; returns the average and the worst-frame PSNR
    w, h, _ = frames(ref)
    log = subprocess.run(['ffmpeg', '-v', 'info', '-i', new, '-i', ref, '-lavfi',
                          f'[0:v]scale={w}:{h}:flags=area,setpts=N/24/TB[a];[1:v]setpts=N/24/TB[b];[a][b]psnr=stats_file=-',
                          '-f', 'null', '-'], capture_output=True, text=True)
    vals = [float(x.split('psnr_avg:')[1].split()[0]) for x in log.stdout.splitlines() if 'psnr_avg:' in x]
    vals = [99.0 if math.isinf(v) else v for v in vals]
    return sum(vals) / len(vals), min(vals), len(vals)


def md5s(path, vf):
    # one md5 per decoded frame, for comparing pictures rather than packets
    r = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:v:0', '-vf', vf, '-fps_mode', 'passthrough',
                        '-f', 'framemd5', '-'], capture_output=True, text=True, check=True)
    return [l.split(',')[-1].strip() for l in r.stdout.splitlines() if l and not l.startswith('#')]


def pick(d, stem):
    mp4, mov = os.path.join(d, stem + '.mp4'), os.path.join(d, stem + '.mov')
    return mp4 if os.path.exists(mp4) or not os.path.exists(mov) else mov


def build(d, name, recipe, rnd):
    src = pick(d, recipe['from'])
    ext = os.path.splitext(src)[1]
    enc = ENC if ext == '.mp4' else ['-c:v', 'prores_ks', '-profile:v', '3', '-pix_fmt', 'yuv422p10le']
    ref, out = os.path.join(rnd, 'clips', name + '.mp4'), os.path.join(d, name + ext)
    part = out + '.part' + ext
    if 'select' in recipe and ext == '.mov':
        # ProRes is intra-only: the selected frames are the source's own packets, copied one at a time and
        # joined, so a Topaz master is not re-encoded. Checked against the decoded select below.
        s = recipe['select']
        idx = [s['first'] + math.ceil(s['step'] * i) for i in range(s['count'])]
        tmp = os.path.join(os.path.dirname(out), '.' + os.path.basename(out) + '.frames')  # dot: land skips it
        os.makedirs(tmp, exist_ok=True)
        for i, f in enumerate(idx):
            run(['-i', src, '-ss', f'{(f - 0.25) / 24:.6f}', '-frames:v', '1', '-map', '0:v:0', '-c:v', 'copy',
                 '-video_track_timescale', '12288', os.path.join(tmp, f'{i:04d}.mov')])
        with open(os.path.join(tmp, 'list.txt'), 'w') as fh:
            fh.writelines(f"file '{os.path.abspath(os.path.join(tmp, f'{i:04d}.mov'))}'\n" for i in range(len(idx)))
        run(['-f', 'concat', '-safe', '0', '-i', os.path.join(tmp, 'list.txt'), '-i', ref, '-map', '0:v', '-map', '1:a',
             '-c', 'copy', part])
        sel = '+'.join(f'eq(n,{f})' for f in idx)
        got = md5s(part, 'null')
        if len(got) != len(idx) or got != md5s(src, f"select='{sel}'"):
            sys.exit(f'{name}: the copied frames are not the source frames {idx[0]}..{idx[-1]}')
        shutil.rmtree(tmp)
    elif 'select' in recipe:
        s = recipe['select']
        sel = '+'.join(f"eq(n,{s['first'] + math.ceil(s['step'] * i)})" for i in range(s['count']))
        run(['-i', src, '-i', ref, '-filter_complex', f"[0:v]select='{sel}',setpts=N/24/TB[v]",
             '-map', '[v]', '-map', '1:a', *enc, '-c:a', 'copy', part])
    elif recipe.get('copy'):
        run(['-i', src, '-i', ref, '-map', '0:v', '-map', '1:a', '-c', 'copy', part])
    elif 'filter' in recipe:
        run(['-i', src, '-i', ref, '-filter_complex', f"[0:v]{recipe['filter']}[v]",
             '-map', '[v]', '-map', '1:a', *enc, '-c:a', 'copy', part])
    else:
        sys.exit(f'{name}: a recipe needs select, copy or filter')
    os.replace(part, out)
    (w, h, n), (_, _, nr) = frames(out), frames(ref)
    avg, worst, k = psnr(out, ref)
    ok = n == nr and worst > 25
    print(f'{name}: {w}x{h} {n}/{nr} frames, PSNR vs 360p derived avg {avg:.1f} dB, worst {worst:.1f} dB '
          f'-> {"OK" if ok else "CHECK"}')
    return ok, out


def main(argv):
    ap = cli.parser('derive', __doc__)
    ap.add_argument('dir', help='the other set of sources, relative to the media root (e.g. r3/clips-720)')
    ap.add_argument('--only', metavar='NAME', help='one derived clip')
    ap.add_argument('--config', metavar='FILE', help='the recipes (default: edl/derive.yaml in the text root)')
    ap.add_argument('--round', metavar='rN', help='whose clips/ holds the 360p derived clips (default: the highest r<N>)')
    ap.add_argument('--no-land', action='store_true', help='build without landing the outputs')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('derive')
    cfg = r.t(a.config or os.path.join('edl', 'derive.yaml'))
    if not os.path.isfile(cfg):
        sys.exit(f'derive: no recipes at {cfg} — derive --help shows the shape')
    recipes = production.load_yaml(cfg) or {}
    os.chdir(r.media)
    rnd = a.round or cli.latest_round(r.media)
    if a.only and a.only not in recipes:
        sys.exit(f'unknown derived clip {a.only}')
    names = [a.only] if a.only else list(recipes)
    bad, made = [], []
    for n in names:
        ok, out = build(a.dir.rstrip('/'), n, recipes[n], rnd)
        made.append(out)
        if not ok:
            bad.append(n)
    if a.no_land:
        print('NOT LANDED (--no-land): in the Mac shell, run: bin/tool land ' + ' '.join(made))
        return 1 if bad else 0
    return 1 if bad or landing.land(r, made, tool='derive') else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
