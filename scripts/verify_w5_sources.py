"""Fetch and verify registered originals. Raw files stay outside Git."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import urllib.request

def acquire(record, root):
    result={k:record[k] for k in ("evidence_key","case_id","source_id","title","publisher","published_at","source_url","locator")}
    result["checked_at"]=datetime.now(timezone.utc).isoformat()
    result["distribution"]="external-original"
    url=record["source_url"]
    suffix=".pdf" if ".pdf" in url.lower() else ".xlsx" if ".xlsx" in url.lower() else ".html"
    target=root/(record["evidence_key"]+suffix)
    try:
        if not target.exists():
            request=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(request,timeout=30) as response:
                content=response.read()
            temporary=target.with_suffix(suffix+".part")
            temporary.write_bytes(content)
            temporary.replace(target)
        content=target.read_bytes()
        digest=sha256(content).hexdigest()
        result.update(file=target.name,sha256=digest,size_bytes=len(content))
        expected=record.get("source_hash")
        if expected and digest!=expected:
            raise ValueError("registered hash mismatch")
        if suffix==".pdf":
            from pypdf import PdfReader
            reader=PdfReader(target)
            page=record["locator"]["page"]
            text=reader.pages[page-1].extract_text()
            result["located_text"]=text
            result["status"]="LOCATED_REQUIRES_REVIEW"
        elif suffix==".xlsx":
            from openpyxl import load_workbook
            book=load_workbook(target,read_only=True,data_only=True)
            result["sheets"]=book.sheetnames
            book.close()
            result["status"]="ORIGINAL_REQUIRES_CELL_REVIEW"
        else:
            result["status"]="WEB_REQUIRES_FROZEN_PDF"
            snapshot=root/(record["evidence_key"]+"-snapshot.pdf")
            if snapshot.exists():
                from pypdf import PdfReader
                pages=PdfReader(snapshot).pages
                for number,page in enumerate(pages,1):
                    text=page.extract_text()
                    if record["title"] in text and record["published_at"] in text:
                        result.update(file=snapshot.name,sha256=sha256(snapshot.read_bytes()).hexdigest(),
                            size_bytes=snapshot.stat().st_size,locator={"kind":"pdf","page":number},
                            status="LOCATED_REQUIRES_REVIEW",located_text=text)
                        break
    except Exception as exc:
        result.update(status="BLOCKED",error=str(exc))
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    args=parser.parse_args()
    args.root.mkdir(parents=True,exist_ok=True)
    records=json.loads(Path("data/samples/w5/evidence.json").read_text(encoding="utf8"))["records"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda r:acquire(r,args.root),records))
    (args.root/"acquisition.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps({status:sum(r["status"]==status for r in results) for status in sorted({r["status"] for r in results})}))

if __name__=="__main__":
    main()
