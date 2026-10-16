"""MLflow registry helpers for retraining (P8.4)."""

from __future__ import annotations


def set_alias(client: object, model_name: str, alias: str, version: str) -> None:
    """Point a registry alias at a version, removing any stale binding first."""
    try:
        current = client.get_model_version_by_alias(model_name, alias)  # type: ignore[union-attr]
    except Exception:
        current = None
    if current is not None and str(current.version) != str(version):
        client.delete_registered_model_alias(model_name, alias)  # type: ignore[union-attr]
    client.set_registered_model_alias(model_name, alias, str(version))  # type: ignore[union-attr]


def tag_version(
    client: object, model_name: str, version: str, tags: dict[str, str]
) -> None:
    """Attach string tags to one registered model version."""
    for key, value in tags.items():
        client.set_model_version_tag(model_name, str(version), key, value)  # type: ignore[union-attr]
