#!/usr/bin/env python3
"""Put a cut on /wip for review, the same way every time, and check both copies.

    bin/tool wip <cut-id>...          build the master and the review copy, verify both (e.g. film-rough-v17)
    bin/tool wip --missing            the same for every edl/<pattern>.edl.tsv lacking a copy
    bin/tool wip --status             one line per cut matching --pattern: master / wip / last verify
    bin/tool wip --only clean <id>    one half only (clean ~50 s, review ~2.8 min for a 4-min cut),
    bin/tool wip --only review <id>   for shells with a short time limit. Then:
    bin/tool wip --verify <id>

Places:  master  <media>/r<N>/cut/<cut-id>.mp4             clean (gitignored), landed on Drive by edl_cut
         wip     <wip>/<label_prefix>-<cut-id>.mp4          review copy, plus -index.md
The /wip folder is --wip, else $CUT_WIP, else production.yaml's `wip` — never a default: the
old default was a local folder, and a mkdir -p there "published" to this Mac's disk.
A folder on an unmounted network share (a mount point under /Volumes) is refused for the same reason. /wip takes clips and
cuts only, never stills.
Before any build, strays checks the edit list: no source cut may sit 1-3 frames inside a
segment's edge (the stray-frame bug). The review copy is built on local disk (--stage,
$CUT_STAGE, .cut-stage in the home folder) and then copied to /wip through a .part name: encoding straight onto
the network share was ~15x slower. Verify = both files decode and both frame counts equal the
index total. The result is appended to r<N>/cut/PUBLISH.log (tsv: time, cut, OK|FAIL, detail)
— a cut counts as published only on OK. --round (default: the highest r<N>); --pattern
(default film-*: the whole-film cuts; START's are film-rough-*); --no-land in the Desktop VM.
Ported from START's publish.sh (2026-09-23), whose "repo copy"
half is now `bin/tool land`, run by edl_cut on every clean cut.
"""
import datetime
import fnmatch
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from lib import cli, production  # noqa: E402


