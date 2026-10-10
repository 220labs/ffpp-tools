"""The lookups every pipeline tool shares.

A tool puts scripts/ on sys.path from its own real path, then `from lib import cli, …`.
Stdlib only, Python 3.9 (the Macs' /usr/bin/python3), plus PyYAML where a YAML file is read.
"""
