from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .domain.events import EventLevel, PipelineStage, ProgressEvent
from .metadata import (
    fmt_ev,
    fmt_f_number,
    fmt_focal,
    fmt_model,
    run_exif,
)


@dataclass(frozen=True)
class RendererContext:
    """Common input contract shared by every presentation renderer."""

    photo_path: object
    source_dir: object
    presentation: object
    layout: str
    exif: dict
    bg: tuple
    camera_png: object = None
    lens_png: object = None
    camera_model: str = ""
    lens_model: str = ""
    line_items: tuple = ()
    custom: dict = field(default_factory=dict)
    artist: str = "Vincent Chyu"
    watermark_text: str = ""
    effective_layout: str = ""
    compression: str = "none"
    debug: bool = False
    debug_dir: object = None
    step_callback: Callable[[ProgressEvent], None] | None = None

    def report_step(self, step_tag: str, message: str, level: str = "info", **details: Any) -> None:
        """统一向外部总线/TUI/CLI 汇报当前微步骤节点。"""
        if self.step_callback:
            lvl = EventLevel(level) if level in EventLevel._value2member_map_ else EventLevel.INFO
            event = ProgressEvent(
                stage=PipelineStage.RENDERING,
                level=lvl,
                message=message,
                step_tag=step_tag,
                photo_path=Path(self.photo_path) if self.photo_path else None,
                details=details,
            )
            self.step_callback(event)


def build_context(
    photo_path,
    source_dir,
    presentation,
    layout,
    compression="none",
    exif=None,
    debug=False,
    debug_dir=None,
    step_callback=None,
    custom=None,
):
    """Build only the shared metadata context; scheme modules supply assets."""
    from .rendering import dominant_bg

    if exif is None:
        exif = run_exif(photo_path)
    custom = dict(custom or {})
    merged_exif = dict(exif or {})

    def custom_value(key):
        value = custom.get(key)
        if value in (None, ""):
            return None
        return str(value).strip()

    field_map = {
        "camera_model": "Model",
        "lens_model": "LensModel",
        "exposure_time": "ExposureTime",
        "f_number": "FNumber",
        "iso": "ISO",
        "focal_length": "FocalLength",
        "exposure_compensation": "ExposureCompensation",
        "white_balance": "WhiteBalance",
        "date": "DateTimeOriginal",
    }
    for custom_key, exif_key in field_map.items():
        value = custom_value(custom_key)
        if value is not None:
            if custom_key == "date":
                merged_exif["DateTimeOriginal"] = value
                merged_exif["CreateDate"] = value
                merged_exif["ModifyDate"] = value
            else:
                merged_exif[exif_key] = value

    gps_lat = custom_value("gps_latitude")
    gps_lon = custom_value("gps_longitude")
    gps_alt = custom_value("gps_altitude")
    if gps_lat:
        try:
            lat = float(gps_lat)
            merged_exif["GPSLatitude"] = abs(lat)
            merged_exif["GPSLatitudeRef"] = "N" if lat >= 0 else "S"
        except ValueError:
            merged_exif["GPSLatitude"] = gps_lat
    if gps_lon:
        try:
            lon = float(gps_lon)
            merged_exif["GPSLongitude"] = abs(lon)
            merged_exif["GPSLongitudeRef"] = "E" if lon >= 0 else "W"
        except ValueError:
            merged_exif["GPSLongitude"] = gps_lon
    if gps_alt:
        merged_exif["GPSAltitude"] = gps_alt

    camera_model = custom_value("camera_model") or fmt_model(merged_exif)

    lens_model = (
        custom_value("lens_model")
        or merged_exif.get("LensModel")
        or merged_exif.get("LensID")
        or merged_exif.get("Lens")
        or ""
    ).strip()

    line_items = [
        merged_exif.get("Format") if merged_exif.get("Format") and str(merged_exif.get("Format")).lower() != "image/jpeg" else None,
        fmt_f_number(merged_exif.get("FNumber") or merged_exif.get("Aperture")),
        merged_exif.get("ExposureTime") or merged_exif.get("ShutterSpeed"),
        f"ISO {merged_exif.get('ISO')}" if merged_exif.get("ISO") else None,
        fmt_focal(merged_exif.get("FocalLength")),
        fmt_ev(merged_exif.get("ExposureCompensation")),
        merged_exif.get("WhiteBalance"),
    ]
    artist = (
        custom_value("artist")
        or merged_exif.get("Artist")
        or merged_exif.get("By-line")
        or merged_exif.get("Creator")
        or merged_exif.get("Photographer")
        or "Vincent Chyu"
    )
    return RendererContext(
        photo_path=photo_path,
        source_dir=source_dir,
        presentation=presentation,
        layout=layout,
        exif=merged_exif,
        bg=dominant_bg(photo_path),
        camera_model=camera_model,
        lens_model=lens_model,
        line_items=tuple(item for item in line_items if item),
        custom=custom,
        artist=artist,
        watermark_text=str(custom_value("watermark_text") or ""),
        effective_layout=layout,
        compression=compression,
        debug=debug,
        debug_dir=debug_dir,
        step_callback=step_callback,
    )

