"""PyTorch Dataset for CUB-200-2011."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from torch.utils.data import Dataset
from torchvision.transforms import Compose

from model.data.preprocessing import CUBSample, load_cub_annotations


class CUB200Dataset(Dataset):
    """CUB-200-2011 dataset with optional bounding-box cropping.

    Class labels are remapped to 0-based indices (original CUB uses 1–200).
    """

    def __init__(
        self,
        root: str | Path,
        train: bool = True,
        transform: Compose | None = None,
        use_bbox: bool = True,
    ) -> None:
        self.root = Path(root)
        self.transform = transform
        self.use_bbox = use_bbox

        all_samples = load_cub_annotations(self.root)
        self.samples = [s for s in all_samples if s.is_train == train]

        unique_ids = sorted({s.class_id for s in all_samples})
        self._id_to_idx = {cid: idx for idx, cid in enumerate(unique_ids)}

        class_names_by_id = {s.class_id: s.class_name for s in all_samples}
        self.class_names = [class_names_by_id[cid] for cid in unique_ids]

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple:
        sample = self.samples[index]
        img = Image.open(self.root / "images" / sample.path).convert("RGB")

        if self.use_bbox:
            x, y, w, h = sample.bbox
            img = img.crop((x, y, x + w, y + h))

        label = self._id_to_idx[sample.class_id]

        if self.transform is not None:
            img = self.transform(img)

        return img, label

    @property
    def num_classes(self) -> int:
        return len(self.class_names)
