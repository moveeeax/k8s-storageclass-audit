from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
EXAMPLES = ROOT / "examples"


def _resolver(base: Path):
    def _path(name: str) -> str:
        path = base / name
        assert path.exists(), f"missing {path}"
        return str(path)

    return _path


@pytest.fixture
def fixture_path():
    return _resolver(FIXTURES)


@pytest.fixture
def example_path():
    return _resolver(EXAMPLES)
