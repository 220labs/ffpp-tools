#!/usr/bin/env python3
"""Subtitles (SRT) for a cut, one file per language, from the spotting sheet's spoken lines and a lines table.

Usage: bin/tool make_srt audio/spotting-<cut>.tsv OUTBASE [--lines subtitles/lines.tsv] [--langs ru,en]
Writes OUTBASE.<lang>.srt for each of --langs (UTF-8), the names Plex reads beside OUTBASE.mp4.
lines.tsv rows: source clip, source second, join ('+' = extends the previous subtitle), style
('i' = italic), then one text column per language, in --langs order (START: ru, en).
Each detected line is matched to lines.tsv by its sound (source clip, source second within 0.15 s); detected
lines with no match (laughter, beeps, cheering) get no subtitle and are listed. A subtitle shows from 60 ms
before the line to 350 ms after it (at least 1 s, at most 6 s), and never overlaps the next one.
Paths are relative to the text root: the spotting sheet, the table and the SRTs are all text.
Ported from START's make_srt.py (2026-09-23): the languages and
the table became arguments; the same SRTs.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from lib import cli  # noqa: E402


def tc(t):
    ms = int(round(t * 1000))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def main(argv):
    ap = cli.parser('make_srt', __doc__)
    ap.add_argument('spotting', help='audio/spotting-<cut>.tsv')
    ap.add_argument('outbase', help='OUTBASE: writes OUTBASE.<lang>.srt')
    ap.add_argument('--lines', default=os.path.join('subtitles', 'lines.tsv'), help='the lines table (default subtitles/lines.tsv)')
    ap.add_argument('--langs', default='ru,en', help='the text columns of the table, in order (default ru,en)')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('make_srt')
    langs = [x for x in a.langs.split(',') if x]
    width = 4 + len(langs)
    spot, base = r.t(a.spotting), r.t(a.outbase)
    tab = [l.rstrip('\n').split('\t') for l in open(r.t(a.lines)) if l.strip() and not l.startswith('#')]
    tab = [(x + [''] * width)[:width] for x in tab]
    rows = [l.rstrip('\n').split('\t') for l in open(spot) if not l.startswith('#')]
    R = [dict(zip(rows[0], x)) for x in rows[1:] if x[0] == 'line']
    subs, unmatched, used = [], [], set()
    for x in R:
        t0, t1 = float(x['sec_in']), float(x['sec_out'])
        hit = next((i for i, y in enumerate(tab) if y[0] == x['source'] and abs(float(y[1]) - float(x['src_in'])) < 0.15 and i not in used), None)
        if hit is None:
            unmatched.append(f"{x['tc_in']} {x['source']} @{x['src_in']}")
            continue
        used.add(hit)
        src, si, join, style, *texts = tab[hit]
        if join == '+' and subs:
            subs[-1][1] = t1
        else:
            subs.append([t0, t1, style, *texts])
    for i, s in enumerate(subs):
        lo = max(0.0, s[0] - 0.06)
        hi = max(s[1] + 0.35, lo + 1.0)
        hi = min(hi, lo + 6.0)
        if i + 1 < len(subs):
            hi = min(hi, subs[i + 1][0] - 0.1)
        s[0], s[1] = lo, hi
    for k, lang in enumerate(langs, 3):
        with open(f'{base}.{lang}.srt', 'w', encoding='utf-8') as f:
            for i, s in enumerate(subs, 1):
                txt = s[k]
                txt = f'<i>{txt}</i>' if s[2] == 'i' else txt
                f.write(f'{i}\n{tc(s[0])} --> {tc(s[1])}\n{txt}\n\n')
    missing = [f'{y[0]} @{y[1]}' for i, y in enumerate(tab) if i not in used]
    shown = f'{a.outbase}.{langs[0]}.srt' + ''.join(f' / .{x}.srt' for x in langs[1:])
    print(f'{shown}: {len(subs)} subtitles; detected lines without text: {unmatched}; table rows not heard: {missing}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
