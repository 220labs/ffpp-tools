"""land: hand the masters a tool just wrote to the production's store, when there is one.

Every tool that writes a master (a cut, a mix, a QR tile) ends with landing.land(r, files,
tool=...). In the FFPP pipeline the store is lib/store.py: the files move into place, go to the
shared drive and the NAS, and get a row in MEDIA-MANIFEST.tsv. lib/store.py is the private
storage layout and is not part of griptruck (the public tools, 220labs/griptruck); without it
the files stay where the tool wrote them, land says so and succeeds.
A store that is present but fails to import is an error, never a quiet skip.
"""
import importlib.util
import os
import sys

store = None
if importlib.util.find_spec("lib.store") is not None:
    from lib import store


def land(r, args, *, tool="land", **kw):
    # An empty list is a tool that wrote nothing (a config of comments only). The store reads an
    # empty list as "land every r<N>/_incoming", which only bin/tool land itself means.
    if not args:
        print(f"{tool}: nothing to land")
        return 0
    if store is not None:
        return store.land(r, args, tool=tool, **kw)
    rc = 0
    for f in args:
        if os.path.exists(r.m(f)):
            print(f"{tool}: kept in place (no store configured): {r.m(f)}")
        else:
            print(f"FAIL  {r.m(f)}: no such file", file=sys.stderr)
            rc = 1
    return rc
