"""Export trained model to ONNX, TorchScript, and TorchServe .mar archive."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import torch

from model.data.preprocessing import load_cub_annotations
from model.src.model import BirdClassifier
from model.src.utils import load_config

CUB_TO_EBIRD = {
    "001.Black_footed_Albatross": "bkfalb", "002.Laysan_Albatross": "layalb",
    "003.Sooty_Albatross": "sooalb1", "004.Groove_billed_Ani": "grbani",
    "005.Crested_Auklet": "creauk", "006.Least_Auklet": "leaauk",
    "007.Parakeet_Auklet": "parauk", "008.Rhinoceros_Auklet": "rhinau",
    "009.Brewer_Blackbird": "brebla", "010.Red_winged_Blackbird": "rewbla",
    "011.Rusty_Blackbird": "rusbla", "012.Yellow_headed_Blackbird": "yehbla",
    "013.Bobolink": "boboli", "014.Indigo_Bunting": "indbun",
    "015.Lazuli_Bunting": "lazbun", "016.Painted_Bunting": "paibun",
    "017.Cardinal": "norcar", "018.Spotted_Catbird": "spocat1",
    "019.Gray_Catbird": "grycat", "020.Yellow_breasted_Chat": "yebcha",
    "021.Eastern_Towhee": "eastow", "022.Chuck_will_Widow": "chwwid",
    "023.Brandt_Cormorant": "bracor", "024.Pelagic_Cormorant": "pelcor",
    "025.Double_crested_Cormorant": "doccor", "026.Bronzed_Cowbird": "brocow",
    "027.Shiny_Cowbird": "shicow", "028.Brown_Creeper": "brncre",
    "029.American_Crow": "amecro", "030.Fish_Crow": "fiscro",
    "031.Black_billed_Cuckoo": "bkbcuc", "032.Mangrove_Cuckoo": "mancuc",
    "033.Yellow_billed_Cuckoo": "yebcuc", "034.Gray_crowned_Rosy_Finch": "gcrfin",
    "035.Purple_Finch": "purfin", "036.Northern_Flicker": "norfli",
    "037.Acadian_Flycatcher": "acafly", "038.Great_Crested_Flycatcher": "grcfly",
    "039.Least_Flycatcher": "leafly", "040.Olive_sided_Flycatcher": "olsfly",
    "041.Scissor_tailed_Flycatcher": "sctfly", "042.Vermilion_Flycatcher": "verfly",
    "043.Yellow_bellied_Flycatcher": "yebfly", "044.Frigatebird": "magfri",
    "045.Northern_Fulmar": "norful", "046.Gadwall": "gadwal",
    "047.American_Goldfinch": "amegfi", "048.European_Goldfinch": "eurgol",
    "049.Boat_tailed_Grackle": "botgra", "050.Eared_Grebe": "eargre",
    "051.Horned_Grebe": "horgre", "052.Pied_billed_Grebe": "pibgre",
    "053.Western_Grebe": "wesgre", "054.Blue_Grosbeak": "blugro",
    "055.Evening_Grosbeak": "evegro", "056.Pine_Grosbeak": "pingro",
    "057.Rose_breasted_Grosbeak": "robgro", "058.Pigeon_Guillemot": "piggui",
    "059.California_Gull": "calgul", "060.Glaucous_winged_Gull": "glwgul",
    "061.Heermann_Gull": "heegul", "062.Herring_Gull": "hergul",
    "063.Ivory_Gull": "ivogul1", "064.Ring_billed_Gull": "ribgul",
    "065.Slaty_backed_Gull": "slbgul", "066.Western_Gull": "wesgul",
    "067.Anna_Hummingbird": "annhum", "068.Ruby_throated_Hummingbird": "rthhum",
    "069.Rufous_Hummingbird": "rufhum", "070.Green_Violetear": "mexvio",
    "071.Long_tailed_Jaeger": "lonjae", "072.Pomarine_Jaeger": "pomjae",
    "073.Blue_Jay": "blujay", "074.Florida_Jay": "flsjay",
    "075.Green_Jay": "grnjay", "076.Dark_eyed_Junco": "daejun",
    "077.Tropical_Kingbird": "trokin", "078.Gray_Kingbird": "grykin",
    "079.Belted_Kingfisher": "belkin1", "080.Green_Kingfisher": "grnkin",
    "081.Pied_Kingfisher": "piekin1", "082.Ringed_Kingfisher": "rinkin1",
    "083.White_breasted_Kingfisher": "wbkkin1", "084.Red_legged_Kittiwake": "relkit1",
    "085.Horned_Lark": "horlar", "086.Pacific_Loon": "pacloo",
    "087.Mallard": "mallar3", "088.Western_Meadowlark": "wesmea",
    "089.Hooded_Merganser": "hoomer", "090.Red_breasted_Merganser": "rebmer",
    "091.Mockingbird": "normod", "092.Nighthawk": "comnig",
    "093.Clark_Nutcracker": "clanut", "094.White_breasted_Nuthatch": "whbnut",
    "095.Baltimore_Oriole": "balori", "096.Hooded_Oriole": "hooori",
    "097.Orchard_Oriole": "orcori", "098.Scott_Oriole": "scoori",
    "099.Ovenbird": "ovenbi1", "100.Brown_Pelican": "brnpel",
    "101.White_Pelican": "amwpel", "102.Western_Wood_Pewee": "wewpew",
    "103.Sayornis": "easpho", "104.American_Pipit": "amepip",
    "105.Whip_poor_Will": "easwpw", "106.Horned_Puffin": "horpuf",
    "107.Common_Raven": "comrav", "108.White_necked_Raven": "whnrav1",
    "109.American_Redstart": "amered", "110.Geococcyx": "greroa",
    "111.Loggerhead_Shrike": "logshr", "112.Great_Grey_Shrike": "norshr",
    "113.Baird_Sparrow": "baispa", "114.Black_throated_Sparrow": "bktspa",
    "115.Brewer_Sparrow": "brespa", "116.Chipping_Sparrow": "chispa",
    "117.Clay_colored_Sparrow": "clcspa", "118.House_Sparrow": "houspa",
    "119.Field_Sparrow": "fiespa", "120.Fox_Sparrow": "foxspa",
    "121.Grasshopper_Sparrow": "graspa", "122.Harris_Sparrow": "harspa",
    "123.Henslow_Sparrow": "henspa", "124.Le_Conte_Sparrow": "lecspa",
    "125.Lincoln_Sparrow": "linspa", "126.Nelson_Sharp_tailed_Sparrow": "nelspa",
    "127.Savannah_Sparrow": "savspa", "128.Seaside_Sparrow": "seaspa",
    "129.Song_Sparrow": "sonspa", "130.Tree_Sparrow": "amtspa",
    "131.Vesper_Sparrow": "vesspa", "132.White_crowned_Sparrow": "whcspa",
    "133.White_throated_Sparrow": "whtspa", "134.Cape_Glossy_Starling": "capgst1",
    "135.Bank_Swallow": "banswa", "136.Barn_Swallow": "barswa",
    "137.Cliff_Swallow": "cliswa", "138.Tree_Swallow": "treswa",
    "139.Scarlet_Tanager": "scatan", "140.Summer_Tanager": "sumtan",
    "141.Artic_Tern": "arcter", "142.Black_Tern": "blkter",
    "143.Caspian_Tern": "caster1", "144.Common_Tern": "comter",
    "145.Elegant_Tern": "eleter", "146.Forsters_Tern": "forter",
    "147.Least_Tern": "leater1", "148.Green_tailed_Towhee": "gnttow",
    "149.Brown_Thrasher": "brntra", "150.Sage_Thrasher": "sagtra",
    "151.Black_capped_Vireo": "bkcvir1", "152.Blue_headed_Vireo": "blhvir",
    "153.Philadelphia_Vireo": "phivir", "154.Red_eyed_Vireo": "reevir1",
    "155.Warbling_Vireo": "warvir", "156.White_eyed_Vireo": "whevir",
    "157.Yellow_throated_Vireo": "yetvir", "158.Bay_breasted_Warbler": "babwar",
    "159.Black_and_white_Warbler": "bawwar", "160.Black_throated_Blue_Warbler": "btbwar",
    "161.Blue_winged_Warbler": "buwwar", "162.Canada_Warbler": "canwar",
    "163.Cape_May_Warbler": "camwar", "164.Cerulean_Warbler": "cerwar",
    "165.Chestnut_sided_Warbler": "chesid", "166.Golden_winged_Warbler": "gowwar",
    "167.Hooded_Warbler": "hoowar", "168.Kentucky_Warbler": "kenwar",
    "169.Magnolia_Warbler": "magwar", "170.Mourning_Warbler": "mouwar",
    "171.Myrtle_Warbler": "yerwar", "172.Nashville_Warbler": "naswar",
    "173.Orange_crowned_Warbler": "orcwar", "174.Palm_Warbler": "palwar",
    "175.Pine_Warbler": "pinwar", "176.Prairie_Warbler": "prawar",
    "177.Prothonotary_Warbler": "prowar", "178.Swainson_Warbler": "swawar",
    "179.Tennessee_Warbler": "tenwar", "180.Wilson_Warbler": "wlswar",
    "181.Worm_eating_Warbler": "woewar1", "182.Yellow_Warbler": "yelwar",
    "183.Northern_Waterthrush": "norwat", "184.Louisiana_Waterthrush": "louwat",
    "185.Bohemian_Waxwing": "bohwax", "186.Cedar_Waxwing": "cedwax",
    "187.American_Three_toed_Woodpecker": "attwoo", "188.Pileated_Woodpecker": "pilwoo",
    "189.Red_bellied_Woodpecker": "rebwoo", "190.Red_cockaded_Woodpecker": "recwoo",
    "191.Red_headed_Woodpecker": "rehwoo", "192.Downy_Woodpecker": "dowwoo",
    "193.Bewick_Wren": "bewwre", "194.Cactus_Wren": "cacwre",
    "195.Carolina_Wren": "carwre", "196.House_Wren": "houwre",
    "197.Marsh_Wren": "marwre", "198.Rock_Wren": "rocwre",
    "199.Winter_Wren": "winwre3", "200.Common_Yellowthroat": "comyel",
}


def _build_class_mappings(data_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Build index-to-name and index-to-eBird-code maps from CUB-200 annotations."""
    samples = load_cub_annotations(data_root)
    unique_ids = sorted({s.class_id for s in samples})
    class_names_by_id = {s.class_id: s.class_name for s in samples}

    idx_to_name: dict[str, str] = {}
    idx_to_code: dict[str, str] = {}

    for idx, cid in enumerate(unique_ids):
        raw_name = class_names_by_id[cid]
        display = raw_name.split(".", 1)[-1].replace("_", " ") if "." in raw_name else raw_name.replace("_", " ")
        idx_to_name[str(idx)] = display
        idx_to_code[str(idx)] = CUB_TO_EBIRD.get(raw_name, "")

    return idx_to_name, idx_to_code


