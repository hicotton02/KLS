"""Read only explicitly cited, complete bill text from the official Record."""
from datetime import datetime
import re

from bs4 import BeautifulSoup


def cited_record_locations(congress: int, kind: str, actions: list[dict]) -> list[tuple[str, str, str]]:
    chamber = {'S': 'S', 'HR': 'H'}.get(kind)
    if not chamber:
        return []
    locations = []
    latest = max((str(action.get('statusDate') or '')[:10] for action in actions), default='')
    for action in actions:
        date = str(action.get('statusDate') or '')[:10]
        if date != latest:
            continue
        try:
            parsed = datetime.strptime(date, '%Y-%m-%d')
        except ValueError:
            continue
        if parsed.year not in (congress * 2 + 1787, congress * 2 + 1788):
            continue
        match = re.search(r'\btext:\s*CR\s+([HS])(\d+)(?:[-\u2013](?:[HS])?\d+)?', str(action.get('statusMessage') or ''))
        if not match or match[1] != chamber:
            continue
        page = chamber + match[2]
        url = f'https://www.govinfo.gov/content/pkg/CREC-{date}/html/CREC-{date}-pt1-Pg{page}.htm'
        location = (date, page, url)
        if location not in locations:
            locations.append(location)
    return locations


def extract_record_bill_text(html: str, *, kind: str, number: str, date: str, page: str) -> str | None:
    if kind not in ('HR', 'S') or not number.isdigit():
        return None
    pre = BeautifulSoup(html, 'html.parser').find('pre')
    if pre is None:
        return None
    text = pre.get_text()
    record_date = re.search(r'\([A-Za-z]+,\s*([A-Za-z]+\s+\d{1,2},\s*\d{4})\)', text[:500])
    if not record_date:
        return None
    try:
        observed = datetime.strptime(record_date[1], '%B %d, %Y').date().isoformat()
    except ValueError:
        return None
    chamber = 'Senate' if kind == 'S' else 'House'
    if (observed != date or f'[{chamber}]' not in text[:500]
            or not re.search(r'\[Pages? ' + re.escape(page) + r'(?:[-\]][^\n]*|\])', text[:500])):
        return None
    label = r'S\.' if kind == 'S' else r'H\.\s*R\.'
    headings = list(re.finditer(r'(?m)^[ \t]*' + label + r'\s*' + re.escape(number) + r'[ \t]*$', text))
    if len(headings) != 1:
        return None
    body = text[headings[0].end():]
    if not re.match(r'\s*Be it enacted by the Senate and House of Representatives', body):
        return None
    footer = re.search(r'(?m)^[ \t]*_{10,}[ \t]*\s*\Z', body)
    if footer is None:
        return None
    body = body[:footer.start()]
    if (re.search(r'(?m)^[ \t]*By (?:Mr|Ms|Mrs)\.', body)
            or re.search(r'(?m)^[ \t]*(?:(?:Mr|Ms|Mrs)\.\s|The (?:SPEAKER|PRESIDING OFFICER)\b)', body)
            or re.search(r'(?m)^[ \t]*(?:S\.|H\.\s*R\.)\s*\d+[ \t]*$', body)
            or not re.search(r'\b(?:SECTION|SEC\.)\s+1\.', body)):
        return None
    body = re.sub(r'(?m)^[ \t]*\[\[Page [HS]\d+\]\][ \t]*$', '', body).strip()
    return f'{kind}{number}\n\n{body}' if len(body) >= 300 else None
