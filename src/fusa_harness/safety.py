"""Offline drafts for FSR (ISO 26262-3:2018 Clause 7) and TSR (ISO 26262-4:2018 Clause 6)."""
from __future__ import annotations

import re

FSR_FIELDS = [
    "FSR 编号",
    "功能或风险描述",
    "相关安全目标（SG）",
    "ASIL 等级",
    "FSR 具体要求",
    "故障检测与控制策略",
    "安全状态（Safe State）",
    "功能降级和应急运行策略",
    "安全时间要求（FTTI/FHTI）",
    "FSR 分配对象",
    "验证和验收准则",
    "SG → FSR 追溯关系",
    "依据与待确认事项",
]

TSR_FIELDS = [
    "TSR 编号",
    "相关安全目标（SG）",
    "相关 FSR",
    "ASIL 等级",
    "TSR 具体要求",
    "技术安全机制",
    "故障检测及诊断方式",
    "故障响应与安全状态切换",
    "安全时间约束",
    "系统接口和通信安全要求",
    "分配对象（系统/硬件/软件）",
    "验证方法和验收准则",
    "FSR → TSR 追溯关系",
    "依据与待确认事项",
]

# 场景识别按优先级排列：具体场景优先于通用高速场景。
SCENARIOS = [
    (("弯道", "curve", "corner"), "弯道行驶"),
    (("湿滑", "slippery", "wet road"), "湿滑路面"),
    (("拥堵", "跟车", "congestion", "traffic jam"), "交通拥堵"),
    (("高速公路", "高速", "highway", "freeway", "motorway"), "高速行驶"),
    (("城区", "城市", "urban road", "urban", "city"), "城区道路"),
    (("泊车", "停车", "parking", "park"), "泊车场景"),
    (("夜间", "night"), "夜间行驶"),
    (("施工", "construction", "work zone"), "施工区域"),
    (("倒车", "reverse"), "倒车场景"),
    (("低速", "low speed"), "低速行驶"),
]
SCENE_WORDS = tuple(dict.fromkeys(word for words, _ in SCENARIOS for word in words))

# 只用于从用户指令中提取“风险主题”，不改变用户原文。
_NOISE = re.compile(
    r"(?:FSR|TSR|功能安全要求|技术安全要求|Functional\s+Safety\s+Requirements|Technical\s+Safety\s+Requirements|"
    r"HARA|FMEA|CTA|STPA|FSC|ASIL|Item\s+Definition|SG|"
    r"终端输出|终端显示|终端回复|在终端回复|直接在终端|以文本(?:方式)?(?:回复|输出)|文本回复|"
    r"输出|导出|生成|编写|制定|推导|设计|分析|评估|请|帮我|麻烦|初稿|"
    r"根据|基于|依据|参考|结合|参照|按照|针对|对于|关于|安全目标|以及|和|与|及|"
    r"\b(?:for|the|of|a|an|to|by|and|at|in|on|during|while|when|under|with)\b)",
    re.I,
)

DOMAINS = {
    "lateral": {
        "control": "横向控制",
        "actuator": "转向",
        "feedback": "实际转向角/横摆率",
        "request": "转向角/横摆率请求",
        "unsafe": "非预期转向或横向运动",
        "safe_state": "退出横向控制，转向指令平滑归零或回正，车辆保持当前行驶方向，并提示驾驶员接管",
        "degrade": "降低横向控制权限（限幅/限速）或退出横向控制",
        "allocation": "横向控制功能与转向执行子系统",
        "tests": "指令卡滞、反向、超幅、执行器无响应",
        "safe_keep": "保持当前行驶方向",
    },
    "longitudinal": {
        "control": "纵向控制",
        "actuator": "驱动/制动",
        "feedback": "实际加速度/减速度",
        "request": "加速度/减速度请求",
        "unsafe": "非预期加速、减速或制动",
        "safe_state": "退出纵向控制，驱动扭矩与制动请求平滑归零，车辆保持可控并由驾驶员接管",
        "degrade": "降低纵向控制权限（限扭矩/限速）或退出纵向控制",
        "allocation": "纵向控制功能与驱动/制动执行子系统",
        "tests": "请求卡滞、反向（制动变加速）、超幅、执行器无响应",
        "safe_keep": "保持当前运动状态或按定义减速",
    },
    "generic": {
        "control": "功能控制",
        "actuator": "执行",
        "feedback": "实际执行量",
        "request": "执行量请求",
        "unsafe": "非预期输出",
        "safe_state": "退出功能控制，输出归零或进入定义的安全状态，并提示用户接管",
        "degrade": "降低功能权限或退出功能",
        "allocation": "功能控制与执行子系统",
        "tests": "输出卡滞、反向、超幅、执行器无响应",
        "safe_keep": "保持可控状态",
    },
}

