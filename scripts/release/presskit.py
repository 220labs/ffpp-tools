#!/usr/bin/env python3
"""Festival press kit as a PDF: HTML set in C059 and printed by Chromium (Playwright), its words from the release
kit and release/presskit.yaml, so the kit and the doc never disagree.

Usage:
  bin/tool presskit [--config release/presskit.yaml] [--poster FILE] [--stills DIR] [--film FILE] [--out FILE]
                    [--fonts DIR] [--final] [--round rN] [--no-land]

The config (text root):
  kit:        RELEASE-KIT.md              the release kit (text root); the four paragraphs are read from it by label
  labels:     {logline: "Logline, EN.", short: "Short synopsis, EN", long: "Long synopsis, EN", statement: "EN."}
  title:      START                       kicker: the line under the title
  film:       r3/cut/<master>.mp4         the film the stills are extracted from when a still is missing (media root)
  poster:     r3/posters/a-onesheet-en.png   the cover (media root; --poster overrides)
  stills:     - [01-door, "00:00:02", "The door opens"]   file stem, timecode, caption
  how_made:   [paragraph, …]              tech: [[field, value], …]      filmmakers: [[name, bio], …]
  screeners:  the closing paragraph       open_items: what is still open (the DRAFT footer)
Stills are <stills dir>/<stem>.jpg (--stills, default r<N>/presskit/stills); one that is missing is extracted from
`film` at its timecode, so the kit rebuilds from the repo and the masters alone. Without --final every page carries a
DRAFT mark and the footer lists the open items. --fonts is the folder holding C059-Roman/Bold/Italic.otf (default: where
scripts/lib/fonts.py finds them). Writes --out (default r<N>/presskit/<title>-press-kit-<draft|final>.pdf) and the
HTML beside it, and lands the PDF (--no-land in the Desktop VM).
Ported from START's scripts/presskit/presskit.py (2026-09-25): every START sentence and table
became the config; the same CSS and pages.
"""
import html
import os
import pathlib
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli, fonts, landing, production  # noqa: E402


def para(md, label):
    """The text after a bold label, e.g. '**Logline, EN.** ...' -> '...'."""
    m = re.search(r'\*\*' + re.escape(label) + r'[^*]*\*\*\s*(.+)', md)
    if not m:
        sys.exit(f'not in the kit: {label}')
    return m.group(1).strip()


def esc(s):
    s = html.escape(str(s))
    return s.replace('&quot;', '"').replace('&#x27;', "'")


def extract(film, tc, out):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', tc, '-i', film, '-frames:v', '1', '-q:v', '2', out], check=True)


