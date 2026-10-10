#!/usr/bin/env python3
"""Find stray frames in an edit list before (or after) rendering.

Usage: bin/tool strays edl/<cut-id>.edl.tsv [--threshold 0.12] [--round rN]

A stray frame is a frame from another shot of the same source clip that slips into a segment,
because an in- or out-point sits on (or a hair past) a cut inside the source clip.
For every segment this works out exactly which source frames the renderer takes
(first frame = edl_cut.first_frame(in), the same rule the renderer uses, then n = round(len*24)
frames, len = (out-in)/speed), and checks them against the cuts found in that source clip
(ffmpeg scene detection at a low threshold, cached in r3/clips/.cuts/ under the media root;
r3 is --round, default the highest r<N>). It reports every segment with a source cut 1-3
frames inside either end (a longer run is a real shot of a multi-shot clip), with the fix
(move the in-point to just below the cut frame's time, or the out-point to cut/24 - 0.01).
Exit status 1 if any are found. It checks the edit list, not the pixels, so it catches
strays the rendered-cut scan can miss (a 1-frame stray between two hard cuts).
The EDL resolves against the text root (a worktree checks its own), clips against the media
root. Ported from START's strays.py (2026-09-23); same report.
"""
import sys, os, math, subprocess, re
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
import edl_cut  # noqa: E402
from edl_cut import rows, first_frame  # noqa: E402
from lib import cli  # noqa: E402

EDGE = 3
ns, argv = cli.split_roots(sys.argv[1:])
if not argv or argv[0].startswith('-'):
    print(__doc__.strip())
    sys.exit(0 if argv and argv[0] in ('-h', '--help') else 2)
R = cli.roots(ns)
R.say('strays')
edl_cut.TEXT = R.text
os.chdir(R.media)
ROUND = argv[argv.index('--round') + 1] if '--round' in argv else cli.latest_round(R.media)
TH = float(argv[argv.index('--threshold') + 1]) if '--threshold' in argv else 0.12
CACHE = f'{ROUND}/clips/.cuts'

def cuts(src):
    os.makedirs(CACHE, exist_ok=True)
    key = os.path.join(CACHE, os.path.basename(src) + f'.{TH}.txt')
    if not os.path.exists(key) or os.path.getmtime(key) < os.path.getmtime(src):
        out = subprocess.run(['ffmpeg', '-i', src, '-vf', f"select='gt(scene,{TH})',metadata=print", '-an', '-f', 'null', '-'],
                             capture_output=True, text=True).stderr
        fr = sorted({int(round(float(t) * 24)) for t in re.findall(r'pts_time:([0-9.]+)', out)})
        open(key, 'w').write('\n'.join(map(str, fr)))
    return [int(x) for x in open(key).read().split()]

bad = 0; t = 0.0
for src, a, b, audio, extra in rows(argv[0]):
    sp = extra[2] if len(extra) > 2 else 1.0
    n = int(round((b - a) / sp * 24))
    if src not in ('BLACK', 'CARD'):
        first = first_frame(a)
        last = first + max(1, math.ceil(n * sp)) - 1
        # a source cut 1-3 frames after the first taken frame, or 1-3 frames before the end,
        # leaves a sliver of another shot at the segment's edge (longer runs are real shots)
        edge = [c for c in cuts(src) if first < c <= last and (c - first <= EDGE or last - c + 1 <= EDGE)]
        for c in edge:
            bad += 1
            side = 'in' if c - first <= EDGE else 'out'
            if side == 'in':
                fix = f'in-point -> {c / 24 - 0.0001:.4f}'
            else:
                fix = (f'out-point -> {a + (c - first) / 24 * sp - 0.01:.3f}'
                       + (f', or in-point -> {first / 24 - 0.0001:.4f} (it sits a hair past frame {first - 1}; ffmpeg starts at the next frame)'
                          if a * 24 - (first - 1) < 0.01 else ''))
            tc = int(round(t * 24))
            print(f'{tc // 86400:02d}:{tc // 1440 % 60:02d}:{tc // 24 % 60:02d}:{tc % 24:02d}  {os.path.basename(src)} {a:.4f}-{b:.4f}: '
                  f'takes source frames {first}-{last}; source cut at frame {c} leaves '
                  f'{(c - first) if side == "in" else (last - c + 1)} stray frame(s) at the {side}; {fix}')
    t += n / 24
print(f'{bad} stray edge(s)' if bad else 'no strays: no source cut within 3 frames of any segment edge')
sys.exit(1 if bad else 0)
