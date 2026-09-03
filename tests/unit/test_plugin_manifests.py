"""The plugin ships three manifests and a skill. These keep them true.

Modelled on ``sdk/doc-eval``'s equivalent, which is the repository's proven
shape for this. The additions here are the ones this plugin needs and that one
does not: it ships an MCP server, so the config that starts it is checked
against the CLI that has to answer.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from convilyn import __version__

REPO = Path(__file__).resolve().parents[2]
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
PLUGIN_DIR = REPO / "plugins" / "convilyn"
PLUGIN = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
MCP_CONFIG = PLUGIN_DIR / ".mcp.json"
SKILL = PLUGIN_DIR / "skills" / "convilyn" / "SKILL.md"
CANONICAL_SKILL = REPO / "src" / "convilyn" / "agent" / "SKILL.md"
GENERATOR = REPO / "scripts" / "build_plugin.py"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def marketplace() -> dict:
    return _load(MARKETPLACE)


@pytest.fixture
def entry(marketplace: dict) -> dict:
    return marketplace["plugins"][0]


@pytest.fixture
def plugin() -> dict:
    return _load(PLUGIN)


class TestTheManifestsExistAndParse:
    @pytest.mark.parametrize("path", [MARKETPLACE, PLUGIN, MCP_CONFIG, SKILL])
    def test_it_is_on_disk(self, path: Path) -> None:
        assert path.is_file(), f"{path.relative_to(REPO)} is missing"

    def test_the_marketplace_holds_exactly_one_plugin(self, marketplace: dict) -> None:
        """Vacuity guard: every test below reads ``plugins[0]``, and an empty
        list would make them all error rather than assert — but a second entry
        would make them silently check only half the file."""
        assert len(marketplace["plugins"]) == 1

    def test_the_marketplace_declares_its_schema(self, marketplace: dict) -> None:
        assert marketplace["$schema"].endswith("claude-code-marketplace.json")


class TestTheTwoManifestsAgree:
    @pytest.mark.parametrize("field", ["name", "version", "description"])
    def test_the_field_matches(self, entry: dict, plugin: dict, field: str) -> None:
        assert entry[field] == plugin[field], f"{field} differs between the two manifests"

    def test_the_version_matches_the_python_package(self, plugin: dict) -> None:
        """Four copies of the version exist — ``_version.py``, both manifests,
        and the skill's frontmatter. This pins them to the one that ships."""
        assert plugin["version"] == __version__

    def test_the_marketplace_version_matches_too(self, marketplace: dict) -> None:
        assert marketplace["version"] == __version__


class TestEveryDeclaredPathExists:
    def test_the_source_directory_exists(self, entry: dict) -> None:
        assert (REPO / entry["source"].lstrip("./")).is_dir()

    def test_the_skill_sits_where_default_discovery_looks(self) -> None:
        """``skills/<name>/SKILL.md`` at the plugin root is the documented
        default location, so nothing has to declare it."""
        on_disk = {p.parent.name for p in PLUGIN_DIR.glob("skills/*/SKILL.md")}

        assert on_disk == {"convilyn"}

    def test_the_entry_declares_no_skills(self, entry: dict) -> None:
        """The entry used to carry ``skills: ["./skills/convilyn"]``, naming the
        path default discovery already finds. Redundant is the best case: the
        plugin docs note that for some ``source`` shapes a declaration
        *replaces* the default scan rather than adding to it, so it traded a
        failure mode for nothing.

        Verified rather than reasoned — installing this marketplace from a local
        path into a scratch HOME resolves the skill with no declaration present.
        """
        assert "skills" not in entry

    def test_it_ships_no_commands_directory(self) -> None:
        """Commands are declared, not discovered. An undeclared ``commands/``
        directory is dead weight in every install."""
        assert not (PLUGIN_DIR / "commands").exists()


class TestTheSkillIsGeneratedAndInStep:
    def test_the_generator_reports_no_drift(self) -> None:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_copy_says_it_is_generated(self) -> None:
        assert "Generated from src/convilyn/agent/SKILL.md" in SKILL.read_text(encoding="utf-8")

    def test_the_canonical_copy_does_not(self) -> None:
        """Otherwise the banner would be in the file the wheel ships, telling a
        PyPI user their own skill file was generated from somewhere else."""
        assert "Generated from" not in CANONICAL_SKILL.read_text(encoding="utf-8")


