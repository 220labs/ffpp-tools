# ffpp-tools

The media tools behind FFPP Pictures' generative-AI films ([ffpp.pictures](https://ffpp.pictures)):
cutting a film from an edit list, mixing it, timing a score, subtitles, labels, posters and a
press kit. Each is a small Python script over ffmpeg, numpy and friends. They are glue, published
as they are used, under the MIT licence.

What is not here: the film-making method, the prompts, the generation driver, the upscaling
workers and where the masters are stored. Those stay in the studio's private pipeline; these
tools run without them.

## Install

- `ffmpeg` and `ffprobe` on the `PATH` (every tool).
- Python 3.9 or newer: `pip3 install --user -r scripts/requirements.txt`. The heavy ones are
  only needed by some tools: `librosa` (`gridcheck`, `lagscan`), `playwright` and its Chromium
  (`presskit`, then `python3 -m playwright install chromium`), `mido` (`notelist`), `qrcode` with
  Pillow (`qr`).
- `mix/devoice.py` and `mix/sep_chunks.py` run the BandIt Plus source-separation model on CPU
  with torch (and librosa, soundfile); their docstrings name the model checkout they expect.
- Fonts: `sh scripts/fonts/fetch-fonts.sh` downloads the faces the cut, label and card tools name
  by role, each checked against a pinned SHA-256. They are not committed.

## Running a tool

Every tool is `scripts/bin/<name>` and takes `--help`. A tool works on two folders:

- `--text DIR`: the production's text, a git checkout: `production.yaml`, `edl/`, `audio/`,
  `subtitles/`, `release/`. Relative text paths on the command line resolve here.
- `--media DIR`: the production's media, often the same folder: `assets/` and one `r<N>/`
  folder per round of renders (`r3/clips/`, `r3/cut/`, `r3/audio/`). Relative media paths
  resolve here.

Without the flags, `--text` is `$TEXT` or the current folder and `--media` is `$MEDIA` or the
text folder. So, from a production folder:

```sh
/path/to/ffpp-tools/scripts/bin/edl_cut --help
/path/to/ffpp-tools/scripts/bin/mix --edl edl/film-v1.edl.tsv --stems r1/audio/stems --cues r1/audio/cues \
    --spot audio/spotting-film-v1.tsv --prod r1/audio/film-v1-production.wav --out r1/audio/mix/film-mix-v1
```

`production.yaml` holds what the tools would otherwise hard-code; a tool that needs a value it
does not find names the field or the flag that supplies it. The fields they read:

```yaml
label_prefix: myfilm        # burned into take labels and review copies; default: the folder name
wip: /path/to/review/folder # where review copies of clips and cuts go
```

The release tools read their own configs, `release/promo.yaml`, `release/poster.yaml` and
`release/presskit.yaml`, each described in the tool's `--help`.

The two longer configs, `edl/derive.yaml` and `audio/mix.yaml`, are described field by field
in `derive --help` and `mix --help`.

## The tools

| Folder | Tools | What they do |
| --- | --- | --- |
| `cut/` | `edl_cut`, `derive`, `masters`, `promo`, `qr`, `strays`, `wip` | build a cut from an edit list, with a review copy that burns in each segment's clip name; rebuild derived clips from other sources (upscales); the four deliverables of a master; trailers, teasers and vertical cuts by film timecode; the end card's QR code; stray frames in an edit list; put a cut in the review folder and check both copies |
| `mix/` | `mix`, `spotting`, `beds`, `toys`, `toys_takes`, `film_clicks`, `ab_reel`, `devoice.py`, `sep_chunks.py` | conform separated stems to a cut, duck the score under dialogue and set the loudness; the spotting sheet (blocks, cards, hit points, spoken lines by timecode); room-tone beds; recorded prop sounds split into takes and placed on hits; single sounds cut from the film's own effects; a listening reel per version of the sound; source separation of a clip's own sound |
| `music/` | `assemble`, `gridcheck`, `lagscan`, `notelist`, `render_in_browser.js` | join generated music chunks into one cue on a sketch's timeline; does a take keep the sketch's grid; its lag in sliding windows; MIDI to the note list an in-browser renderer plays |
| `subs/` | `make_srt`, `check-srt` | one SRT per language from the spotting sheet's spoken lines; a validator for the set |
| `label/` | `label` | burn a take ID into review copies; clean masters are never touched |
| `release/` | `poster`, `presskit` | posters in every size; a festival press kit as a PDF |

## Landing

A tool that writes a master ends by handing it to `scripts/lib/landing.py`. In the studio's own
pipeline that step copies the file to shared storage and records it; here there is no store, so
the file stays where the tool wrote it and the tool says so.

## Licence

MIT, see [LICENSE](LICENSE). Copyright (c) 2026 220 Labs.
