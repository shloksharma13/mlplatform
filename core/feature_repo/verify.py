"""Verify the Feast offline and online stores resolve the same features (P6.3).

Runs inside the cluster (the Redis online store is ClusterIP-only). Prints the
online features for a handful of customers and the size of a point-in-time
historical pull so the P6.3 acceptance can be checked from the Job logs.
"""

from __future__ import annotations

import os

import pandas as pd
import pyarrow.fs
import pyarrow.parquet
from feast import FeatureStore

LABELS_PATH = "feast-offline/labels.parquet"
FEATURE_SERVICE = "loan_default_v1"
SAMPLE_IDS = (1, 2, 3, 4, 5)


def load_entity_frame(ids: tuple[int, ...] = SAMPLE_IDS) -> pd.DataFrame:
    """Project a small, real entity frame from the offline labels Parquet file."""
    endpoint = os.environ.get("FEAST_S3_ENDPOINT_URL")
    filesystem = pyarrow.fs.S3FileSystem(
        endpoint_override=endpoint or None, region="us-east-1"
    )
    table = pyarrow.parquet.read_table(LABELS_PATH, filesystem=filesystem)
    frame = table.to_pandas()
    return (
        frame.loc[frame["customer_id"].isin(ids), ["customer_id", "event_timestamp"]]
        .sort_values("customer_id")
        .reset_index(drop=True)
    )


def main() -> int:
    """Pull the same features offline and online and report both result shapes."""
    store = FeatureStore(repo_path=os.environ.get("FEAST_REPO", "feature_repo"))
    service = store.get_feature_service(FEATURE_SERVICE)

    entity_df = load_entity_frame()
    historical = store.get_historical_features(
        entity_df=entity_df, features=service
    ).to_df()
    print(f"historical rows={len(historical)} cols={sorted(historical.columns)}")

    online = store.get_online_features(
        features=service,
        entity_rows=[{"customer_id": customer_id} for customer_id in SAMPLE_IDS],
    ).to_dict()
    print(f"online customer_ids={online.get('customer_id')}")
    for name in sorted(online):
        if name != "customer_id":
            print(f"online {name}={online[name]}")

    if historical.empty or not online.get("customer_id"):
        msg = "feast verification failed: empty offline or online result"
        raise SystemExit(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