# 每条 FSR 至少覆盖：安全状态、输入/指令合理性、独立监控、运行边界、降级、驾驶员接管。
FSR_SPECS = [
    {
        "requirement": (
            "系统应在检测到{control}不可信、功能不可用或驾驶员接管时，停止输出{control}指令并平滑退出，"
            "使车辆{safe_keep}；退出过程不得产生{unsafe}。"
        ),
        "detection": (
            "监控{control}指令与{feedback}的一致性、驾驶员输入及{actuator}系统可用状态；"
            "偏差超限、功能不可用或驾驶员接管时触发退出。判据与阈值待确认（需结合 FTTI 与车辆动力学标定）。"
        ),
        "degradation": "{degrade}",
        "verification": (
            "在 HIL 或整车环境注入{control}失效与驾驶员接管，验证退出过程{feedback}变化不超过项目限值且提示可感知；"
            "限值待确认。"
        ),
        "basis": "初步草案：未提供 HARA 与 Item Definition，安全状态与检测阈值需在功能安全概念中确认。",
    },
    {
        "requirement": (
            "系统应对{control}依赖的感知输入、驾驶员输入与车辆运动状态进行合理性校验，"
            "并禁止在输入冲突或置信度不足时执行{control}指令。"
        ),
        "detection": (
            "交叉比对目标/车道线置信度、驾驶员输入与{feedback}的响应；识别感知冲突、跳变与传感器不一致。"
            "判据与门限待确认。"
        ),
        "degradation": "降低{control}权限（限幅/限速）或退出{control}，并提示驾驶员接管。",
        "verification": (
            "注入感知冲突、低置信度输入与错误目标，验证{control}指令被抑制并发出提示；通过准则待确认。"
        ),
        "basis": "初步草案：未提供感知与融合架构，置信度门限与冲突判据待确认。",
    },
    {
        "requirement": (
            "系统应设置独立于主{control}路径的监控功能，检测{control}指令卡滞、反向、超幅、变化率异常及"
            "执行器无响应等失效，并在检测到失效时抑制危险控制。"
        ),
        "detection": (
            "独立监控通道按固定周期比较主控制指令与期望值及执行反馈，监控指令时序、幅值与变化率；"
            "监控周期与阈值待确认。"
        ),
        "degradation": "监控功能自身失效时，系统应限制{control}可用性或退出功能（降级策略待确认）。",
        "verification": (
            "通过故障注入（{tests}）验证监控触发与安全反应；覆盖率与通过准则待确认。"
        ),
        "basis": "初步草案：未提供系统架构，监控通道独立性与实现方式待确认。",
    },
    {
        "requirement": (
            "系统应在超出 ODD、感知质量下降或 ODD 边界临近时，及时限制或退出{control}并请求驾驶员接管，"
            "不得在能力边界外维持{control}。"
        ),
        "detection": (
            "监控 ODD 条件（道路类型、曲率、天气/光照、输入质量）与感知置信度；越界或退化时触发限制或退出。"
            "判据待确认。"
        ),
        "degradation": "在仍处于 ODD 内时降低{control}权限（如限速、限制适用曲率范围）。",
        "verification": (
            "在 ODD 边界、感知质量下降与恶劣天气场景验证限制/退出与提示；通过准则待确认。"
        ),
        "basis": "初步草案：未提供 ODD 定义，边界条件与接管时间要求待确认。",
    },
    {
        "requirement": (
            "系统应在部分失效（如单传感器失效、监控通道降级）时进入受限的应急运行模式，"
            "限制{control}权限并保持车辆可控，不得直接进入不可控状态。"
        ),
        "detection": "识别单点失效与可容忍故障，评估剩余功能能力；降级判据待确认。",
        "degradation": "定义分级降级策略（功能受限→仅告警→退出），并明确各级的进入与退出条件（待确认）。",
        "verification": "注入单传感器失效与监控降级，验证系统进入定义的受限模式且车辆仍可控；通过准则待确认。",
        "basis": "初步草案：未提供故障容错概念与应急运行策略，降级分级待确认。",
    },
    {
        "requirement": (
            "系统应在退出{control}、进入降级或检测到故障时，向驾驶员发出明确、及时、可感知的接管请求，"
            "并保证接管后系统不再输出{control}指令。"
        ),
        "detection": "监控功能状态、故障状态与驾驶员接管输入；确认接管请求发出与驾驶员响应。提示方式与去抖要求待确认。",
        "degradation": "驾驶员未响应时，系统应维持或升级提示并实施整车级最小风险策略（待确认）。",
        "verification": (
            "通过驾驶员在环测试验证提示可感知、接管及时、接管后无残余{control}；通过准则待确认。"
        ),
        "basis": "初步草案：未提供 HMI 与接管策略定义，提示方式与最小风险策略待确认。",
    },
]

