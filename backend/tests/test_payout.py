from fastapi.testclient import TestClient

from backend import store
from backend.main import app

client = TestClient(app)


def line(description, amount, kind="item", quantity=1, **extra):
    return {
        "description": description,
        "amount": amount,
        "quantity": quantity,
        "kind": kind,
        "nights": extra.get("nights", 0),
        "hours": extra.get("hours", 0),
    }


def ask(lines, benefit="baggage", paid_by_others=0, policy_id="POL-2011"):
    body = {
        "policy_id": policy_id,
        "benefit": benefit,
        "lines": lines,
        "paid_by_others": paid_by_others,
    }
    return client.post("/api/payouts", json=body)


def test_payout_with_limits_others_and_deductible():
    answer = ask(
        [line("Suitcase", 260), line("Dinner jacket", 400)], paid_by_others=100
    ).json()
    assert [s["amount"] for s in answer["calculation"]] == [260, 300, -100, -50]
    assert answer["payout"] == 410
    assert answer["needs_sign_off"] is False


def test_a_camera_and_its_lens_as_one_set_get_one_item_limit():
    one_set = ask([line("Camera and lens", 520, kind="electronics")]).json()
    two_items = ask([line("Camera and lens", 520, "electronics", quantity=2)]).json()
    assert one_set["calculation"][0]["amount"] == 300
    assert two_items["calculation"][0]["amount"] == 520


def test_cash_hotel_and_meals():
    answer = ask(
        [
            line("Cash", 250, kind="cash"),
            line("Hotel", 900, kind="hotel", nights=2),
            line("Meals", 200, kind="meals_without_receipts", hours=30),
        ],
        benefit="trip_delay",
    ).json()
    amounts = [s["amount"] for s in answer["calculation"]]
    assert amounts == [700, 70, 100, -70]
    assert answer["payout"] == 800


def test_cancellation_is_capped_at_trip_cost_and_never_below_zero():
    capped = ask([line("Prepaid tour", 9000, kind="expense")], "trip_cancellation")
    assert capped.json()["payout"] == 3400
    nothing = ask([line("Taxi", 40, kind="expense")], "trip_delay", 90).json()
    assert nothing["payout"] == 0
    assert nothing["calculation"][-1]["step"] == "Nothing is left to pay"


def test_needs_sign_off_follows_the_limit_in_company_json(monkeypatch):
    lines = [line("Medical bill", 1250, kind="expense")]
    assert ask(lines, "emergency_medical").json()["needs_sign_off"] is True
    real = store.load

    def higher_limit(name):
        data = real(name)
        return (
            {**data, "payout_approval_limit": 5000} if name == "company.json" else data
        )

    monkeypatch.setattr(store, "load", higher_limit)
    assert ask(lines, "emergency_medical").json()["needs_sign_off"] is False


def test_unknown_policy_is_404():
    assert ask([line("Suitcase", 100)], policy_id="POL-9999").status_code == 404


def test_payout_operation_has_no_optional_inputs():
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/payouts"]["post"]
    assert operation["operationId"] == "work_out_payout"
    assert operation["summary"] or operation["description"]
    components = schema["components"]["schemas"]
    for name in ["PayoutQuestion", "Line"]:
        properties = set(components[name]["properties"])
        assert set(components[name]["required"]) == properties