def export(config_path: str) -> None:
    cfg = load_config(config_path)
    data_cfg = cfg["data"]
    export_cfg = cfg["export"]

    model = BirdClassifier(num_classes=data_cfg["num_classes"], pretrained=False)
    ckpt_path = Path(cfg["checkpoint"]["dir"]) / "best.pth"
    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    print(f"Loaded best checkpoint (epoch {ckpt['epoch']}, val_acc={ckpt['val_acc']:.4f})")

    dummy = torch.randn(1, 3, data_cfg["image_size"], data_cfg["image_size"])

    # ONNX (optional — requires onnxscript)
    try:
        onnx_path = Path(export_cfg["onnx_path"])
        onnx_path.parent.mkdir(parents=True, exist_ok=True)
        torch.onnx.export(
            model, dummy, str(onnx_path),
            input_names=["image"],
            output_names=["logits"],
            dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}},
            opset_version=17,
        )
        print(f"ONNX exported → {onnx_path}")
    except Exception as e:
        print(f"ONNX export skipped ({e}). TorchScript + .mar will still be created.")

    # TorchScript
    ts_path = Path(export_cfg["torchscript_path"])
    ts_path.parent.mkdir(parents=True, exist_ok=True)
    scripted = torch.jit.trace(model, dummy)
    scripted.save(str(ts_path))
    print(f"TorchScript exported → {ts_path}")

    # Class mappings for handler
    data_root = Path(data_cfg["root_dir"])
    idx_to_name, idx_to_code = _build_class_mappings(data_root)
    print(f"Built class mappings: {len(idx_to_name)} classes, {sum(1 for v in idx_to_code.values() if v)} eBird codes")

    # .mar archive for TorchServe
    mar_dir = Path(export_cfg["mar_output_dir"])
    mar_dir.mkdir(parents=True, exist_ok=True)
    mar_path = mar_dir / "bird_classifier.mar"
    if mar_path.exists():
        mar_path.unlink()

    with tempfile.TemporaryDirectory() as tmpdir:
        idx_file = Path(tmpdir) / "index_to_name.json"
        codes_file = Path(tmpdir) / "species_codes.json"
        with open(idx_file, "w") as f:
            json.dump(idx_to_name, f)
        with open(codes_file, "w") as f:
            json.dump(idx_to_code, f)

        archiver = shutil.which("torch-model-archiver") or str(Path(sys.executable).parent / "torch-model-archiver")
        cmd = [
            archiver,
            "--model-name", "bird_classifier",
            "--version", "1.0",
            "--serialized-file", str(ts_path),
            "--handler", "serving/handler.py",
            "--extra-files", f"{idx_file},{codes_file}",
            "--export-path", str(mar_dir),
            "--force",
        ]
        print(f"Running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)

    print(f".mar archive → {mar_path}")
    print(f"  Size: {mar_path.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="model/config/training_config.yaml")
    export(parser.parse_args().config)
