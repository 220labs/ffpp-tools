#!/usr/bin/env python3
"""Posters, both directions of the release kit: key art generated in Flow, and film frames. Every size, every
language, lettering set in code from release/poster.yaml.

Usage:
  bin/tool poster [--config release/poster.yaml] [--clips DIR] [--date "12 Dec 2026|12 декабря 2026"] [--wip DIR]
                  [--only NAME] [--round rN] [--no-land]

The config (text root), the tables START's poster.py carried:
  film:      edl/<cut>.edl.tsv        the film whose frames are read (a timecode maps to the clip frame the film shows)
  clips:     r3/clips-720             the clip set the frames come from (media root; --clips overrides; 4K later)
  languages: [en, ru]
  title:     {en: START, ru: СТАРТ}    tagline: {en: …, ru: …}    ages: {en: …, ru: …}
  billing:   {en: [line, …], ru: [line, …]}     the billing block, the film's own card wording
  keyart:    r3/posters/keyart/<file>.jpg       the key art picked (media root); frame: HH:MM:SS:FF, the film frame
  jobs:      - {name: a-onesheet, size: [1200, 1800], layout: keyart, source: keyart, centre: 0.5}
             - {name: onesheet,   size: [1200, 1800], layout: framed, source: frame}
             - {name: thumb-laugh, size: [1280, 720], layout: thumb,  source: "00:03:20:00"}
             layout: keyart | framed | bleed-top | bleed-left | thumb; source: keyart (the config's key art), a
             key-art file, frame (the config's film frame) or a film timecode; centre: the key art's horizontal
             centre for the cover crop (0..1)
Writes r<N>/posters/<name>-<lang>.png plus a .jpg of each (quality 92) for upload, lands them (--no-land in the
Desktop VM), and with --wip copies the .jpg files there as <label_prefix>-poster-<name>-<lang>.jpg.
Lettering: C059 (the film's card face), by role through scripts/lib/fonts.py (Roman, Bold, Italic).
Ported from START's scripts/poster/poster.py (2026-09-25): TITLE, TAG, AGES, BILL, KEYART,
FRAME_B and JOBS became the config; the same layouts and the same pixels.
"""
import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SCRIPTS, 'cut'))
sys.path.insert(0, SCRIPTS)
import edl_cut  # noqa: E402
import promo  # noqa: E402
from lib import cli, fonts, landing, production  # noqa: E402

NAVY, WARM = (14, 22, 48), (255, 243, 222)
ROLE = {'Roman': 'serif', 'Bold': 'serif-bold', 'Italic': 'serif-italic'}
CFG = {}   # the config, read once in main


def font(style, size):
    return ImageFont.truetype(fonts.path(ROLE[style]), int(size))


def frame(tc, segs, out):
    f = promo.tcf(tc)
    for f0, n, src, ff, sp, extra in segs:
        if f0 <= f < f0 + n:
            clip = edl_cut.sub(src)
            k = ff + (f - f0)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', clip, '-vf', f"select='eq(n,{k})'", '-frames:v', '1', out], check=True)
            return Image.open(out).convert('RGB'), f'{os.path.basename(clip)} frame {k}'
    sys.exit(f'{tc}: not in the film')


def centred(d, text, fnt, y, W, fill, x0=None, shadow=True):
    w = d.textlength(text, font=fnt)
    x = (W - w) / 2 if x0 is None else x0
    if shadow:
        d.text((x + 2, y + 3), text, font=fnt, fill=(0, 0, 0, 140))
    d.text((x, y), text, font=fnt, fill=fill)
    return w


def fit(d, text, style, want_w, max_size):
    s = max_size
    while s > 8 and d.textlength(text, font=font(style, s)) > want_w:
        s *= 0.96
    return font(style, s), s


