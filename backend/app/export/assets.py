"""Resolve frozen chart PNGs through an injected version-aware asset reader."""
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from PIL import Image

from ..contracts import models as m
from ..contracts.services import ServiceError

AssetResolver = Callable[[str, str], bytes]

@dataclass(frozen=True)
class ChartAsset:
    content: bytes
    width: int
    height: int

def resolve_chart(snapshot: m.ReportDocument, chart: m.Chart, resolver: AssetResolver | None) -> ChartAsset:
    try:
        if resolver is None:
            raise ValueError("chart asset resolver required")
        content = resolver(snapshot.version_id, chart.asset_id)
        if not isinstance(content, bytes):
            raise ValueError("asset reader must return bytes")
        with Image.open(BytesIO(content)) as image:
            if image.format != "PNG":
                raise ValueError("asset is not PNG")
            width, height = image.size
            image.verify()
        with Image.open(BytesIO(content)) as image:
            image.load()
        return ChartAsset(content, width, height)
    except Exception as exc:
        raise ServiceError(m.ErrorInfo(code="EXPORT_FAILED",message=f"Chart asset unavailable or invalid: {exc}",
            retryable=False,details={"chart_id":chart.chart_id,"asset_id":chart.asset_id})) from exc
