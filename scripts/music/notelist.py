#!/usr/bin/env python3
"""MIDI -> the compact note list the in-browser renderer plays (scripts/music/render_in_browser.js).

Usage:  bin/tool notelist IN.mid > OUT.txt
Path relative to the media root. Prints 't,d,c,p,v;...' in ms (start, duration, channel, pitch, velocity) on
stdout and the count on stderr. Channel 8 on program 24 (nylon guitar) is written as channel 12, which the
renderer maps to its nylon-guitar samples. The list is what `window.__render(notes, name, seconds)` takes
(scripts/music/render_in_browser.js).
Ported from START's scripts/music/notelist.py (2026-09-25), unchanged.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli  # noqa: E402


def main(argv):
    if len(argv) < 1 or '--help' in argv or '-h' in argv:
        print(__doc__.strip())
        return 0 if '--help' in argv or '-h' in argv else 2
    ns, args = cli.split_roots(argv)
    r = cli.roots(ns)
    os.chdir(r.media)
    import mido
    m = mido.MidiFile(args[0])
    t = 0.0
    on, prog, out = {}, {}, []
    for msg in m:
        t += msg.time
        if msg.type == 'program_change':
            prog[msg.channel] = msg.program
        elif msg.type == 'note_on' and msg.velocity > 0:
            on[(msg.channel, msg.note)] = on.get((msg.channel, msg.note), []) + [(t, msg.velocity, prog.get(msg.channel))]
        elif msg.type in ('note_off', 'note_on'):
            k = (msg.channel, msg.note)
            if on.get(k):
                t0, v, pr = on[k].pop(0)
                ch = msg.channel
                if ch == 8 and pr == 24:
                    ch = 12
                out.append((int(round(t0 * 1000)), max(20, int(round((t - t0) * 1000))), ch, msg.note, v))
    out.sort()
    print(';'.join(','.join(map(str, x)) for x in out))
    print(f'{len(out)} notes, end {t:.3f} s', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
