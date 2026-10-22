"""The release model: is the thrower's hand on the handle, or off it?

A ResNet-18 classifies a stack of three grey crops of the delivery lane from
the long camera facing the thrower (`releasetime.stacks` builds them) as "hand
on" (0) or "hand off" (1). `p_off` gives the probability of "off" per stack;
`game/releasetime.py` reads the release from where it flips. Loaded lazily,
like `spinmodel.default_model`, so importing this needs no torch, GPU or
weights.

The weights file is a float16 `state_dict` of :func:`build`'s model. On CUDA
the model runs in half precision, on the CPU in float32.
"""
from __future__ import annotations

import functools

import numpy as np

INPUT_SCALE = 2.0
NORM = (0.45, 0.225)
BATCH = 128


def build():
    """The untrained network: a ResNet-18 with a single "hand off" logit."""
    import torch
    from torchvision.models import resnet18

    model = resnet18(weights=None)
    model.fc = torch.nn.Linear(512, 1)
    return model


def prepare(x):
    """A float tensor ``(N, 3, H, W)`` in 0..1, as the model reads it: upsampled
    by INPUT_SCALE (bilinear) and normalised with NORM. Training applies this
    after augmentation, so training and serving share the one transform."""
    import torch

    x = torch.nn.functional.interpolate(
        x, scale_factor=INPUT_SCALE, mode="bilinear", align_corners=False)
    return (x - NORM[0]) / NORM[1]


@functools.lru_cache(maxsize=2)
def _load(path: str):
    import torch

    cuda = torch.cuda.is_available()
    model = build()
    # weights_only: a weights file must not be able to unpickle arbitrary code.
    state = torch.load(path, map_location="cpu", weights_only=True)
    model.load_state_dict({k: v.float() if v.is_floating_point() else v
                           for k, v in state.items()})
    model = model.half().cuda() if cuda else model.float()
    return model.eval()


def default_model():
    """The release model, or None when none is configured."""
    from curling_score import weights as weights_mod
    path = weights_mod.release_path()
    return None if path is None else _load(str(path))


def p_off(model, stacks: np.ndarray) -> np.ndarray:
    """The probability the hand is off the handle, one per stack: ``stacks`` is
    ``(N, 3, H, W)`` float32 in 0..1, as `releasetime.stacks` gives it."""
    import torch

    param = next(model.parameters())
    out = []
    with torch.no_grad():
        for i in range(0, len(stacks), BATCH):
            x = torch.from_numpy(np.ascontiguousarray(stacks[i:i + BATCH])).to(param.device)
            logits = model(prepare(x.float()).to(param.dtype))
            out.append(torch.sigmoid(logits.float()).reshape(-1).cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0, dtype="float32")
