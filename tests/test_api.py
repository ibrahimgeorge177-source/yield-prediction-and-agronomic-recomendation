"""Tests for the prediction API.

    pytest tests/test_api.py

Tests that need the fitted pipeline are skipped when no artefact is present, so
the suite is still meaningful on a checkout without the model (which is the
normal state -- pipeline.joblib is too large for git). Point MODEL_DIR at an
artefact directory to run them:

    MODEL_DIR=data/models/district_v2 pytest tests/test_api.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.config import get_settings          # noqa: E402
from api.main import app                     # noqa: E402

client = TestClient(app)

needs_model = pytest.mark.skipif(
    not (get_settings().model_dir / "pipeline.joblib").exists(),
    reason="no fitted pipeline.joblib; set MODEL_DIR to an artefact directory")


# --- endpoints that work without the model ---------------------------------
def test_index():
    body = client.get("/").json()
    assert body["endpoints"]["predict"] == "POST /api/v1/predict"


def test_health_always_answers():
    body = client.get("/health").json()
    assert body["status"] in {"ok", "degraded"}
    assert body["defaults_loaded"] is True


def test_model_summary_reports_training_seasons():
    """The summary reads metadata.json, so it works before the weights load."""
    body = client.get("/api/v1/model/summary").json()
    assert body["status"] in {"ready", "not_loaded"}
    assert body["train_seasons"], "training seasons should come from metadata.json"
    assert body["prediction_unit"] == "kg/ha"
    assert len(body["limitations"]) >= 5
    assert body["expected_performance"]["r2_range"] == [0.22, 0.59]


def test_model_summary_validation_metrics():
    body = client.get("/api/v1/model/summary").json()
    for season in body["validation"]:
        assert season["season"] in body["train_seasons"] + body["validation_seasons"]
        assert -1 <= season["r2"] <= 1


def test_districts_reference():
    body = client.get("/api/v1/reference/districts").json()
    assert body["count"] == len(body["districts"]) > 40
    assert "bungoma" in body["districts"]


def test_seed_reference():
    body = client.get("/api/v1/reference/seed-types").json()
    assert set(body["seed_categories"]) == {"hybrid_branded", "other_hybrid", "local", "mixed"}
    assert body["seed_types"]


def test_input_schema_marks_only_district_required():
    body = client.get("/api/v1/reference/input-schema").json()
    required = [f["name"] for f in body["fields"] if f["required"]]
    assert required == ["district"]
    by_name = {f["name"]: f for f in body["fields"]}
    assert by_name["seed_category"]["allowed_values"]
    assert by_name["dap_kg_ph"]["unit"] == "kg/ha"


def test_openapi_renders():
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/predict" in paths and "/api/v1/model/summary" in paths


# --- request validation, no model needed ------------------------------------
@pytest.mark.parametrize("payload", [
    {"plots": []},
    {"plots": [{"year": 2020}]},                            # district is required
    {"plots": [{"district": "bungoma", "dap_kg_ph": -5}]},
    {"plots": [{"district": "bungoma", "seed_category": "nonsense"}]},
    {"plots": [{"district": "bungoma", "unknown_field": 1}]},
    {"plots": [{"district": "bungoma", "plant_date_doy": 400}]},
])
def test_invalid_requests_are_rejected(payload):
    assert client.post("/api/v1/predict", json=payload).status_code == 422


def test_request_size_limit():
    payload = {"plots": [{"district": "bungoma"}] * (get_settings().max_plots_per_request + 1)}
    assert client.post("/api/v1/predict", json=payload).status_code == 422


def test_real_survey_values_are_not_rejected():
    """The raw survey columns carry entry errors far above any agronomic
    ceiling. They must be winsorized like the training data, never 422'd."""
    response = client.post("/api/v1/predict",
                           json={"plots": [{"district": "bungoma", "dap_kg_ph": 45922.0}]})
    assert response.status_code in {200, 503}      # 503 only when the model is absent


