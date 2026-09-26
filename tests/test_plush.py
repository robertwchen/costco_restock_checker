import json
from datetime import UTC, datetime
from email.utils import format_datetime
from urllib.parse import urlencode

import pytest

from app.checker import INVENTORY_API, Availability
from app.plush import ANIMALS, inventory_outcome, variant_map


def evidence(**updates):
    url = INVENTORY_API + '/2005333?' + urlencode({'destinationPostalCode': '00000', 'destinationCountryCode': 'US'})
    args = dict(animal='Capybara', mapped_sku='2005333', requested_url=url,
                response_url=url, zip_code='00000', date_header=format_datetime(datetime.now(UTC)))
    args.update(updates)
    return args


@pytest.mark.parametrize('flag,state,status', [(True, 'INSTOCK', 'in_stock'), (False, 'NOSTOCK', 'out_of_stock'),
    (True, 'NOSTOCK', 'blocked_or_unknown'), (False, 'INSTOCK', 'blocked_or_unknown'),
    ('true', 'INSTOCK', 'blocked_or_unknown'), (None, None, 'blocked_or_unknown')])
def test_exact_inventory(flag, state, status):
    data = {'itemNumber': '2005333', 'availableForSale': flag, 'availability': state}
    assert inventory_outcome(data, **evidence()).status == status


@pytest.mark.parametrize('data', [None, [], {}, 'invalid',
    {'itemNumber': '2005332', 'availableForSale': True, 'availability': 'INSTOCK'},
    [{'itemNumber': '2005333', 'availableForSale': False, 'availability': 'NOSTOCK'},
     {'itemNumber': '2005332', 'availableForSale': True, 'availability': 'INSTOCK'}]])
def test_other_animals_never_alert(data):
    assert inventory_outcome(data, **evidence()).availability == Availability.BLOCKED_OR_UNKNOWN


@pytest.mark.parametrize('updates', [dict(mapped_sku=None), dict(mapped_sku='2005332'),
    dict(zip_code='99999'), dict(zip_code=''), dict(response_url='https://example.com'),
    dict(date_header=None), dict(date_header='Sat, 01 Jan 2000 00:00:00 GMT'),
    dict(age_header='121'), dict(age_header='invalid')])
def test_identity_location_and_freshness(updates):
    data = {'itemNumber': '2005333', 'availableForSale': True, 'availability': 'INSTOCK'}
    assert inventory_outcome(data, **evidence(**updates)).status == 'blocked_or_unknown'


def test_response_location_conflict():
    data = {'itemNumber': '2005333', 'availableForSale': True, 'availability': 'INSTOCK', 'destinationPostalCode': '99999'}
    assert inventory_outcome(data, **evidence()).status == 'blocked_or_unknown'


def test_mapping_requires_parent_and_exact_attribute():
    objects = [{'key': 'Design', 'value': animal, 'itemNumber': sku, 'parentId': '4201016777'} for animal, sku in ANIMALS.items()]
    assert variant_map(['abc:' + json.dumps(objects) + '\n']) == ANIMALS
    objects.append({'key': 'Design', 'value': 'Capybara', 'itemNumber': 'wrong', 'parentId': '4201016777'})
    assert 'Capybara' not in variant_map(['abc:' + json.dumps(objects) + '\n'])
    assert variant_map(['abc:' + json.dumps({'key': 'Design', 'value': 'Capybara', 'itemNumber': '2005333'})]) == {}


def test_seed_preserves_existing_watch(session):
    from app.models import Product
    from app.seed import seed_capybara
    session.add(Product(name='Mattress', url='https://example.com'))
    session.flush()
    assert seed_capybara(session).id == seed_capybara(session).id
    assert session.query(Product).count() == 2
