"""T31 content validation and metadata for STL meshes and DICOM files.

Detection is by content, never by extension. Nothing here modifies the stored file. Patient
identifiers inside a DICOM dataset (name, ID, birth date) are never read into the metadata.
"""

from __future__ import annotations

import math
import struct
from datetime import date
from pathlib import Path

import pydicom
from pydicom.errors import InvalidDicomError

STL_BINARY_HEADER = 84
STL_TRIANGLE_BYTES = 50
STL_FINITE_CHECK_TRIANGLES = 20_000
ASCII_LINE_LIMIT = 4096
UNSUPPORTED_DICOM_MESSAGE = "Format DICOM nesuportat pentru vizualizare. Fișierul poate fi descărcat."
IMPLICIT_LE = "1.2.840.10008.1.2"
EXPLICIT_LE = "1.2.840.10008.1.2.1"
UNCOMPRESSED_SYNTAXES = {IMPLICIT_LE, EXPLICIT_LE}
MAX_FRAMES = 5000


class ImagingRejected(Exception):
    """The bytes are not a valid STL/DICOM file (HTTP 415)."""


def is_dicom(head: bytes) -> bool:
    return len(head) >= 132 and head[128:132] == b"DICM"


def inspect_stl(path: Path, size: int) -> dict | None:
    """Return metadata for a valid STL, None if the file does not look like STL at all, and raise
    ImagingRejected when it looks like STL but is malformed."""
    with path.open("rb") as handle:
        head = handle.read(STL_BINARY_HEADER)
        if len(head) == STL_BINARY_HEADER:
            count = struct.unpack("<I", head[80:84])[0]
            if count > 0 and size == STL_BINARY_HEADER + STL_TRIANGLE_BYTES * count:
                return _binary_stl(handle, count)
        starts_ascii = head.lstrip().lower().startswith(b"solid")
    if not starts_ascii:
        return None
    return _ascii_stl(path)


def _binary_stl(handle, count: int) -> dict:
    checked = min(count, STL_FINITE_CHECK_TRIANGLES)
    block = handle.read(checked * STL_TRIANGLE_BYTES)
    for index in range(checked):
        floats = struct.unpack_from("<12f", block, index * STL_TRIANGLE_BYTES)
        if not all(math.isfinite(value) for value in floats):
            raise ImagingRejected("Fișier STL invalid: coordonate nefinite")
    return {"kind": "stl", "format": "binary", "triangles": count}


def _ascii_stl(path: Path) -> dict:
    facets = vertices = 0
    expect = "facet"  # facet | loop | vertex | endloop | endfacet
    ended = False
    seen_solid = False
    try:
        with path.open("rb") as handle:
            for raw in handle:
                if len(raw) > ASCII_LINE_LIMIT:
                    raise ImagingRejected("Fișier STL invalid: linie prea lungă")
                words = raw.decode("ascii").split()
                if not words:
                    continue
                keyword = words[0].lower()
                if not seen_solid:
                    if keyword != "solid":
                        raise ImagingRejected("Fișier STL invalid")
                    seen_solid = True
                    continue
                if ended:
                    raise ImagingRejected("Fișier STL invalid: conținut după endsolid")
                if keyword == "endsolid" and expect == "facet":
                    ended = True
                elif keyword == "facet" and expect == "facet" and len(words) == 5 and words[1].lower() == "normal":
                    _floats(words[2:5])
                    expect = "loop"
                elif keyword == "outer" and expect == "loop" and len(words) == 2 and words[1].lower() == "loop":
                    expect, vertices = "vertex", 0
                elif keyword == "vertex" and expect == "vertex" and len(words) == 4:
                    _floats(words[1:4])
                    vertices += 1
                    if vertices > 3:
                        raise ImagingRejected("Fișier STL invalid: fațetă cu mai mult de 3 vârfuri")
                elif keyword == "endloop" and expect == "vertex" and vertices == 3:
                    expect = "endfacet"
                elif keyword == "endfacet" and expect == "endfacet":
                    facets += 1
                    expect = "facet"
                else:
                    raise ImagingRejected("Fișier STL invalid: structură neașteptată")
    except UnicodeDecodeError as exc:
        raise ImagingRejected("Fișier STL invalid: nu este text ASCII") from exc
    if not ended or facets == 0:
        raise ImagingRejected("Fișier STL invalid: fără fațete sau fără endsolid")
    return {"kind": "stl", "format": "ascii", "triangles": facets}


