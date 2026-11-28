"""Freeze public web text renditions for manual review, never label them originals.

The optional public text gateway is an acquisition fallback. Its response and
PDF rendition have separate hashes; a successful fetch does not verify facts.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import urllib.request
from urllib.parse import unquote
from xml.sax.saxutils import escape


def validate_rendition(text, source):
    if 'URL Source: ' + unquote(source) not in unquote(text) or len(text) < 400:
        raise ValueError('missing canonical source or incomplete rendition')
    if any(s in text.lower() for s in ('verify that you', 'captcha', 'access denied',
            'request blocked', '403 error', 'target url returned error',
            'javascript is disabled', 'the request could not be satisfied')):
        raise ValueError('challenge/error page is not evidence')


def capture(record, root, font):
    key = record['evidence_key']
    source = record.get('capture_url', record['source_url'])
    gateway = 'https://r.jina.ai/' + source
    raw = root / (key + '-rendition.txt')
    try:
        if raw.exists():
            try:
                validate_rendition(raw.read_text(encoding='utf8'), source)
            except (ValueError, UnicodeError):
                raw.unlink()
        if not raw.exists():
            request = urllib.request.Request(gateway, headers={'User-Agent': 'SEM-W5/1.0'})
            content = urllib.request.urlopen(request, timeout=55).read()
            text = content.decode('utf8')
            validate_rendition(text, source)
            temporary=raw.with_suffix('.txt.part')
            temporary.write_bytes(content)
            temporary.replace(raw)
        text = raw.read_text(encoding='utf8')
        validate_rendition(text, source)
        ranges = []
        if record.get('capture_patterns'):
            selected = []
            for pattern in record['capture_patterns']:
                match = re.search(pattern, text, re.S)
                if match is None:
                    raise ValueError('required source section not found: '+pattern)
                ranges.append([match.start(), match.end()])
                selected.append(match.group())
            text = 'SELECTED SOURCE SECTIONS / 节选转存\n' + '\n\n'.join(selected)
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        if 'W5Source' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('W5Source', font))
        style = ParagraphStyle('source', fontName='W5Source', fontSize=9, leading=14, wordWrap='CJK')
        output = root / (key + '-rendition.pdf')
        story = [Paragraph(escape('PUBLIC TEXT RENDITION / 网页文字转存，非原始PDF'), style),
                 Paragraph(escape(source), style), Spacer(1, 12)]
        for line in text.splitlines():
            line = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', line).strip()
            if line:
                story.append(Paragraph(escape(line), style))
        SimpleDocTemplate(str(output), invariant=1, leftMargin=42, rightMargin=42,
                          topMargin=42, bottomMargin=42).build(story)
        return dict(evidence_key=key, source_url=record['source_url'], capture_url=source, retrieval_url=gateway,
                    acquired_at=datetime.now(timezone.utc).isoformat(),
                    representation='gateway-text-rendition', original_locator=record['locator'],
                    text_file=raw.name, text_sha256=sha256(raw.read_bytes()).hexdigest(),
                    selected_character_ranges=ranges,
                    file=output.name, sha256=sha256(output.read_bytes()).hexdigest(),
                    status='RENDITION_REQUIRES_REVIEW')
    except Exception as exc:
        return dict(evidence_key=key, source_url=source, status='BLOCKED', error=str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--font', required=True)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    records = json.loads(Path('data/samples/w5/evidence.json').read_text(encoding='utf8'))['records']
    routes = json.loads(Path('data/samples/w5/capture_routes.json').read_text(encoding='utf8'))
    records = [{**r, **routes.get(r['evidence_key'], {})} for r in records
               if r['locator']['kind'] == 'web' or r['evidence_key'] in routes]
    # Register the font before workers create PDFs.
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    pdfmetrics.registerFont(TTFont('W5Source', args.font))
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda r: capture(r, args.root, args.font), records))
    (args.root/'renditions.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf8')
    for row in rows:
        print(row['evidence_key'], row['status'], row.get('error', ''))


if __name__ == '__main__':
    main()
