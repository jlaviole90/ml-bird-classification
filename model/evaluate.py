"""Evaluate a trained bird classifier on the CUB-200 test set."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import torch
from torch.utils.data import DataLoader
from tqdm import tqdm

from model.src.dataset import CUB200Dataset
from model.src.model import BirdClassifier
from model.src.transforms import get_val_transforms
from model.src.utils import load_config


def evaluate(config_path: str) -> None:
    cfg = load_config(config_path)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    data_cfg = cfg["data"]

    val_ds = CUB200Dataset(
        root=data_cfg["root_dir"],
        train=False,
        transform=get_val_transforms(data_cfg["image_size"]),
        use_bbox=data_cfg["use_bounding_box"],
    )
    loader = DataLoader(val_ds, batch_size=64, shuffle=False, num_workers=4, pin_memory=True)

    model = BirdClassifier(num_classes=data_cfg["num_classes"], pretrained=False).to(device)
    ckpt_path = Path(cfg["checkpoint"]["dir"]) / "best.pth"
    ckpt = torch.load(ckpt_path, map_location=device, weights_only=False)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    print(f"Loaded checkpoint from epoch {ckpt['epoch']} (val_acc={ckpt['val_acc']:.4f})")

    correct_top1 = 0
    correct_top5 = 0
    total = 0
    per_class_correct: dict[int, int] = defaultdict(int)
    per_class_total: dict[int, int] = defaultdict(int)

    with torch.no_grad():
        for images, labels in tqdm(loader, desc="Evaluating"):
            images, labels = images.to(device), labels.to(device)
            logits = model(images)

            _, top5_preds = logits.topk(5, dim=1)
            preds = top5_preds[:, 0]

            correct_top1 += (preds == labels).sum().item()
            correct_top5 += sum(labels[i] in top5_preds[i] for i in range(labels.size(0)))
            total += labels.size(0)

            for pred, label in zip(preds.cpu().tolist(), labels.cpu().tolist()):
                per_class_total[label] += 1
                if pred == label:
                    per_class_correct[label] += 1

    top1_acc = correct_top1 / total
    top5_acc = correct_top5 / total
    print(f"\nTop-1 accuracy: {top1_acc:.4f}  ({correct_top1}/{total})")
    print(f"Top-5 accuracy: {top5_acc:.4f}  ({correct_top5}/{total})")

    per_class_acc = {
        cls: per_class_correct[cls] / per_class_total[cls]
        for cls in sorted(per_class_total)
    }

    worst_5 = sorted(per_class_acc.items(), key=lambda x: x[1])[:5]
    best_5 = sorted(per_class_acc.items(), key=lambda x: x[1], reverse=True)[:5]

    print("\nBest 5 classes:")
    for cls, acc in best_5:
        print(f"  {val_ds.class_names[cls]:>40s}  {acc:.4f}")

    print("\nWorst 5 classes:")
    for cls, acc in worst_5:
        print(f"  {val_ds.class_names[cls]:>40s}  {acc:.4f}")

    report = {
        "top1_accuracy": top1_acc,
        "top5_accuracy": top5_acc,
        "per_class_accuracy": {val_ds.class_names[k]: v for k, v in per_class_acc.items()},
    }
    out_path = Path(cfg["checkpoint"]["dir"]) / "eval_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nFull report written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="model/config/training_config.yaml")
    evaluate(parser.parse_args().config)
