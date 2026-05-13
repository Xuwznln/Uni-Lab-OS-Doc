try:
    from . import peptide_materials  # noqa: F401  ensure @resource classes are importable for PLR deserialize
except Exception:  # pragma: no cover - 允许轻量环境导入非资源辅助函数
    peptide_materials = None  # type: ignore[assignment]

try:
    from . import sirna_materials  # noqa: F401  ensure @resource classes are importable for PLR deserialize
except Exception:  # pragma: no cover - 允许轻量环境导入非资源辅助函数
    sirna_materials = None  # type: ignore[assignment]
