"""Mechanical redundancy / dead-code audit for the slidenote package.

Pure-stdlib AST analysis. Reports:
  1. unused imports per module (names bound by import but never referenced,
     unless exported via __all__ or used in type-comment/string contexts)
  2. modules that are never imported anywhere in the repository
  3. duplicate function bodies (same AST, same module or cross-module)

Usage: python scripts/audit_redundancy.py
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "slidenote"

# Modules that are legitimate entry points / never need importers.
ENTRY_POINTS = {
    "slidenote.__main__",
    "slidenote.cli",
}


def iter_py_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def module_name(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    parts = list(rel.with_suffix("").parts)
    return ".".join(parts)


def analyze_file(path: Path) -> tuple[set[str], list[tuple[int, str]], list[ast.stmt]]:
    """Return (used_names, unused_imports, module_functions)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    used: set[str] = set()
    imported: list[tuple[int, str, str]] = []  # (lineno, name, source)
    functions: list[ast.stmt] = []

    # Collect __all__ string literals.
    exported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    for elt in node.value.elts:  # type: ignore[attr-defined]
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            exported.add(elt.value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                imported.append((node.lineno, local, alias.name))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                imported.append((node.lineno, local, f"{node.module}.{alias.name}"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_") or node.name.startswith("_test"):
                continue
            functions.append(node)

    unused = [
        (lineno, name, src)
        for (lineno, name, src) in imported
        if name not in used and name not in exported
    ]
    return used, unused, functions


def main() -> int:
    files = iter_py_files(PACKAGE)
    all_unused: list[tuple[str, int, str, str]] = []
    body_hashes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    # (importer_module, imported_module) edges; relative imports resolved.
    import_edges: set[tuple[str, str]] = set()
    module_of: dict[Path, str] = {p: module_name(p, PACKAGE) for p in files}

    def resolve_relative(base: str, level: int, module: str | None) -> str | None:
        parts = base.split(".")
        if level > len(parts):
            return None
        prefix = parts[: len(parts) - level + 1]
        if module:
            return ".".join(prefix + module.split("."))
        return ".".join(prefix)

    for path in files:
        mod = module_of[path]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        _, unused, functions = analyze_file(path)
        for lineno, name, src in unused:
            all_unused.append((mod, lineno, name, src))
        for fn in functions:
            wrapper = ast.Module(body=fn.body, type_ignores=[])
            h = ast.dump(wrapper, include_attributes=False)
            body_hashes[h].append((mod, fn.name))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_edges.add((mod, alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    import_edges.add((mod, node.module))
                elif node.level > 0:
                    resolved = resolve_relative(mod, node.level, node.module)
                    if resolved:
                        import_edges.add((mod, resolved))

    # A module is reachable if some other module imports it (or a submodule of it).
    reachable: set[str] = set()
    for importer, imported in import_edges:
        if importer in module_of.values():
            reachable.add(imported)

    def is_imported(target: str) -> bool:
        return any(imp == target or imp.startswith(target + ".") or target.startswith(imp + ".") for imp in reachable)

    print("=" * 70)
    print("1) UNUSED IMPORTS")
    print("=" * 70)
    if not all_unused:
        print("  (none)")
    for mod, lineno, name, src in sorted(all_unused):
        print(f"  {mod}:{lineno}: unused import {name!r} (from {src})")

    print()
    print("=" * 70)
    print("2) MODULES NEVER IMPORTED ANYWHERE (potential dead modules)")
    print("=" * 70)
    for mod in sorted(module_of.values()):
        if mod in ENTRY_POINTS:
            continue
        if not is_imported(mod):
            print(f"  {mod}")

    print()
    print("=" * 70)
    print("3) DUPLICATE FUNCTION BODIES")
    print("=" * 70)
    seen_pairs: set[tuple[str, str, str, str]] = set()
    for h, locs in sorted(body_hashes.items(), key=lambda kv: -len(kv[1])):
        if len(locs) < 2:
            continue
        for i in range(len(locs)):
            for j in range(i + 1, len(locs)):
                m1, f1 = locs[i]
                m2, f2 = locs[j]
                key = tuple(sorted([(m1, f1), (m2, f2)]))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)  # type: ignore[arg-type]
                print(f"  {m1}.{f1}  ==  {m2}.{f2}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
