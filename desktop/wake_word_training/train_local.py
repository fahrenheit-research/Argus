#!/usr/bin/env python3
"""Local trainer for a custom "Hey Argus" wake-word model.

Identical training loop to the Colab notebook (`hey_argus_synthetic.ipynb`)
but runs on your own GPU. Requires CUDA + ~50GB disk for synthetic samples.

Usage:
    pip install openwakeword piper-tts torch
    python desktop/wake_word_training/train_local.py \
        --wake-phrase "hey argus" \
        --output ~/.argus/wake_models/hey_argus.onnx \
        --n-samples 10000 \
        --epochs 100

If you don't have a CUDA GPU, use the Colab notebook instead — it's
free and finishes in ~30 min.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a custom OpenWakeWord model.")
    parser.add_argument("--wake-phrase", default="hey argus",
                         help="The phrase the model should fire on")
    parser.add_argument("--output", required=True,
                         help="Where to save the trained .onnx model")
    parser.add_argument("--n-samples", type=int, default=10_000,
                         help="Number of synthetic samples to generate (default: 10k)")
    parser.add_argument("--epochs", type=int, default=100,
                         help="Training epochs (default: 100)")
    parser.add_argument("--real-samples-dir", default="",
                         help="Optional: folder of real recordings to mix in (4:1 weight)")
    args = parser.parse_args()

    # ── Dependency check ────────────────────────────────────────────
    try:
        import torch  # noqa: F401
        from openwakeword.train import train_custom_model  # type: ignore  # noqa: F401
    except ImportError as e:
        print(f"✗  Missing dependency: {e}\n"
              f"   Install: pip install openwakeword piper-tts torch")
        return 1

    if not torch.cuda.is_available():
        print("!  No CUDA GPU detected. This will take 4+ hours on CPU.")
        print("   Use the Colab notebook instead: ./hey_argus_synthetic.ipynb")
        if input("Continue on CPU? (y/N): ").lower() != "y":
            return 0

    # ── Training pipeline (delegates to openwakeword's official trainer) ──
    print(f"\n⟨◇⟩  Training custom wake word: '{args.wake_phrase}'")
    print(f"     samples : {args.n_samples}")
    print(f"     epochs  : {args.epochs}")
    print(f"     output  : {args.output}\n")

    output_path = Path(args.output).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # The actual trainer is OpenWakeWord's own. We just orchestrate.
    try:
        from openwakeword.train import train_custom_model  # type: ignore
        train_custom_model(
            target_phrase=args.wake_phrase,
            output_path=str(output_path),
            num_samples=args.n_samples,
            num_epochs=args.epochs,
            real_samples_dir=(args.real_samples_dir or None),
        )
    except Exception as e:  # noqa: BLE001
        print(f"\n✗  Training failed: {type(e).__name__}: {e}")
        print("\n   The OpenWakeWord training API moves around between versions.")
        print("   Falling back to the Colab notebook is the most reliable path:")
        print("   desktop/wake_word_training/hey_argus_synthetic.ipynb")
        return 1

    print(f"\n✓  Model trained → {output_path}")
    print(f"   Test it:  argus listen --wake-model hey_argus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
