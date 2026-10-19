"""release-please's configuration, the one place the version lives, and the changelog."""

from __future__ import annotations

import json
import re

from .helpers import ROOT

CONFIG = json.loads((ROOT / "release-please-config.json").read_text())
MANIFEST = json.loads((ROOT / ".release-please-manifest.json").read_text())


def test_a_breaking_change_is_a_minor_and_a_feature_a_patch_while_0_y_z() -> None:
    assert CONFIG["release-type"] == "python"
    assert CONFIG["bump-minor-pre-major"] is True
    assert CONFIG["bump-patch-for-minor-pre-major"] is True
    assert CONFIG["include-component-in-tag"] is False
    assert CONFIG["pull-request-title-pattern"] == "Release ${version}"


def test_the_version_lives_in_one_annotated_file() -> None:
    assert CONFIG["packages"]["."]["extra-files"] == ["src/haau3/_version.py"]
    text = (ROOT / "src" / "haau3" / "_version.py").read_text()
    assert re.fullmatch(r'__version__ = "[^"]+"  # x-release-please-version\n', text)
    assert 'dynamic = ["version"]' in (ROOT / "pyproject.toml").read_text()


def test_the_first_release_is_0_1_0_and_the_override_goes_after_it() -> None:
    # PyPI holds a 0.0.0 placeholder, so the first real release is forced to 0.1.0; once the
    # manifest is past 0.0.0, the override must be gone or every release would be 0.1.0.
    release_as = CONFIG["packages"]["."].get("release-as")
    if MANIFEST["."] == "0.0.0":
        assert release_as == "0.1.0"
    else:
        assert release_as is None


def test_the_changelog_has_no_em_dash() -> None:
    assert "—" not in (ROOT / "CHANGELOG.md").read_text()