# 每条 TSR 对应一条 FSR（索引从 0 开始），并给出更具体的机制、诊断与接口约束。
TSR_SPECS = [
    {
        "fsr_index": 2,
        "requirement": (
            "系统应设置独立于主{control}路径的安全监控功能，按固定周期比较主控制输出的{request}与监控期望值"
            "（幅值、变化率、方向），偏差超过标定阈值时触发安全反应；监控与主功能不得共用同一失效源。"
        ),
        "mechanism": "双通道比较（主控制 + 独立监控）、指令限幅、故障锁存、安全状态请求。",
        "diagnosis": "周期性诊断：指令一致性、传感器合理性、执行器反馈；诊断结果写入故障存储器并上报整车。",
        "response": "监控触发后抑制{control}指令并切换到安全状态，故障锁存至下电或维修确认。",
        "interface": "监控与主控制之间应定义指令、状态、故障标志及更新周期接口，并采用端到端保护（接口名称待架构确认）。",
        "allocation": "系统：安全监控功能；硬件：独立监控资源/看门狗（待确认）；软件：监控与诊断软件（待确认）。",
        "verification": "通过故障注入（{tests}）验证监控触发与安全反应；覆盖率与通过准则待确认。",
    },
    {
        "fsr_index": 1,
        "requirement": (
            "系统应在{control}指令的传输与处理链路中采用端到端保护（计数器、CRC/校验、超时与序列检查），"
            "接收端应丢弃并上报丢失、重复、延迟或校验失败的指令。"
        ),
        "mechanism": "E2E 保护、指令有效性窗口、超时抑制、故障上报。",
        "diagnosis": "接收端统计校验失败、超时与序列错误并上报诊断；总线类型、信号周期与超时门限待接口定义确认。",
        "response": "保护失败时抑制{control}指令并进入安全状态。",
        "interface": "应定义信号名称、更新周期、有效范围、超时与失效默认值（接口名称与总线拓扑待架构确认）。",
        "allocation": "系统：通信与安全状态管理；硬件：通信控制器（待确认）；软件：通信保护与诊断软件。",
        "verification": "注入丢包、重复、延迟与错误校验的指令，验证接收端抑制并上报；通过准则待确认。",
    },
    {
        "fsr_index": 0,
        "requirement": (
            "控制器应配置独立看门狗与程序流监控，任务超时、死锁或执行流偏离时在看门狗超时时间内复位或进入安全状态，"
            "复位不得产生{unsafe}。"
        ),
        "mechanism": "外部看门狗、任务心跳、程序流检查点、复位后安全状态默认值。",
        "diagnosis": "监控任务周期与程序流签名，异常时记录复位原因。",
        "response": "看门狗触发后进入安全状态，复位后默认不激活{control}。",
        "interface": "看门狗喂狗接口与复位信号定义待硬件/软件架构确认。",
        "allocation": "系统：时序监控；硬件：外部看门狗；软件：任务监控与安全状态管理。",
        "verification": "注入任务阻塞与程序流跳转，验证看门狗复位及复位后安全状态；通过准则待确认。",
    },
    {
        "fsr_index": 2,
        "requirement": (
            "系统应在上电及运行中执行存储器、CPU、电源与传感器合理性自检，诊断覆盖率目标按 ASIL 待确认；"
            "检测到安全相关故障时应记录并上报。"
        ),
        "mechanism": "RAM/ROM 校验、CPU 自检、电源监控、传感器合理性检查、故障存储。",
        "diagnosis": "上电自检与周期自检；故障按安全相关/非安全相关分级。",
        "response": "安全相关故障触发安全反应；非安全相关故障按降级策略处理。",
        "interface": "诊断接口与故障码定义待整车诊断规范确认。",
        "allocation": "系统：诊断管理；硬件：监控电路；软件：自检与诊断软件。",
        "verification": "通过硬件故障注入与软件自检验证覆盖率与响应；指标待确认。",
    },
    {
        "fsr_index": 0,
        "requirement": "检测到安全相关故障后，系统应在 FHTI 内完成故障响应并切换到定义的安全状态，切换过程不得产生{unsafe}。",
        "mechanism": "故障响应表、安全状态机、输出抑制与平滑退出。",
        "diagnosis": "故障确认（去抖）后触发安全状态机迁移并记录事件。",
        "response": "切换到安全状态（{safe_state}），并保持至故障清除或下电。",
        "interface": "安全状态指示与故障状态对外接口待定义。",
        "allocation": "系统：安全状态管理；硬件：执行器安全默认位置；软件：故障响应软件。",
        "verification": "故障注入验证切换时间与切换过程车辆响应；通过准则待确认。",
    },
    {
        "fsr_index": 0,
        "requirement": (
            "系统应基于整车 FTTI 逐级分配传感、处理、通信与执行环节的时间预算，"
            "并保证从故障发生到进入安全状态的端到端时间小于 FTTI。"
        ),
        "mechanism": "时间预算表、监控周期设计、最坏执行时间分析。",
        "diagnosis": "监控各环节处理时间与超时，超时触发安全反应。",
        "response": "超时或预算越界时进入安全状态。",
        "interface": "时间同步与超时参数接口待定义。",
        "allocation": "系统：时间预算与调度；硬件：时钟与看门狗；软件：实时调度与超时监控。",
        "verification": "通过最坏情况时序分析与超时注入验证时间约束；通过准则待确认。",
    },
    {
        "fsr_index": 4,
        "requirement": (
            "系统应定义与外部系统（感知、执行、整车）接口的安全默认值、失效行为与降级策略，"
            "并在接口失效时限制功能进入受限运行模式。"
        ),
        "mechanism": "接口失效默认值、降级模式管理、功能可用性指示。",
        "diagnosis": "接口超时、无效值与状态不一致诊断。",
        "response": "接口失效时抑制{control}或进入受限运行，并提示驾驶员。",
        "interface": "接口信号、默认值、更新率与安全状态指示待接口定义确认。",
        "allocation": "系统：接口与降级管理；硬件：接口收发器；软件：接口监控与降级软件。",
        "verification": "通过接口失效注入验证降级与提示；通过准则待确认。",
    },
]

