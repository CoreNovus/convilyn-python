"""The public `convilyn.local.pdf` namespace — logic / boundary / failure / state.

The engine's own behaviour is covered upstream; what is asserted here is the
part this namespace adds: that the operations are reachable without a key, that
a refusal arrives as a `LocalError` rather than as whatever the underlying
library raised, and that files land where the caller said.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from convilyn.local import pdf
from convilyn.local.errors import LocalError, PdfOperationError


@pytest.fixture
def document(tmp_path: Path) -> Path:
    """A five-page PDF whose pages are distinguishable by width."""
    writer = PdfWriter()
    for index in range(5):
        writer.add_blank_page(width=100 + index, height=200)
    path = tmp_path / "document.pdf"
    with path.open("wb") as handle:
        writer.write(handle)
    return path


# ── 1. Logic — happy path ────────────────────────────────────────────


class TestOperations:
    def test_page_count_reads_the_document(self, document: Path) -> None:
        assert pdf.page_count(document) == 5

    def test_select_keeps_the_named_pages(self, document: Path, tmp_path: Path) -> None:
        out = pdf.select(document, tmp_path / "out.pdf", pages="1-2,5")

        assert pdf.page_count(out) == 3

    def test_merge_concatenates(self, document: Path, tmp_path: Path) -> None:
        out = pdf.merge([document, document], tmp_path / "out.pdf")

        assert pdf.page_count(out) == 10

    def test_rotate_turns_the_pages(self, document: Path, tmp_path: Path) -> None:
        out = pdf.rotate(document, tmp_path / "out.pdf", degrees=270)

        assert [page.rotation for page in PdfReader(out).pages] == [270] * 5

    def test_compress_preserves_the_document(self, document: Path, tmp_path: Path) -> None:
        out = pdf.compress(document, tmp_path / "out.pdf")

        assert pdf.page_count(out) == 5

    def test_encrypt_then_decrypt_round_trips(self, document: Path, tmp_path: Path) -> None:
        locked = pdf.encrypt(document, tmp_path / "locked.pdf", password="hunter2")
        assert PdfReader(locked).is_encrypted

        opened = pdf.decrypt(locked, tmp_path / "open.pdf", password="hunter2")
        assert not PdfReader(opened).is_encrypted

    def test_a_string_path_is_accepted_everywhere(self, document: Path, tmp_path: Path) -> None:
        """A caller with a string should not have to wrap it in Path first."""
        out = pdf.select(str(document), str(tmp_path / "out.pdf"), pages="1")

        assert isinstance(out, Path)
        assert out.is_file()


class TestBurst:
    def test_it_writes_one_file_per_page(self, document: Path, tmp_path: Path) -> None:
        written = pdf.burst(document, tmp_path / "pages")

        assert [p.name for p in written] == [f"page_{n:03d}.pdf" for n in range(1, 6)]
        assert all(p.is_file() for p in written)

    def test_it_creates_the_directory(self, document: Path, tmp_path: Path) -> None:
        written = pdf.burst(document, tmp_path / "a" / "b" / "pages")

        assert written[0].parent.is_dir()

    def test_a_range_limits_what_is_written(self, document: Path, tmp_path: Path) -> None:
        written = pdf.burst(document, tmp_path / "pages", pages="2-3")

        assert [p.name for p in written] == ["page_002.pdf", "page_003.pdf"]

    def test_each_file_is_a_one_page_pdf(self, document: Path, tmp_path: Path) -> None:
        written = pdf.burst(document, tmp_path / "pages", pages="4")

        assert pdf.page_count(written[0]) == 1


class TestText:
    def test_a_document_with_no_text_layer_returns_empty(self, document: Path) -> None:
        """Not a failure: a scan has no text, and OCR is a different capability."""
        assert pdf.extract_text(document) == ""


# ── 2. Boundary — no key, no network ─────────────────────────────────


class TestNoCredentialsNeeded:
    def test_operations_run_with_no_api_key_configured(
        self, document: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The whole point of the namespace, asserted rather than assumed."""
        monkeypatch.delenv("CONVILYN_API_KEY", raising=False)
        monkeypatch.delenv("CONVILYN_BASE_URL", raising=False)

        assert pdf.page_count(document) == 5
        assert pdf.select(document, tmp_path / "out.pdf", pages="1").is_file()

    # Whether importing this namespace pulls a parser (or a transport) is
    # measured in `tests/packaging/test_offline_engine_packaging.py`, in a
    # fresh subprocess. An in-process version here would report every module
    # this suite has already imported and pass regardless.


