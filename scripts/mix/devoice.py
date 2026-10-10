#!/usr/bin/env python3
"""De-voice a source clip: BandIt Plus separation of the clip's own sound, effects stem kept.

Usage:  MSST=<checkout> python3 scripts/mix/devoice.py CLIP.mp4 [CLIP.mp4 ...] --out DIR
Runs against a checkout of ZFTurbo/Music-Source-Separation-Training (050cae7) with the BandIt Plus DnR weights in
its w/ — $MSST names the checkout; without $MSST the
script expects to have been copied into the checkout, the way START ran it in the cloud. Cloud or a big Mac only:
torch on CPU, minutes per clip. The same setup as sep_chunks.py.
Writes DIR/<clip stem>-effects.wav (and -speech.wav, -music.wav, which say why the flag was there).

Why this exists (2026-09-20): edl_cut's `voice-out` flag is a review stopgap — a 150 Hz low-pass at -8 dB. It hides
a doubled line, but it also deletes every effect in the segment. Where a segment really does carry a line we do not
want, the edit list points its sound at the effects stem written here (`from:r3/audio/fx/<clip>-effects.wav@<in>`)
instead of muting the whole band.
Extra packages beyond sep_chunks.py's, all PyPI: omegaconf, ml_collections, einops, tqdm, torchaudio,
asteroid-filterbanks, pytorch_lightning, torchmetrics, spafe, pedalboard, torch_audiomentations.
models/bandit/core/__init__.py imports `asteroid` for training losses only; a stub package on PYTHONPATH
(asteroid/__init__.py: `from . import losses`) is enough for inference.
Ported from START's scripts/mix/devoice.py (2026-09-25), unchanged but for $MSST.
"""
import argparse
import os
import subprocess
import sys
import time

if '--help' in sys.argv or '-h' in sys.argv:
    print(__doc__.strip())
    sys.exit(0)
MSST = os.environ.get('MSST') or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MSST)
os.chdir(MSST)
import librosa  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from utils.model_utils import demix, load_start_checkpoint  # noqa: E402
from utils.settings import get_model_from_config  # noqa: E402

a = [x for x in sys.argv[1:] if x not in ('--text', '--media', '--stage')]
out = a[a.index('--out') + 1] if '--out' in a else '.'
clips = [os.path.abspath(x) for x in a if not x.startswith('--') and x != out]
out = os.path.abspath(out)
os.makedirs(out, exist_ok=True)
torch.set_num_threads(os.cpu_count())
model, config = get_model_from_config('bandit', 'w/config_dnr_bandit_bsrnn_multi_mus64.yaml')
ck = torch.load('w/model_bandit_plus_dnr_sdr_11.47.chpt', weights_only=False, map_location='cpu')
load_start_checkpoint(argparse.Namespace(model_type='bandit', lora_checkpoint_peft=None,
                                         lora_checkpoint_loralib=None, start_check_point='x'), model, ck, type_='inference')
model.eval()
config.inference.num_overlap = 2
SR = 44100
for clip in clips:
    stem = os.path.splitext(os.path.basename(clip))[0]
    wav = f'{out}/{stem}-in.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', clip, '-ac', '2', '-ar', str(SR), wav], check=True)
    mix, _ = librosa.load(wav, sr=SR, mono=False)
    t = time.time()
    with torch.inference_mode():
        res = demix(config, model, mix, 'cpu', 'bandit', False)
    for k, v in res.items():
        sf.write(f'{out}/{stem}-{k}.wav', v.T, SR, subtype='FLOAT')
        rms = 20 * np.log10(np.sqrt((v ** 2).mean()) + 1e-12)
        print(f'{stem} {k:8s} {rms:6.1f} dBFS RMS', flush=True)
    print(f'{stem}: {time.time() - t:.0f} s', flush=True)