_ASIL_RANK = {"QM": 0, "ASIL A": 1, "ASIL B": 2, "ASIL C": 3, "ASIL D": 4}


def _family(text: str) -> str:
    if re.search(r"转向|横向|偏离|车道|方向盘|轨迹|侧滑|甩尾|steer|lateral|lane|drift|departure", text, re.I):
        return "lateral"
    if re.search(r"制动|刹车|加速|减速|纵向|追尾|驱动|油门|brak|accel|decel|longitudinal|throttle", text, re.I):
        return "longitudinal"
    return "generic"


def _scenario(instruction: str) -> str:
    for words, label in SCENARIOS:
        if any(word in instruction for word in words):
            return label
    return "未明确的运行场景（待确认）"


def _clean(instruction: str) -> str:
    text = _NOISE.sub(" ", instruction)
    text = re.sub(r"[，,。；;：:、\s]+", " ", text).strip()
    return re.sub(r"的$", "", text).strip()


def describe(instruction: str) -> dict[str, str]:
    """Extract feature, scenario, risk and hazard family from a free-form instruction."""
    cleaned = _clean(instruction)
    scenario = _scenario(instruction)
    rest = cleaned
    for word in SCENE_WORDS:
        rest = rest.replace(word, "")
    rest = re.sub(r"\s+", " ", rest).strip(" 的")
    rest = re.sub(r"风险$", "", rest).strip()
    match = re.search(r"\b(LCC|ALC|ACC|AEB|LKA|NOA)\b", instruction, re.I)
    feature = match.group(1).upper() if match else rest
    if match and not rest:
        risk = f"{feature} 功能相关的整车级危险（待确认）"
    else:
        risk = rest or "该功能相关的整车级危险（待确认）"
    return {
        "feature": feature or rest or "用户描述的自动驾驶功能",
        "scenario": scenario,
        "risk": risk,
        "family": _family(f"{risk} {instruction}"),
    }


