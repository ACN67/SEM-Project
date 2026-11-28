"""Controlled source roles and section-specific report content."""
TEXT = {
 "industry_prosperity": {
  "summary":"季度销量保持正增长，第二季度同比增速较第一季度放缓。",
  "scope":"以季度销量为指标，同期单位、区域及统计范围一致。缺失基期不填零，零基期同比无定义。",
  "quarterly":"2023 年 Q1、Q2 均为 100 辆，2024 年分别为 130 辆与 120 辆。",
  "change":"2024-Q1 同比 30%，2024-Q2 同比 20%，增速下降 10 个百分点。",
  "drivers":"受控资料仅提供销量观察值，尚不足以证明政策或需求变化的因果关系。",
  "conclusion":"销量仍在增长。驱动解释需补充资料，缺月或基期异常时暂停对应同比判断。"},
 "investment_attraction": {
  "summary":"区域配套需求与候选测试能力构成初步匹配，尚需核实具体需求与合作条件。",
  "demand":"受控区域规划提出补齐动力电池测试配套环节，此处为需求侧依据。",
  "capability":"受控企业能力说明列出电芯测试项目，此处为能力侧依据。",
  "matching":"双侧证据分别支撑需求和能力，缺需求侧时仅保留能力事实并标记待核实。",
  "constraints":"现有说明未确认服务余量、报价、场地条件或具体合作需求。",
  "contact":"公开接洽渠道待核实。建议先确认服务边界和联系人，不能表述为已有投资承诺。"},
 "overseas_assessment": {
  "summary":"市场统计和政策适用边界应分别核验，普通关税缺证限制完整税负结论。",
  "scope":"范围为中国原产新纯电乘用车进入德国，研究截止日以报告参数为准。",
  "market":"受控统计说明采用德国 BEV 全年新注册量，该指标不能直接代表进口量。",
  "policy":"受控政策说明分别列明公布日、实施日及产品对象，普通关税证据仍缺失。",
  "risk":"税项缺证会低估成本的不确定性，准入条件和适用日期需要再次核实。",
  "entry":"在补齐普通关税、准入及接洽依据前，进入建议保留条件限制。"}
}

def prepare_sources(doc, kind):
    if kind=="investment_attraction":
        titles=["受控区域需求说明","受控企业电芯测试能力说明"]
        excerpts=["区域规划提出补齐动力电池测试配套环节。","企业具备电芯测试项目能力，尚未披露具体合作意向。"]
    elif kind=="overseas_assessment":
        titles=["受控德国 BEV 注册统计说明","受控中国原产 BEV 政策说明"]
        excerpts=["德国 BEV 全年新注册量，区别于进口量。","2024-10-29 公布，2024-10-30 生效，适用于中国原产新 BEV。"]
    else:
        return
    doc.metrics=[]
    doc.facts=[]
    for evidence,title,excerpt in zip(doc.evidences,titles,excerpts):
        evidence.title=title
        evidence.publisher="受控验收样例"
        evidence.excerpt=excerpt
        evidence.source_url=None
        evidence.published_at=__import__("datetime").date(2024,10,29)