def natural(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


def frames(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                          "stream=nb_read_frames", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return out.strip()


def has(p):
    return os.path.exists(p) and os.path.getsize(p) > 0


def idof(x):
    b = os.path.basename(x)
    return b[:-len(".edl.tsv")] if b.endswith(".edl.tsv") else b


class Wip:
    def __init__(self, r, a):
        self.r, self.a = r, a
        self.round = a.round or cli.latest_round(r.media)
        if not self.round:
            sys.exit(f"wip: no r<N> folder in {r.media}: pass --round")
        p = production.load(r.text, r.media)
        self.prefix = p.label_prefix
        self.wip = a.wip or os.environ.get("CUT_WIP") or p.wip
        if not self.wip:
            sys.exit(f"wip: no /wip folder — set `wip:` in {p.file}, or pass --wip DIR")
        self.cut = os.path.join(r.media, self.round, "cut")
        self.log = os.path.join(self.cut, "PUBLISH.log")

    def ready(self):
        if not cli.mounted(self.wip):
            sys.exit(f"wip: {self.wip} is on a volume that is not mounted — mount the share; nothing is written in its place")
        os.makedirs(self.wip, exist_ok=True)
        os.makedirs(self.cut, exist_ok=True)
        os.makedirs(self.r.stage, exist_ok=True)

    def names(self, cid):
        return (os.path.join(self.cut, f"{cid}.mp4"), os.path.join(self.wip, f"{self.prefix}-{cid}.mp4"),
                os.path.join(self.wip, f"{self.prefix}-{cid}-index.md"))

    def tool(self, name, *args):
        # the edit list first: edl_cut and strays keep their original argv[0]-is-the-EDL parsing
        return subprocess.run([sys.executable, os.path.join(HERE, name), "--text", self.r.text, "--media", self.r.media,
                               *args, "--round", self.round]).returncode

    def push(self, cid):
        st = self.r.stage
        shutil.copyfile(os.path.join(st, f"{self.prefix}-{cid}-index.md"), os.path.join(self.wip, f"{self.prefix}-{cid}-index.md"))
        dest = os.path.join(self.wip, f"{self.prefix}-{cid}.mp4")
        shutil.copyfile(os.path.join(st, f"{self.prefix}-{cid}.mp4"), dest + ".part")
        os.replace(dest + ".part", dest)

    def build(self, which, cid):
        edl = f"edl/{cid}.edl.tsv"
        if not os.path.isfile(os.path.join(self.r.text, edl)):
            print(f"{cid}: no {edl}", file=sys.stderr)
            return False
        if self.tool("strays.py", edl) != 0:
            print(f"{cid}: stray frames in the edit list - fix before building", file=sys.stderr)
            return False
        land = ["--no-land"] if self.a.no_land else []
        if which == "clean":
            return self.tool("edl_cut.py", edl, *land) == 0
        if which == "review":
            if self.tool("edl_cut.py", edl, "--wip", self.r.stage, "--no-clean") != 0:
                return False
        elif self.tool("edl_cut.py", edl, "--wip", self.r.stage, *land) != 0:
            return False
        self.push(cid)
        return True

    def verify(self, cid):
        clean, rev, idx = self.names(cid)
        if not has(clean):
            res = "FAIL\tno repo copy"
        elif not has(rev) or not has(idx):
            res = "FAIL\tno wip copy or index"
        else:
            m = re.search(r"Total ([0-9:]*)", open(idx, encoding="utf-8").read())
            tc = m.group(1) if m else ""
            try:
                h, mi, s, f = (int(x) for x in tc.split(":"))
                want = str(((h * 60 + mi) * 60 + s) * 24 + f)
            except ValueError:
                want = ""
            fc, fr = frames(clean), frames(rev)
            res = f"OK\t{want} frames, {tc}" if fc == want and fr == want else f"FAIL\tframes repo={fc} wip={fr} index={want}"
        line = f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}\t{cid}\t{res}"
        print(line)
        with open(self.log, "a") as fh:
            fh.write(line + "\n")
        return res.startswith("OK")

    def edls(self):
        d = os.path.join(self.r.text, "edl")
        pat = self.a.pattern + ".edl.tsv"
        return sorted((idof(f) for f in os.listdir(d) if fnmatch.fnmatch(f, pat)), key=natural)

    def status(self):
        if not cli.mounted(self.wip):
            print(f"wip: {self.wip} is not mounted — every wip column reads n", file=sys.stderr)
        last = {}
        if os.path.exists(self.log):
            for l in open(self.log, encoding="utf-8"):
                f = l.rstrip("\n").split("\t")
                if len(f) >= 4:
                    last[f[1]] = f"{f[2]} {f[3]} ({f[0]})"
        for cid in self.edls():
            clean, rev, _ = self.names(cid)
            y = lambda p: "y" if has(p) else "n"
            print(f"{cid:<18} repo:{y(clean)} wip:{y(rev)}  {last.get(cid, 'not verified')}")
        return 0


def main(argv):
    ap = cli.parser("wip", __doc__)
    ap.add_argument("ids", nargs="*", help="cut ids (or edl/<id>.edl.tsv)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--missing", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--verify", action="store_true", help="verify the named cuts only")
    g.add_argument("--only", choices=["clean", "review"])
    ap.add_argument("--wip", metavar="DIR", help="the review folder ($CUT_WIP, else production.yaml wip)")
    ap.add_argument("--round", metavar="rN")
    ap.add_argument("--pattern", default="film-*", help="which EDLs --status and --missing cover (default film-*)")
    ap.add_argument("--no-land", action="store_true", help="build without landing the master (the Desktop VM)")
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say("wip")
    w = Wip(r, a)
    if a.status:
        return w.status()
    w.ready()
    rc = 0
    if a.verify:
        for x in a.ids:
            rc |= 0 if w.verify(idof(x)) else 1
    elif a.only:
        if len(a.ids) != 1:
            ap.error("--only takes one cut id")
        rc = 0 if w.build(a.only, idof(a.ids[0])) else 1
    elif a.missing:
        for cid in w.edls():
            clean, rev, _ = w.names(cid)
            if not has(clean) and not w.build("clean", cid):
                rc = 1
            if not has(rev) and not w.build("review", cid):
                rc = 1
            if has(clean) and has(rev) and not w.verify(cid):
                rc = 1
    else:
        if not a.ids:
            ap.print_help()
            return 2
        for x in a.ids:
            cid = idof(x)
            if not (w.build("both", cid) and w.verify(cid)):
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
