"""Real-drum benchmark on MDB Drums: ADTOF vs. the basic NMF+rules detector.

Setup (3 GB):  git clone --depth 1 https://github.com/CarlSouthall/MDBDrums.git ~/MDBDrums
Run:           python tests/benchmark_mdb.py drum_only    (or full_mix)
"""
import sys, time
from pathlib import Path
sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parent)]
import numpy as np, librosa, torch
import synth_drums as SD
import adtof_pytorch as A
from musicpractice.plugins.drums import transcribe_drums
ROOT = Path.home() / "MDBDrums" / "MDB Drums"
FAM = {35: "KD", 38: "SD", 47: "TT", 42: "HH", 49: "CY"}
MINE = {"kick": "KD", "snare": "SD", "hihat": "HH", "crash": "CY", "ride": "CY",
        "tom_high": "TT", "tom_mid": "TT", "tom_floor": "TT"}
model = A.create_frame_rnn_model(A.calculate_n_bins()); model.eval()
model = A.load_pytorch_weights(model, A.get_default_weights_path(), strict=False)
torch.set_num_threads(4)

def adtof(path):
    x = A.load_audio_for_model(str(path))
    with torch.no_grad(): pred = model(x).numpy()
    peaks = A.PeakPicker(thresholds=A.FRAME_RNN_THRESHOLDS, fps=100).pick(pred, labels=A.LABELS_5)[0]
    return [(float(t), FAM[k]) for k, ts in peaks.items() for t in ts]

def mine(path, beat_s, mix):
    y, _ = librosa.load(str(path), sr=44100, mono=True)
    if mix:
        _, y = librosa.effects.hpss(y, margin=2.0)
    return [(h["time"], MINE[h["piece"]]) for h in transcribe_drums(y, beat_s=beat_s)]

which = sys.argv[1]  # drum_only | full_mix
res = {"adtof": {}, "mine": {}}
tracks = sorted((ROOT / "audio" / which).glob("*.wav"))
for k, wav in enumerate(tracks):
    name = wav.stem.rsplit("_", 1)[0]
    truth = []
    for line in open(ROOT / "annotations/class" / f"{name}_class.txt"):
        t, lab = line.split()[:2]
        if lab in ("KD", "SD", "HH", "TT", "CY"):
            truth.append((float(t), lab))
    beats = [float(l.split()[0]) for l in open(ROOT / "annotations/beats" / f"{name}_MIX.beats")]
    beat_s = float(np.median(np.diff(beats))) if len(beats) > 2 else 0.5
    t0 = time.time()
    for det, found in (("adtof", adtof(wav)), ("mine", mine(wav, beat_s, which == "full_mix"))):
        sc = SD.score(found, truth, tol=0.05)
        for lab, v in sc.items():
            agg = res[det].setdefault(lab, [0, 0, 0])   # tp, found, truth
            tp = round(v["recall"] * v["truth"])
            agg[0] += tp; agg[1] += v["found"]; agg[2] += v["truth"]
    print(f"{k + 1:2d}/{len(tracks)} {name} ({time.time() - t0:.1f}s)", flush=True)
print(f"\n== {which}: F1 summed over all 23 tracks (tolerance 50 ms) ==")
print(f"{'':6s}{'ADTOF':>8s}{'mine':>8s}   truth")
for lab in ("KD", "SD", "HH", "TT", "CY"):
    row = []
    for det in ("adtof", "mine"):
        tp, f, t = res[det].get(lab, [0, 0, 0])
        p = tp / f if f else 0; r = tp / t if t else 0
        row.append(2 * p * r / (p + r) if p + r else 0)
    print(f"{lab:6s}{row[0]:8.2f}{row[1]:8.2f}   {res['adtof'].get(lab, [0,0,0])[2]}")

