#!/usr/bin/env python3
"""Resumable BandIt Plus separation: pieces of CORE s with PAD s context each side.

Usage:  MSST=<checkout> python3 scripts/mix/sep_chunks.py IN.wav OUTDIR [--core 24] [--pad 3] [--overlap 4]
Runs against a checkout of ZFTurbo/Music-Source-Separation-Training (050cae7) with the BandIt Plus DnR weights in
its w/ — $MSST names the checkout; without $MSST the
script expects to have been copied into the checkout, the way START ran it in the cloud. Each piece's stems are
saved as it finishes, so a container restart loses one piece at most; run it again and it carries on. Writes
OUTDIR/speech.wav, music.wav, effects.wav (44.1 kHz float) and prints the residual against the input.
Ported from START's scripts/mix/sep_chunks.py (2026-09-25), unchanged but for $MSST.
"""
import argparse
import os
import sys
import time

if len(sys.argv) < 3 or '--help' in sys.argv or '-h' in sys.argv:
    print(__doc__.strip())
    sys.exit(0 if '--help' in sys.argv or '-h' in sys.argv else 2)
MSST = os.environ.get('MSST') or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, MSST)
import librosa  # noqa: E402
import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402
import torch  # noqa: E402
from utils.model_utils import demix, load_start_checkpoint  # noqa: E402
from utils.settings import get_model_from_config  # noqa: E402

a = [x for x in sys.argv[1:] if x not in ('--text', '--media', '--stage')]
inp, out = os.path.abspath(a[0]), os.path.abspath(a[1])
opt = lambda k, d: float(a[a.index(k) + 1]) if k in a else d  # noqa: E731
CORE, PAD, OVL = opt('--core', 24), opt('--pad', 3), int(opt('--overlap', 4))
os.makedirs(out, exist_ok=True)
os.chdir(MSST)
torch.set_num_threads(os.cpu_count())
model, config = get_model_from_config('bandit', 'w/config_dnr_bandit_bsrnn_multi_mus64.yaml')
ck = torch.load('w/model_bandit_plus_dnr_sdr_11.47.chpt', weights_only=False, map_location='cpu')
load_start_checkpoint(argparse.Namespace(model_type='bandit', lora_checkpoint_peft=None, lora_checkpoint_loralib=None,
                                         start_check_point='x'), model, ck, type_='inference')
model.eval()
config.inference.num_overlap = OVL
sr = 44100
mix, _ = librosa.load(inp, sr=sr, mono=False)
n = mix.shape[1]
core, pad, xf = int(CORE * sr), int(PAD * sr), int(0.05 * sr)
starts = list(range(0, n, core))
print(f'{n/sr:.2f} s, {len(starts)} pieces of {CORE} s (+{PAD} s context, overlap {OVL})', flush=True)
for i, s0 in enumerate(starts):
    f = f'{out}/piece{i:03d}.npz'
    if os.path.exists(f):
        continue
    t = time.time()
    a0, a1 = max(0, s0 - pad), min(n, s0 + core + xf + pad)
    with torch.inference_mode():
        res = demix(config, model, mix[:, a0:a1], 'cpu', 'bandit', False)
    c0, c1 = s0 - a0, min(n, s0 + core + xf) - a0
    np.savez(f + '.tmp.npz', **{k: v[:, c0:c1].astype(np.float32) for k, v in res.items()})
    os.replace(f + '.tmp.npz', f)
    print(f'piece {i+1}/{len(starts)} {time.time()-t:.0f} s', flush=True)
stems = {}
for i, s0 in enumerate(starts):
    d = np.load(f'{out}/piece{i:03d}.npz')
    for k in d.files:
        v = d[k]
        if k not in stems:
            stems[k] = np.zeros((2, n), np.float32)
        seg = v.copy()
        L = seg.shape[1]
        if i > 0:  # linear crossfade over the xf overlap with the previous piece's tail
            rr = np.linspace(0, 1, xf, dtype=np.float32)
            stems[k][:, s0:s0 + xf] = stems[k][:, s0:s0 + xf] * (1 - rr) + seg[:, :xf] * rr
            stems[k][:, s0 + xf:s0 + L] = seg[:, xf:]
        else:
            stems[k][:, s0:s0 + L] = seg
for k, v in stems.items():
    sf.write(f'{out}/{k}.wav', v.T, sr, subtype='FLOAT')
    print('wrote', k, flush=True)
resid = mix - sum(stems.values())
print(f'residual RMS {20*np.log10(np.sqrt((resid**2).mean())+1e-12):.1f} dBFS vs mix {20*np.log10(np.sqrt((mix**2).mean())):.1f}', flush=True)
print('DONE', flush=True)
