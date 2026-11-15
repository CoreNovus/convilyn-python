"""``convilyn rag bench``: the shipped demo and a run on your own corpus."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from convilyn import rag
from convilyn.cli.main import cli
from convilyn.rag import bench
from convilyn.rag.bench._sample import demo


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any connection beyond this machine fails the test.

    Loopback stays open: on Windows an event loop wakes itself through a
    socket pair connected to 127.0.0.1.
    """
    connect = socket.socket.connect

    def local_only(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1"):
            raise AssertionError("the demo opened a network connection")
        connect(self, address)  # type: ignore[arg-type]

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the demo opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", local_only)
    monkeypatch.setattr(socket, "create_connection", refuse)


class TestTheShippedDemo:
    def test_every_question_in_the_demo_is_valid_and_runs(self) -> None:
        report = demo()

        assert (len(report.results), report.drafts) == (23, 0)

    def test_the_demo_covers_both_languages_with_answerable_questions(self) -> None:
        groups = demo().groups

        assert all(groups[name]["ndcg@10"] is not None for name in ("en", "zh-TW"))

    def test_keyword_search_finds_most_answers_in_the_demo(self) -> None:
        """A floor, not a target: a regression in search shows up here first."""
        recall = demo().metrics["recall@10"]

        assert recall is not None and recall >= 0.9


class TestDemoCommand:
    def test_the_demo_writes_a_summary_and_a_report_without_the_network(
        self, runner: CliRunner, tmp_path: Path, no_network: None
    ) -> None:
        out = tmp_path / "bench-demo"

        result = runner.invoke(cli, ["rag", "bench", "demo", "--out", str(out)])

        assert (result.exit_code, (out / "summary.md").read_text(encoding="utf-8")[:18]) == (
            0,
            "# Retrieval report",
        )

    def test_the_report_file_reads_back_as_a_report(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        runner.invoke(cli, ["rag", "bench", "demo", "--out", str(tmp_path)])

        data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))

        assert len(bench.Report.model_validate(data).results) == 23

    def test_the_command_says_where_the_files_are(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(cli, ["rag", "bench", "demo", "--out", str(tmp_path)])

        assert "summary.md" in result.output and "ndcg@10" in result.output


class TestRunCommand:
    def corpus_files(self, directory: Path) -> tuple[Path, Path]:
        index = directory / "kb.sqlite3"
        with rag.open(store=rag.store("sqlite", path=index)) as corpus:
            corpus.add_markdown("# Refunds\n\nRefunds take five days.\n", source_id="refunds")
        config = directory / "rag.toml"
        config.write_text('[store]\nkind = "sqlite"\npath = "kb.sqlite3"\n', encoding="utf-8")
        questions = directory / "questions.jsonl"
        line = {
            "id": "q1",
            "question": "refunds",
            "support": [{"source_id": "refunds", "excerpt": "five days"}],
        }
        questions.write_text(json.dumps(line) + "\n", encoding="utf-8")
        return config, questions

    def test_a_run_on_your_own_corpus_writes_its_report(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        config, questions = self.corpus_files(tmp_path)
        out = tmp_path / "out"

        result = runner.invoke(
            cli,
            [
                "rag",
                "bench",
                "run",
                "--config",
                str(config),
                "--questions",
                str(questions),
                "--out",
                str(out),
            ],
        )

        assert (result.exit_code, "ndcg@10 1.000 over 1 questions" in result.output) == (0, True)

    def test_a_bad_settings_file_is_a_usage_error(self, runner: CliRunner, tmp_path: Path) -> None:
        _, questions = self.corpus_files(tmp_path)
        config = tmp_path / "bad.toml"
        config.write_text("[stores]\n", encoding="utf-8")

        result = runner.invoke(
            cli, ["rag", "bench", "run", "--config", str(config), "--questions", str(questions)]
        )

        assert (result.exit_code, "error:" in result.output, "unknown table" in result.output) == (
            1,
            True,
            True,
        )

    def test_both_files_are_required(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["rag", "bench", "run"])

        assert result.exit_code == 2


class TestFailures:
    """Every failure is one fixed line and an exit code from the CLI's table."""

    def files(self, directory: Path) -> tuple[Path, Path]:
        return TestRunCommand().corpus_files(directory)

    def run(self, runner: CliRunner, config: Path, questions: Path, out: Path):  # type: ignore[no-untyped-def]
        arguments = ["--config", str(config), "--questions", str(questions), "--out", str(out)]
        return runner.invoke(cli, ["rag", "bench", "run", *arguments])

    def test_an_output_that_cannot_be_written_is_a_job_failure(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        config, questions = self.files(tmp_path)
        out = tmp_path / "out"
        (out / "report.json").mkdir(parents=True)

        result = self.run(runner, config, questions, out)

        assert (result.exit_code, "error: could not write the report" in result.output) == (
            3,
            True,
        )

    def test_a_link_where_an_output_goes_is_replaced_not_followed(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        config, questions = self.files(tmp_path)
        out, elsewhere = tmp_path / "out", tmp_path / "elsewhere.md"
        out.mkdir()
        elsewhere.write_text("kept", encoding="utf-8")
        try:
            (out / "summary.md").symlink_to(elsewhere)
        except OSError:
            pytest.skip("this machine does not allow creating symbolic links")

        self.run(runner, config, questions, out)

        assert (elsewhere.read_text(encoding="utf-8"), (out / "summary.md").is_symlink()) == (
            "kept",
            False,
        )

    def test_an_existing_report_is_replaced_and_no_temporary_file_is_left(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        config, questions = self.files(tmp_path)
        out = tmp_path / "out"
        out.mkdir()
        (out / "summary.md").write_text("old", encoding="utf-8")

        self.run(runner, config, questions, out)

        assert sorted(entry.name for entry in out.iterdir()) == ["report.json", "summary.md"]

    def test_a_failed_rename_removes_its_temporary_file(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        config, questions = self.files(tmp_path)
        out = tmp_path / "out"

        def refused(source: str, target: object) -> None:
            raise PermissionError("in use")

        monkeypatch.setattr(os, "replace", refused)
        result = self.run(runner, config, questions, out)

        assert (result.exit_code, list(out.iterdir())) == (3, [])

    def test_a_failed_write_removes_its_temporary_file(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os

        real = os.fdopen

        class Full:
            """A file whose write fails as on a full disk."""

            def __init__(self, descriptor: int, *args: object, **kwargs: object) -> None:
                self._handle = real(descriptor, *args, **kwargs)  # type: ignore[call-overload]

            def __enter__(self) -> Full:
                return self

            def __exit__(self, *exc: object) -> None:
                self._handle.close()

            def write(self, text: str) -> None:
                raise OSError(28, "No space left on device")

        config, questions = self.files(tmp_path)
        out = tmp_path / "out"
        monkeypatch.setattr(os, "fdopen", Full)

        result = self.run(runner, config, questions, out)

        assert (result.exit_code, list(out.iterdir())) == (3, [])

    def test_demo_files_that_cannot_be_read_are_a_job_failure(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from convilyn.rag.bench import _sample

        def broken() -> None:
            raise FileNotFoundError("missing")

        monkeypatch.setattr(_sample, "demo", broken)
        result = runner.invoke(cli, ["rag", "bench", "demo", "--out", str(tmp_path)])

        assert (result.exit_code, "could not read the demo files" in result.output) == (3, True)

    def test_a_service_that_fails_is_an_api_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from convilyn.providers import ProviderError

        def unreachable(**kwargs: object) -> None:
            raise ProviderError("the request to the provider did not complete", category="network")

        monkeypatch.setattr(bench, "run", unreachable)
        config, questions = self.files(tmp_path)

        result = self.run(runner, config, questions, tmp_path / "out")

        assert result.exit_code == 2

    def test_a_file_that_cannot_be_read_is_a_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def locked(*args: object, **kwargs: object) -> None:
            raise PermissionError("locked")

        config, questions = self.files(tmp_path)
        monkeypatch.setattr(rag, "open", locked)

        result = self.run(runner, config, questions, tmp_path / "out")

        assert (result.exit_code, "could not read the settings file" in result.output) == (1, True)

    def test_vector_search_without_numpy_is_one_line_naming_the_extra(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An ``[embed]`` corpus with numpy absent: one ``error:`` line, no traceback."""

        class Embedder:
            model = "test/constant"

            def embed(self, texts: list[str], *, kind: str) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

        config, questions = self.files(tmp_path)
        index = tmp_path / "vectors.sqlite3"
        opened = rag.open

        def with_vectors(*args: object, **kwargs: object) -> rag.Corpus:
            return opened(store=rag.store("sqlite", path=index), embed=Embedder())

        with with_vectors() as corpus:
            corpus.add_markdown("# Refunds\n\nRefunds take five days.\n", source_id="refunds")
        monkeypatch.setattr(rag, "open", with_vectors)
        monkeypatch.setitem(sys.modules, "numpy", None)

        result = self.run(runner, config, questions, tmp_path / "out")

        assert (result.exit_code, result.output.splitlines()) == (
            1,
            ['error: vector search needs numpy; install it with: pip install "convilyn[rag]"'],
        )

    def test_an_error_never_repeats_a_value_from_the_settings_file(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        config, questions = self.files(tmp_path)
        config.write_text(
            '[embed]\nprovider = "openai"\nmodel = "m"\ndimensions = "PLANTED-VALUE"\n',
            encoding="utf-8",
        )

        result = self.run(runner, config, questions, tmp_path / "out")

        assert (result.exit_code, "PLANTED-VALUE" in result.output) == (1, False)


class TestPinAndCheck:
    """`pin` keeps a standard; `check` exits 0 held, 3 worse, 4 not comparable."""

    def setup_corpus(self, directory: Path, *, limit: int | None = None) -> tuple[Path, Path]:
        from importlib import resources

        demo_folder = resources.files("convilyn.rag.bench").joinpath("_demo")
        index = directory / "kb.sqlite3"
        if not index.exists():
            with rag.open(store=rag.store("sqlite", path=index)) as corpus:
                for entry in sorted(demo_folder.iterdir(), key=lambda item: item.name):
                    if entry.name.endswith(".md"):
                        corpus.add_markdown(
                            entry.read_text(encoding="utf-8"), source_id=entry.name[:-3]
                        )
        config = directory / "rag.toml"
        text = '[store]\nkind = "sqlite"\npath = "kb.sqlite3"\n'
        if limit is not None:
            text += f"\n[recipe.search]\nlimit = {limit}\n"
        config.write_text(text, encoding="utf-8")
        questions = directory / "questions.jsonl"
        questions.write_bytes(demo_folder.joinpath("questions.jsonl").read_bytes())
        return config, questions

    def pin(self, runner: CliRunner, directory: Path) -> Path:
        config, questions = self.setup_corpus(directory)
        out = directory / "pin.json"
        arguments = ["--config", str(config), "--questions", str(questions), "--out", str(out)]
        result = runner.invoke(cli, ["rag", "bench", "pin", *arguments])
        assert result.exit_code == 0, result.output
        return out

    def check(self, runner: CliRunner, directory: Path, pin: Path):  # type: ignore[no-untyped-def]
        config, questions = directory / "rag.toml", directory / "questions.jsonl"
        arguments = ["--config", str(config), "--questions", str(questions), "--pin", str(pin)]
        return runner.invoke(cli, ["rag", "bench", "check", *arguments])

    def test_pin_writes_a_pin_that_reads_back(self, runner: CliRunner, tmp_path: Path) -> None:
        pin = self.pin(runner, tmp_path)

        assert bench.Pin.model_validate_json(pin.read_bytes()).metric == "ndcg@10"

    def test_checking_the_same_settings_holds(self, runner: CliRunner, tmp_path: Path) -> None:
        pin = self.pin(runner, tmp_path)

        result = self.check(runner, tmp_path, pin)

        assert (result.exit_code, "# Pin check: held" in result.output) == (0, True)

    def test_a_setting_that_returns_one_passage_fails_the_check(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        pin = self.pin(runner, tmp_path)
        self.setup_corpus(tmp_path, limit=1)

        result = self.check(runner, tmp_path, pin)

        assert (result.exit_code, "# Pin check: worse" in result.output) == (3, True)

    def test_an_edited_question_file_is_not_comparable(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        pin = self.pin(runner, tmp_path)
        questions = tmp_path / "questions.jsonl"
        lines = questions.read_text(encoding="utf-8").splitlines()
        questions.write_text("\n".join(lines[1:]) + "\n", encoding="utf-8")

        result = self.check(runner, tmp_path, pin)

        assert (result.exit_code, "the question set changed" in result.output) == (4, True)

    def test_a_file_that_is_not_a_pin_is_a_usage_error_quoting_nothing(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        self.setup_corpus(tmp_path)
        bad = tmp_path / "bad.json"
        bad.write_text('{"format": 1, "metric": "PLANTED-VALUE"}', encoding="utf-8")

        result = self.check(runner, tmp_path, bad)

        assert (result.exit_code, "is not a bench pin" in result.output) == (1, True)
        assert "PLANTED-VALUE" not in result.output

    def test_a_pin_that_cannot_be_read_is_a_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pin = self.pin(runner, tmp_path)

        def locked(self: Path, *args: object, **kwargs: object) -> object:
            raise PermissionError("locked")

        monkeypatch.setattr(Path, "open", locked)
        result = self.check(runner, tmp_path, pin)

        assert (result.exit_code, "could not read the pin" in result.output) == (1, True)

    def test_a_defect_while_pinning_is_raised_not_disguised(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config, questions = self.setup_corpus(tmp_path)

        def broken(*args: object, **kwargs: object) -> None:
            raise ValueError("an invariant failed")

        monkeypatch.setattr(bench, "pin", broken)
        arguments = ["--config", str(config), "--questions", str(questions)]
        result = runner.invoke(cli, ["rag", "bench", "pin", *arguments])

        assert isinstance(result.exception, ValueError)

    def test_a_pin_that_cannot_be_written_is_a_job_failure(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        config, questions = self.setup_corpus(tmp_path)
        blocker = tmp_path / "not-a-directory"
        blocker.write_text("", encoding="utf-8")
        out = blocker / "pin.json"

        arguments = ["--config", str(config), "--questions", str(questions), "--out", str(out)]
        result = runner.invoke(cli, ["rag", "bench", "pin", *arguments])

        assert (result.exit_code, "could not write the pin" in result.output) == (3, True)


class TestPinFileRefusals:
    """A pin that cannot be used is refused naming only the pin's own fields."""

    def check_with(self, runner: CliRunner, tmp_path: Path, text: str):  # type: ignore[no-untyped-def]
        config, questions = TestPinAndCheck().setup_corpus(tmp_path)
        pin = tmp_path / "pin.json"
        pin.write_text(text, encoding="utf-8")
        arguments = ["--config", str(config), "--questions", str(questions), "--pin", str(pin)]
        return runner.invoke(cli, ["rag", "bench", "check", *arguments])

    @pytest.mark.parametrize(
        "text",
        [
            '{"PLANTED-KEY": 1}',
            '{"report": {"PLANTED-KEY": 1}}',
            '{"report": {"results": [{"scores": {"PLANTED-KEY": "x"}}]}}',
        ],
        ids=["unknown-top-level", "unknown-nested", "dict-key"],
    )
    def test_a_key_from_the_file_is_never_repeated(
        self, runner: CliRunner, tmp_path: Path, text: str
    ) -> None:
        result = self.check_with(runner, tmp_path, text)

        assert (result.exit_code, "is not a bench pin" in result.output) == (1, True)
        assert "PLANTED" not in result.output

    def test_an_unknown_top_level_field_is_named_as_such(
        self, runner: CliRunner, tmp_path: Path, standard_text: str
    ) -> None:
        data = json.loads(standard_text)
        data["PLANTED-KEY"] = 1

        result = self.check_with(runner, tmp_path, json.dumps(data))

        assert "(unknown field: Extra inputs are not permitted)" in result.output

    @pytest.fixture
    def standard_text(self, runner: CliRunner, tmp_path: Path) -> str:
        return TestPinAndCheck().pin(runner, tmp_path).read_text(encoding="utf-8")

    def test_a_file_far_larger_than_a_pin_is_refused_unread(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from convilyn.cli import rag as rag_cli

        monkeypatch.setattr(rag_cli, "_LARGEST_PIN", 10)
        result = self.check_with(runner, tmp_path, '{"format": 1, "metric": "ndcg@10"}')

        assert (result.exit_code, "larger than a bench pin can be" in result.output) == (1, True)

    def test_a_settings_file_that_vanishes_before_pinning_is_a_usage_error(
        self, runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config, questions = TestPinAndCheck().setup_corpus(tmp_path)

        def gone(*args: object, **kwargs: object) -> None:
            raise FileNotFoundError("gone")

        monkeypatch.setattr(bench, "pin", gone)
        arguments = ["--config", str(config), "--questions", str(questions)]
        result = runner.invoke(
            cli, ["rag", "bench", "pin", *arguments, "--out", str(tmp_path / "p.json")]
        )

        assert (result.exit_code, "could not read the settings file" in result.output) == (1, True)