class TestTheManifestsAreGeneratedToo:
    """The two JSON manifests carry no "generated" banner — JSON has no comments,
    and an extra key is not free: ``claude plugin validate --strict`` reports an
    unrecognized field as an error. So byte-equality with the canonical payload
    is the only thing standing between them and a second hand-maintained copy.

    Before the generator owned them they *were* hand-maintained, and adding the
    third consumer (``~/.claude/skills/``) would have made three.
    """

    @pytest.mark.parametrize(
        ("canonical", "generated"),
        [
            ("plugin.json", PLUGIN_DIR / ".claude-plugin" / "plugin.json"),
            ("mcp.json", PLUGIN_DIR / ".mcp.json"),
        ],
    )
    def test_the_generated_manifest_matches_the_canonical_payload(
        self, canonical: str, generated: Path
    ) -> None:
        source = CANONICAL_SKILL.parent / canonical

        assert source.is_file(), f"canonical payload missing: {source}"
        assert generated.read_bytes() == source.read_bytes()

    def test_the_canonical_payload_is_where_the_wheel_will_carry_it(self) -> None:
        """``install.py`` reads these out of the INSTALLED package, so they have
        to live under ``src/convilyn/`` — the wheel target is
        ``packages = ["src/convilyn"]`` and nothing else. A canonical file placed
        beside the plugin instead would leave the install path with nothing to
        copy, on a machine where there is no repository to fall back to."""
        src_root = REPO / "src" / "convilyn"

        for name in ("SKILL.md", "plugin.json", "mcp.json"):
            source = CANONICAL_SKILL.parent / name
            assert source.is_file()
            assert src_root in source.parents


class TestTheSkillFrontmatter:
    @pytest.fixture
    def text(self) -> str:
        return SKILL.read_text(encoding="utf-8")

    def test_it_opens_with_frontmatter(self, text: str) -> None:
        assert text.startswith("---\n")

    def test_it_declares_a_name(self, text: str) -> None:
        assert "\nname: convilyn\n" in text

    def test_it_declares_a_description(self, text: str) -> None:
        assert "\ndescription: >-\n" in text

    def test_the_description_says_when_to_use_it(self, text: str) -> None:
        """A description that only says what a tool *is* leaves the choice to
        chance. The two open skill standards both key selection off this field."""
        assert "Use when" in text

    def test_it_also_says_when_not_to(self, text: str) -> None:
        """The half that stops the tool being reached for on files that are
        already text — where reading them directly genuinely wins."""
        assert "Do NOT use" in text

    def test_the_version_matches_the_manifest(self, text: str) -> None:
        assert f'version: "{__version__}"' in text


class TestTheMcpConfig:
    @pytest.fixture
    def config(self) -> dict:
        return _load(MCP_CONFIG)

    @pytest.fixture
    def server(self, config: dict) -> dict:
        return config["mcpServers"]["convilyn"]

    def test_it_declares_one_server(self, config: dict) -> None:
        assert list(config["mcpServers"]) == ["convilyn"]

    def test_it_carries_no_credential(self, server: dict) -> None:
        """The whole reason this file has four lines in it. ``convilyn setup``
        stores the key where the CLI finds it; a copy here would be a secret in
        a file that gets committed, shared and pasted into bug reports."""
        assert "env" not in server
        blob = json.dumps(server).lower()
        assert "key" not in blob and "token" not in blob

    def test_the_command_is_one_the_cli_actually_has(self, server: dict) -> None:
        """The check that a manifest test usually skips, and the one that would
        actually fire: a config naming a command that does not exist produces a
        server which fails to start, and the failure surfaces inside someone
        else's editor rather than here."""
        assert server["command"] == "convilyn"

        result = subprocess.run(
            [sys.executable, "-m", "convilyn.cli.main", *server["args"], "--help"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "Usage:" in result.stdout


class TestThePublishedToolTableNamesRealTools:
    """`plugins/convilyn/README.md` lists the five tools, and nothing wrote it.

    `build_plugin.py`'s ``OUTPUTS`` covers SKILL.md / plugin.json / mcp.json —
    this README is hand-maintained and ships to the public mirror, so a rename
    that missed it would publish a table naming tools that do not exist. Rather
    than adding a fourth generated file (the README is prose around the table,
    not a rendering of anything), the table is *derived* here: the names come
    from the live server, so the check cannot drift the way a second hand-typed
    list would.
    """

    README = PLUGIN_DIR / "README.md"

    def _table_names(self) -> set[str]:
        import re

        rows = re.findall(r"^\| `([a-z_]+)` \|", self.README.read_text(encoding="utf-8"), re.M)
        return set(rows)

    async def _registered(self) -> set[str]:
        from convilyn.mcp.server import build_server

        return {tool.name for tool in await build_server().list_tools()}

    async def test_the_table_lists_exactly_the_registered_tools(self) -> None:
        assert self._table_names() == await self._registered()

    def test_the_table_is_not_empty(self) -> None:
        """Vacuity guard: an empty set equals an empty set, so a regex that
        stopped matching would make the assertion above pass while the README
        said anything at all."""
        assert len(self._table_names()) == 5
