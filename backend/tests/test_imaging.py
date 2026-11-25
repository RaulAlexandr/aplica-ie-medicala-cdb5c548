"""T31 STL and DICOM in the patient documents: content validation, metadata, rights, isolation, hash."""
import hashlib
import io
import struct

import pytest
from httpx import ASGITransport, AsyncClient
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.encaps import encapsulate
from pydicom.uid import (
    ExplicitVRLittleEndian,
    ImplicitVRLittleEndian,
    JPEG2000Lossless,
    generate_uid,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.database import Base, get_db
from app.main import app
from tests.test_documents import (  # noqa: F401  (fixtures and helpers shared with T11)
    add_user,
    clinic,
    doc_count,
    h,
    patient,
    png,
    stored_files,
    upload,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def env(tmp_path, monkeypatch):
    store = tmp_path / "t31-store"
    monkeypatch.setattr(settings, "document_storage_dir", str(store))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_db] = override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, sessions, store
    app.dependency_overrides.clear()
    await engine.dispose()


def stl_binary(triangles: int = 12) -> bytes:
    """A synthetic cube-like mesh: 80-byte header + count + 50 bytes per triangle."""
    out = bytearray(b"synthetic cube".ljust(80, b" ") + struct.pack("<I", triangles))
    for index in range(triangles):
        out += struct.pack("<12fH", 0, 0, 1, index, 0, 0, 1, 0, 0, 0, 1, 0, 0)
    return bytes(out)


def stl_ascii(facets: int = 2) -> bytes:
    text = "solid cube\n"
    for index in range(facets):
        text += (f"  facet normal 0 0 1\n    outer loop\n      vertex {index} 0 0\n      vertex 1 {index} 0\n"
                 "      vertex 0 1 1\n    endloop\n  endfacet\n")
    return (text + "endsolid cube\n").encode()


def dicom(rows=8, cols=6, frames=1, bits=16, syntax=ExplicitVRLittleEndian, modality="CR", compressed=False,
          body="JAW", study_date="20261105", preamble=True) -> bytes:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.1"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = syntax
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128 if preamble else None)
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.PatientName, ds.PatientID = "Fictiv^Pacient", "FICT-0001"  # synthetic, must never be exposed
    ds.Modality, ds.StudyDate, ds.BodyPartExamined = modality, study_date, body
    ds.StudyDescription = "Panoramic fictiv"
    ds.Rows, ds.Columns = rows, cols
    ds.SamplesPerPixel, ds.PhotometricInterpretation, ds.PixelRepresentation = 1, "MONOCHROME2", 0
    ds.BitsAllocated, ds.BitsStored, ds.HighBit = bits, bits, bits - 1
    ds.WindowCenter, ds.WindowWidth = 1000, 2000
    if frames > 1:
        ds.NumberOfFrames = str(frames)
    frame = bytes(range(256)) * ((rows * cols * (bits // 8) + 255) // 256)
    frame = frame[: rows * cols * (bits // 8)]
    if compressed:
        ds.PixelData = encapsulate([frame] * frames)
        ds["PixelData"].is_undefined_length = True
    else:
        ds.PixelData = frame * frames
    buffer = io.BytesIO()
    ds.save_as(buffer, enforce_file_format=preamble)
    return buffer.getvalue()


async def setup(client, sessions=None):
    ctx = await clinic(client, f"t31-{__import__('uuid').uuid4().hex[:8]}@example.com")
    return ctx, await patient(client, ctx["auth"])


async def up(client, ctx, pid, content, name, category="imaging", **kw):
    return await upload(client, ctx["auth"], pid, content, name, category=category, **kw)


async def test_binary_and_ascii_stl_are_accepted_with_metadata_and_correct_headers(env):
    client, _s, _store = env
    ctx, pid = await setup(client)
    for content, name, fmt, triangles in ((stl_binary(12), "cub.stl", "binary", 12), (stl_ascii(2), "cub-ascii.STL", "ascii", 2)):
        response = await up(client, ctx, pid, content, name)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["category"] == "imaging" and body["content_type"] == "model/stl" and body["is_image"] is False
        assert body["imaging"]["kind"] == "stl" and body["imaging"]["format"] == fmt and body["imaging"]["triangles"] == triangles
        content_response = await client.get(f"/api/documents/{body['id']}/content", headers=ctx["auth"])
        assert content_response.status_code == 200 and content_response.content == content
        assert content_response.headers["content-type"].startswith("model/stl")
        assert content_response.headers["x-content-type-options"] == "nosniff"
        assert hashlib.sha256(content_response.content).hexdigest() == body["sha256"] == hashlib.sha256(content).hexdigest()


async def test_dicom_single_and_multiframe_metadata_and_identical_download(env):
    client, _s, _store = env
    ctx, pid = await setup(client)
    simple = dicom()
    response = await up(client, ctx, pid, simple, "panoramica.dcm")
    assert response.status_code == 201, response.text
    body = response.json()
    meta = body["imaging"]
    assert body["content_type"] == "application/dicom" and meta["kind"] == "dicom"
    assert (meta["modality"], meta["study_date"], meta["body_part"]) == ("CR", "2026-11-05", "JAW")
    assert (meta["rows"], meta["columns"], meta["frames"], meta["bits_allocated"]) == (8, 6, 1, 16)
    assert meta["transfer_syntax_uid"] == "1.2.840.10008.1.2.1" and "Explicit" in meta["transfer_syntax_name"]
    assert meta["viewable"] is True and meta["window_center"] == 1000 and meta["window_width"] == 2000
    assert meta["pixel_length"] == 8 * 6 * 2
    assert simple[meta["pixel_offset"]: meta["pixel_offset"] + meta["pixel_length"]] == bytes(range(96))
    assert body["clinical_date"] == "2026-11-05"
    for forbidden in ("Fictiv", "FICT-0001"):
        assert forbidden not in response.text  # patient identifiers inside the file are not copied to the API
    downloaded = await client.get(f"/api/documents/{body['id']}/content", params={"download": True}, headers=ctx["auth"])
    assert downloaded.content == simple and hashlib.sha256(downloaded.content).hexdigest() == body["sha256"]
    assert downloaded.headers["content-type"].startswith("application/dicom") and downloaded.headers["x-content-type-options"] == "nosniff"

    multi = dicom(frames=3, syntax=ImplicitVRLittleEndian, bits=8, modality="CT")
    response = await up(client, ctx, pid, multi, "serie.dcm")
    assert response.status_code == 201, response.text
    meta = response.json()["imaging"]
    assert meta["frames"] == 3 and meta["viewable"] is True and meta["pixel_length"] == 8 * 6 * 3
    assert meta["transfer_syntax_uid"] == "1.2.840.10008.1.2"
    offset = meta["pixel_offset"]
    assert multi[offset: offset + meta["pixel_length"]] == (bytes(range(256))[:48]) * 3


async def test_unsupported_transfer_syntax_is_stored_but_marked_not_viewable(env):
    client, _s, _store = env
    ctx, pid = await setup(client)
    response = await up(client, ctx, pid, dicom(syntax=JPEG2000Lossless, compressed=True), "jp2.dcm")
    assert response.status_code == 201, response.text
    meta = response.json()["imaging"]
    assert meta["viewable"] is False and meta["transfer_syntax_uid"] == "1.2.840.10008.1.2.4.90"
    assert "JPEG 2000" in meta["transfer_syntax_name"]
    assert meta["viewer_message"] == "Format DICOM nesuportat pentru vizualizare. Fișierul poate fi descărcat."


async def test_renamed_fake_and_corrupt_files_are_rejected_by_content(env):
    client, _s, store = env
    ctx, pid = await setup(client)
    cases = {
        "png-as-stl": (png(), "model.stl"),
        "png-as-dcm": (png(), "scan.dcm"),
        "garbage-dcm": (b"\x00" * 200 + b"not dicom", "scan.dcm"),
        "garbage-stl": (b"hello world, not a mesh at all", "mesh.stl"),
        "stl-as-png": (stl_binary(), "mesh.png"),
        "dicom-as-stl": (dicom(), "scan.stl"),
        "stl-as-dcm": (stl_binary(), "mesh.dcm"),
        "truncated-binary-stl": (stl_binary(12)[:-20], "cut.stl"),
        "count-mismatch-stl": (stl_binary(12)[:80] + struct.pack("<I", 99) + stl_binary(12)[84:], "bad.stl"),
        "ascii-no-facet": (b"solid x\nendsolid x\n", "empty.stl"),
        "ascii-bad-vertex": (b"solid x\nfacet normal 0 0 1\nouter loop\nvertex a b c\nvertex 1 1 1\nvertex 2 2 2\nendloop\nendfacet\nendsolid x\n", "nan.stl"),
        "dicom-without-preamble": (dicom(preamble=False), "nopre.dcm"),
        "dicom-truncated-pixels": (dicom()[:-40], "cut.dcm"),
    }
    for label, (content, name) in cases.items():
        response = await up(client, ctx, pid, content, name)
        assert response.status_code in (415, 422), (label, response.status_code, response.text)
    assert await doc_count(_s) == 0 and stored_files(store) == []


async def test_category_must_match_the_format(env):
    client, _s, _store = env
    ctx, pid = await setup(client)
    assert (await up(client, ctx, pid, stl_binary(), "m.stl", category="document")).status_code == 422
    assert (await up(client, ctx, pid, dicom(), "s.dcm", category="radiograph")).status_code == 422
    assert (await up(client, ctx, pid, png(), "p.png", category="imaging")).status_code == 422
    assert (await up(client, ctx, pid, png(), "p.png", category="photo")).status_code == 201  # T11 flow unchanged


async def test_size_limit_for_imaging_comes_from_configuration_with_clear_message(env, monkeypatch):
    client, _s, store = env
    ctx, pid = await setup(client)
    limits = (await client.get("/api/documents/limits", headers=ctx["auth"])).json()
    assert settings.imaging_max_bytes == 100 * 1024 * 1024 and limits["imaging_max_bytes"] == 100 * 1024 * 1024
    assert ".stl" in limits["imaging_extensions"] and ".dcm" in limits["imaging_extensions"]
    assert limits["max_bytes"] == 20 * 1024 * 1024  # T11 limit untouched
    monkeypatch.setattr(settings, "imaging_max_bytes", 4096)
    big = stl_binary(200)  # ~10 KB
    declared = await up(client, ctx, pid, big, "mare.stl")
    assert declared.status_code == 413 and "limita" in declared.json()["detail"]

    async def chunked():
        for start in range(0, len(big), 512):
            yield big[start:start + 512]

    headers = {**ctx["auth"], "Content-Type": "application/octet-stream", "X-File-Name": "mare.stl"}
    streamed = await client.post(f"/api/documents/patients/{pid}", params={"category": "imaging"}, headers=headers, content=chunked())
    assert streamed.status_code == 413 and "limita" in streamed.json()["detail"]
    assert stored_files(store) == []
    # the larger imaging limit does not relax the T11 limit for photos
    monkeypatch.setattr(settings, "imaging_max_bytes", 100 * 1024 * 1024)
    monkeypatch.setattr(settings, "document_max_bytes", 1024)
    assert (await up(client, ctx, pid, png() + b"\0" * 2048, "p.png", category="photo")).status_code == 413
    assert (await up(client, ctx, pid, stl_binary(200), "ok.stl")).status_code == 201


async def test_rights_follow_t11_and_other_clinic_gets_404(env):
    client, sessions, _store = env
    ctx, pid = await setup(client)
    foreign, _ = await setup(client)
    created = (await up(client, ctx, pid, stl_binary(), "m.stl")).json()
    dicom_doc = (await up(client, ctx, pid, dicom(), "s.dcm")).json()
    clinic_id = ctx["me"]["clinic_id"]
    doctor = await add_user(sessions, clinic_id, "doctor")
    reception = await add_user(sessions, clinic_id, "reception")
    assistant = await add_user(sessions, clinic_id, "assistant")
    for doc in (created, dicom_doc):
        assert (await client.get(f"/api/documents/{doc['id']}/content", headers=doctor)).status_code == 200
        for denied in (reception, assistant):
            assert (await client.get(f"/api/documents/{doc['id']}/content", headers=denied)).status_code == 403
            assert (await client.get(f"/api/documents/{doc['id']}", headers=denied)).status_code == 403
        assert (await client.get(f"/api/documents/{doc['id']}/content", headers=foreign["auth"])).status_code == 404
        assert (await client.get(f"/api/documents/{doc['id']}", headers=foreign["auth"])).status_code == 404
        assert (await client.get(f"/api/documents/{doc['id']}/content")).status_code in (401, 403)  # no public URL
    for denied in (reception, assistant):
        assert (await client.get(f"/api/documents/patients/{pid}", headers=denied)).status_code == 403
        assert (await up(client, {"auth": denied}, pid, stl_binary(), "x.stl")).status_code == 403
    listing = (await client.get(f"/api/documents/patients/{pid}", headers=doctor)).json()
    assert {item["imaging"]["kind"] for item in listing} == {"stl", "dicom"}
    assert (await up(client, foreign, pid, stl_binary(), "x.stl")).status_code == 404
