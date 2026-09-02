"""``convilyn agent install`` edits a file the user owns. These are the guards.

The skill half is a file copy and is barely interesting. The TOML half is: it
appends to a config that other tools also write, so every test below is about a
shape that config might already be in, and what must NOT happen to it.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from convilyn.agent.install import (
    PAST_TENSE_ACTIONS,
    SECTION,
    TENSELESS_ACTIONS,
    Step,
    claude_plugin_root,
    codex_config,
    install,
    install_claude_code_plugin,
    install_codex_mcp,
    install_skill,
    mcp_extra_installed,
    payload,
    skill_destination,
    skill_source,
)
from convilyn.cli import agent as agent_cli
from convilyn.cli.main import cli

#: A config with unrelated content in it, in the two shapes Codex actually
#: writes: a sibling server, and a top-level table after it.
EXISTING = '[mcp_servers.other]\ncommand = "other"\n\n[tui]\ntheme = "dark"\n'


@pytest.fixture
def home(tmp_path):
    return tmp_path / "home"


class TestTheSkillIsCopiedVerbatim:
    def test_the_packaged_skill_resolves(self) -> None:
        """Vacuity guard for every test below: they all compare against this
        file, and a resource that failed to resolve would make them compare
        nothing to nothing."""
        assert skill_source().is_file()
        assert len(skill_source().read_bytes()) > 500

    def test_it_lands_where_agents_scan(self, home) -> None:
        step = install_skill(home)
        assert step.action == "created"
        assert step.target == home / ".agents" / "skills" / "convilyn" / "SKILL.md"

    def test_the_bytes_are_identical(self, home) -> None:
        """Byte comparison, not text. ``write_text`` would translate newlines on
        Windows and ship a file that differs from the one in the wheel."""
        install_skill(home)
        assert skill_destination(home).read_bytes() == skill_source().read_bytes()

    def test_re_running_reports_no_change(self, home) -> None:
        install_skill(home)
        assert install_skill(home).changed is False

    def test_a_stale_copy_is_refreshed(self, home) -> None:
        destination = skill_destination(home)
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"an old version")

        step = install_skill(home)

        assert step.action == "updated"
        assert destination.read_bytes() == skill_source().read_bytes()

    def test_dry_run_writes_nothing(self, home) -> None:
        step = install_skill(home, dry_run=True)
        assert step.changed is True
        assert not skill_destination(home).exists()


class TestTheClaudeCodePluginIsWrittenInPlace:
    """Claude Code does not read ``~/.agents/skills`` — it scans
    ``~/.claude/skills``, the project's ``.claude/skills``, the enterprise
    directory, and installed plugins. A folder under a skills directory carrying
    ``.claude-plugin/plugin.json`` loads as ``convilyn@skills-dir`` with no
    marketplace and no install step, which is what makes "one command" true for
    that host rather than a claim about a different one.
    """

    def test_the_packaged_manifests_resolve(self) -> None:
        """Vacuity guard. Every assertion below compares against these, and a
        resource that failed to resolve would make them compare nothing to
        nothing. They are also what the wheel must actually carry — the whole
        route reads them out of the installed package."""
        assert payload("plugin.json").is_file()
        assert payload("mcp.json").is_file()
        assert payload("SKILL.md").is_file()

    def test_it_writes_the_three_files_the_host_needs(self, home) -> None:
        install_claude_code_plugin(home)
        root = claude_plugin_root(home)

        assert (root / ".claude-plugin" / "plugin.json").is_file()
        assert (root / ".mcp.json").is_file()
        assert (root / "SKILL.md").is_file()

    def test_the_skill_sits_at_the_plugin_root_not_under_skills(self, home) -> None:
        """A plugin shipping exactly one skill may put ``SKILL.md`` at its root,
        which is what ``claude plugin init`` scaffolds. The nested layout would
        name the command ``/convilyn:convilyn``."""
        install_claude_code_plugin(home)

        assert not (claude_plugin_root(home) / "skills").exists()

    def test_the_manifests_are_byte_identical_to_the_wheel_copy(self, home) -> None:
        install_claude_code_plugin(home)
        root = claude_plugin_root(home)

        assert (root / ".claude-plugin" / "plugin.json").read_bytes() == payload(
            "plugin.json"
        ).read_bytes()
        assert (root / ".mcp.json").read_bytes() == payload("mcp.json").read_bytes()

    def test_no_credential_is_written(self, home) -> None:
        """``convilyn setup`` already stores the key where the CLI finds it. A
        config file gets copied between machines and pasted into issues."""
        install_claude_code_plugin(home)
        text = (claude_plugin_root(home) / ".mcp.json").read_text(encoding="utf-8").lower()

        assert "env" not in text
        assert "key" not in text
        assert "token" not in text

    def test_re_running_reports_unchanged(self, home) -> None:
        install_claude_code_plugin(home)

        assert install_claude_code_plugin(home).action == "unchanged"

    def test_a_half_written_tree_is_repaired(self, home) -> None:
        """``unchanged`` requires every file to match. One deleted file is a
        half-installed plugin, not partial progress — the host would load a
        manifest with no skill behind it."""
        install_claude_code_plugin(home)
        (claude_plugin_root(home) / ".mcp.json").unlink()

        step = install_claude_code_plugin(home)

        assert step.action == "updated"
        assert (claude_plugin_root(home) / ".mcp.json").is_file()

    def test_dry_run_writes_nothing(self, home) -> None:
        step = install_claude_code_plugin(home, dry_run=True)

        assert step.changed is True
        assert not claude_plugin_root(home).exists()


class TestTheCodexConfigIsMergedNotOverwritten:
    def test_it_creates_the_file_when_absent(self, home) -> None:
        step = install_codex_mcp(home)
        assert step.action == "created"
        assert SECTION in codex_config(home).read_text(encoding="utf-8")

    def test_existing_content_survives(self, home) -> None:
        """The one that matters. Everything else is convenience; this is the
        difference between a helpful command and one that eats a config."""
        config = codex_config(home)
        config.parent.mkdir(parents=True)
        config.write_text(EXISTING, encoding="utf-8")

        install_codex_mcp(home)
        after = config.read_text(encoding="utf-8")

        assert EXISTING in after, "pre-existing configuration was not preserved verbatim"
        assert "[mcp_servers.other]" in after
        assert 'theme = "dark"' in after
        assert SECTION in after

    def test_it_appends_rather_than_prepends(self, home) -> None:
        """A ``[table]`` header terminates the table above it. Written at the
        top, our block would swallow the file's own first table."""
        config = codex_config(home)
        config.parent.mkdir(parents=True)
        config.write_text(EXISTING, encoding="utf-8")

        install_codex_mcp(home)

        after = config.read_text(encoding="utf-8")
        assert after.index("[mcp_servers.other]") < after.index(SECTION)

    def test_a_file_without_a_trailing_newline_is_still_valid(self, home) -> None:
        config = codex_config(home)
        config.parent.mkdir(parents=True)
        config.write_text('[tui]\ntheme = "dark"', encoding="utf-8")

        install_codex_mcp(home)

        assert 'theme = "dark"\n' in config.read_text(encoding="utf-8")

    def test_re_running_changes_nothing(self, home) -> None:
        install_codex_mcp(home)
        before = codex_config(home).read_text(encoding="utf-8")

        step = install_codex_mcp(home)

        assert step.changed is False
        assert codex_config(home).read_text(encoding="utf-8") == before

    def test_an_inline_table_is_refused_not_mangled(self, home) -> None:
        """``mcp_servers = {...}`` cannot take an appended section — the result
        would be a config that no longer parses. Refuse and say what to do."""
        config = codex_config(home)
        config.parent.mkdir(parents=True)
        original = 'mcp_servers = { other = { command = "other" } }\n'
        config.write_text(original, encoding="utf-8")

        step = install_codex_mcp(home)

        assert step.action == "refused"
        assert step.changed is False
        assert config.read_text(encoding="utf-8") == original
        assert "inline table" in step.detail

    def test_dry_run_writes_nothing(self, home) -> None:
        step = install_codex_mcp(home, dry_run=True)
        assert step.changed is True
        assert not codex_config(home).exists()


