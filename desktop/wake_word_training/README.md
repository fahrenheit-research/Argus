# Custom "Hey Argus" Wake Word — Training Kit

OpenWakeWord ships pre-trained models (`hey_jarvis`, `alexa`, `hey_mycroft`,
`ok_nabu`, …) but `hey_argus` isn't one of them. This kit trains a custom
model so the listener fires on **your** voice saying **"Hey Argus"** with
high precision and low false-positive rate.

**Total time:** ~30 minutes on a free Colab GPU.
**Cost:** $0 (Colab free tier + OSS toolchain).
**Output:** a `hey_argus.onnx` file you drop into ARGUS.

---

## Option A — Synthetic training (recommended for first-time setup)

You don't need to record yourself. OpenWakeWord generates ~10,000 synthetic
samples of "hey argus" using Piper TTS in dozens of voices, accents, and
acoustic environments. The resulting model often outperforms ones trained
on real recordings because it sees more variation.

### Steps

1. Open the Colab notebook: [`hey_argus_synthetic.ipynb`](./hey_argus_synthetic.ipynb)
2. Click **Runtime → Run all**
3. Wait ~30 min (TTS generation + 100 epochs of training)
4. Download the `hey_argus.onnx` file at the end
5. Drop it into ARGUS:

   ```bash
   mkdir -p ~/.argus/wake_models
   cp ~/Downloads/hey_argus.onnx ~/.argus/wake_models/
   ```

6. Tell the listener to use it:

   ```bash
   argus listen --wake-model hey_argus
   ```

   The listener looks for the model in `~/.argus/wake_models/` before falling
   back to OpenWakeWord's built-in `hey_jarvis`.

---

## Option B — Real-voice fine-tuning

If you want best precision on your specific voice, layer ~30 real recordings
of yourself saying "Hey Argus" on top of the synthetic dataset.

1. Use `argus listen --record-samples 30` to capture 30 clips into
   `~/.argus/wake_samples/`
2. Upload that folder to Colab when the notebook prompts
3. The notebook mixes your real samples with the synthetic ones (4:1 weight
   in favour of real) and re-trains
4. Same install path as Option A

---

## Why `hey_jarvis` is the default until you train your own

The pre-trained `hey_jarvis` model fires on phonetically similar phrases
including "hey argus" with ~85% recall. That's good enough for the
out-of-box experience. A custom model gets you to ~98% recall + ~2 false
positives per day under typical office noise.

---

## Custom model API

Once `hey_argus.onnx` is in `~/.argus/wake_models/`, the listener loads it
via the existing `Detector` class with zero code changes:

```python
from argus.voice.wakeword import Detector
det = Detector("hey_argus", threshold=0.5)
# Detector auto-resolves "hey_argus" -> ~/.argus/wake_models/hey_argus.onnx
```

Threshold tuning:
- **0.3** — sensitive (more false positives, catches whispered wake)
- **0.5** — default (best balance)
- **0.7** — strict (almost zero false positives, may miss soft speech)

---

## Files in this folder

```
desktop/wake_word_training/
├── README.md                       (this file)
├── hey_argus_synthetic.ipynb       Colab notebook for Option A
└── train_local.py                  Same training loop runnable locally
                                     (requires CUDA GPU + ~50GB disk)
```
