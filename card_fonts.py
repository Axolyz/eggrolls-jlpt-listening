"""Pin the GenWan fonts shared by the exported and local card templates."""

from hashlib import sha256
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.request import urlopen


FONT_ORIGIN = "https://raw.githubusercontent.com/ButTaiwan/genwan-font/9c47ad1c99e0c1ec335c2aab46941d8eaf1418ce/"
FONT_CACHE = Path(__file__).parent / "dist" / "fonts" / "genwan-v2.100"
FONT_LICENSE = "SIL_Open_Font_License_1.1.txt"
FONT_ASSETS = {
    "_GenWanMin2TW-M.otf": ("otf/TW/GenWanMin2TW-M.otf", "828b83e22e825030204183422701ae42afe24c93b1b131717e7678bd86b57768"),
    "_GenWanMin2JP-M.otf": ("otf/JP/GenWanMin2JP-M.otf", "2188de838954c1b9d7d91ba91335d040c04a9e0e8e8a545928b7175e81ab7311"),
    "_genwanmin2jp-sb.otf": ("otf/JP/GenWanMin2JP-SB.otf", "05cf76cd79545d7a0d7d9fe54f2f6e7b707a5d9c35af4bccc93ea2a5fb13e116"),
    "_genwanmin2tw-sb.otf": ("otf/TW/GenWanMin2TW-SB.otf", "86cc4abaec4151b5ad9ac6d39f635c974a2dc4155fe041e6a4fa9730114487d1"),
    FONT_LICENSE: (FONT_LICENSE, "d84b652ca46586043946969a33c1dbf5a43028440825ae744a325965bec4c8fb"),
}
REGIONS = {"CN": "TW", "TW": "TW", "JP": "JP"}
FAMILY = re.compile(r"Source Han Serif (CN|TW|JP)")
MEDIA = re.compile(r"_SourceHanSerif(CN|TW|JP)-Medium\.otf", re.IGNORECASE)
TRADITIONAL_FACE = re.compile(r"@font-face\s*\{[^{}]*['\"]Source Han Serif TW['\"][^{}]*\}\s*")


def with_genwan(css):
    css = TRADITIONAL_FACE.sub("", css)
    css = FAMILY.sub(lambda match: f"GenWanMin2 {REGIONS[match[1]]}", css)
    css = css.replace("'GenWanMin2 TW', 'GenWanMin2 JP', 'GenWanMin2 TW'", "'GenWanMin2 TW', 'GenWanMin2 JP'")
    return MEDIA.sub(lambda match: f"_GenWanMin2{REGIONS[match[1].upper()]}-M.otf", css)


def font_asset(name):
    relative_path, checksum = FONT_ASSETS[name]
    target = FONT_CACHE / name
    if target.is_file() and sha256(target.read_bytes()).hexdigest() == checksum:
        return target
    with urlopen(FONT_ORIGIN + relative_path, timeout=60) as response:
        data = response.read()
    if sha256(data).hexdigest() != checksum:
        raise ValueError(f"Unexpected GenWan font asset content: {name}")
    FONT_CACHE.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".download-", dir=FONT_CACHE) as directory:
        staged = Path(directory) / name
        staged.write_bytes(data)
        staged.replace(target)
    return target