# ── 3. Failure — refusals arrive in this taxonomy ────────────────────


class TestRefusals:
    def test_a_bad_page_range_is_a_local_error(self, document: Path, tmp_path: Path) -> None:
        """The engine raises ValueError; a caller of this namespace must not see it."""
        with pytest.raises(LocalError):
            pdf.select(document, tmp_path / "out.pdf", pages="oops")

    def test_the_message_describes_the_input_not_the_parser(
        self, document: Path, tmp_path: Path
    ) -> None:
        with pytest.raises(PdfOperationError) as caught:
            pdf.select(document, tmp_path / "out.pdf", pages="oops")

        assert "invalid literal for int" not in str(caught.value)
        assert "oops" in str(caught.value)

    def test_a_rotation_that_is_not_a_quarter_turn_is_refused(
        self, document: Path, tmp_path: Path
    ) -> None:
        with pytest.raises(PdfOperationError):
            pdf.rotate(document, tmp_path / "out.pdf", degrees=45)

    def test_the_wrong_password_is_refused(self, document: Path, tmp_path: Path) -> None:
        locked = pdf.encrypt(document, tmp_path / "locked.pdf", password="hunter2")

        with pytest.raises(PdfOperationError):
            pdf.decrypt(locked, tmp_path / "out.pdf", password="wrong")

    def test_selecting_no_existing_page_is_refused(self, document: Path, tmp_path: Path) -> None:
        with pytest.raises(PdfOperationError):
            pdf.select(document, tmp_path / "out.pdf", pages="90-95")

    def test_merging_nothing_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(PdfOperationError):
            pdf.merge([], tmp_path / "out.pdf")

    def test_every_public_operation_refuses_in_this_taxonomy(
        self, document: Path, tmp_path: Path
    ) -> None:
        """A new operation that forgot to translate would pass every test above.

        Each of those names one function; this one enumerates the module, so a
        function added without the translation wrapper fails here rather than
        going unnoticed until a user sees a raw library exception.
        """
        takes_a_page_range = {"select", "burst", "extract_text", "rotate"}
        assert takes_a_page_range <= set(pdf.__all__), "a listed name left the module"

        for name in sorted(takes_a_page_range):
            call = getattr(pdf, name)
            arguments = (
                (document,) if name == "extract_text" else (document, tmp_path / f"{name}-output")
            )

            with pytest.raises(LocalError):
                call(*arguments, pages="oops")


