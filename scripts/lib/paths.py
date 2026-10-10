"""Where things are: the pipeline, the productions, and a worktree's main checkout.

Two directions:
  pipeline from a production   bin/tool finds it by the ancestor rule and passes it as $PIPELINE;
                               a tool running from the pipeline's scripts/ already knows it: its
                               own location. find_pipeline() is the same rule, for code that
                               starts from a production path.
  productions from the pipeline  <pipeline main checkout>/../productions/<group>/<slug>/production.yaml,
                               exactly two levels (a production's own Claude Code worktrees are never
                               mistaken for productions); $PRODUCTIONS overrides the folder.
Group and slug are the two path parts above production.yaml, never fields in it.
"""
import glob
import os

SCRIPTS = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
HERE = os.path.dirname(SCRIPTS)          # the pipeline checkout these tools run from
NAMES = ("pipeline", "see-poetry")       # the old name: still accepted for a checkout not yet renamed


def main_checkout(path):
    """A path inside a Claude Code worktree of <repo> (the folders .claude, worktrees, <name>, then
    <rest>) → <repo>/<rest>; any other path unchanged."""
    parts = os.path.abspath(path).split(os.sep)
    for i in range(len(parts) - 2):
        if parts[i] == ".claude" and parts[i + 1] == "worktrees":
            return os.sep.join(parts[:i] + parts[i + 3:]) or os.sep
    return os.path.abspath(path)


def pipeline_root():
    """$PIPELINE when bin/tool (or the caller) set it, else the checkout these scripts sit in."""
    return os.path.abspath(os.environ.get("PIPELINE") or HERE)


def find_pipeline(start):
    """bin/tool's ancestor rule: the nearest ancestor of `start` with a pipeline/ or see-poetry/
    child holding scripts/bin. $PIPELINE overrides. Returns (path or None, dirs walked)."""
    if os.environ.get("PIPELINE"):
        return os.path.abspath(os.environ["PIPELINE"]), []
    d, walked = os.path.abspath(start), []
    while True:
        walked.append(d)
        for n in NAMES:
            if os.path.isdir(os.path.join(d, n, "scripts", "bin")):
                return os.path.join(d, n), walked
        if d == os.path.dirname(d):
            return None, walked
        d = os.path.dirname(d)


def productions_root():
    if os.environ.get("PRODUCTIONS"):
        return os.path.abspath(os.environ["PRODUCTIONS"])
    return os.path.join(os.path.dirname(main_checkout(pipeline_root())), "productions")


def productions():
    """[(group, slug, folder)] for every production.yaml two levels under productions_root()."""
    root = productions_root()
    out = []
    for f in sorted(glob.glob(os.path.join(glob.escape(root), "*", "*", "production.yaml"))):
        d = os.path.dirname(f)
        out.append((os.path.basename(os.path.dirname(d)), os.path.basename(d), d))
    return out


def find_production(ref):
    """'<group>/<slug>' (or a folder holding production.yaml) → its folder, or None."""
    if os.path.isfile(os.path.join(ref, "production.yaml")):
        return os.path.abspath(ref)
    for group, slug, d in productions():
        if ref in (f"{group}/{slug}", slug):
            return d
    return None
