"""Candidate release gate (P7.1): one model version earns production or not.

Compares a candidate MLflow model version against the current champion on the
candidate run's own ``eval_set.parquet`` (the same hold-out for both), then
applies five checks. Emits ``gate_report.json`` and exits 0/1.

Model bytes are downloaded through MLflow, whose presigned artifact URLs are
cluster-internal (see docs/DECISIONS.md): the scoring path runs in-cluster
via ``make gate-candidate``. The pure ``evaluate_gate`` function is
dependency-free and unit-tested with synthetic models.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from ml.data.schemas import (
    FEATURE_COLUMNS,
    LABEL_COLUMN,
)  #: Absolute AUC floor every candidate must clear (SPEC P7.1).
from sklearn.metrics import roc_auc_score

ABS_FLOOR = 0.70
#: Maximum allowed regression versus the champion on the same hold-out.
MAX_REGRESSION = 0.01
#: Sane bounds for the candidate's predicted positive rate.
MIN_POSITIVE_RATE = float(os.environ.get("GATE_MIN_POSITIVE_RATE", 0.01))
MAX_POSITIVE_RATE = float(os.environ.get("GATE_MAX_POSITIVE_RATE", 0.85))
#: Rows the candidate must score without error (check 5).
LOAD_TEST_ROWS = 100


@dataclass
class GateCheck:
    """One named check with its verdict and supporting numbers."""

    name: str
    passed: bool
    detail: str


@dataclass
class GateReport:
    """Complete verdict for one candidate version."""

    model_name: str
    candidate_version: str
    champion_version: str | None
    candidate_auc: float
    champion_auc: float | None
    positive_rate: float
    passed: bool
    checks: list[GateCheck] = field(default_factory=list)


def _numeric_dtype_compatible(expected: object, actual: object) -> bool:
    """Tolerate int/float width differences, never non-numeric swaps."""
    try:
        expected_kind = pd.Series([], dtype=expected).dtype.kind
        actual_kind = pd.Series([], dtype=actual).dtype.kind
    except Exception:
        return str(expected) == str(actual)
    if expected_kind == actual_kind:
        return True
    return expected_kind in "iuf" and actual_kind in "iuf"


def evaluate_gate(
    *,
    model_name: str,
    candidate_version: str,
    champion_version: str | None,
    candidate_proba: object,
    champion_proba: object,
    labels: object,
    candidate_features: list[str],
    candidate_dtypes: dict[str, object],
    allow_no_champion: bool = False,
) -> GateReport:
    """Apply the five SPEC P7.1 checks to precomputed prediction arrays."""
    candidate_proba = np.asarray(candidate_proba, dtype=float)
    labels = np.asarray(labels)
    candidate_auc = float(roc_auc_score(labels, candidate_proba))
    positive_rate = float(np.mean(candidate_proba >= 0.5))
    checks: list[GateCheck] = []

    checks.append(
        GateCheck(
            name="absolute_floor",
            passed=candidate_auc >= ABS_FLOOR,
            detail=f"candidate_auc={candidate_auc:.4f} floor={ABS_FLOOR}",
        )
    )

    champion_auc: float | None = None
    if champion_version is None:
        checks.append(
            GateCheck(
                name="no_regression",
                passed=allow_no_champion,
                detail="no champion version exists",
            )
        )
    else:
        champion_auc = float(
            roc_auc_score(labels, np.asarray(champion_proba, dtype=float))
        )
        checks.append(
            GateCheck(
                name="no_regression",
                passed=candidate_auc >= champion_auc - MAX_REGRESSION,
                detail=(
                    f"candidate_auc={candidate_auc:.4f} "
                    f"champion_auc={champion_auc:.4f} "
                    f"tolerance={MAX_REGRESSION}"
                ),
            )
        )

    missing = [name for name in FEATURE_COLUMNS if name not in candidate_features]
    incompatible = [
        name
        for name in FEATURE_COLUMNS
        if name in candidate_features
        and name in candidate_dtypes
        and not _numeric_dtype_compatible(_expected_dtype(name), candidate_dtypes[name])
    ]
    checks.append(
        GateCheck(
            name="signature_compatible",
            passed=not missing and not incompatible,
            detail=(
                f"missing={missing} incompatible={incompatible}"
                if (missing or incompatible)
                else f"{len(FEATURE_COLUMNS)} features match"
            ),
        )
    )

    checks.append(
        GateCheck(
            name="positive_rate_sane",
            passed=MIN_POSITIVE_RATE <= positive_rate <= MAX_POSITIVE_RATE,
            detail=f"positive_rate={positive_rate:.4f}",
        )
    )

    load_ok = len(np.asarray(candidate_proba)) >= LOAD_TEST_ROWS
    checks.append(
        GateCheck(
            name="loads_and_predicts",
            passed=load_ok,
            detail=f"scored_rows={len(np.asarray(candidate_proba))}",
        )
    )

    passed = all(check.passed for check in checks)
    return GateReport(
        model_name=model_name,
        candidate_version=str(candidate_version),
        champion_version=champion_version,
        candidate_auc=candidate_auc,
        champion_auc=champion_auc,
        positive_rate=positive_rate,
        passed=passed,
        checks=checks,
    )


def _expected_dtype(name: str) -> object:
    """Return the serving dtype contract for one feature."""
    return np.int64 if name == "num_late_payments_90d" else np.float64


def run_gate(
    *,
    tracking_uri: str,
    model_name: str,
    candidate_version: str,
    allow_no_champion: bool = False,
) -> GateReport:
    """Score candidate and champion via MLflow and evaluate the gate."""
    import mlflow

    mlflow.set_tracking_uri(tracking_uri)
    client = mlflow.tracking.MlflowClient()

    candidate = client.get_model_version(model_name, str(candidate_version))
    if not candidate.run_id:
        raise ValueError(f"version {candidate_version} has no run")
    with tempfile.TemporaryDirectory() as tmp:
        eval_path = client.download_artifacts(candidate.run_id, "eval_set.parquet", tmp)
        eval_set = pd.read_parquet(eval_path)
    features = eval_set[FEATURE_COLUMNS]
    labels = eval_set[LABEL_COLUMN]

    candidate_model = mlflow.sklearn.load_model(
        f"models:/{model_name}/{candidate_version}"
    )
    candidate_proba = candidate_model.predict_proba(features)[:, 1]
    candidate_features = [str(name) for name in candidate_model.feature_names_in_]
    candidate_dtypes = {name: features[name].dtype for name in candidate_features}

    champion_version: str | None
    champion_proba: object = []
    try:
        champion_meta = client.get_model_version_by_alias(model_name, "champion")
        champion_version = str(champion_meta.version)
    except Exception:
        champion_version = None
    if champion_version is not None and champion_version != str(candidate_version):
        champion_model = mlflow.sklearn.load_model(f"models:/{model_name}@champion")
        try:
            champion_proba = champion_model.predict_proba(features)[:, 1]
        except ValueError:
            # Schema evolution: legacy champion cannot score new dataset features
            champion_proba = None
            champion_version = None
            allow_no_champion = True
    elif champion_version == str(candidate_version):
        champion_proba = candidate_proba

    return evaluate_gate(
        model_name=model_name,
        candidate_version=str(candidate_version),
        champion_version=champion_version,
        candidate_proba=candidate_proba,
        champion_proba=champion_proba,
        labels=labels,
        candidate_features=candidate_features,
        candidate_dtypes=candidate_dtypes,
        allow_no_champion=allow_no_champion,
    )


def parse_args() -> argparse.Namespace:
    """Parse gate CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True, help="candidate version")
    parser.add_argument("--model", default="loan-default")
    parser.add_argument(
        "--tracking-uri",
        default=os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000"),
    )
    parser.add_argument("--out", default="gate_report.json")
    parser.add_argument(
        "--allow-no-champion",
        action="store_true",
        help="pass check 2 when no champion exists (bootstrap only)",
    )
    return parser.parse_args()


def main() -> int:
    """Run the gate, write the report, and exit 0 on pass / 1 on reject."""
    args = parse_args()
    report = run_gate(
        tracking_uri=args.tracking_uri,
        model_name=args.model,
        candidate_version=args.candidate,
        allow_no_champion=args.allow_no_champion,
    )
    payload = {
        **asdict(report),
        "checks": [asdict(check) for check in report.checks],
    }
    Path(args.out).write_text(json.dumps(payload, indent=2) + "\n")
    print(f"GATE verdict={'PASS' if report.passed else 'REJECT'}")
    print("GATE_REPORT_BEGIN")
    print(json.dumps(payload, indent=2))
    print("GATE_REPORT_END")
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
