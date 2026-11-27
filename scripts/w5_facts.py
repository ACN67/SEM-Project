"""Explicit source facts, independently checkable against original pages."""
import unicodedata

EXPECTED = {
    "EV-R02-BYD-2023-HK-P273": ["39,917,743"],
    "EV-R02-BYD-CNINFO-P34": ["54,160,964,000.00"],
    "EV-R02-SAIC-2023-P13": ["22,013,246,493.11", "元"],
    "EV-R02-SAIC-2024-P13": ["21,813,090,045.30", "元"],
    "EV-R04-HEFEI-IMPL-P3": ["动力电池"],
    "EV-R04-HEFEI-PLAN-P11": ["目标"],
    "EV-R04-HEFEI-PLAN-P12": ["300", "GWh"],
    "EV-R05-KBA-PDF-P1": ["380.609"],
    "EV-R04-GOTION-LAB-2023": ["大众", "认证", "2023-02-21"],
}

def check_fact(key, evidences):
    def normalize(text):
        return "".join(unicodedata.normalize("NFKC",text).split())
    text=normalize(" ".join(e.excerpt for e in evidences))
    tokens=EXPECTED.get(key)
    return bool(tokens) and all(normalize(token) in text for token in tokens)
