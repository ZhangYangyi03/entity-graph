"""The 6+1 taxonomy, and the four chains the industry map has to carry.

This module holds no data source of its own. It holds the *shape* of the map the
实施方案 asks for: 脑机接口6+1 (six technical directions plus organoid interfaces) and
生物制造 (synthetic biology, enzyme engineering, fermentation, cell engineering), each
node tagged with the chain it belongs to -- 产业链 / 创新链 / 人才链 / 资金链 -- and a
correctness note on every tag, because a keyword match is a guess and must read as one.

Direction definitions are quoted from the project document (图1, 6+1 布局):
    神经调控              电/磁/光/超声/化学手段调节神经活动; DBS, SCS, VNS, TMAS, 闭环
    神经修复              脊髓损伤/脑卒中/膀胱感觉缺失的再生与功能修复; 支架, 胶原材料
    微针电极              微纳加工的针状神经电极; 刚性/柔性/复合, 头皮微针, 植入阵列
    生物人工脑盖骨双向接口  颅骨修补+双向神经信号传输一体化植入体
    脑深区磁声成像        磁场-超声耦合的深部脑区成像与调控 (TMAS)
    脑控装备              脑控轮椅/外骨骼/无人机/疲劳监测, 非侵入解码与信息传输率
    +1 类器官/片上脑机接口  脑类器官 + 微电极/微流控/光遗传 = BoCI; 类器官智能 OI

The measured sources that feed each direction live in bci.py and biomed.py; this module
only decides which direction a fetched record belongs to, and says how sure it is.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- taxonomy
DIRECTIONS: dict[str, dict] = {
    "neuromodulation": {
        "name": "神经调控",
        "en": "neuromodulation",
        "params": "闭环延迟; 多模态刺激参数>=4类(电/磁/声/光)",
        "keywords": ["deep brain stimulation", "dbs", "spinal cord stimulation",
                     "vagus nerve stimulation", "neuromodulation", "transcranial",
                     "closed-loop stimulation", "ultrasound stimulation",
                     "optogenetic", "magneto-acoustic", "tmas", "帕金森", "脊髓电刺激"],
    },
    "neurorepair": {
        "name": "神经修复",
        "en": "neurorepair",
        "params": "运动/感觉/语言功能修复评价参数体系",
        "keywords": ["nerve regeneration", "spinal cord injury", "axon regeneration",
                     "scaffold", "collagen", "stroke rehabilitation", "neuroregen",
                     "neural repair", "graft", "脊髓损伤", "脑卒中", "再生修复"],
    },
    "microneedle": {
        "name": "微针电极",
        "en": "microneedle electrode",
        "params": "通道数; 长期稳定性; 阻抗漂移; TRL",
        "keywords": ["microneedle", "microelectrode array", "utah array", "neural probe",
                     "flexible electrode", "dry electrode", "eeg electrode",
                     # EEG acquisition hardware is the 采集端 of this direction: the
                     # document's promise is 头皮微针电极 replacing 湿电极, so an EEG
                     # machine or cap is the incumbent it is measured against.
                     "electroencephalograph", "eeg electrode", "eeg cap", "eeg headset",
                     "gel electrode", "impedance", "微针", "电极阵列", "柔性电极"],
    },
    "skull_interface": {
        "name": "生物人工脑盖骨双向接口",
        "en": "bio-artificial skull interface",
        "params": "双向传输误码率; TRL",
        "keywords": ["cranioplasty", "skull", "cranial implant", "bone flap",
                     "wireless implant", "transcranial transmission", "ultrasonic through skull",
                     "颅骨", "人工骨", "无线传输"],
    },
    "magneto_acoustic": {
        "name": "脑深区磁声成像",
        "en": "magneto-acoustic imaging",
        "params": "深区空间分辨; 无创穿透量(cm级); 颅骨等效衰减量",
        "keywords": ["magnetoacoustic", "magneto-acoustic", "thermoacoustic",
                     "acoustoelectric", "focused ultrasound imaging", "deep brain imaging",
                     "磁声", "深部脑区成像"],
    },
    "brain_control": {
        "name": "脑控装备",
        "en": "brain-controlled equipment",
        "params": "非侵入解码指令; 信息传输率; 对标国际梯队",
        # 脑控装备 is the decoding/output end, so any named BCI paradigm (mi, p300, ssvep,
        # ecog) belongs here; a single generic hit is still labelled weak downstream.
        "keywords": ["brain-controlled", "brain actuated", "eeg wheelchair",
                     "exoskeleton", "brain-machine interface robot", "drone control",
                     "fatigue monitoring", "ssvep", "p300 speller", "brain computer interface",
                     "brain-computer interface", "motor imagery", "p300", "erp-based",
                     "ecog", "eeg-based", "neuroprosthetic", "movement decoding",
                     "brain click", "bci", "脑控", "外骨骼", "意念"],
    },
    "organoid": {
        "name": "+1 类器官/片上脑机接口",
        "en": "organoid / brain-on-a-chip interface",
        "params": "离体类脑器官双向耦合存活天数; 可稳定记录时长",
        "keywords": ["brain organoid", "cerebral organoid", "organoid intelligence",
                     "brain-on-a-chip", "multielectrode array organoid", "neurosphere",
                     "ipsc neuron", "类器官", "片上脑机接口"],
    },
}

BIO_MANUFACTURING: dict[str, dict] = {
    "synthetic_biology": {
        "name": "合成生物学",
        "keywords": ["synthetic biology", "gene circuit", "crispr", "genome engineering",
                     "metabolic engineering", "基因线路", "合成生物"],
    },
    "enzyme_engineering": {
        "name": "酶工程",
        "keywords": ["directed evolution", "enzyme engineering", "biocatalysis",
                     "immobilized enzyme", "酶工程", "生物催化"],
    },
    "fermentation": {
        "name": "发酵工程",
        "keywords": ["fermentation", "bioreactor", "fed-batch", "strain improvement",
                     "发酵", "生物反应器"],
    },
    "cell_engineering": {
        "name": "细胞工程",
        "keywords": ["cell culture", "stem cell", "car-t", "cho cell", "bioprocess",
                     "细胞培养", "干细胞", "细胞工程"],
    },
    "organoid_manufacturing": {
        "name": "类器官生物制造",
        "keywords": ["organoid", "organ-on-a-chip", "3d bioprinting", "tissue engineering",
                     "类器官", "器官芯片", "生物打印"],
    },
}

# 产业链环节: the stage a node sits at. Same three-part cut the document uses
# (上游材料器件 -- 中游系统装备 -- 下游临床与应用), plus the four chains as tags.
CHAIN_STAGES = {
    "upstream":   {"name": "上游(材料/器件/芯片)", "keywords":
                   ["electrode material", "chip", "asic", "sensor", "biocompatib",
                    "encapsulation", "材料", "器件", "芯片", "封装"]},
    "midstream":  {"name": "中游(系统/装备/算法)", "keywords":
                   ["decoder", "algorithm", "system", "device", "platform", "arm",
                    "exoskeleton", "wheelchair", "系统", "装备", "算法", "解码"]},
    "downstream": {"name": "下游(临床/应用)", "keywords":
                   ["clinical trial", "patient", "implant", "rehabilitation", "hospital",
                    "market", "临床", "患者", "康复", "医院"]},
}

FOUR_CHAINS = ("产业链", "创新链", "人才链", "资金链")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


# A short CJK keyword can be a substring of a word with nothing to do with us. Measured
# in this repo: 「脑控」 inside 「电脑控制系统」(睿能科技 针织袜机 公告) — the title hit the
# 6+1 layer and put a sock-machine company into 脑控装备. Rather than drop short CJK
# keywords (「脑控」 is the most useful one), refuse the match when it sits inside the
# longer word that contains it. Each entry is (keyword, forbidden preceding chars).
CJK_FALSE_FRIENDS = {"脑控": ("电",)}


def _cjk_ok(text: str, kw: str) -> bool:
    """True when every occurrence of kw is not part of a longer unrelated word."""
    bad = CJK_FALSE_FRIENDS.get(kw)
    if not bad:
        return True
    start = 0
    while True:
        i = text.find(kw, start)
        if i < 0:
            return True
        if i > 0 and text[i - 1] in bad:
            return False
        start = i + 1


def classify(text: str) -> list[dict]:
    """Which 6+1 directions a piece of text touches, with the keyword that decided it.

    Returns one entry per hit: {"direction", "name", "match", "confidence"}.
    Confidence is "weak" when only one keyword hit and none of them is BCI-specific --
    a paper about "electrode" is not automatically a 微针电极 paper, and the graph says so.
    """
    t = _norm(text)
    if not t:
        return []
    out, seen = [], set()
    for did, d in DIRECTIONS.items():
        hits = [k for k in d["keywords"] if k.lower() in t and _cjk_ok(t, k)]
        if not hits:
            continue
        if did in seen:
            continue
        seen.add(did)
        strong = any(k.lower() in t for k in
                     ("brain-computer", "brain computer", "bci", "motor imagery",
                      "ssvep", "p300", "intracortical", "eeg", "neural interface"))
        out.append({"direction": did, "name": d["name"], "match": hits[0],
                    "confidence": "ok" if (len(hits) > 1 or strong) else "weak"})
    return out


def classify_bio(text: str) -> list[dict]:
    t = _norm(text)
    out = []
    for bid, b in BIO_MANUFACTURING.items():
        hits = [k for k in b["keywords"] if k.lower() in t]
        if hits:
            out.append({"domain": bid, "name": b["name"], "match": hits[0],
                        "confidence": "ok" if len(hits) > 1 else "weak"})
    return out


def chain_stage(text: str) -> dict | None:
    """The 产业链 stage, or None when the text does not say. No default is invented."""
    t = _norm(text)
    best = None
    for sid, s in CHAIN_STAGES.items():
        hits = [k for k in s["keywords"] if k.lower() in t]
        if hits and (best is None or len(hits) > len(best[1])):
            best = (sid, hits)
    if not best:
        return None
    return {"stage": best[0], "name": CHAIN_STAGES[best[0]]["name"], "match": best[1][0]}


def tagged(direction_ids=None, bio_ids=None, stages=None) -> dict:
    """The attribute block a node carries: every tag plus the basis for it."""
    a: dict = {}
    if direction_ids:
        a["bci6plus1"] = list(direction_ids)
    if bio_ids:
        a["bio_domain"] = list(bio_ids)
    if stages:
        a["chain_stage"] = list(stages)
    a["chains"] = list(FOUR_CHAINS)
    return a


# --------------------------------------------------------------------------- actors
# The organisations the 实施方案 names, with the role the document gives each. Every
# entry cites the document, not a guess: fact="..." is the sentence it rests on.
# Anything not named in the document is left out -- an actor list that quietly grows
# from my own recollection would be the same failure as an edge with no source.
ACTORS = [
    {"name": "中国医学科学院生物医学工程研究所", "short": "医工所",
     "kind": "institute", "chain": "创新链", "dirs": ["neuromodulation", "neurorepair",
                                                       "microneedle", "skull_interface",
                                                       "magneto_acoustic", "brain_control"],
     "fact": "6+1 技术部署的实施主体（项目承担单位）"},
    {"name": "天津大学医学部/神经工程团队", "short": "天大神经工程",
     "kind": "university", "chain": "人才链", "dirs": ["brain_control", "neuromodulation"],
     "fact": "'神工'系列、脑机交互与人机共融海河实验室；神经调控处于国际第一梯队"},
    {"name": "脑机交互与人机共融海河实验室", "short": "海河实验室",
     "kind": "laboratory", "chain": "创新链", "dirs": ["brain_control", "organoid"],
     "fact": "2024年7月研发全球首个片上脑机接口智能交互系统（培养脑控制机器人避障/抓握）"},
    {"name": "南开大学脑机接口基础科学前沿中心", "short": "南开前沿中心",
     "kind": "university", "chain": "人才链", "dirs": ["brain_control"],
     "fact": "高校调研对象；基础科学前沿"},
    {"name": "脑虎科技", "short": "NeuroXess", "kind": "company", "chain": "产业链",
     "dirs": ["microneedle", "skull_interface"],
     "fact": "国家制造业转型升级基金以7+3年长存续期投资的企业之一"},
    {"name": "阶梯医疗", "short": "StairMed", "kind": "company", "chain": "产业链",
     "dirs": ["microneedle", "skull_interface"],
     "fact": "WRS 01 已完成42例长期植入，受试者累计在体时长超5000天"},
    {"name": "博睿康", "short": "Neuracle", "kind": "company", "chain": "产业链",
     "dirs": ["brain_control", "neuromodulation"],
     "fact": "NEO-ONE SCI 于2026年3月获批第三类医疗器械注册证，全球首款获批上市的侵入式BCI医疗器械"},
    {"name": "华大智造", "short": "MGI", "kind": "company", "chain": "产业链",
     "dirs": [], "bio": ["synthetic_biology"],
     "fact": "上下游企业调研对象（京津沪苏龙头企业）"},
    {"name": "金斯瑞", "short": "GenScript", "kind": "company", "chain": "产业链",
     "dirs": [], "bio": ["synthetic_biology"],
     "fact": "上下游企业调研对象（京津沪苏龙头企业）"},
    {"name": "凯莱英", "short": "Asymchem", "kind": "company", "chain": "产业链",
     "dirs": [], "bio": ["enzyme_engineering", "fermentation"],
     "fact": "上下游企业调研对象（京津沪苏龙头企业）"},
    {"name": "复旦大学附属华山医院", "short": "华山医院", "kind": "hospital",
     "chain": "产业链", "dirs": ["microneedle", "skull_interface"],
     "fact": "2025年10月底完成国内首例全植入、全无线、全功能柔性脑机接口植入（28岁高位截瘫）"},
    {"name": "首都医科大学宣武医院", "short": "宣武医院", "kind": "hospital",
     "chain": "产业链", "dirs": ["neurorepair", "neuromodulation"],
     "fact": "全球首例'北脑一号+时序脊髓电刺激+外骨骼'联合治疗，完全性脊髓损伤跨级修复"},
    {"name": "同济医院", "short": "同济医院", "kind": "hospital", "chain": "创新链",
     "dirs": ["skull_interface"],
     "fact": "颅骨改性技术（超声在颅骨局部形成微孔改善跨颅信号传导）的早期探索"},
    {"name": "联影医疗", "short": "United Imaging", "kind": "company", "chain": "产业链",
     "dirs": ["magneto_acoustic"],
     "fact": "医工全重共建单位；脑成像与脑活动观测技术创新合作"},
    {"name": "天开园", "short": "天开高教科技园", "kind": "park", "chain": "资金链",
     "dirs": [], "fact": "园区调研对象"},
    {"name": "天津滨海新区", "short": "滨海新区", "kind": "park", "chain": "资金链",
     "dirs": [], "fact": "园区调研对象"},
    {"name": "武清生物制造聚集区", "short": "武清", "kind": "park", "chain": "资金链",
     "dirs": [], "bio": ["fermentation", "synthetic_biology"], "fact": "园区调研对象"},
]

# Institutions that fund or regulate the two domains, as named in the document.
GOVERNMENT = ["京津冀发改委", "工信局", "医保局", "药监局", "科技局"]
