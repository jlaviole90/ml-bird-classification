"""EfficientNet-B4 fine-tuned for bird species classification."""

from __future__ import annotations

import torch
import torch.nn as nn
from torchvision.models import EfficientNet_B4_Weights, efficientnet_b4


class BirdClassifier(nn.Module):
    """EfficientNet-B4 with a custom classification head for CUB-200."""

    def __init__(self, num_classes: int = 200, pretrained: bool = True, dropout: float = 0.3):
        super().__init__()
        weights = EfficientNet_B4_Weights.IMAGENET1K_V1 if pretrained else None
        self.backbone = efficientnet_b4(weights=weights)

        in_features = self.backbone.classifier[1].in_features
        self.backbone.classifier = nn.Sequential(
            nn.Dropout(p=dropout),
            nn.Linear(in_features, num_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.backbone(x)

    def freeze_backbone(self) -> None:
        for param in self.backbone.features.parameters():
            param.requires_grad = False

    def unfreeze_backbone(self) -> None:
        for param in self.backbone.features.parameters():
            param.requires_grad = True
