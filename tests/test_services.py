from app import services
from app.checker import Availability, CheckOutcome
from app.models import CheckResult, Product
from app.services import is_restock, run_check_for_product


def test_is_restock_truth_table():
    assert is_restock(None, "in_stock") is True
    assert is_restock("out_of_stock", "in_stock") is True
    assert is_restock("blocked_or_unknown", "in_stock") is True
    assert is_restock("in_stock", "in_stock") is False
    assert is_restock(None, "out_of_stock") is False


def test_run_check_stores_result_and_alerts_on_restock(session, monkeypatch):
    monkeypatch.setattr(
        services, "run_check", lambda url, **kwargs: CheckOutcome(Availability.IN_STOCK, "mock")
    )
    sent: list[bool] = []
    monkeypatch.setattr(
        services, "dispatch_restock_alerts", lambda *args, **kwargs: sent.append(True)
    )

    product = Product(name="X", url="https://u", variant={}, zip_code="22903")
    session.add(product)
    session.flush()

    result = run_check_for_product(session, product)
    assert result.status == "in_stock"
    assert session.query(CheckResult).count() == 1
    assert sent == [True]


def test_no_alert_when_already_in_stock(session, monkeypatch):
    monkeypatch.setattr(
        services, "run_check", lambda url, **kwargs: CheckOutcome(Availability.IN_STOCK, "mock")
    )
    sent: list[bool] = []
    monkeypatch.setattr(
        services, "dispatch_restock_alerts", lambda *args, **kwargs: sent.append(True)
    )

    product = Product(name="X", url="https://u", variant={})
    session.add(product)
    session.flush()
    session.add(CheckResult(product_id=product.id, status="in_stock", detail="prev"))
    session.flush()

    run_check_for_product(session, product)
    assert sent == []


def test_out_of_stock_does_not_alert(session, monkeypatch):
    monkeypatch.setattr(
        services, "run_check", lambda url, **kwargs: CheckOutcome(Availability.OUT_OF_STOCK, "mock")
    )
    sent: list[bool] = []
    monkeypatch.setattr(
        services, "dispatch_restock_alerts", lambda *args, **kwargs: sent.append(True)
    )

    product = Product(name="X", url="https://u", variant={})
    session.add(product)
    session.flush()

    run_check_for_product(session, product)
    assert sent == []


def test_restock_alerts_through_a_blocked_gap(session, monkeypatch):
    # out_of_stock -> blocked -> in_stock must still alert (last known is out_of_stock).
    monkeypatch.setattr(
        services, "run_check", lambda url, **kwargs: CheckOutcome(Availability.IN_STOCK, "mock")
    )
    sent: list[bool] = []
    monkeypatch.setattr(
        services, "dispatch_restock_alerts", lambda *args, **kwargs: sent.append(True)
    )

    product = Product(name="X", url="https://u", variant={})
    session.add(product)
    session.flush()
    session.add(CheckResult(product_id=product.id, status="out_of_stock", detail=""))
    session.add(CheckResult(product_id=product.id, status="blocked_or_unknown", detail=""))
    session.flush()

    run_check_for_product(session, product)
    assert sent == [True]


def test_no_realert_when_blocked_between_in_stock_checks(session, monkeypatch):
    # in_stock -> blocked -> in_stock must not re-alert (last known is in_stock).
    monkeypatch.setattr(
        services, "run_check", lambda url, **kwargs: CheckOutcome(Availability.IN_STOCK, "mock")
    )
    sent: list[bool] = []
    monkeypatch.setattr(
        services, "dispatch_restock_alerts", lambda *args, **kwargs: sent.append(True)
    )

    product = Product(name="X", url="https://u", variant={})
    session.add(product)
    session.flush()
    session.add(CheckResult(product_id=product.id, status="in_stock", detail=""))
    session.add(CheckResult(product_id=product.id, status="blocked_or_unknown", detail=""))
    session.flush()

    run_check_for_product(session, product)
    assert sent == []
