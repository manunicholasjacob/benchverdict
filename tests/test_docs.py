"""Guards against the documentation drifting away from the code.

The README publishes the reason code vocabulary and the verdict list as tables.
Those tables are the contract a consumer reads, so a code added, renamed or
re-severitied in the source must not silently disappear from the README.
"""

from __future__ import annotations

import os
import re

import pytest

from benchverdict import SCHEMA_ID, __version__
from benchverdict.checks import REASON_CODES, Severity, Verdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(name: str) -> str:
    path = os.path.join(REPO_ROOT, name)
    if not os.path.isfile(path):
        pytest.skip("%s is not present in this checkout" % name)
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def readme_reason_table():
    """Parse the README's reason code table into {code: (severity, meaning)}."""
    text = read("README.md")
    rows = {}
    for match in re.finditer(
        r"^\|\s*`([A-Z][A-Z0-9_]*)`\s*\|\s*(invalidating|warning|info)\s*\|\s*(.+?)\s*\|$",
        text,
        re.MULTILINE,
    ):
        rows[match.group(1)] = (match.group(2), match.group(3))
    return rows


class TestReasonTable:
    def test_the_readme_table_parses(self):
        assert len(readme_reason_table()) > 0

    def test_every_code_is_documented(self):
        documented = readme_reason_table()
        missing = sorted(set(REASON_CODES) - set(documented))
        assert missing == [], "codes missing from the README table: %s" % missing

    def test_no_code_is_documented_that_does_not_exist(self):
        documented = readme_reason_table()
        extra = sorted(set(documented) - set(REASON_CODES))
        assert extra == [], "README documents codes that do not exist: %s" % extra

    @pytest.mark.parametrize("code", sorted(REASON_CODES))
    def test_severity_and_meaning_match_the_source(self, code):
        severity, meaning = readme_reason_table()[code]
        assert severity == REASON_CODES[code][0]
        assert meaning == REASON_CODES[code][1]


class TestOtherDocumentedFacts:
    def test_every_verdict_appears_in_the_readme(self):
        text = read("README.md")
        for verdict in Verdict.ALL:
            assert "`%s`" % verdict in text, verdict

    def test_every_severity_appears_in_the_readme(self):
        text = read("README.md")
        for severity in Severity.ALL:
            assert severity in text, severity

    def test_the_schema_id_appears_in_the_readme(self):
        assert SCHEMA_ID in read("README.md")

    def test_the_version_matches_the_changelog(self):
        changelog = read("CHANGELOG.md")
        assert "[%s]" % __version__ in changelog

    def test_the_version_matches_the_citation_file(self):
        assert "version: %s" % __version__ in read("CITATION.cff")

    def test_the_action_is_at_the_repo_root(self):
        # GitHub only serves an action referenced as owner/repo@ref when
        # action.yml sits at the root, so this is a packaging invariant.
        assert os.path.isfile(os.path.join(REPO_ROOT, "action.yml"))

    def test_the_readme_claims_no_adoption(self):
        text = read("README.md").lower()
        for phrase in (
            "widely used",
            "trusted by",
            "thousands of",
            "downloads per",
            "as cited by",
        ):
            assert phrase not in text, phrase


class TestAsciiOnly:
    @pytest.mark.parametrize(
        "name",
        [
            "README.md",
            "CHANGELOG.md",
            "RELEASE_CHECKLIST.md",
            "CITATION.cff",
            "LICENSE",
            "pyproject.toml",
            "action.yml",
        ],
    )
    def test_repo_text_files_are_pure_ascii(self, name):
        path = os.path.join(REPO_ROOT, name)
        if not os.path.isfile(path):
            pytest.skip("%s is not present" % name)
        with open(path, "rb") as fh:
            data = fh.read()
        bad = sorted({b for b in data if b > 0x7E or (b < 0x20 and b not in (0x0A, 0x0D, 0x09))})
        assert bad == [], "%s has non-ASCII bytes: %s" % (name, bad)

    def test_source_and_tests_are_pure_ascii(self):
        offenders = []
        for folder in ("src", "tests"):
            for dirpath, _dirs, files in os.walk(os.path.join(REPO_ROOT, folder)):
                for name in files:
                    if not name.endswith((".py", ".json")):
                        continue
                    path = os.path.join(dirpath, name)
                    with open(path, "rb") as fh:
                        data = fh.read()
                    if any(
                        b > 0x7E or (b < 0x20 and b not in (0x0A, 0x0D, 0x09)) for b in data
                    ):
                        offenders.append(os.path.relpath(path, REPO_ROOT))
        assert offenders == []

    def test_no_dash_or_quote_lookalikes_anywhere_in_the_repo(self):
        # Implied by the ASCII tests above, but named explicitly so a regression
        # reports the actual rule that was broken rather than a byte value.
        forbidden = {
            0x2014: "em dash",
            0x2013: "en dash",
            0x2018: "left single quote",
            0x2019: "right single quote",
            0x201C: "left double quote",
            0x201D: "right double quote",
            0x00A0: "non-breaking space",
        }
        offenders = []
        for dirpath, dirs, files in os.walk(REPO_ROOT):
            dirs[:] = [
                d for d in dirs if d not in (".git", ".ruff_cache", "__pycache__", "dist",
                                             "build", ".pytest_cache")
                and not d.endswith(".egg-info")
            ]
            for name in files:
                if not name.endswith((".py", ".md", ".yml", ".yaml", ".toml", ".cff",
                                      ".json", ".txt")) and name != "LICENSE":
                    continue
                path = os.path.join(dirpath, name)
                with open(path, "rb") as fh:
                    raw = fh.read()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    offenders.append((os.path.relpath(path, REPO_ROOT), "not utf-8"))
                    continue
                for code, label in forbidden.items():
                    if chr(code) in text:
                        offenders.append((os.path.relpath(path, REPO_ROOT), label))
        assert offenders == []