def _floats(words: list[str]) -> None:
    try:
        values = [float(word) for word in words]
    except ValueError as exc:
        raise ImagingRejected("Fișier STL invalid: număr necitibil") from exc
    if not all(math.isfinite(value) for value in values):
        raise ImagingRejected("Fișier STL invalid: coordonate nefinite")


def _text(ds, name: str) -> str | None:
    value = ds.get(name)
    if value is None:
        return None
    text = str(value).strip()
    return text[:128] or None


def _number(ds, name: str, kind=int):
    value = ds.get(name)
    if value is None:
        return None
    if hasattr(value, "__iter__") and not isinstance(value, str | bytes):
        value = next(iter(value), None)
    try:
        return kind(value)
    except (TypeError, ValueError):
        return None


def _study_date(raw: str | None) -> str | None:
    if not raw or len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()
    except ValueError:
        return None


def inspect_dicom(path: Path, size: int) -> dict:
    try:
        ds = pydicom.dcmread(str(path), defer_size=1024)
    except (InvalidDicomError, OSError, EOFError, ValueError, AttributeError, KeyError, IndexError, struct.error) as exc:
        raise ImagingRejected("Fișier DICOM invalid sau deteriorat") from exc
    meta = getattr(ds, "file_meta", None)
    syntax = str(meta.get("TransferSyntaxUID")) if meta is not None and meta.get("TransferSyntaxUID") else None
    if not syntax:
        raise ImagingRejected("Fișier DICOM invalid: lipsește transfer syntax-ul")
    rows, columns = _number(ds, "Rows"), _number(ds, "Columns")
    frames = _number(ds, "NumberOfFrames") or 1
    bits = _number(ds, "BitsAllocated")
    samples = _number(ds, "SamplesPerPixel") or 1
    photometric = _text(ds, "PhotometricInterpretation")
    representation = _number(ds, "PixelRepresentation") or 0
    if not 1 <= frames <= MAX_FRAMES:
        raise ImagingRejected("Fișier DICOM invalid: număr de cadre necorespunzător")
    # Keep deferred values deferred. Dataset.get_item() otherwise materializes PixelData and
    # converts it to a DataElement, losing the RawDataElement offset/length used below. That
    # made valid images larger than pydicom's defer threshold look truncated (and read the full
    # image into memory during validation).
    raw = ds.get_item("PixelData", keep_deferred=True) if "PixelData" in ds else None
    pixel_offset = pixel_length = None
    if raw is not None:
        pixel_offset, pixel_length = getattr(raw, "value_tell", None), getattr(raw, "length", None)
        if pixel_length is None or pixel_length == 0xFFFFFFFF:
            pixel_offset = pixel_length = None  # encapsulated (compressed) data: no flat offset
    uncompressed = syntax in UNCOMPRESSED_SYNTAXES
    if raw is not None and uncompressed and rows and columns and bits and bits % 8 == 0:
        expected = rows * columns * samples * frames * (bits // 8)
        if pixel_length is None or pixel_length < expected or pixel_offset + pixel_length > size:
            raise ImagingRejected("Fișier DICOM trunchiat: datele de imagine sunt incomplete")
    viewable = bool(
        raw is not None and uncompressed and rows and columns and samples == 1 and bits in (8, 16)
        and photometric in ("MONOCHROME1", "MONOCHROME2") and representation in (0, 1) and pixel_offset is not None)
    syntax_name = str(pydicom.uid.UID(syntax).name)
    return {
        "kind": "dicom", "modality": _text(ds, "Modality"), "study_date": _study_date(_text(ds, "StudyDate")),
        "body_part": _text(ds, "BodyPartExamined"), "study_description": _text(ds, "StudyDescription"),
        "rows": rows, "columns": columns, "frames": frames, "bits_allocated": bits, "bits_stored": _number(ds, "BitsStored"),
        "samples_per_pixel": samples, "photometric": photometric, "pixel_representation": representation,
        "transfer_syntax_uid": syntax, "transfer_syntax_name": syntax_name,
        "window_center": _number(ds, "WindowCenter", float), "window_width": _number(ds, "WindowWidth", float),
        "rescale_slope": _number(ds, "RescaleSlope", float), "rescale_intercept": _number(ds, "RescaleIntercept", float),
        "pixel_spacing": [float(v) for v in ds.PixelSpacing] if "PixelSpacing" in ds and len(ds.PixelSpacing) == 2 else None,
        "pixel_offset": pixel_offset, "pixel_length": pixel_length,
        "viewable": viewable, "viewer_message": None if viewable else UNSUPPORTED_DICOM_MESSAGE,
    }