class TestTheConfigStaysParseable:
    """The tests above assert on text. This one asserts on meaning.

    ``tomllib`` is 3.11+, and this package supports 3.10 — so the *installer*
    cannot use it, but the *test* can, on the interpreters that have it. Skipped
    below 3.11 rather than dropped: a check that runs on most machines beats no
    check at all, and it is exactly the check that catches a malformed append.
    """

    @pytest.fixture
    def tomllib(self):
        return pytest.importorskip("tomllib")

    def test_a_fresh_config_parses(self, home, tomllib) -> None:
        install_codex_mcp(home)
        parsed = tomllib.loads(codex_config(home).read_text(encoding="utf-8"))
        assert parsed["mcp_servers"]["convilyn"]["command"] == "convilyn"
        assert parsed["mcp_servers"]["convilyn"]["args"] == ["mcp", "serve"]

    def test_a_merged_config_parses_and_keeps_the_neighbour(self, home, tomllib) -> None:
        config = codex_config(home)
        config.parent.mkdir(parents=True)
        config.write_text(EXISTING, encoding="utf-8")

        install_codex_mcp(home)
        parsed = tomllib.loads(config.read_text(encoding="utf-8"))

        assert parsed["mcp_servers"]["other"]["command"] == "other"
        assert parsed["mcp_servers"]["convilyn"]["args"] == ["mcp", "serve"]
        assert parsed["tui"]["theme"] == "dark"

    def test_no_credential_is_written_into_the_config(self, home, tomllib) -> None:
        """``convilyn setup`` owns the key. A config file is a worse place for a
        secret: it gets copied between machines and pasted into bug reports."""
        install_codex_mcp(home)
        entry = tomllib.loads(codex_config(home).read_text(encoding="utf-8"))
        assert "env" not in entry["mcp_servers"]["convilyn"]