def main(argv):
    ap = cli.parser('presskit', __doc__)
    ap.add_argument('--config', metavar='FILE', help='the words and tables (default: release/presskit.yaml in the text root)')
    ap.add_argument('--poster', metavar='FILE', help='the cover (default: the config\'s)')
    ap.add_argument('--stills', metavar='DIR', help='the stills (default: r<N>/presskit/stills)')
    ap.add_argument('--film', metavar='FILE', help='the film a missing still is extracted from (default: the config\'s)')
    ap.add_argument('--out', metavar='FILE', help='the PDF (default: r<N>/presskit/<title>-press-kit-<draft|final>.pdf)')
    ap.add_argument('--fonts', metavar='DIR', help='the folder with C059-Roman/Bold/Italic.otf')
    ap.add_argument('--final', action='store_true', help='no DRAFT mark, no open-items footer')
    ap.add_argument('--round', metavar='rN')
    ap.add_argument('--no-land', action='store_true')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('presskit')
    cfgp = r.t(a.config or os.path.join('release', 'presskit.yaml'))
    if not os.path.isfile(cfgp):
        sys.exit(f'presskit: no config at {cfgp} — release/presskit.yaml holds the kit\'s words and tables (the template has the shape)')
    c = production.load_yaml(cfgp) or {}
    for k in ('kit', 'title', 'stills', 'tech', 'how_made'):
        if not c.get(k):
            sys.exit(f'presskit: {cfgp} lacks `{k}`')
    labels = dict({'logline': 'Logline, EN.', 'short': 'Short synopsis, EN', 'long': 'Long synopsis, EN', 'statement': 'EN.'},
                  **(c.get('labels') or {}))
    md = open(r.t(c['kit']), encoding='utf-8').read()
    rnd = a.round or cli.latest_round(r.media)
    if not rnd:
        sys.exit(f'presskit: no r<N> folder in {r.media}: pass --round')
    os.chdir(r.media)
    final = a.final
    fdir = pathlib.Path(a.fonts or os.path.dirname(fonts.path('serif'))).resolve()
    for f in ('C059-Roman.otf', 'C059-Bold.otf', 'C059-Italic.otf'):
        if not (fdir / f).is_file():
            sys.exit(f'presskit: {fdir / f} missing — run scripts/fonts/fetch-fonts.sh or pass --fonts DIR')
    poster = a.poster or c.get('poster')
    if not poster or not os.path.isfile(poster):
        sys.exit(f'presskit: no poster at {poster!r} (--poster, or `poster:` in the config)')
    poster = pathlib.Path(poster).resolve()
    stills = pathlib.Path(a.stills or os.path.join(rnd, 'presskit', 'stills')).resolve()
    stills.mkdir(parents=True, exist_ok=True)
    film = a.film or c.get('film')
    for stem, tc, _ in c['stills']:
        if not (stills / f'{stem}.jpg').is_file():
            if not film or not os.path.isfile(film):
                sys.exit(f'presskit: still {stem}.jpg missing in {stills} and no film to extract it from (`film:` in the config)')
            extract(film, tc, str(stills / f'{stem}.jpg'))
            print(f'extracted {stem}.jpg from {os.path.basename(film)} at {tc}')
    title = c['title']
    out = a.out or os.path.join(rnd, 'presskit', f'{title}-press-kit-{"final" if final else "draft"}.pdf')
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    logline, short, long_ = para(md, labels['logline']), para(md, labels['short']), para(md, labels['long'])
    statement = para(md, labels['statement'])
    open_items = c.get('open_items', '')

    css = f"""
    @font-face {{ font-family: C059; font-weight: 400; src: url('{(fdir / 'C059-Roman.otf').as_uri()}'); }}
    @font-face {{ font-family: C059; font-weight: 700; src: url('{(fdir / 'C059-Bold.otf').as_uri()}'); }}
    @font-face {{ font-family: C059; font-style: italic; src: url('{(fdir / 'C059-Italic.otf').as_uri()}'); }}
    @page {{ size: A4; margin: 0; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: C059, serif; color: #1b2440; font-size: 11pt; line-height: 1.45; }}
    .page {{ width: 210mm; height: 297mm; padding: 20mm 20mm 18mm; position: relative; page-break-after: always; overflow: hidden; background: #fbf6ee; }}
    .page:last-child {{ page-break-after: auto; }}
    .cover {{ padding: 0; background: #0e1630; display: flex; align-items: center; justify-content: center; }}
    .cover img {{ height: 297mm; width: auto; display: block; }}
    h1 {{ font-size: 30pt; margin: 0 0 2mm; letter-spacing: 1px; }}
    h2 {{ font-size: 15pt; margin: 9mm 0 3mm; border-bottom: 0.4pt solid #b9a98f; padding-bottom: 1.5mm; }}
    .kicker {{ font-style: italic; color: #6b5536; margin: 0 0 6mm; font-size: 12pt; }}
    .logline {{ font-size: 14pt; font-style: italic; margin: 4mm 0 2mm; }}
    p {{ margin: 0 0 3mm; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 5mm 6mm; margin-top: 4mm; }}
    .grid figure {{ margin: 0; }}
    .grid img {{ width: 100%; display: block; border-radius: 1.5mm; }}
    figcaption {{ font-size: 9pt; color: #5b4a33; margin-top: 1.2mm; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 10.5pt; }}
    td {{ padding: 2mm 2mm; border-bottom: 0.3pt solid #d8ccb8; vertical-align: top; }}
    td:first-child {{ width: 45mm; color: #6b5536; }}
    .draft {{ position: absolute; top: 120mm; left: -10mm; width: 230mm; text-align: center; transform: rotate(-30deg);
             font-size: 90pt; font-weight: 700; color: rgba(160, 40, 40, 0.10); pointer-events: none; }}
    .foot {{ position: absolute; bottom: 8mm; left: 20mm; right: 20mm; font-size: 8pt; color: #8a7a60; display: flex; justify-content: space-between; }}
    """

    def page(body, n):
        mark = '' if final else '<div class="draft">DRAFT</div>'
        foot = (f'<div class="foot"><span>{esc(title)} · press kit</span>'
                f'<span>{"" if final else esc(open_items) + " · "}{n}</span></div>')
        return f'<section class="page">{mark}{body}{foot}</section>'

    pages = [f'<section class="page cover"><img src="{poster.as_uri()}"></section>']
    pages.append(page(f"""
      <h1>{esc(title)}</h1><p class="kicker">{esc(c.get('kicker', ''))}</p>
      <p class="logline">{esc(logline)}</p>
      <h2>Synopsis</h2><p>{esc(short)}</p>
      <h2>Long synopsis</h2><p>{esc(long_)}</p>""", 2))
    how = ''.join(f'<p>{esc(x)}</p>' for x in c['how_made'])
    pages.append(page(f"""
      <h2 style="margin-top:0">Director's statement</h2><p>{esc(statement)}</p>
      <h2>How it was made</h2>{how}""", 3))
    figs = ''.join(f'<figure><img src="{(stills / (stem + ".jpg")).as_uri()}"><figcaption>{esc(tc)} · {esc(cap)}</figcaption></figure>'
                   for stem, tc, cap in c['stills'])
    pages.append(page(f'<h2 style="margin-top:0">Stills</h2><div class="grid">{figs}</div>', 4))
    rows = ''.join(f'<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>' for k, v in c['tech'])
    makers = ''.join(f'<p><b>{esc(n)}</b> · {esc(b)}</p>' for n, b in (c.get('filmmakers') or []))
    pages.append(page(f"""
      <h2 style="margin-top:0">Credits and technical specifications</h2><table>{rows}</table>
      <h2>The filmmakers</h2>{makers}
      <h2>Screeners and materials</h2>
      <p>{esc(c.get('screeners', ''))}</p>""", 5))
    doc = (f'<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)} press kit</title><style>{css}</style></head>'
           f'<body>{"".join(pages)}</body></html>')
    htmlp = pathlib.Path(out).with_suffix('.html')
    htmlp.write_text(doc, encoding='utf-8')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page()
        pg.goto(htmlp.resolve().as_uri(), wait_until='networkidle')
        pg.pdf(path=out, format='A4', print_background=True, prefer_css_page_size=True)
        b.close()
    print('wrote', out)
    if a.no_land:
        print('NOT LANDED (--no-land): in the Mac shell, run: bin/tool land ' + out)
        return 0
    return landing.land(r, [out], tool='presskit')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
