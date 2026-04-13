"""Unit tests for the bird classifier model architecture."""

from __future__ import annotations

import torch
import pytest

from model.src.model import BirdClassifier


class TestBirdClassifier:
    def test_forward_shape(self):
        model = BirdClassifier(num_classes=200, pretrained=False)
        x = torch.randn(2, 3, 224, 224)
        out = model(x)
        assert out.shape == (2, 200)

    def test_freeze_backbone(self):
        model = BirdClassifier(num_classes=200, pretrained=False)
        model.freeze_backbone()
        frozen = all(not p.requires_grad for p in model.backbone.features.parameters())
        assert frozen

    def test_unfreeze_backbone(self):
        model = BirdClassifier(num_classes=200, pretrained=False)
        model.freeze_backbone()
        model.unfreeze_backbone()
        unfrozen = all(p.requires_grad for p in model.backbone.features.parameters())
        assert unfrozen

    def test_custom_num_classes(self):
        model = BirdClassifier(num_classes=50, pretrained=False)
        x = torch.randn(1, 3, 224, 224)
        out = model(x)
        assert out.shape == (1, 50)
