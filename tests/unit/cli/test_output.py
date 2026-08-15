"""Output renderers — JSON vs human — logic / boundary / error / object-state.

These cover the two implementations of the ``OutputRenderer`` Protocol
in isolation; CLI sub-command tests then drive the renderers end-to-end.
"""

from __future__ import annotations

import io
import json

import pytest

from convilyn.cli._output import (
    HumanRenderer,
    JsonRenderer,
    OutputRenderer,
    make_renderer,
)

# ── 1. Logic — renderers do their primary job ───────────────────────


class TestJsonRendererLogic:
    def test_final_emits_single_json_line(self) -> None:
        buf = io.StringIO()
        renderer = JsonRenderer(stdout=buf)
        renderer.final({"a": 1, "b": "hello"})
        emitted = buf.getvalue().strip()
        parsed = json.loads(emitted)
        assert parsed == {"a": 1, "b": "hello"}

    def test_event_is_silent(self) -> None:
        buf = io.StringIO()
        renderer = JsonRenderer(stdout=buf)
        renderer.event("upload", filename="x.docx")
        assert buf.getvalue() == ""

    def test_final_output_is_ascii_only(self) -> None:
        """Non-ASCII in a payload (e.g. a ``✓`` from a job) must be escaped so
        the JSON line is portable across console codepages — writing a raw glyph
        to a non-UTF-8 stdout (Windows cp950) raises UnicodeEncodeError."""
        buf = io.StringIO()
        renderer = JsonRenderer(stdout=buf)
        renderer.final({"msg": "done ✓", "name": "café"})
        emitted = buf.getvalue()
        assert emitted.isascii(), "JSON output must be ASCII-only for portability"
        assert "✓" not in emitted  # the raw glyph, not the escape
        assert json.loads(emitted) == {"msg": "done ✓", "name": "café"}


class TestHumanRendererLogic:
    def test_event_writes_to_stderr(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        renderer = HumanRenderer(stdout=out, stderr=err)
        renderer.event("upload", filename="report.docx", size_bytes=1024)
        assert "Uploading" in err.getvalue()
        assert "report.docx" in err.getvalue()
        assert out.getvalue() == ""

    def test_final_writes_summary_to_stdout(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        renderer = HumanRenderer(stdout=out, stderr=err)
        renderer.final({"output_path": "/tmp/x.pdf", "output_size_bytes": 1024})
        assert "/tmp/x.pdf" in out.getvalue()
        assert err.getvalue() == ""


# ── 2. Boundary — pluggable factory + Protocol conformance ──────────


class TestRendererFactory:
    def test_make_renderer_json_true(self) -> None:
        assert isinstance(make_renderer(json_output=True), JsonRenderer)

    def test_make_renderer_json_false(self) -> None:
        assert isinstance(make_renderer(json_output=False), HumanRenderer)

    def test_both_renderers_satisfy_protocol(self) -> None:
        # runtime_checkable Protocols allow isinstance() checks; this
        # is what binds future renderers to the same contract.
        assert isinstance(JsonRenderer(), OutputRenderer)
        assert isinstance(HumanRenderer(), OutputRenderer)


# ── 3. Error — pathological payloads do not blow up ─────────────────


class TestRendererErrors:
    def test_json_renderer_with_unserialisable_raises(self) -> None:
        renderer = JsonRenderer(stdout=io.StringIO())
        with pytest.raises(TypeError):
            renderer.final({"socket": object()})

    def test_human_renderer_handles_empty_payload(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        renderer = HumanRenderer(stdout=out, stderr=err)
        renderer.final({})
        # Should still emit *something* — a sensible fallback summary.
        assert out.getvalue().strip() != ""


# ── 4. Object-state — repeated calls do not interfere ───────────────


class TestRendererObjectState:
    def test_json_renderer_multiple_events_then_one_final(self) -> None:
        buf = io.StringIO()
        renderer = JsonRenderer(stdout=buf)
        for kind in ("upload", "create", "wait", "download"):
            renderer.event(kind, message="ignored")
        renderer.final({"status": "completed"})
        # Only the final JSON object should appear on stdout.
        emitted = buf.getvalue().strip().splitlines()
        assert len(emitted) == 1
        assert json.loads(emitted[0]) == {"status": "completed"}

    def test_human_renderer_event_then_final_order(self) -> None:
        out = io.StringIO()
        err = io.StringIO()
        renderer = HumanRenderer(stdout=out, stderr=err)
        renderer.event("upload", filename="x.docx", size_bytes=1)
        renderer.event("create", job_id="job_xyz")
        renderer.final({"output_path": "y.pdf", "output_size_bytes": 1})
        # Two events on stderr (separate lines), one summary on stdout.
        assert len(err.getvalue().strip().splitlines()) == 2
        assert len(out.getvalue().strip().splitlines()) == 1


class TestNarrowConsoleCodepage:
    """A console that cannot encode the CLI's glyphs must not crash it.

    Windows leaves plenty of consoles on `cp437` or `cp850`, neither of which
    can represent `✓` or an em dash. Python raises `UnicodeEncodeError` from
    inside `print` rather than dropping the character, so before this guard a
    finished conversion ended in a traceback — after the output file had
    already been written.
    """

    @staticmethod
    def _narrow() -> tuple[io.TextIOWrapper, io.BytesIO]:
        raw = io.BytesIO()
        return io.TextIOWrapper(raw, encoding="cp437", newline="\n"), raw

    def test_the_stream_really_cannot_encode_the_glyph(self) -> None:
        """Without this, the two tests below prove nothing."""
        stream, _raw = self._narrow()

        with pytest.raises(UnicodeEncodeError):
            print("✓", file=stream)

    def test_an_event_survives_as_an_escape(self) -> None:
        stream, raw = self._narrow()

        HumanRenderer(stdout=stream, stderr=stream).event("ok", message="Wrote a — b")
        stream.flush()

        written = raw.getvalue().decode("cp437")
        assert "Wrote a" in written
        # Both directions: the character itself is gone, and the escape naming
        # it is present. Asserting only the second would also pass if the line
        # had been dropped and something else printed "u2014".
        assert chr(0x2014) not in written
        assert "u2014" in written

    def test_a_summary_survives_too(self) -> None:
        stream, raw = self._narrow()

        HumanRenderer(stdout=stream, stderr=stream).final({"summary": "Converted 1 file ✓"})
        stream.flush()

        assert "Converted 1 file" in raw.getvalue().decode("cp437")
