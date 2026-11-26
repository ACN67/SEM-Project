"""Render chart data as deterministic PNG resources without storing them."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from io import BytesIO
import os
from math import ceil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..contracts import models as m
from .common import period_sort_key

_PALETTE = (
    "#2F6BFF",
    "#EF7A35",
    "#2FA36B",
    "#9B59B6",
    "#D64545",
    "#D19A20",
    "#357A85",
    "#666666",
)

_FONT_CANDIDATES = {
    False: (
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ),
    True: (
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "C:/Windows/Fonts/msyhbd.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ),
}


class ChartRenderError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _font(size: int, *, bold: bool = False, override: str | Path | None = None) -> ImageFont.FreeTypeFont:
    candidates = []
    if override:
        candidates.append(Path(override))
    environment_font = os.environ.get("SEM_CHART_FONT")
    if environment_font:
        candidates.append(Path(environment_font))
    candidates.extend(Path(path) for path in _FONT_CANDIDATES[bold])
    for path in candidates:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    raise ChartRenderError(
        "CHART_FONT_UNAVAILABLE",
        "未找到可显示中文的字体；请通过 SEM_CHART_FONT 指定字体文件。",
    )


def _point_decimal(point: m.Point) -> Decimal | None:
    if point.value is None:
        return None
    try:
        value = Decimal(point.value)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _number_label(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if len(text) > 14:
        text = format(value.quantize(Decimal("0.01")), "f")
    return text


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    left, right = 0, len(text)
    while left < right:
        middle = (left + right + 1) // 2
        candidate = text[:middle] + suffix
        if draw.textlength(candidate, font=font) <= max_width:
            left = middle
        else:
            right = middle - 1
    return text[:left] + suffix


def _series_and_labels(data: list[m.Point]) -> tuple[list[str], list[str], dict[tuple[str, str], Decimal | None]]:
    series: list[str] = []
    labels: list[str] = []
    values: dict[tuple[str, str], Decimal | None] = {}
    metric_ids: dict[tuple[str, str], str] = {}
    for point in data:
        if point.series not in series:
            series.append(point.series)
        if point.label not in labels:
            labels.append(point.label)
        key = (point.series, point.label)
        if key in values:
            raise ChartRenderError(
                "CHART_DATA_CONFLICT",
                f"Duplicate chart coordinate {key}: {metric_ids[key]}, {point.metric_id}",
            )
        metric_ids[key] = point.metric_id
        values[key] = _point_decimal(point)
    def label_sort_key(label: str):
        # Chart points carry the period label but not the metric granularity.
        # Reuse the common calendar ordering and use the label as a stable tie-breaker.
        granularity = "quarter" if "-Q" in label else "month" if "-" in label else "year"
        return period_sort_key(label, granularity), label

    return series, sorted(labels, key=label_sort_key), values


def _draw_line_or_bar(
    chart: m.Chart,
    *,
    width: int,
    height: int,
    font_path: str | Path | None,
) -> Image.Image:
    scale = 2
    image = Image.new("RGB", (width * scale, height * scale), "white")
    draw = ImageDraw.Draw(image)
    small = _font(18 * scale, override=font_path)
    bold = _font(34 * scale, bold=True, override=font_path)
    title = _fit_text(draw, chart.title, bold, (width - 160) * scale)
    draw.text(
        (width * scale / 2, 34 * scale),
        title,
        font=bold,
        fill="#172033",
        anchor="ma",
    )
    draw.text(
        (80 * scale, 80 * scale),
        f"单位：{chart.unit}",
        font=small,
        fill="#5D6676",
    )

    series, labels, values = _series_and_labels(chart.data)
    numeric = [value for value in values.values() if value is not None]
    if not numeric or not labels or not series:
        raise ChartRenderError("CHART_DATA_INSUFFICIENT", "图表没有可渲染的数据。")

    colors = {name: _PALETTE[index] for index, name in enumerate(series)} if len(series) <= len(_PALETTE) else {}
    if not colors:
        raise ChartRenderError("CHART_DENSITY_EXCEEDED", "Too many series to distinguish using chart colors.")
    legend_x, legend_y = 80 * scale, 112 * scale
    legend_right = (width - 70) * scale
    for name in series:
        item_width = draw.textlength(name, font=small) + 60 * scale
        if item_width > legend_right - 80 * scale:
            raise ChartRenderError("CHART_DENSITY_EXCEEDED", "Series name exceeds legend width.")
        if legend_x + item_width > legend_right:
            legend_x = 80 * scale
            legend_y += 30 * scale
        draw.rectangle([legend_x, legend_y + 5 * scale, legend_x + 18 * scale, legend_y + 18 * scale], fill=colors[name])
        draw.text((legend_x + 26 * scale, legend_y), name, font=small, fill="#374151")
        legend_x += item_width

    plot_left = 100 * scale
    plot_top = max(155 * scale, legend_y + 43 * scale)
    plot_right = (width - 70) * scale
    plot_bottom = (height - 105) * scale
    if plot_bottom - plot_top < 100 * scale:
        raise ChartRenderError("CHART_DENSITY_EXCEEDED", "Legend leaves insufficient plot space.")
    draw.line(
        [(plot_left, plot_bottom), (plot_left, plot_top)],
        fill="#4A5365",
        width=2 * scale,
    )
    draw.line(
        [(plot_left, plot_bottom), (plot_right, plot_bottom)],
        fill="#4A5365",
        width=2 * scale,
    )

    minimum = min(Decimal(0), min(numeric))
    maximum = max(Decimal(0), max(numeric))
    if maximum == minimum:
        maximum = minimum + Decimal("1")
    padding = (maximum - minimum) * Decimal("0.08")
    if padding == 0:
        padding = Decimal("0.1")
    if minimum < 0:
        minimum -= padding
    maximum += padding

    def y_position(value: Decimal) -> float:
        ratio = float((value - minimum) / (maximum - minimum))
        return plot_bottom - ratio * (plot_bottom - plot_top)

    zero_y = y_position(Decimal(0))
    if chart.type == "bar":
        draw.line([(plot_left, zero_y), (plot_right, zero_y)], fill="#4A5365", width=2 * scale)

    for tick in range(5):
        ratio = Decimal(tick) / Decimal(4)
        tick_value = minimum + (maximum - minimum) * ratio
        y = y_position(tick_value)
        draw.line(
            [(plot_left, y), (plot_right, y)],
            fill="#E3E7EE",
            width=scale,
        )
        draw.text(
            (plot_left - 14 * scale, y),
            _number_label(tick_value),
            font=small,
            fill="#5D6676",
            anchor="rm",
        )

    if chart.type == "bar":
        cluster_width = (plot_right - plot_left) / len(labels)
        x_positions = [plot_left + (index + 0.5) * cluster_width for index in range(len(labels))]
    elif len(labels) == 1:
        x_positions = [(plot_left + plot_right) / 2]
    else:
        x_step = (plot_right - plot_left) / (len(labels) - 1)
        x_positions = [plot_left + index * x_step for index in range(len(labels))]
    widest = max(draw.textlength(label, font=small) for label in labels)
    spacing = (plot_right - plot_left) / max(1, len(labels) - 1)
    step = max(1, ceil((widest + 18 * scale) / spacing))
    indices = list(range(0, len(labels), step))
    if indices[-1] != len(labels) - 1:
        if len(indices) > 1 and len(labels) - 1 - indices[-1] < step:
            indices.pop()
        indices.append(len(labels) - 1)
    for index in indices:
        draw.text((x_positions[index], plot_bottom + 22 * scale), labels[index],
                  font=small, fill="#374151", anchor="ma")

    if chart.type == "line":
        for name in series:
            previous: tuple[float, float] | None = None
            for label, x in zip(labels, x_positions, strict=True):
                value = values.get((name, label))
                if value is None:
                    previous = None
                    continue
                y = y_position(value)
                if previous is not None:
                    draw.line(
                        [previous, (x, y)],
                        fill=colors[name],
                        width=5 * scale,
                    )
                radius = min(7 * scale, max(1 * scale, spacing / 4))
                draw.ellipse(
                    [x - radius, y - radius, x + radius, y + radius],
                    fill="white",
                    outline=colors[name],
                    width=3 * scale,
                )
                previous = (x, y)
    else:
        cluster_width = (plot_right - plot_left) / max(1, len(labels))
        bar_width = min(
            52 * scale,
            cluster_width / max(1, len(series) + 1),
        )
        if bar_width < scale:
            raise ChartRenderError("CHART_DENSITY_EXCEEDED", "Bars are too narrow to distinguish.")
        gap = min(3 * scale, bar_width * 0.15)
        for label_index, label in enumerate(labels):
            cluster_center = x_positions[label_index]
            start = cluster_center - bar_width * len(series) / 2
            for series_index, name in enumerate(series):
                value = values.get((name, label))
                if value is None or value == 0:
                    continue
                y = y_position(value)
                left = start + series_index * bar_width + gap
                right = left + bar_width - 2 * gap
                draw.rounded_rectangle(
                    [left, min(y, zero_y), right, max(y, zero_y)],
                    radius=min(4 * scale, (right - left) / 2),
                    fill=colors[name],
                )
    return image.resize((width, height), Image.Resampling.LANCZOS)


def _draw_table(
    chart: m.Chart,
    *,
    width: int,
    height: int,
    font_path: str | Path | None,
) -> Image.Image:
    scale = 2
    image = Image.new("RGB", (width * scale, height * scale), "white")
    draw = ImageDraw.Draw(image)
    regular = _font(20 * scale, override=font_path)
    header = _font(20 * scale, bold=True, override=font_path)
    bold = _font(32 * scale, bold=True, override=font_path)
    title = _fit_text(draw, chart.title, bold, (width - 160) * scale)
    draw.text(
        (width * scale / 2, 32 * scale),
        title,
        font=bold,
        fill="#172033",
        anchor="ma",
    )
    draw.text(
        (70 * scale, 82 * scale),
        f"单位：{chart.unit}",
        font=regular,
        fill="#5D6676",
    )
    columns = ("期间", "系列", f"数值（{chart.unit}）")
    left = 65 * scale
    right = (width - 65) * scale
    top = 130 * scale
    widths = (
        int((right - left) * 0.25),
        int((right - left) * 0.42),
        right - left - int((right - left) * 0.25) - int((right - left) * 0.42),
    )
    row_height = 46 * scale
    x = left
    for column, column_width in zip(columns, widths, strict=True):
        draw.rectangle([x, top, x + column_width, top + row_height], fill="#E8EEF9")
        draw.text(
            (x + 14 * scale, top + row_height / 2),
            column,
            font=header,
            fill="#1C3156",
            anchor="lm",
        )
        x += column_width
    max_rows = max(1, int((height * scale - top - 90 * scale) / row_height))
    for row_index, point in enumerate(chart.data[:max_rows]):
        y = top + (row_index + 1) * row_height
        if row_index % 2:
            draw.rectangle([left, y, right, y + row_height], fill="#F8FAFD")
        values = (
            point.label,
            point.series,
            point.value if point.value is not None else "缺失",
        )
        x = left
        for text, column_width in zip(values, widths, strict=True):
            draw.text(
                (x + 14 * scale, y + row_height / 2),
                _fit_text(draw, str(text), regular, column_width - 28 * scale),
                font=regular,
                fill="#263244",
                anchor="lm",
            )
            x += column_width
        draw.line(
            [(left, y + row_height), (right, y + row_height)],
            fill="#D8DEE8",
            width=scale,
        )
    if len(chart.data) > max_rows:
        draw.text(
            (right, height * scale - 40 * scale),
            f"仅显示前 {max_rows} 行",
            font=regular,
            fill="#8A334A",
            anchor="ra",
        )
    return image.resize((width, height), Image.Resampling.LANCZOS)


def render_chart_png(
    chart: m.Chart,
    *,
    width: int = 1200,
    height: int = 720,
    font_path: str | Path | None = None,
) -> bytes:
    """Render chart data to PNG bytes; persistence remains an orchestration concern."""
    if width < 640 or height < 480:
        raise ChartRenderError("CHART_SIZE_INVALID", "图表宽高不得小于 640x480。")
    if not chart.data:
        raise ChartRenderError("CHART_DATA_INSUFFICIENT", "图表没有可渲染的数据。")
    if chart.type == "table":
        image = _draw_table(
            chart,
            width=width,
            height=height,
            font_path=font_path,
        )
    else:
        image = _draw_line_or_bar(
            chart,
            width=width,
            height=height,
            font_path=font_path,
        )
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    return output.getvalue()
