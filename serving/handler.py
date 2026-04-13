"""Custom TorchServe handler for the bird species classifier."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import torch
import torch.nn.functional as F
from PIL import Image
from torchvision import transforms
from ts.torch_handler.base_handler import BaseHandler

logger = logging.getLogger(__name__)

IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


class BirdClassifierHandler(BaseHandler):
    """Accepts JPEG bytes, returns top-5 species predictions with confidence."""

    def __init__(self):
        super().__init__()
        self.transform = None
        self.class_names: list[str] = []
        self.species_codes: list[str] = []

    def initialize(self, context) -> None:
        super().initialize(context)

        self.transform = transforms.Compose([
            transforms.Resize(256),
            transforms.CenterCrop(224),
            transforms.ToTensor(),
            transforms.Normalize(mean=IMAGENET_MEAN, std=IMAGENET_STD),
        ])

        model_dir = Path(context.system_properties["model_dir"])

        mapping_file = model_dir / "index_to_name.json"
        if mapping_file.exists():
            with open(mapping_file) as f:
                idx_map = json.load(f)
            self.class_names = [idx_map[str(i)] for i in range(len(idx_map))]
        else:
            self.class_names = [f"class_{i}" for i in range(200)]

        codes_file = model_dir / "species_codes.json"
        if codes_file.exists():
            with open(codes_file) as f:
                codes_map = json.load(f)
            self.species_codes = [codes_map.get(str(i), "") for i in range(len(self.class_names))]
        else:
            self.species_codes = [""] * len(self.class_names)

        logger.info("BirdClassifierHandler initialized with %d classes", len(self.class_names))

    def preprocess(self, data: list) -> torch.Tensor:
        images = []
        for row in data:
            raw = row.get("data") or row.get("body")
            if isinstance(raw, (bytes, bytearray)):
                img = Image.open(io.BytesIO(raw)).convert("RGB")
            else:
                img = Image.open(io.BytesIO(raw.read())).convert("RGB")
            images.append(self.transform(img))
        return torch.stack(images).to(self.device)

    def inference(self, data: torch.Tensor, *args, **kwargs) -> torch.Tensor:
        with torch.no_grad():
            return self.model(data)

    def postprocess(self, inference_output: torch.Tensor) -> list[dict]:
        probs = F.softmax(inference_output, dim=1)
        top5_probs, top5_indices = probs.topk(5, dim=1)

        results = []
        for i in range(probs.size(0)):
            predictions = []
            for j in range(5):
                idx = top5_indices[i, j].item()
                predictions.append({
                    "species": self.class_names[idx] if idx < len(self.class_names) else f"class_{idx}",
                    "species_code": self.species_codes[idx] if idx < len(self.species_codes) else "",
                    "class_id": idx,
                    "confidence": round(top5_probs[i, j].item(), 6),
                })
            results.append({"predictions": predictions})
        return results
