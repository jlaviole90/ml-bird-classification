#!/usr/bin/env python3
"""Bootstrap a pretrained EfficientNet-B4 into a TorchServe .mar archive.

This creates a working model for the e2e pipeline WITHOUT requiring the full
CUB-200 training cycle. The model uses ImageNet-pretrained weights with a
200-class head initialized to common North American bird species.

Once you train a fine-tuned model, re-export with `make export` to replace it.

Usage:
    python tools/bootstrap_model.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import torch

SPECIES = [
    ("Black_footed_Albatross", "bkfalb"),
    ("Laysan_Albatross", "layalb"),
    ("Sooty_Albatross", "sooalb1"),
    ("Groove_billed_Ani", "grbani"),
    ("Crested_Auklet", "creauk"),
    ("Least_Auklet", "leaauk"),
    ("Parakeet_Auklet", "parauk"),
    ("Rhinoceros_Auklet", "rhinau"),
    ("Brewer_Blackbird", "brebla"),
    ("Red_winged_Blackbird", "rewbla"),
    ("Rusty_Blackbird", "rusbla"),
    ("Yellow_headed_Blackbird", "yehbla"),
    ("Bobolink", "boboli"),
    ("Indigo_Bunting", "indbun"),
    ("Lazuli_Bunting", "lazbun"),
    ("Painted_Bunting", "paibun"),
    ("Cardinal", "norcar"),
    ("Spotted_Catbird", "spocat1"),
    ("Gray_Catbird", "grycat"),
    ("Yellow_breasted_Chat", "yebcha"),
    ("Eastern_Towhee", "eastow"),
    ("Chuck_will_Widow", "chwwid"),
    ("Brandt_Cormorant", "bracor"),
    ("Pelagic_Cormorant", "pelcor"),
    ("Double_crested_Cormorant", "doccor"),
    ("Brown_Cowbird", "brocow1"),
    ("Bronzed_Cowbird", "brocow"),
    ("Shiny_Cowbird", "shicow"),
    ("Brown_Creeper", "brncre"),
    ("American_Crow", "amecro"),
    ("Fish_Crow", "fiscro"),
    ("Black_billed_Cuckoo", "bkbcuc"),
    ("Mangrove_Cuckoo", "mancuc"),
    ("Yellow_billed_Cuckoo", "yebcuc"),
    ("Gray_crowned_Rosy_Finch", "gcrfin"),
    ("Purple_Finch", "purfin"),
    ("Northern_Flicker", "norfli"),
    ("Acadian_Flycatcher", "acafly"),
    ("Great_Crested_Flycatcher", "grcfly"),
    ("Least_Flycatcher", "leafly"),
    ("Olive_sided_Flycatcher", "olsfly"),
    ("Scissor_tailed_Flycatcher", "sctfly"),
    ("Vermilion_Flycatcher", "verfly"),
    ("Yellow_bellied_Flycatcher", "yebfly"),
    ("Frigatebird", "magfri"),
    ("Northern_Fulmar", "norful"),
    ("Gadwall", "gadwal"),
    ("American_Goldfinch", "amegfi"),
    ("European_Goldfinch", "eurgol"),
    ("Boat_tailed_Grackle", "botgra"),
    ("Eared_Grebe", "eargre"),
    ("Horned_Grebe", "horgre"),
    ("Pied_billed_Grebe", "pibgre"),
    ("Western_Grebe", "wesgre"),
    ("Blue_Grosbeak", "blugro"),
    ("Evening_Grosbeak", "evegro"),
    ("Pine_Grosbeak", "pingro"),
    ("Rose_breasted_Grosbeak", "robgro"),
    ("Pigeon_Guillemot", "piggui"),
    ("California_Gull", "calgul"),
    ("Glaucous_winged_Gull", "glwgul"),
    ("Heermann_Gull", "heegul"),
    ("Herring_Gull", "hergul"),
    ("Ivory_Gull", "ivogul1"),
    ("Ring_billed_Gull", "ribgul"),
    ("Slaty_backed_Gull", "slbgul"),
    ("Western_Gull", "wesgul"),
    ("Anna_Hummingbird", "annhum"),
    ("Ruby_throated_Hummingbird", "rthhum"),
    ("Rufous_Hummingbird", "rufhum"),
    ("Green_Violetear", "mexvio"),
    ("Long_tailed_Jaeger", "lonjae"),
    ("Pomarine_Jaeger", "pomjae"),
    ("Blue_Jay", "blujay"),
    ("Florida_Jay", "flsjay"),
    ("Green_Jay", "grnjay"),
    ("Dark_eyed_Junco", "daejun"),
    ("Tropical_Kingbird", "trokin"),
    ("Gray_Kingbird", "grykin"),
    ("Belted_Kingfisher", "belkin1"),
    ("Green_Kingfisher", "grnkin"),
    ("Pied_Kingfisher", "piekin1"),
    ("Ringed_Kingfisher", "rinkin1"),
    ("White_breasted_Kingfisher", "wbkkin1"),
    ("Red_legged_Kittiwake", "relkit1"),
    ("Horned_Lark", "horlar"),
    ("Pacific_Loon", "pacloo"),
    ("Mallard", "mallar3"),
    ("Western_Meadowlark", "wesmea"),
    ("Hooded_Merganser", "hoomer"),
    ("Red_breasted_Merganser", "rebmer"),
    ("Mockingbird", "normod"),
    ("Nighthawk", "comnig"),
    ("Clark_Nutcracker", "clanut"),
    ("White_breasted_Nuthatch", "whbnut"),
    ("Baltimore_Oriole", "balori"),
    ("Hooded_Oriole", "hooori"),
    ("Orchard_Oriole", "orcori"),
    ("Scott_Oriole", "scoori"),
    ("Ovenbird", "ovenbi1"),
    ("Brown_Pelican", "brnpel"),
    ("White_Pelican", "amwpel"),
    ("Western_Wood_Pewee", "wewpew"),
    ("Sayornis", "easpho"),
    ("American_Pipit", "amepip"),
    ("Whip_poor_Will", "easwpw"),
    ("Horned_Puffin", "horpuf"),
    ("Common_Raven", "comrav"),
    ("White_necked_Raven", "whnrav1"),
    ("American_Redstart", "amered"),
    ("Geococcyx", "greroa"),
    ("Loggerhead_Shrike", "logshr"),
    ("Great_Grey_Shrike", "norshr"),
    ("Baird_Sparrow", "baispa"),
    ("Black_throated_Sparrow", "bktspa"),
    ("Brewer_Sparrow", "brespa"),
    ("Chipping_Sparrow", "chispa"),
    ("Clay_colored_Sparrow", "clcspa"),
    ("House_Sparrow", "houspa"),
    ("Field_Sparrow", "fiespa"),
    ("Fox_Sparrow", "foxspa"),
    ("Grasshopper_Sparrow", "graspa"),
    ("Harris_Sparrow", "harspa"),
    ("Henslow_Sparrow", "henspa"),
    ("Le_Conte_Sparrow", "lecspa"),
    ("Lincoln_Sparrow", "linspa"),
    ("Nelson_Sharp_tailed_Sparrow", "nelspa"),
    ("Savannah_Sparrow", "savspa"),
    ("Seaside_Sparrow", "seaspa"),
    ("Song_Sparrow", "sonspa"),
    ("Tree_Sparrow", "amtspa"),
    ("Vesper_Sparrow", "vesspa"),
    ("White_crowned_Sparrow", "whcspa"),
    ("White_throated_Sparrow", "whtspa"),
    ("Cape_Glossy_Starling", "capgst1"),
    ("Bank_Swallow", "banswa"),
    ("Barn_Swallow", "barswa"),
    ("Cliff_Swallow", "cliswa"),
    ("Tree_Swallow", "treswa"),
    ("Scarlet_Tanager", "scatan"),
    ("Summer_Tanager", "sumtan"),
    ("Artic_Tern", "arcter"),
    ("Black_Tern", "blkter"),
    ("Caspian_Tern", "caster1"),
    ("Common_Tern", "comter"),
    ("Elegant_Tern", "eleter"),
    ("Forsters_Tern", "forter"),
    ("Least_Tern", "leater1"),
    ("Green_tailed_Towhee", "gnttow"),
    ("Brown_Thrasher", "brntra"),
    ("Sage_Thrasher", "sagtra"),
    ("Black_capped_Vireo", "bkcvir1"),
    ("Blue_headed_Vireo", "blhvir"),
    ("Philadelphia_Vireo", "phivir"),
    ("Red_eyed_Vireo", "reevir1"),
    ("Warbling_Vireo", "warvir"),
    ("White_eyed_Vireo", "whevir"),
    ("Yellow_throated_Vireo", "yetvir"),
    ("Bay_breasted_Warbler", "babwar"),
    ("Black_and_white_Warbler", "bawwar"),
    ("Black_throated_Blue_Warbler", "btbwar"),
    ("Blue_winged_Warbler", "buwwar"),
    ("Canada_Warbler", "canwar"),
    ("Cape_May_Warbler", "camwar"),
    ("Cerulean_Warbler", "cerwar"),
    ("Chestnut_sided_Warbler", "chesid"),
    ("Golden_winged_Warbler", "gowwar"),
    ("Hooded_Warbler", "hoowar"),
    ("Kentucky_Warbler", "kenwar"),
    ("Magnolia_Warbler", "magwar"),
    ("Mourning_Warbler", "mouwar"),
    ("Myrtle_Warbler", "yerwar"),
    ("Nashville_Warbler", "naswar"),
    ("Orange_crowned_Warbler", "orcwar"),
    ("Palm_Warbler", "palwar"),
    ("Pine_Warbler", "pinwar"),
    ("Prairie_Warbler", "prawar"),
    ("Prothonotary_Warbler", "prowar"),
    ("Swainson_Warbler", "swawar"),
    ("Tennessee_Warbler", "tenwar"),
    ("Wilson_Warbler", "wlswar"),
    ("Worm_eating_Warbler", "woewar1"),
    ("Yellow_Warbler", "yelwar"),
    ("Northern_Waterthrush", "norwat"),
    ("Louisiana_Waterthrush", "louwat"),
    ("Bohemian_Waxwing", "bohwax"),
    ("Cedar_Waxwing", "cedwax"),
    ("American_Three_toed_Woodpecker", "attwoo"),
    ("Pileated_Woodpecker", "pilwoo"),
    ("Red_bellied_Woodpecker", "rebwoo"),
    ("Red_cockaded_Woodpecker", "recwoo"),
    ("Red_headed_Woodpecker", "rehwoo"),
    ("Downy_Woodpecker", "dowwoo"),
    ("Bewick_Wren", "bewwre"),
    ("Cactus_Wren", "cacwre"),
    ("Carolina_Wren", "carwre"),
    ("House_Wren", "houwre"),
    ("Marsh_Wren", "marwre"),
    ("Rock_Wren", "rocwre"),
    ("Winter_Wren", "winwre3"),
    ("Common_Yellowthroat", "comyel"),
]


def main():
    project_root = Path(__file__).resolve().parent.parent
    model_store = project_root / "serving" / "model_store"
    model_store.mkdir(parents=True, exist_ok=True)

    print("Loading pretrained EfficientNet-B4...")
    sys.path.insert(0, str(project_root))
    from model.src.model import BirdClassifier

    model = BirdClassifier(num_classes=len(SPECIES), pretrained=True)
    model.eval()

    print("Tracing model to TorchScript...")
    dummy = torch.randn(1, 3, 224, 224)
    traced = torch.jit.trace(model, dummy)

    with tempfile.TemporaryDirectory() as tmpdir:
        ts_path = Path(tmpdir) / "bird_classifier.pt"
        traced.save(str(ts_path))

        idx_map = {str(i): name.replace("_", " ") for i, (name, _) in enumerate(SPECIES)}
        idx_file = Path(tmpdir) / "index_to_name.json"
        with open(idx_file, "w") as f:
            json.dump(idx_map, f)

        species_codes = {str(i): code for i, (_, code) in enumerate(SPECIES)}
        codes_file = Path(tmpdir) / "species_codes.json"
        with open(codes_file, "w") as f:
            json.dump(species_codes, f)

        mar_path = model_store / "bird_classifier.mar"
        if mar_path.exists():
            mar_path.unlink()

        cmd = [
            "torch-model-archiver",
            "--model-name", "bird_classifier",
            "--version", "1.0",
            "--serialized-file", str(ts_path),
            "--handler", str(project_root / "serving" / "handler.py"),
            "--extra-files", f"{idx_file},{codes_file}",
            "--export-path", str(model_store),
            "--force",
        ]
        print(f"Archiving: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)

    print(f"\nModel archived to {mar_path}")
    print(f"  Classes: {len(SPECIES)}")
    print(f"  Size:    {mar_path.stat().st_size / 1024 / 1024:.1f} MB")
    print("\nRestart TorchServe to load: docker compose restart torchserve")


if __name__ == "__main__":
    main()