class TestMissingDependency:
    def test_the_error_names_the_install_command(
        self, document: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pypdf is installed in CI, so the missing arm needs the probe stubbed."""
        from convilyn.local import pdf as pdf_module
        from convilyn.local.errors import MissingDependencyError

        monkeypatch.setattr(pdf_module, "package_available", lambda _name: False)

        with pytest.raises(MissingDependencyError) as caught:
            pdf.page_count(document)

        assert "convilyn[pdf]" in str(caught.value)
        assert caught.value.requirement.extra == "pdf"

    def test_it_carries_no_fictional_route(
        self, document: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A page operation has no route, and the attribute must not pretend."""
        from convilyn.local import pdf as pdf_module
        from convilyn.local.errors import MissingDependencyError

        monkeypatch.setattr(pdf_module, "package_available", lambda _name: False)

        with pytest.raises(MissingDependencyError) as caught:
            pdf.page_count(document)

        assert caught.value.route is None


# ── 4. Object state — where output lands, what is left alone ─────────


class TestOutputPlacement:
    def test_the_caller_names_the_path(self, document: Path, tmp_path: Path) -> None:
        destination = tmp_path / "named" / "result.pdf"

        assert pdf.select(document, destination, pages="1") == destination
        assert destination.is_file()

    def test_the_source_is_not_modified(self, document: Path, tmp_path: Path) -> None:
        before = document.read_bytes()

        pdf.rotate(document, tmp_path / "out.pdf", degrees=90)

        assert document.read_bytes() == before

    def test_an_operation_cannot_consume_its_own_input(
        self, document: Path, tmp_path: Path
    ) -> None:
        """Chaining two operations must not depend on ordering luck."""
        first = pdf.select(document, tmp_path / "a.pdf", pages="1-3")
        second = pdf.rotate(first, tmp_path / "b.pdf", degrees=90)

        assert first.is_file()
        assert pdf.page_count(second) == 3


# ── 8. The library's own exceptions never reach the caller (#4108) ────


class TestEncryptedSourcesStayInTheTaxonomy:
    """`docs/STABILITY.md` promises this namespace's errors are `LocalError`s.

    Every operation opens the document with `PdfReader` and then touches
    `.pages`, so an encrypted source raises `pypdf.errors.FileNotDecryptedError`
    from inside the library — past the engine's own error class and past
    `ValueError`, which were the only two shapes the five hand-rolled `except`
    lists knew about. Measured before the fix: 7 of the 8 public operations
    leaked it. Only `decrypt` was right, because it checks `is_encrypted` first.

    Parametrised over the whole surface rather than the one operation the bug
    was reported against — the report named `page_count`, and the count was
    seven.
    """

    @pytest.fixture
    def locked(self, document: Path, tmp_path: Path) -> Path:
        out = tmp_path / "locked.pdf"
        pdf.encrypt(document, out, password="s3cret")  # pragma: allowlist secret
        return out

    @pytest.mark.parametrize(
        "operation",
        [
            pytest.param(lambda p, d: pdf.page_count(p), id="page_count"),
            pytest.param(lambda p, d: pdf.extract_text(p), id="extract_text"),
            pytest.param(lambda p, d: pdf.select(p, d / "o.pdf", pages="1"), id="select"),
            pytest.param(lambda p, d: pdf.merge([p], d / "o.pdf"), id="merge"),
            pytest.param(lambda p, d: pdf.rotate(p, d / "o.pdf"), id="rotate"),
            pytest.param(lambda p, d: pdf.compress(p, d / "o.pdf"), id="compress"),
            pytest.param(lambda p, d: pdf.burst(p, d / "b"), id="burst"),
        ],
    )
    def test_an_encrypted_source_raises_this_namespaces_error(
        self, locked: Path, tmp_path: Path, operation
    ) -> None:
        with pytest.raises(PdfOperationError):
            operation(locked, tmp_path)

    def test_decrypt_with_the_wrong_password_still_reports_that(
        self, locked: Path, tmp_path: Path
    ) -> None:
        """The one operation that was already correct — kept so a fix that
        routed everything through one guard cannot flatten its message."""
        with pytest.raises(PdfOperationError, match="Incorrect password"):
            pdf.decrypt(locked, tmp_path / "out.pdf", password="wrong")  # pragma: allowlist secret

    def test_the_fixture_really_is_encrypted(self, locked: Path) -> None:
        """Non-vacuity: every assertion above passes trivially if `encrypt`
        silently produced an unprotected copy, because the operations would
        simply succeed and never raise at all."""
        assert PdfReader(locked).is_encrypted

    def test_a_malformed_page_range_is_still_a_local_error(self, document: Path) -> None:
        """The other shape the shared guard has to keep catching. Two of the
        five hand-rolled sites had already forgotten this one."""
        with pytest.raises(LocalError):
            pdf.extract_text(document, pages="banana")
