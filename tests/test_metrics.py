"""The labelled series exist from the first scrape, at zero."""

from ocr_adapter.metrics import Metrics


def test_labelled_series_exist_before_the_first_observation():
    text = Metrics().render().decode()
    assert 'ocr_documents_total{status="ok"} 0.0' in text
    assert 'ocr_documents_total{status="timeout"} 0.0' in text
    assert 'ocr_pages_total{source="text"} 0.0' in text
    assert 'ocr_pages_total{source="ocr"} 0.0' in text
    assert 'ocr_page_seconds_count{source="ocr"} 0.0' in text
    assert 'ocr_errors_total{type="upstream_error"} 0.0' in text
    assert 'ocr_errors_total{type="internal_error"} 0.0' in text
