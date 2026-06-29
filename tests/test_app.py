from fastapi.testclient import TestClient

from app.main import app, parse_variant


def test_parse_variant():
    assert parse_variant("Bed Size=Full, Firmness=Firm") == {
        "Bed Size": "Full",
        "Firmness": "Firm",
    }
    assert parse_variant("") == {}
    assert parse_variant("junk, =value, key=") == {"key": ""}


def test_health():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_index_lists_seeded_product():
    with TestClient(app) as client:
        response = client.get("/")
        assert response.status_code == 200
        assert "Novaform" in response.text


def test_create_then_remove_product():
    with TestClient(app) as client:
        response = client.post(
            "/products",
            data={
                "name": "Widget",
                "url": "https://www.costco.com/p/widget/9",
                "item_number": "",
                "zip_code": "",
                "variant": "Color=Red",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert "Widget" in client.get("/").text

        # The seeded product is id 1, so the new product is id 2.
        client.post("/products/2/delete", follow_redirects=False)
        assert "Widget" not in client.get("/").text
