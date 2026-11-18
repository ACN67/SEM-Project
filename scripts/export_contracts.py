"""Run from any directory. --check detects drift without overwriting files."""
import argparse
import json
import re
import sys
from pathlib import Path
from pydantic import TypeAdapter
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.app.contracts import models as m
from backend.app.contracts import services as svc

CHECK = "--check" in sys.argv
def save(path, data):
    dest = ROOT / "shared/contracts/schema" / path
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if CHECK:
        if dest.read_text(encoding="utf-8") != content:
            raise SystemExit(f"Schema drift: {dest}")
    else:
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(content,encoding="utf-8")

schemas = {}
def schema(model):
    wire = TypeAdapter(model).json_schema(ref_template="#/components/schemas/{model}")
    schemas.update(wire.pop("$defs",{}))
    if "title" in wire:
        name = re.sub(r"[^a-zA-Z0-9_]","_",wire["title"])
        schemas[name] = wire
        return {"$ref":"#/components/schemas/"+name}
    return wire

def json_body(s):
    return {"application/json":{"schema":s}}

paths = {}
def route(method, path, response=None, body=None, code=200, *, paged=False, key=False, auth=False, upload=False, download=False, errors=()):
    params = []
    for name in re.findall(r"{(\w+)}",path):
        params.append({"name":name,"in":"path","required":True,"schema":{"type":"integer","minimum":1} if name=="version" else {"type":"string","minLength":1}})
    if paged:
        for name,default in [("page",1),("limit",20)]:
            params.append({"name":name,"in":"query","schema":{"type":"integer","minimum":1,"default":default,**({"maximum":100} if name=="limit" else {})}})
    if key:
        params.append({"name":"Idempotency-Key","in":"header","required":True,"schema":{"type":"string","minLength":1,"maxLength":128}})
    success = {"description":"成功"}
    if response is not None:
        success["content"] = json_body(schema(m.Page[response] if paged else response))
    if download:
        success["content"] = {t:{"schema":{"type":"string","format":"binary"}} for t in ["application/pdf","application/vnd.openxmlformats-officedocument.wordprocessingml.document"]}
    responses = {str(code):success}
    for status in sorted({422,500,503,*errors}):
        responses[str(status)]={"description":"见 error.code 和 details","content":json_body(schema(m.Error))}
    op={"operationId":method+"_"+re.sub(r"\W+","_",path).strip("_"),"parameters":params,"responses":responses}
    if auth:
        op["security"]=[{"adminSession":[]}]
    if body:
        op["requestBody"]={"required":True,"content":json_body(schema(body))}
    if upload:
        op["requestBody"]={"required":True,"content":{"multipart/form-data":{"schema":{"type":"object","required":["file","metadata"],"properties":{"file":{"type":"string","format":"binary"},"metadata":schema(m.Metadata)}},"encoding":{"metadata":{"contentType":"application/json"}}}}}
    paths.setdefault(path,{})[method.lower()] = op

route("POST","/admin/session",body=m.Login,code=204,errors=(401,429))
paths["/admin/session"]["post"]["responses"]["204"]["headers"]={"Set-Cookie":{"schema":{"type":"string"},"description":"sem_admin 管理会话"}}
route("DELETE","/admin/session",code=204,auth=True,errors=(401,403))
route("GET","/templates",m.Template,paged=True)
route("GET","/templates/{template_id}/versions/{version}",m.Template,errors=(404,))
route("POST","/documents",m.IngestAccepted,code=202,upload=True,auth=True,errors=(401,403,413,415))
route("GET","/documents",m.DocumentRecord,paged=True)
route("GET","/documents/{document_id}",m.DocumentRecord,errors=(404,))
route("GET","/documents/{document_id}/versions/{document_version_id}",m.DocumentRecord,errors=(404,))
route("GET","/reports",m.ReportSummary,paged=True)
route("POST","/reports",m.JobAccepted,m.Spec,202,key=True,errors=(404,409,429))
report="/reports/{report_id}"
job="/jobs/{job_id}"
version=report+"/versions/{version_id}"
chapter=report+"/chapters/{chapter_id}"
route("GET",report,m.ReportSummary,errors=(404,))
route("GET",report+"/versions",m.Version,paged=True,errors=(404,))
route("GET",version,m.ReportDocument,errors=(404,))
route("GET",job,m.JobDetail,errors=(404,))
route("GET",job+"/chapters/{chapter_id}",m.Preview,errors=(404,))
route("POST",job+"/cancel",m.JobDetail,errors=(404,409))
route("POST",job+"/retry",m.JobAccepted,code=202,key=True,errors=(404,409,429))
route("PATCH",chapter,m.Edited,m.Edit,errors=(404,409))
route("POST",chapter+"/regenerate",m.JobAccepted,m.Rewrite,202,key=True,errors=(404,409,429))
route("POST",version+"/exports",m.ExportAccepted,m.ExportRequest,202,key=True,errors=(404,409))
route("GET","/exports/{export_id}",m.ExportSummary,errors=(404,))
route("GET","/exports/{export_id}/download",download=True,errors=(404,409))
route("GET",version+"/assets/{asset_id}",errors=(404,))
paths[version+"/assets/{asset_id}"]["get"]["responses"]["200"]["content"]={"image/png":{"schema":{"type":"string","format":"binary"}}}

for model in (m.Industry,m.Comparison,m.Prosperity,m.Investment,m.Overseas):
    save("forms/"+model.__name__+".json",model.model_json_schema())
for model in (m.ChapterInput,m.Filters,m.Coverage,svc.Artifact,svc.Calculation,svc.ChartResult):
    schema(model)
save("openapi.json",{"openapi":"3.1.0","info":{"title":"SEM Project","version":"1.0.0"},"servers":[{"url":"/api/v1"}],"paths":paths,"components":{"securitySchemes":{"adminSession":{"type":"apiKey","in":"cookie","name":"sem_admin"}},"schemas":schemas}})