# --- prediction -------------------------------------------------------------
@needs_model
def test_minimal_request_scores():
    body = client.post("/api/v1/predict", json={"plots": [{"district": "Kisii"}]}).json()
    plot = body["plots"][0]
    assert plot["district"] == "kisii", "district matching is case-insensitive"
    assert 0 < plot["predicted_yield_kg_ph"] < 10000
    assert body["districts"][0]["n_plots"] == 1


@needs_model
def test_supplied_inputs_are_recorded_and_used():
    payload = {"plots": [{"plot_id": "p1", "district": "bungoma", "year": 2020,
                          "plot_acres": 1.5, "seed_category": "hybrid_branded",
                          "dap_kg_ph": 60, "can_kg_ph": 50, "plant_date": "2020-03-15"}]}
    plot = client.post("/api/v1/predict", json=payload).json()["plots"][0]
    assert plot["plot_id"] == "p1"
    # A supplied fertiliser rate must move every column it moved in training.
    for column in ("dap_kg_ph", "dap_kg_ph_winsorized", "n_kg_ph", "total_nutrient_kg_ph",
                   "topdress_applied", "plant_date_doy", "plot_hectares"):
        assert column in plot["inputs_supplied"], column


@needs_model
def test_inputs_change_the_prediction():
    payload = {"plots": [
        {"district": "bungoma", "year": 2020, "seed_category": "local",
         "dap_kg_ph": 0, "can_kg_ph": 0},
        {"district": "bungoma", "year": 2020, "seed_category": "hybrid_branded",
         "dap_kg_ph": 150, "can_kg_ph": 120},
    ]}
    low, high = [p["predicted_yield_kg_ph"]
                 for p in client.post("/api/v1/predict", json=payload).json()["plots"]]
    assert high > low, "hybrid seed and fertiliser should not lower the prediction"


@needs_model
def test_district_aggregation_and_intervals():
    plots = ([{"district": "bungoma", "year": 2020, "dap_kg_ph": 50}] * 25
             + [{"district": "kisii", "year": 2020, "dap_kg_ph": 100}] * 30)
    body = client.post("/api/v1/predict", json={"plots": plots}).json()
    assert body["n_plots_scored"] == 55
    assert sorted(d["n_plots"] for d in body["districts"]) == [25, 30]
    for district in body["districts"]:
        assert (district["interval_low_kg_ph"]
                < district["predicted_mean_yield_kg_ph"]
                < district["interval_high_kg_ph"])
        assert district["below_reporting_threshold"] is False


@needs_model
def test_thin_district_is_flagged_not_dropped():
    body = client.post("/api/v1/predict", json={"plots": [{"district": "bungoma"}]}).json()
    assert body["districts"][0]["below_reporting_threshold"] is True
    assert any("sampling noise" in w for w in body["warnings"])


@needs_model
def test_unknown_district_is_scored_with_a_warning():
    body = client.post("/api/v1/predict", json={"plots": [{"district": "atlantis"}]}).json()
    plot = body["plots"][0]
    assert plot["district_known"] is False
    assert plot["predicted_yield_kg_ph"] > 0
    assert any("not one the model was trained on" in w for w in plot["warnings"])


@needs_model
def test_winsorization_is_warned_about():
    body = client.post("/api/v1/predict",
                       json={"plots": [{"district": "bungoma", "dap_kg_ph": 45922.0}]}).json()
    assert any("winsorization ceiling" in w for w in body["plots"][0]["warnings"])


@needs_model
def test_plot_predictions_can_be_suppressed():
    body = client.post("/api/v1/predict",
                       json={"plots": [{"district": "bungoma"}],
                             "include_plot_predictions": False}).json()
    assert body["plots"] == []
    assert body["districts"]


@needs_model
def test_ready_returns_200_once_the_model_loads():
    assert client.get("/ready").status_code == 200