class TestTheCommand:
    def test_it_installs_both_halves(self, home) -> None:
        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home), "--json"])

        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert {step["action"] for step in payload["steps"]} == {"created"}
        assert skill_destination(home).is_file()
        assert codex_config(home).is_file()

    def test_it_offers_the_marketplace_as_an_alternative_not_as_the_route(self, home) -> None:
        """This used to be keyed ``claude_code`` and printed as *the* Claude Code
        instruction, because nothing the command wrote was read by Claude Code.
        The skills-directory plugin changed that, so the marketplace is now the
        team-distribution alternative — and the key says so, rather than
        continuing to name a host the command already serves directly.
        """
        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home), "--json"])
        payload = json.loads(result.stdout)

        assert payload["marketplace_alternative"] == [
            "/plugin marketplace add CoreNovus/convilyn-python",
            "/plugin install convilyn@convilyn",
        ]
        assert "claude_code" not in payload

    def test_a_refusal_is_a_non_zero_exit(self, home) -> None:
        config = codex_config(home)
        config.parent.mkdir(parents=True)
        config.write_text('mcp_servers = { other = { command = "other" } }\n', encoding="utf-8")

        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home), "--json"])

        assert result.exit_code != 0
        assert json.loads(result.stdout)["ok"] is False

    def test_dry_run_leaves_the_disk_alone(self, home) -> None:
        result = CliRunner().invoke(
            cli, ["agent", "install", "--home", str(home), "--dry-run", "--json"]
        )

        assert result.exit_code == 0
        assert json.loads(result.stdout)["dry_run"] is True
        assert not skill_destination(home).exists()
        assert not codex_config(home).exists()


def test_install_reports_every_destination(tmp_path) -> None:
    """Vacuity guard for the command tests: they assert on a list, and a list
    that silently lost an entry would satisfy most of them.

    Three, not two: Claude Code's skills-directory plugin, Codex's skill file,
    and Codex's MCP config. A host losing its destination is exactly the quiet
    failure this command exists to prevent.
    """
    steps = install(tmp_path / "home", dry_run=True)

    assert len(steps) == 3
    assert {step.target.name for step in steps} == {"convilyn", "SKILL.md", "config.toml"}


class TestItSaysWhenTheServerCannotStartYet:
    """The two halves come apart: the base package writes the config, and the
    server it points at needs an extra the base package does not install.

    Without this the command is green and the failure surfaces inside someone's
    editor, as a server that will not start, with nothing connecting it back to
    the command that registered it.
    """

    def test_it_reports_the_extra_as_present_here(self) -> None:
        """The dev environment has it, so this is the positive control: an
        `mcp_extra_installed` that always returned False would satisfy every
        assertion below on its own."""
        assert mcp_extra_installed() is True

    def test_the_json_carries_the_answer(self, home) -> None:
        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home), "--json"])
        assert json.loads(result.stdout)["mcp_extra_installed"] is True

    def test_a_missing_extra_is_warned_about_not_failed(self, home, monkeypatch) -> None:
        monkeypatch.setattr("convilyn.cli.agent.mcp_extra_installed", lambda: False)

        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home), "--json"])

        assert result.exit_code == 0, "a missing extra is a next step, not a failure"
        assert json.loads(result.stdout)["mcp_extra_installed"] is False

    def test_the_warning_names_the_install_command(self, home, monkeypatch) -> None:
        monkeypatch.setattr("convilyn.cli.agent.mcp_extra_installed", lambda: False)

        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home)])

        assert "convilyn[mcp]" in result.stderr


