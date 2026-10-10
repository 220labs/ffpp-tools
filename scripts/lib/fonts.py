"""A font by role, found through one search list; a missing font is a named error.

The tools were written in a Linux container whose system fonts they named by absolute path
(/usr/share/fonts/truetype/dejavu/…). On a Mac those paths do not exist, and a silent
fallback would change every burned-in label and card. So each role names exact files, and
the search list is: $FFPP_FONTS, the Linux system folders the tools carried, /Library/Fonts,
the user's own Library/Fonts, then scripts/fonts/ (filled by scripts/fonts/fetch-fonts.sh).
"""
import os

from lib import paths

ROLES = {
    "sans": "DejaVuSans.ttf",
    "sans-bold": "DejaVuSans-Bold.ttf",
    "mono-bold": "DejaVuSansMono-Bold.ttf",
    "serif": "C059-Roman.otf",
    "serif-bold": "C059-Bold.otf",
    "serif-italic": "C059-Italic.otf",
}


class FontMissing(SystemExit):
    pass


def dirs():
    out = [os.environ.get("FFPP_FONTS"),
           "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/opentype/urw-base35",
           "/Library/Fonts", os.path.expanduser(os.path.join("~", "Library", "Fonts")),
           os.path.join(paths.SCRIPTS, "fonts"),
           # a worktree has no fonts of its own (gitignored, fetched once per checkout): its main
           # checkout's
           os.path.join(paths.main_checkout(paths.SCRIPTS), "fonts")]
    return list(dict.fromkeys(d for d in out if d))


def path(role):
    name = ROLES[role]
    for d in dirs():
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    raise FontMissing(f"font '{role}' ({name}) not found in: {', '.join(dirs())} — "
                      f"run: sh {os.path.join(paths.SCRIPTS, 'fonts', 'fetch-fonts.sh')}")
