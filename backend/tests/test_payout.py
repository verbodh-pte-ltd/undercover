from fastapi.testclient import TestClient
from sqlalchemy import update

from backend import store
from backend.main import app
from backend.tests.signing import AGENT

client = TestClient(app, headers=AGENT)


def line(description, amount, kind="item", quantity=1, **extra):
    return {
        "description": description,
        "amount": amount,
        "quantity": quantity,
        "kind": kind,
        "nights": extra.get("nights", 0),
        "hours": extra.get("hours", 0),
    }


# CLM-1011 is on POL-2011, a Premium policy with a $3,400 trip.
def ask(lines, benefit="baggage", paid_by_others=0, claim_id="CLM-1011"):
    body = {"benefit": benefit, "lines": lines, "paid_by_others": paid_by_others}
    return client.post(f"/api/claims/{claim_id}/payout", json=body)


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


def test_needs_sign_off_follows_the_approval_limit_setting():
    lines = [line("Medical bill", 1250, kind="expense")]
    assert ask(lines, "emergency_medical").json()["needs_sign_off"] is True
    with store.connect() as conn:
        conn.execute(
            update(store.settings)
            .where(store.settings.c.name == "approval_limit")
            .values(value="5000")
        )
        conn.commit()
    assert ask(lines, "emergency_medical").json()["needs_sign_off"] is False


def test_unknown_claim_is_404():
    assert ask([line("Suitcase", 100)], claim_id="CLM-0000").status_code == 404


def test_the_policy_comes_from_the_claim():
    # CLM-1006 is on a First policy, which has no deductible.
    lines = [line("Medical bill", 400, kind="expense")]
    assert ask(lines, "emergency_medical", claim_id="CLM-1006").json()["payout"] == 400
    assert ask(lines, "emergency_medical").json()["payout"] == 350


def test_payout_operation_has_no_optional_inputs():
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/claims/{claim_id}/payout"]["post"]
    assert operation["operationId"] == "calculate_payout"
    assert operation["summary"] or operation["description"]
    components = schema["components"]["schemas"]
    for name in ["PayoutQuestion", "Line"]:
        properties = set(components[name]["properties"])
        assert set(components[name]["required"]) == properties
