"""Materialize offline features into the Feast online store (P6.2)."""

from __future__ import annotations

import argparse
import datetime as dt

from feast import FeatureStore


def main() -> int:
    """Materialize the full feature window into Redis."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="feature_repo")
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default="")
    args = parser.parse_args()

    store = FeatureStore(repo_path=args.repo)
    end = (
        dt.datetime.fromisoformat(args.end)
        if args.end
        else dt.datetime.now(dt.UTC).replace(tzinfo=None)
    )
    start = dt.datetime.fromisoformat(args.start)
    store.materialize(start_date=start, end_date=end)
    print(f"materialized {start.isoformat()}..{end.isoformat()} to online store")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
