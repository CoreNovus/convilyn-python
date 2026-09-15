"""`download_to` will not write a package into a name that denies being one.

A conversion whose output carries assets returns a ZIP — the artifact plus an
`assets/` directory — and the natural `to=` for a Markdown conversion is
`something.md`. So the default outcome was a file whose name guarantees Markdown
and whose first two bytes are `PK`, written without a word, with
`result_files[0].mimetype == "application/zip"` sitting in the response unread.

The position is not new to this package. `markdown/images.py::media_type_for`
already states it: *"Naming an unknown stream `.png` produces a file that no
viewer opens under a name promising it should."* `download_to` was doing that
one field over.

New file rather than an addition to `test_convert.py` — 676 lines and climbing,
and the file-size ratchet's instruction is to extract rather than to grow.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx

from convilyn import AsyncConvilyn

API_BASE = "https://api.convilyn.com"


def _job(*, filename: str, mimetype: str) -> dict:
    """A terminal job whose single result file is `filename` / `mimetype`."""
    return {
        "jobId": "job_1",
        "status": "completed",
        "progress": 100,
        "processorType": "document_conversion",
        "sourceFormat": "docx",
        "targetFormat": "md",
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "resultFiles": [
            {
                "filename": filename,
                "size": 12,
                "mimetype": mimetype,
                "url": "https://storage.example.com/out",
            }
        ],
    }


def _mock_job(mock: respx.Router, payload: dict) -> None:
    mock.get(f"{API_BASE}/api/v1/jobs/job_1").mock(return_value=httpx.Response(200, json=payload))


# ── 1. Error — the archive refusal ───────────────────────────────────


class TestAPackageIsNotWrittenUnderAnArtifactName:
    @pytest.mark.asyncio
    async def test_a_zip_result_refuses_a_markdown_destination(self, tmp_path) -> None:
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="report.zip", mimetype="application/zip"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="produced a package"):
                    await client.convert.download_to("job_1", to=tmp_path / "report.md")

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_way_out(self, tmp_path) -> None:
        """A refusal that does not say what to do instead is a dead end — the
        same standard the rest of this resource's errors are held to."""
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="report.zip", mimetype="application/zip"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="to_dir="):
                    await client.convert.download_to("job_1", to=tmp_path / "report.md")

    @pytest.mark.asyncio
    async def test_nothing_is_written_when_it_refuses(self, tmp_path) -> None:
        """The refusal is before the download, not after it."""
        target = tmp_path / "report.md"
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="report.zip", mimetype="application/zip"))
            storage = mock.get("https://storage.example.com/out")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError):
                    await client.convert.download_to("job_1", to=target)

        assert not target.exists()
        assert not storage.called


# ── 2. Logic — every other case still writes ─────────────────────────


class TestOnlyAnArchiveIsRefused:
    """Vacuity guards, and the boundary of the rule.

    A check that refused everything would satisfy the class above completely.
    These say what it must NOT refuse — which is also the design boundary: the
    bytes being what the name promises is the caller's business, and only a
    CONTAINER holding the promised thing is a lie.
    """

    @pytest.mark.asyncio
    async def test_a_zip_result_accepts_a_zip_destination(self, tmp_path) -> None:
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="report.zip", mimetype="application/zip"))
            mock.get("https://storage.example.com/out").mock(
                return_value=httpx.Response(200, content=b"PK\x03\x04stub")
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                landed = await client.convert.download_to("job_1", to=tmp_path / "keep.zip")

        assert landed.read_bytes().startswith(b"PK")

    @pytest.mark.asyncio
    async def test_an_ordinary_result_is_untouched_by_the_check(self, tmp_path) -> None:
        """A PDF into an unconventional extension still writes: the bytes ARE a
        PDF, and only the name is unusual."""
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="report.pdf", mimetype="application/pdf"))
            mock.get("https://storage.example.com/out").mock(
                return_value=httpx.Response(200, content=b"%PDF-1.7")
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                landed = await client.convert.download_to("job_1", to=tmp_path / "report.output")

        assert landed.read_bytes() == b"%PDF-1.7"


# ── 3. Object state — to_dir uses the platform's own name ────────────


class TestToDir:
    @pytest.mark.asyncio
    async def test_it_lands_under_the_platform_filename(self, tmp_path) -> None:
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="report.zip", mimetype="application/zip"))
            mock.get("https://storage.example.com/out").mock(
                return_value=httpx.Response(200, content=b"PK\x03\x04stub")
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                landed = await client.convert.download_to("job_1", to_dir=tmp_path)

        assert landed.name == "report.zip"
        assert landed.parent == tmp_path

    @pytest.mark.asyncio
    async def test_both_is_refused(self, tmp_path) -> None:
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="report.zip", mimetype="application/zip"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(TypeError, match="not both and not neither"):
                    await client.convert.download_to(
                        "job_1", to=tmp_path / "a.zip", to_dir=tmp_path
                    )

    @pytest.mark.asyncio
    async def test_neither_is_refused(self) -> None:
        """The mutual exclusion has to fail in BOTH directions, or `to=` being
        optional silently becomes "download somewhere"."""
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="report.zip", mimetype="application/zip"))
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(TypeError, match="not both and not neither"):
                    await client.convert.download_to("job_1")