def _asil(value: str) -> str:
    text = str(value or "")
    match = re.search(r"ASIL\s*([A-D])", text, re.I)
    if match:
        return f"ASIL {match.group(1).upper()}"
    if re.search(r"\bQM\b", text, re.I):
        return "QM"
    if text.strip().upper() in {"A", "B", "C", "D"}:
        return f"ASIL {text.strip().upper()}"
    return "待确认"


def _goals(rows: list[dict]) -> list[dict]:
    goals: dict[str, dict] = {}
    order: list[str] = []
    for row in rows:
        raw = str(row.get("Safety Goal") or row.get("相关安全目标（SG）") or row.get("安全目标") or "").strip()
        if not raw:
            continue
        match = re.match(r"\s*\[?(G\d+)\]?\s*(.*)", raw)
        goal_id = match.group(1) if match else ""
        body = (match.group(2) if match else raw).strip()
        body = re.sub(r"[（(]\s*ASIL[^）)]*[）)]", "", body).strip()
        if not goal_id:
            candidate = re.match(r"候选\s*(SG-\d+)", raw)
            goal_id = candidate.group(1) if candidate else f"SG-{len(order) + 1:02d}"
        asil = _asil(row.get("ASIL") or row.get("ASIL 等级") or "")
        if goal_id not in goals:
            goals[goal_id] = {"id": goal_id, "text": body or raw, "asil": asil}
            order.append(goal_id)
        elif _ASIL_RANK.get(asil, -1) > _ASIL_RANK.get(goals[goal_id]["asil"], -1):
            goals[goal_id]["asil"] = asil
    return [goals[goal_id] for goal_id in order]


def _best_goal(goals: list[dict], risk: str, family: str) -> dict | None:
    if not goals:
        return None
    tokens = {
        "lateral": ["转向", "横向", "偏离", "车道", "轨迹"],
        "longitudinal": ["制动", "减速", "加速", "纵向", "追尾"],
        "generic": [],
    }[family]
    tokens = list(tokens) + [risk[index:index + 4] for index in range(0, max(len(risk) - 3, 0), 2)]
    best, best_score = goals[0], -1
    for goal in goals:
        score = sum(1 for token in tokens if token and token in goal["text"])
        if score > best_score:
            best, best_score = goal, score
    return best


def _context(feature: str, instruction: str, source_rows: list[dict]) -> dict[str, str]:
    topic = describe(instruction)
    if feature and feature != "用户描述的自动驾驶功能":
        topic["feature"] = feature
    extra = " ".join(
        " ".join(str(row.get(field) or "") for field in ("FSR 具体要求", "功能或风险描述", "相关安全目标（SG）", "Safety Goal"))
        for row in source_rows
    ).strip()
    if extra:
        if topic["family"] == "generic":
            topic["family"] = _family(extra)
        if topic["risk"].startswith("该功能"):
            described = next((str(row.get("功能或风险描述") or "").strip() for row in source_rows if row.get("功能或风险描述")), "")
            matched = re.match(r"(.+?)场景下(.+)", described)
            if matched:
                if topic["scenario"].startswith("未明确"):
                    topic["scenario"] = matched.group(1)
                topic["risk"] = matched.group(2).strip()
            elif described:
                topic["risk"] = described
    topic.update(DOMAINS[topic["family"]])
    return topic


