"""Pydantic schemas shared by the serving API and its clients."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

CustomerId = Annotated[int, Field(ge=0)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]

MODEL_FEATURES = [
    "limit_bal",
    "sex",
    "education",
    "marriage",
    "age",
    "pay_0",
    "pay_2",
    "pay_3",
    "pay_4",
    "pay_5",
    "pay_6",
    "bill_amt1",
    "bill_amt2",
    "bill_amt3",
    "bill_amt4",
    "bill_amt5",
    "bill_amt6",
    "pay_amt1",
    "pay_amt2",
    "pay_amt3",
    "pay_amt4",
    "pay_amt5",
    "pay_amt6",
]


class StrictSchema(BaseModel):
    """Base schema that rejects misspelled or unexpected fields."""

    model_config = ConfigDict(extra="forbid")


class PredictRequest(StrictSchema):
    """Batch prediction request for between one and one hundred customers."""

    customer_ids: Annotated[list[CustomerId], Field(min_length=1, max_length=100)]


class Prediction(StrictSchema):
    """One customer's default probability and thresholded label."""

    customer_id: CustomerId
    default_probability: Probability
    label: Literal[0, 1]


class PredictResponse(StrictSchema):
    """Successful batch prediction response."""

    request_id: str
    model_version: str
    predictions: list[Prediction]


class UnknownCustomersError(StrictSchema):
    """Error body returned when any requested customer is unavailable."""

    error: Literal["unknown_customers"] = "unknown_customers"
    ids: list[CustomerId]


class HealthResponse(StrictSchema):
    """Liveness or readiness state."""

    status: Literal["ok", "not_ready"]


class ModelInfo(StrictSchema):
    """Metadata about the immutable model loaded by one API pod."""

    name: str
    version: str
    source: str
    loaded_at: datetime
    features: list[str]
    mlflow_run_id: str | None = None
    feature_service: str | None = None
    registered_at: str | None = None
