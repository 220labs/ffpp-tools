#!/usr/bin/env python3
"""The studio's QR code for a film's last card: black on white, quiet zone included.

    bin/tool qr https://ffpp.pictures assets/brand/ffpp-pictures-qr.png [--no-land]

Error correction Q (25%), so the code still reads if the card is filmed off a screen or the
picture is re-compressed. Black modules on white with a 4-module border: a QR inverted (light
on dark) is out of spec and many phone cameras refuse it, which is why the card shows a white
tile rather than a transparent one. edl_cut places it with a card line '@<path>|<height>'.
The output path is relative to the media root; the tile is an asset, so the run ends by
landing it (--no-land in the Desktop VM, said out loud).
Needs the qrcode package with Pillow (pip3 install --user 'qrcode[pil]'), as the original did.
Ported from START's qr.py (2026-09-23); the same QR parameters.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from lib import cli, landing  # noqa: E402


def main(argv):
    ap = cli.parser('qr', __doc__)
    ap.add_argument('url')
    ap.add_argument('out', help='the PNG, relative to the media root')
    ap.add_argument('--no-land', action='store_true')
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say('qr')
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_Q
    except ImportError:
        sys.exit("qr: needs the qrcode package: pip3 install --user 'qrcode[pil]'")
    out = r.m(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_Q, box_size=20, border=4)
    qr.add_data(a.url)
    qr.make(fit=True)
    qr.make_image(fill_color='black', back_color='white').convert('RGB').save(out)
    print(f'{a.out}: version {qr.version}, {qr.modules_count} modules, {a.url}')
    if a.no_land:
        print(f'NOT LANDED (--no-land): in the Mac shell, run: bin/tool land {a.out}')
        return 0
    return landing.land(r, [out], tool='qr')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
