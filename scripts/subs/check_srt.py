#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tiny validator for a production's translated subtitle set.

    bin/tool check-srt [--base film-rough-v31] [--ref en] [--langs uk,es,fr] [--cues 54] [--dir subtitles]

Checks, for every translated SRT next to <base>.<ref>.srt:
  * the file parses as SRT (index / timecode / >=1 text line, blank-separated)
  * it has exactly --cues cues (default: as many as the reference)
  * cue numbers and timecodes are identical to the reference file
  * <= 2 lines per cue, and <= 42 visible characters a line (zh: <= 16)
  * italic markup <i>...</i> sits on the same cues as in the reference
Defaults: --dir subtitles/ in the text root; --base the one base that has a <base>.<ref>.srt
there; --langs every other <base>.<lang>.srt beside it. START's set, as the original checked it:
  bin/tool check-srt --base film-rough-v31 --langs uk,es,fr,de,tr,zh --cues 54
Exit code 0 = all good.
Ported from START's check-srt.py (2026-09-23): BASE, N_CUES and
LANGS became arguments; the same checks and report.
"""
import io
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
from lib import cli  # noqa: E402

MAX_LINES = 2
MAX_CHARS = {"zh": 16}
DEFAULT_MAX_CHARS = 42

TC = re.compile(
    r"^(\d\d:\d\d:\d\d,\d\d\d) --> (\d\d:\d\d:\d\d,\d\d\d)$")
TAG = re.compile(r"</?i>")


def parse(path):
    """Return [(index, timecode, [lines])] or raise ValueError."""
    raw = io.open(path, encoding="utf-8-sig").read().replace("\r\n", "\n")
    cues = []
    for n, block in enumerate(b for b in raw.split("\n\n") if b.strip()):
        lines = block.strip("\n").split("\n")
        if len(lines) < 3:
            raise ValueError("block %d: fewer than 3 lines" % (n + 1))
        if not lines[0].strip().isdigit():
            raise ValueError("block %d: index %r is not a number"
                             % (n + 1, lines[0]))
        if not TC.match(lines[1].strip()):
            raise ValueError("block %d: bad timecode %r" % (n + 1, lines[1]))
        if not any(l.strip() for l in lines[2:]):
            raise ValueError("block %d: empty text" % (n + 1))
        cues.append((int(lines[0]), lines[1].strip(), lines[2:]))
    return cues


def visible(line):
    """Length as the viewer sees it: markup stripped."""
    return len(TAG.sub("", line))


def check(here, base, n_cues, lang, ref):
    path = os.path.join(here, "%s.%s.srt" % (base, lang))
    problems = []
    if not os.path.exists(path):
        return ["missing file %s" % path], 0
    try:
        cues = parse(path)
    except ValueError as exc:
        return ["parse error: %s" % exc], 0

    if len(cues) != n_cues:
        problems.append("has %d cues, expected %d" % (len(cues), n_cues))

    limit = MAX_CHARS.get(lang, DEFAULT_MAX_CHARS)
    for i, (num, tc, lines) in enumerate(cues):
        if i < len(ref):
            rnum, rtc, rlines = ref[i]
            if num != rnum:
                problems.append("cue #%d: index %d != EN %d" % (i + 1, num, rnum))
            if tc != rtc:
                problems.append("cue %d: timing %s != EN %s" % (num, tc, rtc))
            ital_ref = any("<i>" in l for l in rlines)
            ital_now = any("<i>" in l for l in lines)
            if ital_ref != ital_now:
                problems.append(
                    "cue %d: italics %s, EN has %s"
                    % (num, "present" if ital_now else "absent",
                       "them" if ital_ref else "none"))
            for l in lines:
                if ("<i>" in l) != ("</i>" in l):
                    problems.append("cue %d: unbalanced <i> tag" % num)
        if len(lines) > MAX_LINES:
            problems.append("cue %d: %d lines (max %d)" % (num, len(lines), MAX_LINES))
        for j, l in enumerate(lines):
            if visible(l) > limit:
                problems.append("cue %d line %d: %d chars (max %d) — %r"
                                % (num, j + 1, visible(l), limit, l))
    return problems, len(cues)


def main(argv):
    ap = cli.parser("check-srt", __doc__)
    ap.add_argument("--dir", default="subtitles", help="the SRT folder, relative to the text root (default subtitles)")
    ap.add_argument("--base", help="the cut the SRTs belong to, e.g. film-rough-v31")
    ap.add_argument("--ref", default="en", help="the reference language (default en)")
    ap.add_argument("--langs", help="comma-separated languages to check (default: every other one beside the reference)")
    ap.add_argument("--cues", type=int, help="the cue count every file must have (default: the reference's)")
    a = ap.parse_args(argv)
    r = cli.roots(a)
    r.say("check-srt")
    here = r.t(a.dir)
    names = sorted(os.listdir(here)) if os.path.isdir(here) else []
    base = a.base
    if not base:
        bases = sorted(n[:-len(".%s.srt" % a.ref)] for n in names if n.endswith(".%s.srt" % a.ref))
        if len(bases) != 1:
            sys.exit("check-srt: pass --base; %s holds %s" % (here, ", ".join(bases) or "no %s SRT" % a.ref))
        base = bases[0]
    ref_path = os.path.join(here, "%s.%s.srt" % (base, a.ref))
    ref = parse(ref_path)
    langs = [x for x in (a.langs or "").split(",") if x] or [
        n[len(base) + 1:-len(".srt")] for n in names
        if n.startswith(base + ".") and n.endswith(".srt") and n != "%s.%s.srt" % (base, a.ref)]
    n_cues = a.cues if a.cues is not None else len(ref)
    print("reference: %s.%s.srt — %d cues" % (base, a.ref, len(ref)))
    failed = 0
    for lang in langs:
        problems, n = check(here, base, n_cues, lang, ref)
        limit = MAX_CHARS.get(lang, DEFAULT_MAX_CHARS)
        if problems:
            failed += 1
            print("FAIL %s  (%d cues)" % (lang, n))
            for p in problems:
                print("       - %s" % p)
        else:
            print("ok   %s  %d cues, timings match EN, <=%d lines, <=%d chars/line"
                  % (lang, n, MAX_LINES, limit))
    print("%d/%d files pass" % (len(langs) - failed, len(langs)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
