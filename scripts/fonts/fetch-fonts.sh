#!/bin/sh
# Fill scripts/fonts/ with the faces the cut, label and card tools name by role
# (scripts/lib/fonts.py). The Linux container these tools were written in has them as system
# fonts (/usr/share/fonts/...); a Mac has none of them, and a missing font is a named error,
# never a silent substitute.
#   sh scripts/fonts/fetch-fonts.sh          idempotent; FONTS_DIR=<dir> puts them elsewhere
# Fonts are binaries: *.ttf and *.otf are gitignored by type, so nothing here reaches git.
# Every file is checked against the SHA-256 pinned below (read 2026-09-23 from these URLs).
set -eu
dir=${FONTS_DIR:-$(cd "$(dirname "$0")" && pwd)}
mkdir -p "$dir"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT

DEJAVU=https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2
URW=https://raw.githubusercontent.com/ArtifexSoftware/urw-base35-fonts/master/fonts

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
check() {  # <file> <expected sha256>
  got=$(sha "$1")
  [ "$got" = "$2" ] || { echo "fetch-fonts: $(basename "$1") has SHA-256 $got, expected $2 — not installed" >&2; rm -f "$1"; exit 1; }
}

need=
for f in DejaVuSans.ttf DejaVuSans-Bold.ttf DejaVuSansMono-Bold.ttf; do [ -f "$dir/$f" ] || need=1; done
if [ -n "$need" ]; then
  curl -fsSL "$DEJAVU" -o "$tmp/dejavu.tar.bz2"
  tar -xjf "$tmp/dejavu.tar.bz2" -C "$tmp"
  for f in DejaVuSans.ttf DejaVuSans-Bold.ttf DejaVuSansMono-Bold.ttf; do
    cp "$tmp/dejavu-fonts-ttf-2.37/ttf/$f" "$dir/$f.part" && mv "$dir/$f.part" "$dir/$f"
  done
fi
for f in C059-Roman.otf C059-Bold.otf C059-Italic.otf; do
  [ -f "$dir/$f" ] || { curl -fsSL "$URW/$f" -o "$dir/$f.part" && mv "$dir/$f.part" "$dir/$f"; }
done

check "$dir/DejaVuSans.ttf"          7da195a74c55bef988d0d48f9508bd5d849425c1770dba5d7bfc6ce9ed848954
check "$dir/DejaVuSans-Bold.ttf"     e6476c1b80502924294eed40894c5b18e06c181444ca953e5334262df9c27724
check "$dir/DejaVuSansMono-Bold.ttf" bce60f1b4421acd9ea51ba6623d7024ecbe6817a953e3654df62a5e6bdf8f769
check "$dir/C059-Roman.otf"          e00cc7b88f0cf25ae43f0e48de39d0043fe042b55719a3ac32669ab3369542ec
check "$dir/C059-Bold.otf"           75dea10068264324522e7b96612083582eee0158eb728fa9c219279890f33e26
check "$dir/C059-Italic.otf"         77852deabd3a84f7d0213932239de522410309374c2255ccbd510f01dc7c6e80
echo "fonts in $dir: DejaVu Sans (regular, bold), DejaVu Sans Mono bold, C059 (roman, bold, italic) — SHA-256 match"
