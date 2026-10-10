"""The contract every tool honours.

  --text DIR    the caller's toplevel (worktree or main): LOCKS.yaml, production.yaml, edl/,
                RENDER-LOG.md, MEDIA-MANIFEST.tsv, audio/, subtitles/ are read and written here
  --media DIR   the main checkout, the local mirror: assets/ and r<N>/ are read and written here
  --stage DIR   local scratch for encoding ($CUT_STAGE, default .cut-stage in the home folder): never encode
                onto a network mount (about 15x slower)
bin/tool passes --text and --media first and exports TEXT, MEDIA and PIPELINE; run by hand,
--text defaults to $TEXT or the current folder and --media to $MEDIA or the text root.
Tools never use __file__ to find production files, only to find the pipeline itself.
Relative paths on the command line resolve against the root their kind lives in: text files
(EDLs, spotting sheets, subtitles) against --text, media (clips, cuts, sheets) against --media.
"""
import argparse
import os
import re
import sys

from lib import paths


class Roots:
    def __init__(self, text, media, stage):
        self.text, self.media, self.stage = text, media, stage
        self.pipeline = paths.pipeline_root()

    def t(self, p):
        return p if os.path.isabs(p) else os.path.join(self.text, p)

    def m(self, p):
        return p if os.path.isabs(p) else os.path.join(self.media, p)

    def say(self, tool):
        print(f"{tool}: text {self.text} · media {self.media} · pipeline {self.pipeline}", file=sys.stderr)


def parser(prog, doc):
    first, _, rest = (doc or "").strip().partition("\n")
    ap = argparse.ArgumentParser(prog=prog, description=first, epilog=rest,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_argument_group("roots (bin/tool passes --text and --media)")
    g.add_argument("--text", metavar="DIR", help="the text root: this checkout's toplevel ($TEXT, else .)")
    g.add_argument("--media", metavar="DIR", help="the media root: the main checkout ($MEDIA, else the text root)")
    g.add_argument("--stage", metavar="DIR", help="local encoding scratch ($CUT_STAGE, else .cut-stage in the home folder)")
    return ap


def roots(a):
    text = os.path.abspath(a.text or os.environ.get("TEXT") or os.getcwd())
    media = os.path.abspath(a.media or os.environ.get("MEDIA") or text)
    stage = os.path.abspath(os.path.expanduser(a.stage or os.environ.get("CUT_STAGE") or os.path.join("~", ".cut-stage")))
    for name, d in (("text", text), ("media", media)):
        if not os.path.isdir(d):
            sys.exit(f"--{name} {d}: no such folder")
    return Roots(text, media, stage)


def split_roots(argv):
    """For the tools that keep their original hand-rolled argument lists (edl_cut, strays):
    take --text/--media/--stage out of argv and return (namespace, the rest)."""
    ns = argparse.Namespace(text=None, media=None, stage=None)
    rest, i = [], 0
    while i < len(argv):
        k = argv[i]
        m = re.match(r"^--(text|media|stage)(?:=(.*))?$", k)
        if m:
            if m.group(2) is not None:
                setattr(ns, m.group(1), m.group(2))
            else:
                if i + 1 >= len(argv):
                    sys.exit(f"{k} needs a folder")
                setattr(ns, m.group(1), argv[i + 1])
                i += 1
        else:
            rest.append(k)
        i += 1
    return ns, rest


def latest_round(media):
    """The highest-numbered r<N> folder in the media root (r3 for START), or None."""
    rs = [d for d in os.listdir(media) if re.fullmatch(r"r\d+", d) and os.path.isdir(os.path.join(media, d))]
    return max(rs, key=lambda d: int(d[1:])) if rs else None


def mounted(path):
    """False when `path` sits on a volume under /Volumes that is not mounted — the case where a
    mkdir -p would quietly build the folder on the local disk instead."""
    p = os.path.abspath(path).split(os.sep)
    if len(p) > 2 and p[1] == "Volumes":
        return os.path.ismount(os.sep.join(p[:3]))
    return True