# ── 4. Boundary — to_dir contains, whatever the server sent ──────────


class TestToDirContainsWhateverTheServerSent:
    """`to_dir` joins a SERVER-supplied filename, and `/` honours an absolute
    right-hand operand: `Path("/out") / "/etc/passwd"` discards `/out`.

    Not exploitable today — the backend's `_output_filename` strips `/`, `\\`
    and `:` before any of these can form. That sanitiser lives in a different
    tree from this published package, though, with no gate spanning the two,
    so the containment is asserted where the join happens.

    Each case is a filename that ESCAPES under the old join, which is what
    makes these tests discriminating: a fixture like `"evil.txt"` is already
    inside `to_dir` and would pass against the unfixed line.
    """

    @pytest.mark.parametrize(
        "sent",
        [
            "../../evil.md",
            "/tmp/evil.md",
            "a/../../b.md",
            "sub/dir/evil.md",
        ],
    )
    @pytest.mark.asyncio
    async def test_it_lands_directly_under_to_dir(self, tmp_path, sent: str) -> None:
        async with respx.mock as mock:
            _mock_job(mock, _job(filename=sent, mimetype="text/markdown"))
            mock.get("https://storage.example.com/out").mock(
                return_value=httpx.Response(200, content=b"# hi")
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                landed = await client.convert.download_to("job_1", to_dir=tmp_path)

        assert landed.parent == tmp_path
        assert landed.read_bytes() == b"# hi"

    @pytest.mark.asyncio
    async def test_a_drive_absolute_filename_cannot_pick_the_volume(self, tmp_path) -> None:
        """`Path("/out") / "C:/evil.md"` keeps `/out` on POSIX but not on
        Windows, where the same published wheel runs."""
        async with respx.mock as mock:
            _mock_job(mock, _job(filename="C:/evil.md", mimetype="text/markdown"))
            mock.get("https://storage.example.com/out").mock(
                return_value=httpx.Response(200, content=b"# hi")
            )
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                landed = await client.convert.download_to("job_1", to_dir=tmp_path)

        assert landed == tmp_path / "evil.md"

    @pytest.mark.parametrize("sent", ["..", ".", "", "/"])
    @pytest.mark.asyncio
    async def test_a_filename_that_names_no_file_is_refused(self, tmp_path, sent: str) -> None:
        """The residue that taking the final component does NOT contain.

        `Path("..").name` is `".."`, not empty — so `to_dir / ".."` is still
        the parent directory. The other three collapse onto `to_dir` itself.
        None is a filename, so none reaches the writer.
        """
        async with respx.mock as mock:
            _mock_job(mock, _job(filename=sent, mimetype="text/markdown"))
            storage = mock.get("https://storage.example.com/out")
            async with AsyncConvilyn(api_key="ck_test") as client:  # pragma: allowlist secret
                with pytest.raises(ValueError, match="names no file"):
                    await client.convert.download_to("job_1", to_dir=tmp_path)

        assert not storage.called
        assert list(tmp_path.iterdir()) == []
