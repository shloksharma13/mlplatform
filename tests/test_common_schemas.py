"""Tests for the serving API contract schemas."""

import pytest
from mlp_common.schemas import Prediction, PredictRequest
from pydantic import ValidationError


def test_predict_request_accepts_contract_batch() -> None:
    """A normal batch is accepted without transformation."""
    request = PredictRequest(customer_ids=[1, 2, 3])
    assert request.customer_ids == [1, 2, 3]


@pytest.mark.parametrize("customer_ids", [[], list(range(101)), [-1]])
def test_predict_request_rejects_invalid_batch(customer_ids: list[int]) -> None:
    """Empty, oversized, and negative-ID batches fail validation."""
    with pytest.raises(ValidationError):
        PredictRequest(customer_ids=customer_ids)


def test_prediction_probability_is_bounded() -> None:
    """Probabilities outside the unit interval cannot reach clients."""
    with pytest.raises(ValidationError):
        Prediction(customer_id=1, default_probability=1.1, label=1)
