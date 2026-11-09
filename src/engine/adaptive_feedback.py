"""Low-overhead online learning from manual notification-score overrides.

The learner intentionally uses ``array('d')`` and the standard library instead
of importing scikit-learn. For a fixed embedding dimension it is an equivalent
binary logistic SGD model with log-loss and L2 regularization. Only model
parameters are retained in memory; feedback examples are never accumulated.
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from array import array
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence


MODEL_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS adaptive_models (
    model_id TEXT PRIMARY KEY,
    dimension INTEGER NOT NULL CHECK (dimension > 0),
    weights_json TEXT NOT NULL,
    intercept REAL NOT NULL,
    learning_rate REAL NOT NULL CHECK (learning_rate > 0),
    l2 REAL NOT NULL CHECK (l2 >= 0),
    samples_seen INTEGER NOT NULL CHECK (samples_seen >= 0),
    updated_at TEXT NOT NULL
) STRICT
"""


@dataclass(frozen=True)
class ModelParameters:
    """Portable representation of a trained model."""

    model_id: str
    dimension: int
    weights: tuple[float, ...]
    intercept: float
    learning_rate: float
    l2: float
    samples_seen: int
    updated_at: str


class AdaptiveBinaryClassifier:
    """Incremental binary logistic classifier for one embedding space.

    ``label=1`` means the user considers the notification score/action
    desirable; ``label=0`` means the user overrode it downward. Each call to
    :meth:`update_from_override` performs exactly one SGD step.
    """

    def __init__(
        self,
        dimension: int,
        model_id: str = "notification-score-v1",
        learning_rate: float = 0.05,
        l2: float = 1e-4,
    ) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        if not math.isfinite(learning_rate) or learning_rate <= 0:
            raise ValueError("learning_rate must be finite and positive")
        if not math.isfinite(l2) or l2 < 0:
            raise ValueError("l2 must be finite and non-negative")
        self.model_id = model_id
        self.dimension = dimension
        self.learning_rate = learning_rate
        self.l2 = l2
        self._weights = array("d", [0.0]) * dimension
        self._intercept = 0.0
        self._samples_seen = 0
        self._updated_at = datetime.now(timezone.utc).isoformat()

    def _validate_embedding(self, embedding: Sequence[float]) -> None:
        if len(embedding) != self.dimension:
            raise ValueError(
                f"embedding dimension {len(embedding)} does not match model "
                f"dimension {self.dimension}"
            )
        if not all(math.isfinite(float(value)) for value in embedding):
            raise ValueError("embedding values must be finite")

    @staticmethod
    def _sigmoid(value: float) -> float:
        if value >= 0:
            return 1.0 / (1.0 + math.exp(-value))
        exp_value = math.exp(value)
        return exp_value / (1.0 + exp_value)

    def predict_proba(self, embedding: Sequence[float]) -> float:
        """Return the local probability of the positive feedback label."""
        self._validate_embedding(embedding)
        score = self._intercept + sum(
            weight * float(value)
            for weight, value in zip(self._weights, embedding)
        )
        return self._sigmoid(score)

    def predict(self, embedding: Sequence[float], threshold: float = 0.5) -> int:
        """Return ``0`` or ``1`` using a configurable decision threshold."""
        if not 0 < threshold < 1:
            raise ValueError("threshold must be between 0 and 1")
        return int(self.predict_proba(embedding) >= threshold)

    def update_from_override(
        self,
        embedding: Sequence[float],
        label: int | bool,
    ) -> float:
        """Apply one online log-loss SGD update and return pre-update loss.

        The gradient is ``(prediction - label) * x`` plus L2 regularization.
        No training examples are retained, so memory stays O(embedding
        dimension), independent of the number of user overrides.
        """
        self._validate_embedding(embedding)
        if label not in (0, 1, False, True):
            raise ValueError("label must be binary: 0 or 1")
        target = int(label)
        probability = self.predict_proba(embedding)
        clipped = min(max(probability, 1e-15), 1.0 - 1e-15)
        loss = -(target * math.log(clipped) + (1 - target) * math.log(1 - clipped))
        error = probability - target
        for index, value in enumerate(embedding):
            self._weights[index] -= self.learning_rate * (
                error * float(value) + self.l2 * self._weights[index]
            )
        self._intercept -= self.learning_rate * error
        self._samples_seen += 1
        self._updated_at = datetime.now(timezone.utc).isoformat()
        return loss

    def export_parameters(self) -> ModelParameters:
        """Return a serializable immutable snapshot of current parameters."""
        return ModelParameters(
            model_id=self.model_id,
            dimension=self.dimension,
            weights=tuple(self._weights),
            intercept=self._intercept,
            learning_rate=self.learning_rate,
            l2=self.l2,
            samples_seen=self._samples_seen,
            updated_at=self._updated_at,
        )

    def export_json(self) -> str:
        """Export parameters as canonical JSON for backup or IPC."""
        parameters = self.export_parameters()
        return json.dumps({
            "model_id": parameters.model_id,
            "dimension": parameters.dimension,
            "weights": parameters.weights,
            "intercept": parameters.intercept,
            "learning_rate": parameters.learning_rate,
            "l2": parameters.l2,
            "samples_seen": parameters.samples_seen,
            "updated_at": parameters.updated_at,
        }, separators=(",", ":"), allow_nan=False)

    def persist(self, connection: sqlite3.Connection) -> None:
        """Atomically upsert the current model parameters into SQLite."""
        parameters = self.export_parameters()
        connection.execute(MODEL_TABLE_SQL)
        connection.execute(
            """
            INSERT INTO adaptive_models (
                model_id, dimension, weights_json, intercept, learning_rate,
                l2, samples_seen, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(model_id) DO UPDATE SET
                dimension = excluded.dimension,
                weights_json = excluded.weights_json,
                intercept = excluded.intercept,
                learning_rate = excluded.learning_rate,
                l2 = excluded.l2,
                samples_seen = excluded.samples_seen,
                updated_at = excluded.updated_at
            """,
            (
                parameters.model_id,
                parameters.dimension,
                json.dumps(parameters.weights, separators=(",", ":")),
                parameters.intercept,
                parameters.learning_rate,
                parameters.l2,
                parameters.samples_seen,
                parameters.updated_at,
            ),
        )
        connection.commit()

    @classmethod
    def load(
        cls,
        connection: sqlite3.Connection,
        model_id: str,
    ) -> AdaptiveBinaryClassifier | None:
        """Load a persisted model, returning ``None`` when it does not exist."""
        connection.execute(MODEL_TABLE_SQL)
        row = connection.execute(
            """
            SELECT dimension, weights_json, intercept, learning_rate, l2,
                   samples_seen, updated_at
            FROM adaptive_models
            WHERE model_id = ?
            """,
            (model_id,),
        ).fetchone()
        if row is None:
            return None
        dimension, weights_json, intercept, learning_rate, l2, samples_seen, updated_at = row
        weights = json.loads(weights_json)
        if not isinstance(weights, list) or len(weights) != dimension:
            raise ValueError("persisted adaptive model has invalid weights")
        model = cls(dimension, model_id, learning_rate, l2)
        if not all(math.isfinite(float(value)) for value in weights):
            raise ValueError("persisted adaptive model contains non-finite weights")
        model._weights = array("d", (float(value) for value in weights))
        model._intercept = float(intercept)
        model._samples_seen = int(samples_seen)
        model._updated_at = str(updated_at)
        return model

    def memory_overhead_bytes(self) -> int:
        """Estimate learner-owned parameter memory, excluding interpreter state."""
        return (
            sys.getsizeof(self._weights)
            + sys.getsizeof(self._intercept)
            + sys.getsizeof(self._samples_seen)
            + sys.getsizeof(self._updated_at)
        )