def _description(context: dict[str, str]) -> str:
    if context["scenario"].startswith("未明确"):
        return f"{context['risk']}（运行场景待确认）"
    return f"{context['scenario']}场景下{context['risk']}"


def _sg_field(goal: dict | None, risk: str) -> str:
    if goal is None:
        return f"[候选 SG-01] 避免{risk}（ASIL 待确认）"
    level = goal["asil"] if goal["asil"] != "待确认" else "ASIL 待确认"
    return f"[{goal['id']}] {goal['text']}（{level}）"


def _asil_field(asil: str, candidate: bool, source: str = "HARA") -> str:
    if asil != "待确认":
        return asil
    if candidate:
        return "待确认（未提供 ASIL 依据）"
    return f"待确认（{source} 未给出 ASIL）"


def fsr_draft(feature: str, instruction: str, source_rows: list[dict]) -> dict:
    context = _context(feature, instruction, source_rows)
    goals = _goals(source_rows)
    goal = _best_goal(goals, context["risk"], context["family"])
    candidate = goal is None
    rows = []
    for index, spec in enumerate(FSR_SPECS, 1):
        number = f"FSR-{index:03d}"
        trace = f"{goal['id'] if goal else '候选 SG-01'} → {number}"
        if candidate:
            trace += "（候选追溯，待确认）"
        basis = spec["basis"].rstrip("。") + ("；SG/ASIL 为候选值。" if candidate else "；SG/ASIL 依据提供的 HARA。")
        rows.append({
            "FSR 编号": number,
            "功能或风险描述": _description(context),
            "相关安全目标（SG）": _sg_field(goal, context["risk"]),
            "ASIL 等级": _asil_field(goal["asil"] if goal else "待确认", candidate),
            "FSR 具体要求": spec["requirement"].format(**context),
            "故障检测与控制策略": spec["detection"].format(**context),
            "安全状态（Safe State）": context["safe_state"],
            "功能降级和应急运行策略": spec["degradation"].format(**context),
            "安全时间要求（FTTI/FHTI）": "待确认：从检测到不安全状态至进入安全状态的时间应小于 FTTI；FTTI/FHTI 数值未提供，需按整车安全概念分配。",
            "FSR 分配对象": f"{context['allocation']}（候选分配，待架构确认）",
            "验证和验收准则": spec["verification"].format(**context),
            "SG → FSR 追溯关系": trace,
            "依据与待确认事项": basis + "检测阈值、FTTI、分配对象与验证限值均待确认。",
        })
    return {"rows": rows, "notes": fsr_gap_notes(not candidate)}


def _upstream_fsrs(feature: str, instruction: str, source_rows: list[dict]) -> tuple[list[dict], bool, bool]:
    provided = [row for row in source_rows if str(row.get("FSR 编号") or "").strip()]
    if provided:
        entries = [{
            "id": str(row.get("FSR 编号")).strip(),
            "text": str(row.get("FSR 具体要求") or "（提供的 FSR 未给出具体要求）").strip(),
            "sg": str(row.get("相关安全目标（SG）") or "").strip(),
            "asil": _asil(row.get("ASIL 等级") or ""),
            "candidate": False,
        } for row in provided]
        return entries, True, False
    draft = fsr_draft(feature, instruction, source_rows)
    entries = [{
        "id": row["FSR 编号"],
        "text": row["FSR 具体要求"],
        "sg": row["相关安全目标（SG）"],
        "asil": _asil(row["ASIL 等级"]),
        "candidate": True,
    } for row in draft["rows"]]
    return entries, False, bool(source_rows)


