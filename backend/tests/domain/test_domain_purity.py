"""The domain layer must stay framework-free.

The desktop version's detection logic was reachable only through a Qt widget tree, which is
why none of it had tests. Keeping ``app.domain`` free of the web and persistence frameworks is
what makes the port verifiable now and re-testable when Phases 2-4 land on top of it.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

FORBIDDEN_ROOTS = {"fastapi", "starlette", "sqlalchemy", "alembic", "PyQt6", "PyQt5"}

DOMAIN_DIR = pathlib.Path(__file__).resolve().parents[2] / "app" / "domain"


def domain_modules() -> list[pathlib.Path]:
    return sorted(p for p in DOMAIN_DIR.glob("*.py") if p.name != "__init__.py")


def imported_roots(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])

    return roots


def test_domain_directory_is_not_empty():
    assert domain_modules(), "no domain modules found — check the test's path assumption"


@pytest.mark.parametrize("module", domain_modules(), ids=lambda p: p.name)
def test_domain_module_imports_no_framework(module: pathlib.Path):
    leaked = imported_roots(module) & FORBIDDEN_ROOTS

    assert not leaked, f"{module.name} imports {sorted(leaked)}"


@pytest.mark.parametrize("module", domain_modules(), ids=lambda p: p.name)
def test_domain_module_does_not_import_app_config(module: pathlib.Path):
    """Settings are passed in, never reached for.

    ``utils/config_manager.py`` was a global singleton read from deep inside the vision code,
    so constructing an engine with different parameters in a test was impossible. Domain
    classes now take their parameters as arguments; the ``from_settings`` helpers accept a
    settings object structurally without importing it.
    """
    tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

    offenders = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.config"
    ]

    assert not offenders, f"{module.name} imports app.config directly"
