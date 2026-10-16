"""Render ``feature_store.yaml`` from its template using the environment (P6.2).

The Feast configuration embeds the Postgres password, so the rendered file is
gitignored/dockerignored and only this creds-free template is versioned. In the
cluster the Feast Job feeds the values from the ``postgres-auth`` Kubernetes
Secret, so no credential is ever committed or baked into an image.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from pathlib import Path
from string import Template

#: Environment variables the template requires, in a stable reporting order.
REQUIRED_VARIABLES: tuple[str, ...] = (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "FEAST_POSTGRES_DB",
    "REDIS_HOST",
    "REDIS_PORT",
)


def render(template_path: Path, *, environ: Mapping[str, str]) -> str:
    """Substitute ``${VAR}`` placeholders, failing loudly on anything missing."""
    missing = [name for name in REQUIRED_VARIABLES if not environ.get(name)]
    if missing:
        msg = f"missing required environment variables: {', '.join(missing)}"
        raise ValueError(msg)
    template = Template(template_path.read_text(encoding="utf-8"))
    return template.substitute({name: environ[name] for name in REQUIRED_VARIABLES})


def main() -> int:
    """Render the template beside itself and report only the output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    repo = Path(__file__).resolve().parent
    parser.add_argument(
        "--template", type=Path, default=repo / "feature_store.yaml.tmpl"
    )
    parser.add_argument("--output", type=Path, default=repo / "feature_store.yaml")
    args = parser.parse_args()

    rendered = render(args.template, environ=os.environ)
    args.output.write_text(rendered, encoding="utf-8")
    # Never log values: the output contains the database password.
    print(f"rendered {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
