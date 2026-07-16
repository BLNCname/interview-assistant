from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_package_exposes_version() -> None:
    import interview_assistant

    assert interview_assistant.__version__ == "0.1.0"


def test_setuptools_discovers_only_the_root_package() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "[tool.setuptools.packages.find]" in pyproject
    assert 'where = ["."]' in pyproject
    assert 'include = ["interview_assistant*"]' in pyproject
    assert 'exclude = ["src*"]' in pyproject
    assert "*.egg-info/" in gitignore
