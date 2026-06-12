from app.text_utils import html_to_text


def test_html_to_text_strips_markup_and_keeps_words() -> None:
    html = "<html><body><h1>Bill Summary</h1><p>This bill creates a test rule.</p></body></html>"
    text = html_to_text(html)
    assert "Bill Summary" in text
    assert "This bill creates a test rule." in text
