"""Build traceable manual transcriptions from acquired texts, not expected outputs.

Every CSV row has a sidecar locating its original value(s). Quarterly observations
are differences of cumulative endpoints, never sums of cumulative values.
"""
import csv
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.knowledge.tabular import COLUMNS


def build(root):
    from pypdf import PdfReader
    records = {r['evidence_key']: r for r in json.loads(Path('data/samples/w5/evidence.json').read_text(encoding='utf8'))['records']}
    paths = {}
    def text(key, page=None):
        p = root/(key+'.pdf' if page else key+'-rendition.txt')
        s = PdfReader(p).pages[page-1].extract_text() if page else p.read_text(encoding='utf8')
        if not page:
            from scripts.capture_w5_web import validate_rendition
            validate_rendition(s, records[key]['source_url'])
        paths[key] = dict(file=p.name, sha256=sha256(p.read_bytes()).hexdigest(),
                          source_url=records[key]['source_url'], original_page=page)
        return s
    outputs = []
    def write(case, rows):
        p=root/(case+'-transcribed.csv')
        with p.open('w',encoding='utf8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=COLUMNS);w.writeheader()
            for row, provenance in rows:
                w.writerow(row)
        outputs.append(dict(case_id=case,file=p.name,sha256=sha256(p.read_bytes()).hexdigest(),
                            representation='manual-transcription',rows=[dict(csv_row=i+2,**prov) for i,(_,prov) in enumerate(rows)]))
    def row(value,period,*,indicator='sales',entity='',scope='中国汽车工业协会统计口径；全国新能源汽车销量',unit='万辆',currency='',granularity='year'):
        return dict(indicator=indicator,name=indicator,entity_id=entity,period=period,
                    granularity=granularity,region='中国',scope=scope,raw_value=str(value),
                    raw_unit=unit,multiplier='10000' if unit=='万辆' else '1',unit='辆' if unit=='万辆' else unit,currency=currency)
    annual=[]
    for year,key,value in [(2022,'EV-R01-MIIT-2022-ANNUAL','688.7'),(2023,'EV-R01-MIIT-2023-ANNUAL','949.5'),(2024,'EV-R01-MIIT-2024-ANNUAL','1286.6')]:
        s=text(key)
        # Validate the actual cumulative sales clause, not an arbitrary number.
        compact=re.sub(r'\s','',s)
        if not re.search(r'1[—－–-]12月.{0,35}新能源汽车产销分别完成[\d.]+万辆和'+re.escape(value)+r'万辆',compact):
            raise ValueError('annual source clause not found: '+key)
        annual.append((row(value,str(year)),dict(evidence_keys=[key],source_values=[value],operation='direct')))
    write('R01',annual)
    quarters=[]
    for year,values in [(2023,['158.6','374.7','627.8','949.5']),(2024,['209','494.4','832','1286.6'])]:
        previous=Decimal(0); previous_key=None
        for i,(suffix,value) in enumerate(zip(['Q1','H1','9M','ANNUAL'],values),1):
            key=f'EV-R03-MIIT-{year}-{suffix}'
            s=re.sub(r'\s','',text(key))
            if not re.search(r'新能源汽车产销分别完成[\d.]+万辆和'+re.escape(value)+r'(?:\.0)?万辆',s):
                raise ValueError('cumulative sales clause missing: '+key)
            number=Decimal(value)
            keys=[key]+([previous_key] if previous_key else [])
            quarters.append((row(number-previous,f'{year}-Q{i}',granularity='quarter'),dict(
                evidence_keys=keys,source_values=[value,str(previous)],operation='cumulative_difference')))
            previous,previous_key=number,key
    write('R03',quarters)
    byds=text('EV-R02-BYD-CNINFO-P34',34)
    matched=re.search(r'研发投入金额（元）\s*([\d,.]+)\s+([\d,.]+)',byds)
    if not matched:raise ValueError('BYD table row missing')
    rd=[]
    for year,value in [('2024',matched[1]),('2023',matched[2])]:
        rd.append((row(value.replace(',',''),year,indicator='rd_investment',entity='byd',unit='元',currency='CNY',scope='比亚迪合并口径；费用化与资本化研发投入合计'),dict(evidence_keys=['EV-R02-BYD-CNINFO-P34'],source_values=[value],operation='direct')))
    for year in ('2023','2024'):
        key=f'EV-R02-SAIC-{year}-P13';s=text(key,13)
        match=re.search(r'研发投入(?:合计|总额)\s*([\d,.]+)',s)
        if not match:raise ValueError('SAIC total row missing '+year)
        rd.append((row(match[1].replace(',',''),year,indicator='rd_investment',entity='saic',unit='元',currency='CNY',scope='上汽集团合并口径；费用化与资本化研发投入合计'),dict(evidence_keys=[key],source_values=[match[1]],operation='direct')))
    write('R02',rd)
    manifest=dict(sources=paths,tables=outputs)
    (root/'transcriptions.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    return manifest


if __name__=='__main__':
    build(Path(sys.argv[1]))
