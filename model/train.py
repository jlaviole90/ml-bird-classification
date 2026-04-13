"""Training loop for the bird species classifier."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm

from model.src.dataset import CUB200Dataset
from model.src.model import BirdClassifier
from model.src.transforms import get_train_transforms, get_val_transforms
from model.src.utils import (
    AverageMeter,
    EarlyStopping,
    load_config,
    save_checkpoint,
)


def train_one_epoch(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    optimizer: torch.optim.Optimizer,
    scaler: torch.amp.GradScaler | None,
    device: torch.device,
    accum_steps: int,
    log_interval: int,
    writer: SummaryWriter,
    global_step: int,
) -> tuple[float, float, int]:
    model.train()
    loss_meter = AverageMeter()
    acc_meter = AverageMeter()
    optimizer.zero_grad()
    use_amp = scaler is not None

    for i, (images, labels) in enumerate(tqdm(loader, desc="  train", leave=False)):
        images, labels = images.to(device), labels.to(device)

        if use_amp:
            with torch.amp.autocast(device_type=device.type):
                logits = model(images)
                loss = criterion(logits, labels) / accum_steps
            scaler.scale(loss).backward()
            if (i + 1) % accum_steps == 0 or (i + 1) == len(loader):
                scaler.step(optimizer)
                scaler.update()
                optimizer.zero_grad()
        else:
            logits = model(images)
            loss = criterion(logits, labels) / accum_steps
            loss.backward()
            if (i + 1) % accum_steps == 0 or (i + 1) == len(loader):
                optimizer.step()
                optimizer.zero_grad()

        batch_loss = loss.item() * accum_steps
        preds = logits.argmax(dim=1)
        batch_acc = (preds == labels).float().mean().item()

        loss_meter.update(batch_loss, images.size(0))
        acc_meter.update(batch_acc, images.size(0))

        if (i + 1) % log_interval == 0:
            writer.add_scalar("train/loss_step", batch_loss, global_step)
            writer.add_scalar("train/acc_step", batch_acc, global_step)

        global_step += 1

    return loss_meter.avg, acc_meter.avg, global_step


@torch.no_grad()
def validate(
    model: nn.Module,
    loader: DataLoader,
    criterion: nn.Module,
    device: torch.device,
) -> tuple[float, float]:
    model.eval()
    loss_meter = AverageMeter()
    acc_meter = AverageMeter()

    for images, labels in tqdm(loader, desc="  val", leave=False):
        images, labels = images.to(device), labels.to(device)
        logits = model(images)
        loss = criterion(logits, labels)

        preds = logits.argmax(dim=1)
        loss_meter.update(loss.item(), images.size(0))
        acc_meter.update((preds == labels).float().mean().item(), images.size(0))

    return loss_meter.avg, acc_meter.avg


def main(config_path: str) -> None:
    cfg = load_config(config_path)
    if torch.cuda.is_available():
        device = torch.device("cuda")
    elif torch.backends.mps.is_available():
        device = torch.device("mps")
    else:
        device = torch.device("cpu")
    print(f"Device: {device}")

    data_cfg = cfg["data"]
    train_ds = CUB200Dataset(
        root=data_cfg["root_dir"],
        train=True,
        transform=get_train_transforms(data_cfg["image_size"]),
        use_bbox=data_cfg["use_bounding_box"],
    )
    val_ds = CUB200Dataset(
        root=data_cfg["root_dir"],
        train=False,
        transform=get_val_transforms(data_cfg["image_size"]),
        use_bbox=data_cfg["use_bounding_box"],
    )
    print(f"Train samples: {len(train_ds)}  |  Val samples: {len(val_ds)}")

    t_cfg = cfg["training"]
    train_loader = DataLoader(
        train_ds, batch_size=t_cfg["batch_size"], shuffle=True,
        num_workers=t_cfg["num_workers"], pin_memory=True,
    )
    val_loader = DataLoader(
        val_ds, batch_size=t_cfg["batch_size"] * 2, shuffle=False,
        num_workers=t_cfg["num_workers"], pin_memory=True,
    )

    m_cfg = cfg["model"]
    model = BirdClassifier(
        num_classes=data_cfg["num_classes"],
        pretrained=m_cfg["pretrained"],
    ).to(device)

    if m_cfg["freeze_backbone_epochs"] > 0:
        model.freeze_backbone()
        print(f"Backbone frozen for first {m_cfg['freeze_backbone_epochs']} epochs")

    criterion = nn.CrossEntropyLoss(label_smoothing=t_cfg["label_smoothing"])
    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=t_cfg["learning_rate"],
        weight_decay=t_cfg["weight_decay"],
    )
    s_cfg = cfg["scheduler"]
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=s_cfg["T_max"], eta_min=s_cfg["eta_min"],
    )

    scaler = torch.amp.GradScaler() if device.type == "cuda" else None
    early_stop = EarlyStopping(patience=t_cfg["early_stopping_patience"])
    writer = SummaryWriter(cfg["logging"]["tensorboard_dir"])

    ckpt_dir = Path(cfg["checkpoint"]["dir"])
    best_acc = 0.0
    global_step = 0

    for epoch in range(1, t_cfg["epochs"] + 1):
        if epoch == m_cfg["freeze_backbone_epochs"] + 1:
            model.unfreeze_backbone()
            optimizer = torch.optim.AdamW(
                model.parameters(),
                lr=t_cfg["learning_rate"] * 0.1,
                weight_decay=t_cfg["weight_decay"],
            )
            scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
                optimizer,
                T_max=t_cfg["epochs"] - epoch + 1,
                eta_min=s_cfg["eta_min"],
            )
            print("Backbone unfrozen — LR reduced to fine-tune rate")

        t0 = time.time()
        train_loss, train_acc, global_step = train_one_epoch(
            model, train_loader, criterion, optimizer, scaler,
            device, t_cfg["gradient_accumulation_steps"],
            cfg["logging"]["log_interval"], writer, global_step,
        )
        val_loss, val_acc = validate(model, val_loader, criterion, device)
        scheduler.step()

        elapsed = time.time() - t0
        lr = optimizer.param_groups[0]["lr"]
        print(
            f"Epoch {epoch:3d}/{t_cfg['epochs']}  "
            f"train_loss={train_loss:.4f}  train_acc={train_acc:.4f}  "
            f"val_loss={val_loss:.4f}  val_acc={val_acc:.4f}  "
            f"lr={lr:.2e}  [{elapsed:.1f}s]"
        )

        writer.add_scalar("epoch/train_loss", train_loss, epoch)
        writer.add_scalar("epoch/train_acc", train_acc, epoch)
        writer.add_scalar("epoch/val_loss", val_loss, epoch)
        writer.add_scalar("epoch/val_acc", val_acc, epoch)
        writer.add_scalar("epoch/lr", lr, epoch)

        if val_acc > best_acc:
            best_acc = val_acc
            save_checkpoint(model, optimizer, epoch, val_acc, ckpt_dir / "best.pth")
            print(f"  ↑ New best val_acc={val_acc:.4f}")

        if early_stop(val_loss):
            print(f"Early stopping at epoch {epoch}")
            break

    writer.close()
    print(f"Training complete — best val_acc={best_acc:.4f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="model/config/training_config.yaml")
    main(parser.parse_args().config)