class TestADryRunReportsInTheConditional:
    """`--dry-run` must not describe changes it did not make.

    The `Step.action` vocabulary is past tense (`created`, `updated`) because it
    describes what a real run did. Printed verbatim under `--dry-run` it told the
    user their machine had changed while nothing was written — measured against
    the built 3.5.0 wheel: three `created:` lines, zero files on disk.

    The JSON `action` field deliberately keeps the single vocabulary, so a caller
    diffing a dry run against a real one compares the same words. Only the human
    line is rewritten.
    """

    def test_the_human_output_says_would_create(self, home) -> None:
        """Asserted on the exact phrase, not a substring of it.

        The first version of this test checked `"would create" in stderr` against
        an implementation that emitted `would created` — and passed, because one
        is a substring of the other. That is the same shape as the two other
        substring assertions this work has had to correct; here the fix is to pin
        the whole word.
        """
        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home), "--dry-run"])

        assert "would create:" in result.stderr, result.stderr
        assert "would created" not in result.stderr, "'would created' is not English"
        assert "\ncreated:" not in result.stderr

    def test_a_real_run_still_says_created(self, home) -> None:
        """Vacuity guard: a renderer that always says "would" is equally wrong."""
        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home)])

        assert "created:" in result.stderr, result.stderr
        assert "would" not in result.stderr

    def test_an_unchanged_destination_is_not_conditional(self, home) -> None:
        """ "would unchanged" is nonsense — only a change is hypothetical."""
        CliRunner().invoke(cli, ["agent", "install", "--home", str(home)])

        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home), "--dry-run"])

        assert "unchanged:" in result.stderr
        assert "would unchanged" not in result.stderr

    def test_the_json_action_vocabulary_is_unchanged(self, home) -> None:
        result = CliRunner().invoke(
            cli, ["agent", "install", "--home", str(home), "--dry-run", "--json"]
        )

        actions = {step["action"] for step in json.loads(result.stdout)["steps"]}
        assert actions, "no steps reported"
        assert not any(action.startswith("would ") for action in actions), actions


class TestTheConditionalCoversTheWholeVocabulary:
    """Every action a real run can report has to be renderable in the conditional.

    `_CONDITIONAL` was a hand-kept pair living beside the renderer, and it
    covered two of the three past-tense actions. The third, `appended`, is what
    `install_codex_mcp` returns when `~/.codex/config.toml` ALREADY EXISTS — so
    the case it got wrong is not a rare one, it is every Codex user, and the
    class above never saw it because all four of its tests start from an empty
    `home`.

    So these tests derive from `install.PAST_TENSE_ACTIONS` rather than naming
    words, and the rendering test starts from a config that is already there.
    """

    def test_it_maps_exactly_the_past_tense_actions(self) -> None:
        assert set(agent_cli._CONDITIONAL) == PAST_TENSE_ACTIONS

    @pytest.mark.parametrize("action", sorted(PAST_TENSE_ACTIONS))
    def test_every_past_tense_action_gets_an_infinitive(self, action: str) -> None:
        phrase = agent_cli._phrase(action, dry_run=True)

        assert phrase.startswith("would ")
        assert not phrase.endswith(("ed", "ted")), f"{phrase!r} is not an infinitive"

    @pytest.mark.parametrize("action", sorted(TENSELESS_ACTIONS))
    def test_a_tenseless_action_is_left_alone(self, action: str) -> None:
        assert agent_cli._phrase(action, dry_run=True) == action

    def test_the_two_sets_do_not_overlap(self) -> None:
        """Vacuity guard for both parametrized tests above: an action in both
        sets would let each of them pass on the other's expectation."""
        assert PAST_TENSE_ACTIONS and TENSELESS_ACTIONS
        assert not (PAST_TENSE_ACTIONS & TENSELESS_ACTIONS)

    def test_a_step_cannot_carry_an_unclassified_action(self, home) -> None:
        """The whole scheme rests on the vocabulary being closed."""
        with pytest.raises(ValueError, match="unknown Step action"):
            Step(home, "vaporised", True)

    def test_a_dry_run_over_an_existing_codex_config_says_would_append(self, home) -> None:
        """The case the class above structurally could not reach."""
        config = codex_config(home)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(EXISTING, encoding="utf-8")

        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home), "--dry-run"])

        assert "would append:" in result.stderr, result.stderr
        assert "\nappended:" not in result.stderr
        assert config.read_text(encoding="utf-8") == EXISTING

    def test_a_real_run_over_an_existing_codex_config_still_says_appended(self, home) -> None:
        """Vacuity guard: a renderer that always says "would" is equally wrong."""
        config = codex_config(home)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(EXISTING, encoding="utf-8")

        result = CliRunner().invoke(cli, ["agent", "install", "--home", str(home)])

        assert "appended:" in result.stderr, result.stderr
        assert "would append" not in result.stderr
