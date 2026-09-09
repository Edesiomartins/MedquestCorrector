from pathlib import Path
import re


def test_backend_does_not_use_deprecated_fitz_import():
    backend_root = Path(__file__).resolve().parents[1]
    python_files = [
        *backend_root.joinpath("app").rglob("*.py"),
        *backend_root.joinpath("tests").rglob("*.py"),
    ]

    deprecated_imports = [
        str(path.relative_to(backend_root))
        for path in python_files
        if re.search(r"^\s*import fitz(?:\s|$)", path.read_text(encoding="utf-8"), re.MULTILINE)
    ]

    assert deprecated_imports == []
