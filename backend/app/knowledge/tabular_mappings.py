"""Reviewed mappings for the KBA annual BEV registration workbook."""
from hashlib import sha256
from pathlib import Path

KBA_2024_SHA256 = "856b9afe515d51aa52bcb34d645dce2c5cdeaf47ef398b4e0a754c1bd5813dbf"
KBA_2024 = [("FZ 10.1", "AL488", {
    "indicator": "bev_registrations", "name": "BEV new passenger car registrations",
    "entity_id": "", "period": "2024", "granularity": "year", "region": "德国",
    "scope": "new passenger car registrations, BEV, Jan-Dec",
    "raw_unit": "辆", "multiplier": "1", "unit": "辆", "currency": "",
})]

def registered_rows(path: Path):
    with path.open("rb") as stream:
        digest = sha256()
        while block := stream.read(1024 * 1024):
            digest.update(block)
    if digest.hexdigest() == KBA_2024_SHA256:
        return KBA_2024
    if digest.hexdigest() == "3fd1b4604bb957de8faa3b96be385254fe9c4b3d0a18c2083d98ff5cdffeec4c":
        sheet, _, fields = KBA_2024[0]
        return [(sheet, "AL461", {**fields, "period": "2023"})]
    return None