def make(job, lang, img, date):
    name, (W, H), layout = job['name'], job['size'], job['layout']
    TITLE, TAG, AGES, BILL = (CFG[k][lang] for k in ('title', 'tagline', 'ages', 'billing'))
    can = Image.new('RGB', (W, H), NAVY)
    d = ImageDraw.Draw(can, 'RGBA')
    if layout == 'keyart':
        # full bleed, cover-cropped around cx; the key art keeps its sky clear for the title, so dark type on it
        ink, ink2 = (20, 32, 63), (58, 42, 26)
        sc = max(W / img.width, H / img.height)
        big = img.resize((round(img.width * sc), round(img.height * sc)), Image.LANCZOS)
        cx = float(job.get('centre', 0.5)) * big.width
        x0 = int(min(max(0, cx - W / 2), big.width - W))
        can.paste(big.crop((x0, 0, x0 + W, H)), (0, 0))
        d = ImageDraw.Draw(can, 'RGBA')
        tf, ts = fit(d, TITLE, 'Bold', W * 0.66, H * 0.16)
        y = H * 0.055
        centred(d, AGES, font('Roman', W * 0.032), y, W, ink + (235,), shadow=False)
        centred(d, TITLE, tf, y + W * 0.05, W, ink, shadow=False)
        centred(d, TAG, font('Italic', W * 0.042), y + W * 0.05 + ts * 1.0, W, ink2, shadow=False)
        if date:
            centred(d, date, font('Bold', W * 0.034), y + W * 0.05 + ts * 1.0 + W * 0.06, W, ink, shadow=False)
        shade = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        for yy in range(int(H * 0.16)):
            sd.line([(0, H - yy), (W, H - yy)], fill=NAVY + (int(200 * (1 - yy / (H * 0.16))),))
        can.paste(shade, (0, 0), shade)
        d = ImageDraw.Draw(can, 'RGBA')
        bf = font('Roman', W * 0.021)
        yb = H - W * 0.05 - 3 * W * 0.032
        for line in BILL:
            centred(d, line, bf, yb, W, WARM + (230,), shadow=False)
            yb += W * 0.032
    elif layout == 'framed':
        fw = int(W * 0.88)
        fh = int(fw * img.height / img.width)
        pic = img.resize((fw, fh), Image.LANCZOS)
        fy = int(H * (0.40 if H / W > 1.6 else 0.36))
        can.paste(pic, ((W - fw) // 2, fy))
        d.rectangle([(W - fw) // 2 - 1, fy - 1, (W + fw) // 2, fy + fh], outline=(255, 243, 222, 90), width=2)
        tf, ts = fit(d, TITLE, 'Bold', W * 0.70, W * 0.24)
        top = fy - ts * 1.25 - W * 0.07
        centred(d, AGES, font('Roman', W * 0.03), top - W * 0.05, W, WARM + (230,))
        centred(d, TITLE, tf, top, W, WARM)
        centred(d, TAG, font('Italic', W * 0.042), fy + fh + W * 0.045, W, WARM)
        if date:
            centred(d, date, font('Bold', W * 0.034), fy + fh + W * 0.11, W, WARM)
        bf = font('Roman', W * 0.021)
        y = H - W * 0.05 - 3 * W * 0.032
        for line in BILL:
            centred(d, line, bf, y, W, WARM + (215,), shadow=False)
            y += W * 0.032
    else:
        pic = img.resize((W, int(W * img.height / img.width)), Image.LANCZOS).crop((0, 0, W, H))
        can.paste(pic, (0, 0))
        shade = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(shade)
        if layout == 'bleed-top':
            for yy in range(int(H * 0.18)):
                sd.line([(0, H - yy), (W, H - yy)], fill=NAVY + (int(210 * (1 - yy / (H * 0.18))),))
        elif layout == 'bleed-left':
            for x in range(int(W * 0.55)):
                sd.line([(x, 0), (x, H)], fill=NAVY + (int(225 * (1 - x / (W * 0.55)) ** 1.4),))
        else:
            for y in range(int(H * 0.45)):
                yy = H - y
                sd.line([(0, yy), (W, yy)], fill=(0, 0, 0, int(200 * (1 - y / (H * 0.45)) ** 1.3)))
        can.paste(shade, (0, 0), shade)
        d = ImageDraw.Draw(can, 'RGBA')
        if layout == 'bleed-top':
            # the title in the open sky, top left, in dark ink as on the key art; the billing on the darkened tarmac
            ink, ink2, x0 = (20, 32, 63), (58, 42, 26), W * 0.06
            tf, ts = fit(d, TITLE, 'Bold', W * 0.30, H * 0.16)
            y = H * 0.055
            centred(d, AGES, font('Roman', H * 0.032), y, W, ink + (235,), x0=x0, shadow=False)
            centred(d, TITLE, tf, y + H * 0.045, W, ink, x0=x0, shadow=False)
            centred(d, TAG, font('Italic', H * 0.042), y + H * 0.045 + ts * 1.02, W, ink2, x0=x0, shadow=False)
            if date:
                centred(d, date, font('Bold', H * 0.034), y + H * 0.045 + ts * 1.02 + H * 0.06, W, ink, x0=x0, shadow=False)
            # billing right-aligned, where this layout's key art is plain;
            # the credits line splits in two here: at full length it ran into the art
            bill = BILL[:1] + BILL[1].split('  ·  ') + BILL[2:]
            bf = font('Roman', H * 0.022)
            yb = H - H * 0.04 - (len(bill) - 1) * H * 0.032
            for line in bill:
                centred(d, line, bf, yb, W, WARM + (235,), x0=W * 0.96 - d.textlength(line, font=bf), shadow=False)
                yb += H * 0.032
        elif layout == 'bleed-left':
            x0 = W * 0.06
            tf, ts = fit(d, TITLE, 'Bold', W * 0.34, H * 0.22)
            y = H * 0.30
            centred(d, AGES, font('Roman', H * 0.032), y - H * 0.06, W, WARM, x0=x0)
            centred(d, TITLE, tf, y, W, WARM, x0=x0)
            centred(d, TAG, font('Italic', H * 0.045), y + ts * 1.2, W, WARM, x0=x0)
            if date:
                centred(d, date, font('Bold', H * 0.036), y + ts * 1.2 + H * 0.08, W, WARM, x0=x0)
            bf = font('Roman', H * 0.022)
            yb = H - H * 0.06 - 3 * H * 0.032
            for line in BILL:
                centred(d, line, bf, yb, W, WARM + (215,), x0=x0, shadow=False)
                yb += H * 0.032
        else:   # thumbnail: the title only, big enough for a phone
            tf, ts = fit(d, TITLE, 'Bold', W * 0.46, H * 0.30)
            centred(d, TITLE, tf, H - ts * 1.28, W, WARM, x0=W * 0.05)
    return can


def main(argv):
    ap = cli.parser('poster', __doc__)
    ap.add_argument('--config', metavar='FILE', help='the tables (default: release/poster.yaml in the text root)')
    ap.add_argument('--clips', metavar='DIR', help='the clip set the frames are read from (default: the config\'s)')
    ap.add_argument('--date', metavar='EN|RU', help='a date line under the tagline, one per language, | between')
    ap.add_argument('--wip', metavar='DIR', help='copy the .jpg files there for review')
    ap.add_argument('--only', metavar='NAME', help='one job')
    ap.add_argument('--round', metavar='rN', help='whose posters/ and clips/ (default: the highest r<N>)')
    ap.add_argument('--no-land', action='store_true', help='build without landing the outputs')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('poster')
    p = production.load(r.text, r.media)
    cfgp = r.t(a.config or os.path.join('release', 'poster.yaml'))
    if not os.path.isfile(cfgp):
        sys.exit(f'poster: no config at {cfgp} — release/poster.yaml holds the title, tagline, billing, key art, '
                 'frame and jobs (the template has the shape)')
    CFG.update(production.load_yaml(cfgp) or {})
    for k in ('film', 'title', 'tagline', 'ages', 'billing', 'jobs'):
        if not CFG.get(k):
            sys.exit(f'poster: {cfgp} lacks `{k}`')
    langs = CFG.get('languages') or ['en', 'ru']
    rnd = a.round or cli.latest_round(r.media)
    if not rnd:
        sys.exit(f'poster: no r<N> folder in {r.media}: pass --round')
    edl_cut.TEXT, edl_cut.ROUND, edl_cut.CLIPS = r.text, rnd, a.clips or CFG.get('clips') or f'{rnd}/clips-720'
    os.chdir(r.media)
    dates = dict(zip(langs, a.date.split('|'))) if a.date else {}
    segs = promo.film_segments(r.t(CFG['film']))
    out = os.path.join(rnd, 'posters')
    tmp = os.path.join(out, '.tmp')
    os.makedirs(tmp, exist_ok=True)
    cache, made = {}, []
    jobs = [j for j in CFG['jobs'] if not a.only or j['name'] == a.only]
    if a.only and not jobs:
        sys.exit(f'poster: no job named {a.only} in {cfgp}')
    for job in jobs:
        src = job['source']
        if job['layout'] == 'keyart' or (src != 'frame' and ':' not in src):
            path = CFG['keyart'] if src == 'keyart' else src
            img, where = Image.open(path).convert('RGB'), f'key art {os.path.basename(path)}'
        else:
            tc = CFG['frame'] if src == 'frame' else src
            if tc not in cache:
                cache[tc] = frame(tc, segs, os.path.join(tmp, tc.replace(':', '') + '.png'))
            img, where = cache[tc]
        for lang in langs:
            pic = make(job, lang, img, dates.get(lang))
            base = os.path.join(out, f"{job['name']}-{lang}")
            pic.save(base + '.png')
            pic.save(base + '.jpg', quality=92)
            made += [base + '.png', base + '.jpg']
            print(f'{base}.png  {pic.width}x{pic.height}  ({where})')
            if a.wip:
                os.makedirs(a.wip, exist_ok=True)
                pic.save(os.path.join(a.wip, f"{p.label_prefix}-poster-{job['name']}-{lang}.jpg"), quality=92)
    if a.no_land:
        print('NOT LANDED (--no-land): in the Mac shell, run: bin/tool land ' + ' '.join(made))
        return 0
    return landing.land(r, made, tool='poster')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
