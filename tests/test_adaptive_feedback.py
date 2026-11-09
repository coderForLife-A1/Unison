import sqlite3

import pytest

from src.engine.adaptive_feedback import AdaptiveBinaryClassifier


def test_override_updates_prediction_and_round_trips_sqlite():
    model = AdaptiveBinaryClassifier(dimension=3, learning_rate=0.5, l2=0)
    embedding = [1.0, 0.0, 0.0]
    assert model.predict_proba(embedding) == pytest.approx(0.5)

    model.update_from_override(embedding, label=1)
    assert model.predict_proba(embedding) > 0.5

    connection = sqlite3.connect(":memory:")
    model.persist(connection)
    restored = AdaptiveBinaryClassifier.load(connection, model.model_id)
    assert restored is not None
    assert restored.predict_proba(embedding) == pytest.approx(
        model.predict_proba(embedding)
    )
    assert restored.export_parameters().samples_seen == 1


def test_model_memory_is_bounded_for_common_embedding_dimension():
    model = AdaptiveBinaryClassifier(dimension=384)
    for _ in range(500):
        model.update_from_override([0.0] * 384, label=0)
    assert model.memory_overhead_bytes() < 15 * 1024 * 1024
    assert model.export_parameters().samples_seen == 500


def test_rejects_bad_embedding_and_non_binary_label():
    model = AdaptiveBinaryClassifier(dimension=2)
    with pytest.raises(ValueError, match="dimension"):
        model.update_from_override([1.0], label=1)
    with pytest.raises(ValueError, match="binary"):
        model.update_from_override([1.0, 0.0], label=2)
