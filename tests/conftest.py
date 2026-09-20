"""Use an explicit synthetic template for offline tests, never install it in production data."""
import os
import pytest


@pytest.fixture(scope='session')
def synthetic_quote_template(tmp_path_factory):
    from scripts.build_test_quote_template import build
    return str(build(tmp_path_factory.mktemp('quote-fixture') / 'test_quote_template.xlsx'))


@pytest.fixture(autouse=True)
def offline_quote_template(monkeypatch, synthetic_quote_template):
    from catalog import quote
    if not os.environ.get('CATALOG_QUOTE_TEMPLATE'):
        monkeypatch.setattr(quote, 'TEMPLATE_V2_PATH', synthetic_quote_template)
