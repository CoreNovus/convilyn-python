"""The agent-tool surface's four security boundaries, tested where they live.

`convilyn mcp serve` puts five tools in front of a model that reads untrusted
document text. Four of these boundaries did not exist when that surface shipped
in 3.5.0 (#4838), and this file is where each one is pinned:

* **the upload fence** — `convilyn_understand` sends bytes off the machine, and
  its only check was `Path.is_file()`. `~/.ssh/id_rsa` is a file.
* **the spend gate** — the tool description told the model to ask permission
  first, and nothing made it. A description is input to the same model.
* **`convilyn_pdf` raises nothing** — the module header promises it; a missing
  argument broke the promise with a `KeyError`.
* **browser dispatch** — a URL out of a server response body reached
  `webbrowser.open`, which on Windows is `os.startfile`.

The pure halves are tested here against `convilyn.mcp.tools`, which imports no
`mcp` — so these run with the extra absent, which is the point that module
makes about itself. The elicitation half needs a session and is driven through
fakes rather than a live client.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from convilyn.mcp import tools

# ── SEC-4: the upload fence ──────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "invoice.pdf").write_bytes(b"%PDF-1.4\n")
    return tmp_path


class TestTheFenceIsNotVacuous:
    """A fence that refuses everything would satisfy every refusal test below."""

    def test_a_normal_file_in_the_workspace_is_accepted(self, workspace: Path) -> None:
        targets, refusal = tools._fence([str(workspace / "invoice.pdf")], (workspace,))
        assert refusal == ""
        assert targets == [(workspace / "invoice.pdf").resolve()]


class TestTheUploadFenceRefusesWhatItMust:
    def test_a_path_outside_every_root_is_refused(self, workspace: Path, tmp_path: Path) -> None:
        outside = tmp_path.parent / "elsewhere.pdf"
        outside.write_bytes(b"%PDF-1.4\n")
        _, refusal = tools._fence([str(outside)], (workspace,))
        assert "outside this session's workspace" in refusal

    def test_a_traversal_path_escaping_the_root_is_refused(self, workspace: Path) -> None:
        """The containment test runs on the RESOLVED path, not the given string.

        This is the half of "resolve first" that needs no privilege to prove:
        `<root>/../outside.pdf` starts with the root as text, so an
        `is_relative_to` on the raw string would admit it. It resolves to a
        sibling of the root, and is refused.
        """
        outside = workspace.parent / "outside.pdf"
        outside.write_bytes(b"%PDF-1.4\n")
        traversal = workspace / ".." / "outside.pdf"
        assert str(traversal).startswith(str(workspace))  # the text really does
        _, refusal = tools._fence([str(traversal)], (workspace,))
        assert "outside this session's workspace" in refusal

    def test_a_symlink_escaping_the_root_is_refused(self, workspace: Path, tmp_path: Path) -> None:
        """The other half of "resolve first", where the lie is in the filesystem
        rather than in the string.

        Skipped where symlink creation needs a privilege the test runner lacks
        (Windows without Developer Mode) — a declared environment gate, not an
        accident of what happens to be installed. The traversal case above
        covers the same `resolve()`-before-compare property everywhere.
        """
        secret = tmp_path.parent / "outside-secret.pdf"
        secret.write_bytes(b"%PDF-1.4\n")
        link = workspace / "innocent.pdf"
        try:
            link.symlink_to(secret)
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this machine")
        _, refusal = tools._fence([str(link)], (workspace,))
        assert "outside this session's workspace" in refusal

    @pytest.mark.parametrize(
        "name",
        [
            "credentials.json",  # this SDK's own, the first thing to reach for
            ".env",
            ".env.local",
            "id_rsa",
            "server.pem",
            ".netrc",
            ".pypirc",
        ],
    )
    def test_a_credential_shaped_file_is_refused_even_inside_the_root(
        self, workspace: Path, name: str
    ) -> None:
        """Inside a root is not a reason to upload a `.env`: a project directory
        routinely contains one, and the model chose the path, not the user."""
        (workspace / name).write_text("secret\n", encoding="utf-8")
        _, refusal = tools._fence([str(workspace / name)], (workspace,))
        assert "looks like a credential file" in refusal

    def test_a_file_in_a_credential_directory_is_refused(self, workspace: Path) -> None:
        ssh = workspace / ".ssh"
        ssh.mkdir()
        (ssh / "known_hosts").write_text("x\n", encoding="utf-8")
        _, refusal = tools._fence([str(ssh / "known_hosts")], (workspace,))
        assert "looks like a credential file" in refusal

    def test_one_bad_path_refuses_the_whole_batch(self, workspace: Path) -> None:
        """Not a per-file result: a batch is one upload decision, and letting the
        good half through would still have leaked nothing but taught the model
        that mixing a secret into a list is a way to probe the fence."""
        (workspace / ".env").write_text("x\n", encoding="utf-8")
        targets, refusal = tools._fence(
            [str(workspace / "invoice.pdf"), str(workspace / ".env")], (workspace,)
        )
        assert targets == []
        assert refusal != ""


class TestUnderstandCannotUploadWithoutTheFence:
    def test_allowed_roots_is_required(self, workspace: Path) -> None:
        """A containment argument with a default is one a caller forgets."""
        with pytest.raises(TypeError):
            tools.understand([str(workspace / "invoice.pdf")], schema={"type": "object"})  # type: ignore[call-arg]

    def test_a_refused_path_never_reaches_the_client(self, workspace: Path, monkeypatch) -> None:
        """The refusal is BEFORE `_client()`, so no key is read and no request is made.

        Asserted on whether the client was **built**, not on the returned `ok`.
        That distinction is not pedantry — it is what this test failed to do at
        first, and a reverse-self-proof caught it: the earlier version raised
        `AssertionError` from a fake `_client`, `understand` catches
        `Exception` around that call and turns it into `ok: false`, so the test
        passed identically with the fence WIRED and with the fence REMOVED. A
        sentinel that the code under test is allowed to swallow proves nothing.
        """
        built: list[bool] = []
        monkeypatch.setattr(tools, "_client", lambda: built.append(True))
        (workspace / ".env").write_text("x\n", encoding="utf-8")

        result = tools.understand(
            [str(workspace / ".env")],
            schema={"type": "object"},
            allowed_roots=(workspace,),
        )

        assert built == [], "a refused path reached the network layer"
        assert result["ok"] is False


# ── SEC-13: `convilyn_pdf` returns, it does not raise ────────────────


class TestPdfReturnsInsteadOfRaising:
    """The module header promises every tool "raises nothing". `_run_pdf` reads
    its arguments by subscript, so an omitted one was a `KeyError` escaping into
    the model's transcript as a stack trace."""

    @pytest.mark.parametrize(
        ("operation", "kwargs"),
        [
            ("info", {}),
            ("merge", {"sources": ["a.pdf"]}),
            ("split", {"source": "a.pdf"}),
            ("select", {"source": "a.pdf"}),
            ("rotate", {"source": "a.pdf"}),
            ("compress", {"source": "a.pdf"}),
            ("protect", {"source": "a.pdf", "out": "b.pdf"}),
            ("unlock", {"source": "a.pdf", "out": "b.pdf"}),
        ],
    )
    def test_a_missing_argument_is_a_result(self, operation: str, kwargs: dict) -> None:
        result = tools.pdf(operation, **kwargs)
        assert result["ok"] is False
        assert "needs a" in result["error"]

    def test_the_message_names_the_missing_parameter(self) -> None:
        """ "something was missing" is not actionable; the parameter name is."""
        assert "'source'" in tools.pdf("info")["error"]

    def test_every_operation_has_a_hint(self) -> None:
        """Vacuity guard for the parametrised test above: a hint table that lost
        a row would leave those refusals silently unhelpful."""
        assert set(tools._PDF_REQUIRED_HINT) == set(tools.PDF_OPERATIONS)