def tsr_draft(feature: str, instruction: str, source_rows: list[dict]) -> dict:
    context = _context(feature, instruction, source_rows)
    entries, has_fsr, has_hara = _upstream_fsrs(feature, instruction, source_rows)
    rows = []
    for index, spec in enumerate(TSR_SPECS, 1):
        number = f"TSR-{index:03d}"
        entry = entries[spec["fsr_index"] % len(entries)]
        if entry["candidate"]:
            related = f"[候选 {entry['id']}] {entry['text']}（待确认的上游要求）"
            trace = f"候选 {entry['id']} → {number}（待确认）"
        else:
            related = f"[{entry['id']}] {entry['text']}"
            trace = f"{entry['id']} → {number}"
        basis = "初步草案："
        basis += "候选 FSR 为待确认的上游要求；" if entry["candidate"] else "依据提供的 FSR 推导；"
        basis += "接口、诊断阈值与安全时间数值待确认。"
        rows.append({
            "TSR 编号": number,
            "相关安全目标（SG）": entry["sg"] or _sg_field(None, context["risk"]),
            "相关 FSR": related,
            "ASIL 等级": _asil_field(entry["asil"], entry["candidate"], "上游 FSR"),
            "TSR 具体要求": spec["requirement"].format(**context),
            "技术安全机制": spec["mechanism"].format(**context),
            "故障检测及诊断方式": spec["diagnosis"].format(**context),
            "故障响应与安全状态切换": spec["response"].format(**context),
            "安全时间约束": "待确认：故障检测与响应时间应满足 FTTI/FHTI 分配；数值未提供。",
            "系统接口和通信安全要求": spec["interface"].format(**context),
            "分配对象（系统/硬件/软件）": spec["allocation"].format(**context),
            "验证方法和验收准则": spec["verification"].format(**context),
            "FSR → TSR 追溯关系": trace,
            "依据与待确认事项": basis,
        })
    return {"rows": rows, "notes": tsr_gap_notes(has_fsr, has_hara)}


def fsr_gap_notes(has_hara: bool) -> list[str]:
    if has_hara:
        notes = ["已依据提供的 HARA 分析结果选取安全目标与 ASIL；SG 与 FSR 的分解、分配关系仍需项目安全团队确认。"]
    else:
        notes = ["未提供 HARA 分析结果：本文件中的安全目标（SG）与 ASIL 均为候选/待确认，不能作为已确认结论。"]
    notes.extend([
        "未提供 Item Definition 与系统架构：FSR 分配对象、检测点与接口边界需在架构确定后确认。",
        "未提供整车级 FTTI/FHTI 与安全时间预算：文中安全时间要求均为待确认，需按整车安全概念分配。",
        "未提供安全状态与应急运行策略的整车级定义（最小风险状态、驾驶员接管方式）：需在功能安全概念中确认。",
        "未提供验证与验收准则（测试环境、限值、通过判据）：需在验证计划中确认。",
        "建议补充资料：HARA 工作表、安全目标确认记录、Item Definition、系统架构图、接口定义、FSC（如已开展）。",
    ])
    return notes


def tsr_gap_notes(has_fsr: bool, has_hara: bool = False) -> list[str]:
    if has_fsr:
        notes = ["已依据提供的 FSR 推导 TSR 并建立 FSR → TSR 追溯；所选 FSR 的 ASIL 与分配关系仍需确认。"]
    elif has_hara:
        notes = ["未提供 FSR：本文件基于 HARA 结果先推导候选 FSR，再据此生成候选 TSR；候选 FSR 为待确认的上游要求。"]
    else:
        notes = ["未提供 FSR/FSC：本文件先推导候选 FSR 并据此生成 TSR；候选 FSR 为待确认的上游要求，不得视为已确认基线。"]
    notes.extend([
        "未提供系统架构与接口定义：技术安全机制、接口名称、ECU 与总线拓扑均为建议方案，需架构确认。",
        "ASIL 等级与安全机制诊断覆盖率目标：需结合上游 FSR/SG 的 ASIL 确认。",
        "未提供 FTTI/FHTI 与诊断时间预算：安全时间约束待确认。",
        "未提供硬件/软件开发平台约束与验证准则：需在软硬件安全需求与验证计划中确认。",
        "建议补充资料：FSR 基线、FSC、系统架构图、接口定义（信号/总线/时序）、ASIL 分配记录、诊断与安全机制规范。",
    ])
    return notes


def default_notes(task: str) -> list[str]:
    return fsr_gap_notes(False) if task == "FSR" else tsr_gap_notes(False, False)
