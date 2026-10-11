#!/bin/sh
# Fetch the face detector scripts/blur/blur.py runs: OpenCV Zoo's YuNet (2023mar, MIT licence,
# 232,589 bytes) into scripts/blur/models/. A model is a binary: *.onnx is gitignored, so it never
# reaches git, and every checkout fetches its own, as scripts/fonts/fetch-fonts.sh does for fonts.
# The URL is pinned to the commit that last
# changed the file (f12e127, "update yunet to v2", 2023-06-06), and the SHA-256 below was read from
# that download on 2026-10-09; a mismatch removes the file and fails.
#   sh scripts/blur/fetch-model.sh       idempotent; MODEL_DIR=<dir> puts it elsewhere
set -eu
dir=${MODEL_DIR:-$(cd "$(dirname "$0")" && pwd)/models}
f=face_detection_yunet_2023mar.onnx
url=https://media.githubusercontent.com/media/opencv/opencv_zoo/f12e12798e8314f7c074a6656816c048dcc95b7a/models/face_detection_yunet/$f
want=8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
mkdir -p "$dir"
[ -f "$dir/$f" ] || { curl -fsSL "$url" -o "$dir/$f.part" && mv "$dir/$f.part" "$dir/$f"; }
got=$(shasum -a 256 "$dir/$f" | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "fetch-model: $f has SHA-256 $got, expected $want — removed" >&2; rm -f "$dir/$f"; exit 1; }
echo "face model in $dir: YuNet 2023mar — SHA-256 match"
