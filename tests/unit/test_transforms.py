"""Unit tests for image transforms."""

from __future__ import annotations

import torch
from PIL import Image

from model.src.transforms import get_train_transforms, get_val_transforms, get_inference_transforms


def _make_dummy_image(w: int = 300, h: int = 300) -> Image.Image:
    return Image.new("RGB", (w, h), color=(128, 64, 32))


class TestTransforms:
    def test_train_output_shape(self):
        t = get_train_transforms(224)
        tensor = t(_make_dummy_image())
        assert tensor.shape == (3, 224, 224)

    def test_val_output_shape(self):
        t = get_val_transforms(224)
        tensor = t(_make_dummy_image())
        assert tensor.shape == (3, 224, 224)

    def test_inference_same_as_val(self):
        img = _make_dummy_image()
        val = get_val_transforms(224)(img)
        inf = get_inference_transforms(224)(img)
        assert torch.equal(val, inf)
