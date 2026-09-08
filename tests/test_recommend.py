"""Tests for the recommendation layer.

    pytest tests/test_recommend.py

None of these need the fitted pipeline. That is the point of the design: lifts
come from the committed lever curves, not from inverting the yield model, so
/api/v1/recommend answers on a deployment whose 125 MB artefact never arrived.

Everything is in kg/ha. There is no costing and no budget anywhere in this
layer -- the survey carries no price data, so advice stays in yield units.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api.curves import basis, contrast, get_curves, value_at   # noqa: E402
from api.main import app                                       # noqa: E402

client = TestClient(app)

# A plot doing everything the survey says is unhelpful: local seed, no
# fertiliser, sown in mid-April well past the favourable window.
POOR_PRACTICE = {"district": "bungoma", "plant_date_doy": 110,
                 "seed_category": "local", "dap_kg_ph": 0, "can_kg_ph": 0,
                 "plot_acres": 1.0}

# The same plot already at the fitted optimum on the levers that carry weight.
GOOD_PRACTICE = {"district": "bungoma", "plant_date_doy": 65,
                 "seed_category": "hybrid_branded", "dap_kg_ph": 130,
                 "can_kg_ph": 125, "plot_acres": 1.0}


def post(body: dict) -> dict:
    response = client.post("/api/v1/recommend", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- reference --------------------------------------------------------------
def test_levers_are_discoverable_and_every_curve_is_plausible():
    body = client.get("/api/v1/reference/levers").json()
    names = {l["name"] for l in body["levers"]}
    assert {"planting_date", "basal_fertiliser", "topdress_fertiliser",
            "hybrid_seed"} <= names
    # CRISP-DM §1.3: advice that contradicts known agronomics will not be
    # adopted. The check is meaningful because the curve shape is fitted.
    assert all(l["agronomically_plausible"] for l in body["levers"])


def test_levers_do_not_double_count_the_same_input():
    """n_kg_ph is arithmetic on DAP/urea/NPK/CAN; advising on both would credit
    one bag of fertiliser twice."""
    columns = {l["column"] for l in get_curves().levers}
    assert not columns & {"n_kg_ph", "p2o5_kg_ph", "total_nutrient_kg_ph",
                          "uses_hybrid_seed", "compost_applied"}


def test_index_advertises_the_endpoint():
    assert client.get("/").json()["endpoints"]["recommend"] == "POST /api/v1/recommend"


# --- the shape of the advice ------------------------------------------------
def test_poor_practice_plot_is_told_to_plant_earlier_and_use_hybrid():
    body = post({"plot": POOR_PRACTICE, "year": 2020})
    actions = {r["lever"]: r for r in body["recommendations"]}
    assert "hybrid_seed" in actions
    assert actions["planting_date"]["recommended_value"] < 110
    assert all(r["expected_lift_kg_ph"] > 0 for r in body["recommendations"])
    # Ranked by size of the lift.
    lifts = [r["expected_lift_kg_ph"] for r in body["recommendations"]]
    assert lifts == sorted(lifts, reverse=True)


def test_good_practice_plot_is_not_told_to_change_what_it_already_gets_right():
    body = post({"plot": GOOD_PRACTICE, "year": 2020})
    changed = {r["lever"] for r in body["recommendations"]}
    assert not ({"planting_date", "hybrid_seed", "basal_fertiliser"} & changed)
    skipped = {s["lever"] for s in body["skipped"]}
    assert {"planting_date", "hybrid_seed"} <= skipped


def test_doing_nothing_is_a_permitted_answer():
    """Every lever restricted to one the plot already gets right must yield an
    empty, explained recommendation list rather than an invented change."""
    body = post({"plot": GOOD_PRACTICE, "year": 2020, "levers": ["planting_date"]})
    assert body["recommendations"] == []
    assert body["bundle"]["total_expected_lift_kg_ph"] == 0
    assert "not a gap" in body["bundle"]["note"]


def test_supplying_inputs_changes_the_advice():
    late = post({"plot": POOR_PRACTICE, "year": 2020})
    early = post({"plot": {**POOR_PRACTICE, "plant_date_doy": 62}, "year": 2020})
    late_timing = [r for r in late["recommendations"] if r["lever"] == "planting_date"]
    early_timing = [r for r in early["recommendations"] if r["lever"] == "planting_date"]
    assert late_timing and not early_timing


def test_unstated_inputs_are_flagged_as_the_districts_practice_not_this_plot():
    body = post({"plot": {"district": "bungoma"}, "year": 2020})
    assert any(r["current_is_assumed"] for r in body["recommendations"])
    assert any("median practice" in w for w in body["warnings"])


# --- gates ------------------------------------------------------------------
def test_thin_support_is_refused():
    """Lime is applied by 4% of plots, so its fitted optimum sits in a region
    holding ~100 of them. Raising the support floor must retire it."""
    loose = post({"plot": POOR_PRACTICE, "year": 2020, "min_support": 200})
    strict = post({"plot": POOR_PRACTICE, "year": 2020, "min_support": 2000})
    assert "lime" in {r["lever"] for r in loose["recommendations"]}
    assert "lime" not in {r["lever"] for r in strict["recommendations"]}
    assert any(s["lever"] == "lime" for s in strict["skipped"])


def test_min_lift_filters_small_changes():
    big = post({"plot": POOR_PRACTICE, "year": 2020, "min_lift_kg_ph": 400})
    assert big["recommendations"]
    assert all(r["expected_lift_kg_ph"] >= 400 for r in big["recommendations"])


def test_no_recommendation_extrapolates_beyond_the_fitted_range():
    body = post({"plot": POOR_PRACTICE, "year": 2020})
    curves = get_curves()
    for rec in body["recommendations"]:
        spec = curves.get(rec["lever"])
        if spec["kind"] == "categorical":
            assert rec["recommended_value"] in spec["levels"]
        else:
            low, high = spec["basis"]["domain"]
            assert low <= rec["recommended_value"] <= high


def test_every_reported_interval_excludes_zero():
    """The evidence gate is a one-sided t-test, so a 90% lower bound below zero
    would mean the gate leaked."""
    body = post({"plot": POOR_PRACTICE, "year": 2020})
    assert all(r["lift_low_kg_ph"] > 0 for r in body["recommendations"])


def test_unknown_lever_is_rejected_with_the_valid_names():
    response = client.post("/api/v1/recommend",
                           json={"plot": POOR_PRACTICE, "levers": ["fertiliser"]})
    assert response.status_code == 422
    assert "planting_date" in response.json()["detail"]


def test_unknown_district_is_advised_with_a_warning_not_refused():
    body = post({"plot": {**POOR_PRACTICE, "district": "atlantis"}, "year": 2020})
    assert body["district_known"] is False
    assert body["recommendations"]
    assert any("not one the model was trained on" in w for w in body["warnings"])


# --- no money anywhere -----------------------------------------------------
def test_the_response_carries_no_cost_or_budget_fields():
    """The layer is deliberately priceless: report §1.4 says Project 1 must stay
    in yield units rather than depend on price data this survey does not have."""
    body = post({"plot": POOR_PRACTICE, "year": 2020})
    banned = {"cost", "kg_per_currency_unit", "in_budget", "currency",
              "budget", "budget_remaining", "total_cost", "price_key"}
    assert not banned & set(body["bundle"])
    for rec in body["recommendations"]:
        assert not banned & set(rec)
    assert "kg/ha" in body["bundle"]["note"]


def test_pricing_fields_are_rejected_rather_than_silently_ignored():
    for payload in ({"plot": POOR_PRACTICE, "budget": 4000},
                    {"plot": POOR_PRACTICE, "prices": {"dap_kg": 65}},
                    {"plot": POOR_PRACTICE, "objective": "expected"}):
        response = client.post("/api/v1/recommend", json=payload)
        assert response.status_code == 422, payload


def test_the_lever_reference_advertises_no_prices():
    for lever in client.get("/api/v1/reference/levers").json()["levers"]:
        assert "price_key" not in lever


def test_the_bundle_is_every_recommendation():
    body = post({"plot": POOR_PRACTICE, "year": 2020})
    assert body["bundle"]["levers"] == [r["lever"] for r in body["recommendations"]]
    assert body["bundle"]["total_expected_lift_kg_ph"] == pytest.approx(
        sum(r["expected_lift_kg_ph"] for r in body["recommendations"]), abs=0.11)


def test_the_plateau_rule_asks_for_the_smallest_adequate_change():
    """The DAP curve is flat above ~120 kg/ha, so the advice must be the near
    edge of that plateau, not the top of the fitted range."""
    body = post({"plot": POOR_PRACTICE, "year": 2020, "levers": ["basal_fertiliser"]})
    rec = body["recommendations"][0]
    domain_high = get_curves().get("basal_fertiliser")["basis"]["domain"][1]
    assert rec["recommended_value"] < domain_high / 2


# --- the curve maths --------------------------------------------------------
def test_contrast_of_a_value_with_itself_is_exactly_zero():
    for spec in get_curves().levers:
        current = spec["population"]["median"]
        lift, se = contrast(spec, current, current)
        assert lift == 0.0 and se == 0.0


def test_contrasts_are_antisymmetric_and_additive():
    spec = get_curves().get("topdress_fertiliser")
    a, b, mid = 0.0, 150.0, 75.0
    assert contrast(spec, b, a)[0] == pytest.approx(-contrast(spec, a, b)[0])
    assert (contrast(spec, mid, a)[0] + contrast(spec, b, mid)[0]
            == pytest.approx(contrast(spec, b, a)[0]))


def test_values_outside_the_fitted_support_are_clamped_not_extrapolated():
    spec = get_curves().get("basal_fertiliser")
    high = spec["basis"]["domain"][1]
    assert value_at(spec, high * 10) == pytest.approx(value_at(spec, high))
    assert basis(spec, -50).tolist() == basis(spec, spec["basis"]["domain"][0]).tolist()


def test_the_planting_window_reproduces_the_notebook_finding():
    """Notebook 03 §6: flat through mid-March, then a monotone penalty of a few
    hundred kg/ha by early May. If a refit loses that, the artefact is wrong."""
    spec = get_curves().get("planting_date")
    early, late = value_at(spec, 62), value_at(spec, 122)
    assert early - late > 200
    assert value_at(spec, 55) == pytest.approx(value_at(spec, 70), abs=50)


def test_fertiliser_curves_show_diminishing_returns():
    for name in ("basal_fertiliser", "topdress_fertiliser"):
        spec = get_curves().get(name)
        low, high = spec["basis"]["domain"]
        first = value_at(spec, low + (high - low) * 0.25) - value_at(spec, low)
        last = value_at(spec, high) - value_at(spec, high - (high - low) * 0.25)
        assert first > last, f"{name} does not flatten"
