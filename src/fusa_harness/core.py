"""Task routing, source handling, generation and validation."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from zipfile import ZipFile


FIELDS = {
    "HARA": [
        "Hazard", "编号", "位置", "道路条件", "环境条件", "本车车速", "本车操作", "相关项工作状态",
        "Potential Effect", "Severity", "Severity Comment", "Exposure", "Exposure Comment",
        "Controllability", "Controllability Comment", "ASIL", "Safety Goal", "Safe State", "备注",
    ],
    "FMEA": ["编号", "分析对象或接口", "功能", "失效模式", "失效原因", "局部影响", "整车影响", "是否可能形成危害", "现有控制", "安全措施"],
    "CTA": ["编号", "顶层危险行为", "相关功能不足", "触发条件", "条件组合", "可能后果", "针对性措施"],
    "STPA": ["编号", "控制结构", "控制动作", "不安全控制动作及其发生情境", "致因场景", "安全约束"],
    "TEST": ["编号", "对应分析编号", "测试目标", "场景与前置条件", "输入或故障注入", "执行步骤", "预期表现", "通过准则"],
}
ORDER = list(FIELDS)
PREFIX = {"HARA": "H", "FMEA": "F", "CTA": "C", "STPA": "S", "TEST": "T"}
HARA_MIN_ROWS = 50
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_TYPES = {".txt", ".md", ".json", ".csv", ".tsv"}

# ISO 26262-3 表 4：S/E/C 组合对应的 ASIL 等级。
ASIL_TABLE = {
    ("S1", "E1", "C1"): "QM", ("S1", "E1", "C2"): "QM", ("S1", "E1", "C3"): "QM",
    ("S1", "E2", "C1"): "QM", ("S1", "E2", "C2"): "QM", ("S1", "E2", "C3"): "QM",
    ("S1", "E3", "C1"): "QM", ("S1", "E3", "C2"): "QM", ("S1", "E3", "C3"): "ASIL A",
    ("S1", "E4", "C1"): "QM", ("S1", "E4", "C2"): "ASIL A", ("S1", "E4", "C3"): "ASIL B",
    ("S2", "E1", "C1"): "QM", ("S2", "E1", "C2"): "QM", ("S2", "E1", "C3"): "QM",
    ("S2", "E2", "C1"): "QM", ("S2", "E2", "C2"): "QM", ("S2", "E2", "C3"): "ASIL A",
    ("S2", "E3", "C1"): "QM", ("S2", "E3", "C2"): "ASIL A", ("S2", "E3", "C3"): "ASIL B",
    ("S2", "E4", "C1"): "ASIL A", ("S2", "E4", "C2"): "ASIL B", ("S2", "E4", "C3"): "ASIL C",
    ("S3", "E1", "C1"): "QM", ("S3", "E1", "C2"): "QM", ("S3", "E1", "C3"): "ASIL A",
    ("S3", "E2", "C1"): "QM", ("S3", "E2", "C2"): "ASIL A", ("S3", "E2", "C3"): "ASIL B",
    ("S3", "E3", "C1"): "ASIL A", ("S3", "E3", "C2"): "ASIL B", ("S3", "E3", "C3"): "ASIL C",
    ("S3", "E4", "C1"): "ASIL B", ("S3", "E4", "C2"): "ASIL C", ("S3", "E4", "C3"): "ASIL D",
}
ASIL_RANK = {"QM": 0, "ASIL A": 1, "ASIL B": 2, "ASIL C": 3, "ASIL D": 4}
SEVERITY_REASON = {
    "S3": "碰撞相对速度大于20 km/h或车辆失稳后发生侧碰，按J2980取S3",
    "S2": "碰撞相对速度约20 km/h或与固定设施碰撞，按J2980取S2",
    "S1": "低速刮擦或轻微碰撞，AIS 1~6概率大于10%但伤害较轻，按J2980取S1",
}

# 参照 HARA Report 模板的驾驶情景，横向与纵向后果分开描述。
HARA_SCENARIOS = [
    {
        "key": "straight", "label": "高速公路直道", "位置": "高速公路(ODD内)",
        "道路条件": "直道，干燥路面",
        "环境条件": "本车周围有交通流（前车、相邻车道和后方有车流）",
        "本车车速": "车速60~110", "本车操作": "车道内行驶",
        "E": "E4", "C": "C3", "s_adj": 0,
        "e_comment": "高速公路场景占平均运行时间大于10%，取E4",
        "c_comment": "系统主动控制横向运动，驾驶员超控需克服转向扭矩，少于90%的驾驶员能可靠避免危害，取C3",
        "s_context": "高速直道车速60~110 km/h",
        "lat": "与相邻车道车辆发生侧碰或撞向道路护栏",
        "lon": "与前车发生追尾碰撞或撞向道路设施",
    },
    {
        "key": "congestion", "label": "交通拥堵", "位置": "高速公路(ODD内)",
        "道路条件": "直道，干燥路面，跟车距离短",
        "环境条件": "交通拥堵，车速慢，跟车距离短或跟停",
        "本车车速": "车速0~60", "本车操作": "车道内行驶",
        "E": "E3", "C": "C3", "s_adj": -1,
        "e_comment": "高速公路拥堵工况约占1%~10%运行时间，取E3",
        "c_comment": "系统主动控制横向运动，驾驶员超控需克服转向扭矩，少于90%的驾驶员能可靠避免危害，取C3",
        "s_context": "拥堵工况车速0~60 km/h",
        "lat": "与相邻车辆发生低速刮擦或侧碰",
        "lon": "与前车发生低速追尾，或紧急制动后被后车追尾",
    },
    {
        "key": "curve", "label": "高速公路弯道", "位置": "高速公路(ODD内)",
        "道路条件": "弯道，曲率半径≥400 m，干燥路面",
        "环境条件": "本车周围有交通流（前车、相邻车道和后方有车流）",
        "本车车速": "车速60~110", "本车操作": "车道内行驶",
        "E": "E3", "C": "C3", "s_adj": 0,
        "e_comment": "曲率半径≥400 m的弯道约占1%~10%运行时间，取E3",
        "c_comment": "系统主动控制横向运动，驾驶员超控需克服转向扭矩，少于90%的驾驶员能可靠避免危害，取C3",
        "s_context": "高速弯道车速60~110 km/h",
        "lat": "与相邻车道车辆发生侧碰或驶出弯道",
        "lon": "与弯道内前车发生追尾碰撞",
    },
    {
        "key": "slippery", "label": "湿滑弯道", "位置": "高速公路(ODD内)",
        "道路条件": "弯道，曲率半径≥400 m，路面湿滑",
        "环境条件": "湿滑路面，可能出现侧向风",
        "本车车速": "车速60~110", "本车操作": "车道内行驶",
        "E": "E2", "C": "C3", "s_adj": 1,
        "e_comment": "湿滑路面附着条件少见，出现概率小于1%，取E2",
        "c_comment": "湿滑路面下车辆易失稳，驾驶员难以通过转向或制动恢复轨迹，取C3",
        "s_context": "湿滑弯道车速60~110 km/h，车辆易失稳",
        "lat": "车辆侧滑失稳后与其他车道车辆发生侧碰",
        "lon": "制动距离增加，与前车发生追尾碰撞",
    },
    {
        "key": "oddboundary", "label": "接近ODD边界", "位置": "高速公路(ODD内)，接近ODD边界",
        "道路条件": "接近ODD边界（施工区域、收费站或高速尽头）",
        "环境条件": "本车周围有交通流，存在施工或收费设施",
        "本车车速": "车速60~110", "本车操作": "车道内行驶",
        "E": "E4", "C": "C2", "s_adj": -1,
        "e_comment": "接近ODD边界的情况每天都会发生，占运行时间大于10%，取E4",
        "c_comment": "系统已请求驾驶员接管，多数驾驶员能及时制动或转向避让，90%以上可避免危害，取C2",
        "s_context": "接近ODD边界，车速60~110 km/h",
        "lat": "在施工区或收费站与设施或车辆发生碰撞",
        "lon": "在施工区或收费站与前车或设施发生碰撞",
    },
]

LATERAL_MODES = [
    ("产生非预期横向偏移", "本车向相邻车道或路缘偏移", "避免LCC产生非预期横向控制", "偏离车道相关", 3),
    ("产生反向转向", "本车向与期望相反的方向转向", "避免LCC输出反向转向", "偏离车道相关", 3),
    ("横向控制能力不足（转向不足）", "本车横向位移不足以维持目标车道", "避免LCC横向控制能力不足", "偏离车道相关", 3),
    ("横向控制过量（过度转向）", "本车横向位移超出目标行驶轨迹", "避免LCC过度横向控制", "偏离车道相关", 3),
    ("转向输出卡滞", "转向输出保持在固定角度且不再更新", "避免LCC转向输出卡滞", "偏离车道相关", 3),
    ("横向控制意外中断", "本车失去LCC横向控制能力", "避免LCC横向控制意外中断", "偏离车道相关", 3),
    ("横向控制持续振荡", "本车在车道内持续左右摆动", "避免LCC横向控制振荡", "稳定性相关", 2),
    ("横向响应明显滞后", "本车对弯道和道路变化的横向响应滞后", "避免LCC横向响应延迟", "偏离车道相关", 2),
    ("跟随错误车道或错误目标", "本车跟随相邻车道或错误目标行驶", "避免LCC选择错误车道或目标", "偏离车道相关", 3),
    ("超出ODD或未激活时输出横向控制", "功能未激活或超出ODD时仍输出横向控制", "避免LCC在ODD外或未激活时控制车辆", "激活与退出相关", 2),
]

LONGITUDINAL_MODES = [
    ("出现非预期加速", "本车在无加速请求时加速", "避免功能产生非预期加速", "纵向控制相关", 2),
    ("出现非预期减速", "本车在无减速请求时减速", "避免功能产生非预期减速", "纵向控制相关", 2),
    ("制动减速度不足", "本车制动减速度不足以避免碰撞", "避免功能制动能力不足", "纵向控制相关", 3),
    ("出现过度减速", "本车减速度明显大于需求", "避免功能过度减速", "纵向控制相关", 2),
    ("未输出期望制动", "本车未按请求输出制动", "避免功能无法提供制动", "纵向控制相关", 3),
    ("制动力卡滞不释放", "制动压力保持不释放", "避免功能制动力卡滞", "纵向控制相关", 2),
    ("未对静止目标及时响应", "本车未对静止目标减速", "避免功能漏检静止目标", "纵向控制相关", 3),
    ("对切入目标响应过晚", "本车对切入车辆响应滞后", "避免功能对切入目标响应过晚", "纵向控制相关", 3),
    ("跟停与起步控制异常", "本车跟停后非预期起步或无法起步", "避免功能起步与跟停控制异常", "纵向控制相关", 2),
    ("超出ODD或未激活时输出纵向控制", "功能未激活或超出ODD时仍控制车辆纵向运动", "避免功能在ODD外或未激活时控制车辆", "激活与退出相关", 2),
]


@dataclass
class Request:
    instruction: str
    feature: str
    tasks: list[str]
    output: str


def parse_request(instruction: str, output_override: str | None = None) -> Request:
    text = instruction.strip()
    if not text:
        raise ValueError("请输入分析指令。")
    # A named analysis after 根据/基于 is source material for a test request, not another requested task.
    task_text = re.sub(r"(?:根据|基于)[^，,。；;]*?(?:HARA|FMEA|CTA|STPA|危害分析|失效分析)[^，,。；;]*?(?:生成|编写|设计)[^，,。；;]*?测试用例", "生成测试用例", text, flags=re.I)
    patterns = {
        "HARA": r"HARA|危害分析(?:和风险评估)?",
        "FMEA": r"FMEA|失效模式(?:与影响)?分析",
        "CTA": r"CTA|触发条件分析",
        "STPA": r"STPA|系统理论过程分析",
        "TEST": r"测试用例|测试案例|test cases?",
    }
    tasks = [task for task in ORDER if re.search(patterns[task], task_text, re.I)]
    if not tasks:
        raise ValueError("未识别到任务类型；请指定 HARA、系统 FMEA、CTA、STPA 或测试用例。")
    match = re.search(r"\b(LCC|ALC|ACC|AEB|LKA|NOA)\b", text, re.I)
    if match:
        feature = match.group(1).upper()
    else:
        match = re.search(r"(?:针对|对|分析|评估)\s*([^，,。；;]{2,40}?)(?:功能)?\s*(?:在|生成|开展|进行|做|的|，|,)", text)
        feature = match.group(1).strip() if match else "用户描述的自动驾驶功能"
    output = output_override or ("xlsx" if re.search(r"XLSX|EXCEL|电子表格|导出.*表格", text, re.I) else "text")
    return Request(text, feature, tasks, output)


def read_reference(path: Path) -> tuple[str, str | None]:
    if not path.is_file():
        raise ValueError(f"参考文件不存在：{path}")
    suffix = path.suffix.lower()
    if suffix in IMAGE_TYPES:
        if path.stat().st_size > 15_000_000:
            raise ValueError(f"图片超过 15 MB：{path}")
        mime = mimetypes.guess_type(path.name)[0] or "image/png"
        return "", f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    if suffix in TEXT_TYPES:
        return path.read_text(encoding="utf-8-sig")[:60000], None
    if suffix == ".docx":
        with ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        return "\n".join(n.text or "" for n in root.iter() if n.tag.endswith("}t"))[:60000], None
    if suffix == ".pdf":
        try:
            result = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise ValueError("PDF 读取需要系统安装 pdftotext，且文件包含可提取文字。") from error
        if not result.stdout.strip():
            raise ValueError("PDF 未提取到文字；请改用清晰图片或可选中文本 PDF。")
        return result.stdout[:60000], None
    raise ValueError(f"不支持的参考文件类型：{suffix}")


def read_analysis_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"分析文件不存在：{path}")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return [row for section in data.get("analyses", []) for row in section.get("rows", []) if row.get("编号")]
    if path.suffix.lower() == ".xlsx":
        analysis_rows = []
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with ZipFile(path) as book:
            for name in book.namelist():
                if not re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name):
                    continue
                root = ET.fromstring(book.read(name))
                rows = root.findall(".//m:sheetData/m:row", ns)
                if not rows:
                    continue
                header = ["".join(c.itertext()) for c in rows[0].findall("m:c", ns)]
                if "编号" not in header:
                    continue
                for row in rows[1:]:
                    cells = row.findall("m:c", ns)
                    values = ["".join(cell.itertext()).strip() for cell in cells]
                    item = dict(zip(header, values))
                    if item.get("编号"):
                        analysis_rows.append(item)
        return analysis_rows
    raise ValueError("来源分析仅支持本工具导出的 JSON 或 XLSX 文件。")


def read_analysis_ids(path: Path) -> list[str]:
    return [str(row["编号"]) for row in read_analysis_rows(path)]


def schema_for(task: str) -> dict:
    fields = FIELDS[task]
    properties = {name: {"type": "string"} for name in fields}
    if task == "HARA":
        for name, values in {
            "Severity": ["S1", "S2", "S3"],
            "Exposure": ["E1", "E2", "E3", "E4"],
            "Controllability": ["C1", "C2", "C3"],
            "ASIL": ["QM", "ASIL A", "ASIL B", "ASIL C", "ASIL D"],
        }.items():
            properties[name] = {"type": "string", "enum": values}
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "rows": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                     "properties": properties, "required": fields}},
        },
        "required": ["rows"],
    }


def call_model(task: str, request: Request, references: list[Path], source_rows: list[dict], model: str) -> dict:
    source_ids = [str(row["编号"]) for row in source_rows]
    minimum = HARA_MIN_ROWS if task == "HARA" else 2
    if task == "HARA":
        framework = (
            "HARA 按 HARA Report 模板逐行输出：Hazard（[Hxxx] 编号与危害描述）、编号（HE 开头的唯一标识）、位置、道路条件、"
            "环境条件、本车车速、本车操作、相关项工作状态、Potential Effect、Severity（S1~S3）、Severity Comment、"
            "Exposure（E1~E4）、Exposure Comment、Controllability（C1~C3）、Controllability Comment、ASIL、"
            "Safety Goal、Safe State、备注。ASIL 必须与 S/E/C 组合的 ISO 26262 查表结果一致。"
        )
    else:
        framework = "按各任务字段逐条写出实质内容。"
    content: list[dict] = [{"type": "input_text", "text": (
        f"用户指令：{request.instruction}\n分析对象：{request.feature}\n只生成任务：{task}。\n"
        f"来源分析编号：{', '.join(source_ids) if source_ids else '无'}\n"
        f"请用中文生成专业但可修改的初稿，至少 {minimum} 条具有不同情境的具体分析条目。所有字段写出实质内容。"
        f"{framework}"
        "资料只支持其实际陈述的结构；不要猜测 ECU、传感器、接口名称。基于来源分析生成测试时保留对应编号。"
        "HARA 必须逐条给出 S/E/C 评级理由，按真实危险事件审慎评级；FMEA 只分析随机硬件/系统故障链；"
        "CTA 只分析预期功能不足与触发条件，不给 CTA 行赋 ASIL；STPA 使用控制动作和约束；测试用例需可执行、通过准则可观察。"
    )}]
    if source_rows:
        content.append({"type": "input_text", "text": "来源分析条目（测试用例应逐项对应其风险、原因与措施）：\n" + json.dumps(source_rows, ensure_ascii=False)[:60000]})
    for path in references:
        body, image = read_reference(path)
        content.append({"type": "input_text", "text": f"参考资料 {path.name}：\n{body}"} if image is None else {"type": "input_text", "text": f"参考架构图 {path.name}："})
        if image:
            content.append({"type": "input_image", "image_url": image})
    payload = {
        "model": model,
        "instructions": "你是自动驾驶功能安全与预期功能安全分析工程师。区分证据与假设，不得编造未提供的具体架构。只返回符合 schema 的 JSON。",
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": f"analysis_{task.lower()}", "strict": True, "schema": schema_for(task)}},
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=data, headers={
        "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise ValueError(f"模型请求失败（HTTP {error.code}）：{detail}") from error
    if result.get("status") != "completed":
        raise ValueError(f"模型响应未完成：{result.get('status')}")
    texts = [item.get("text", "") for output in result.get("output", []) for item in output.get("content", []) if item.get("type") == "output_text"]
    if not texts:
        raise ValueError("模型未返回结构化文本；请检查模型支持情况。")
    return json.loads("".join(texts))


def _asil_for(severity: str, exposure: str, controllability: str) -> str:
    key = (severity.strip().upper(), exposure.strip().upper(), controllability.strip().upper())
    if key not in ASIL_TABLE:
        raise ValueError(f"无效的 S/E/C 组合：{severity}/{exposure}/{controllability}")
    return ASIL_TABLE[key]


def hara_rows(feature: str, instruction: str) -> list[dict]:
    """按 HARA Report 模板生成 50 条危害事件：10 类失效 × 5 个驾驶情景。"""
    lane = feature.upper() in {"LCC", "ALC", "LKA", "NOA"}
    modes = LATERAL_MODES if lane else LONGITUDINAL_MODES
    suffix = "lat" if lane else "lon"
    rows: list[dict] = []
    numbering = 0
    for index, (hazard, effect, goal, note, base_severity) in enumerate(modes, 1):
        goal_id = f"G{index:03d}"
        goal = goal.replace("LCC", feature)
        for scenario in HARA_SCENARIOS:
            numbering += 1
            severity = f"S{max(1, min(3, base_severity + scenario['s_adj']))}"
            exposure, controllability = scenario["E"], scenario["C"]
            asil = _asil_for(severity, exposure, controllability)
            state = f"{feature}{'请求接管' if scenario['key'] == 'oddboundary' else '激活'}"
            rows.append({
                "Hazard": f"[H{numbering:03d}] {feature}在{scenario['label']}场景下{hazard}",
                "编号": f"HE{numbering:04d}",
                "位置": scenario["位置"],
                "道路条件": scenario["道路条件"],
                "环境条件": scenario["环境条件"],
                "本车车速": scenario["本车车速"],
                "本车操作": scenario["本车操作"],
                "相关项工作状态": state,
                "Potential Effect": f"{effect}，{scenario[suffix]}",
                "Severity": severity,
                "Severity Comment": f"{scenario['s_context']}；{SEVERITY_REASON[severity]}",
                "Exposure": exposure,
                "Exposure Comment": scenario["e_comment"],
                "Controllability": controllability,
                "Controllability Comment": scenario["c_comment"],
                "ASIL": asil,
                "Safety Goal": f"[{goal_id}] {goal}（ASIL {asil.replace('ASIL ', '') if asil != 'QM' else 'QM'}）",
                "Safe State": f"退出 {feature} 控制并提示驾驶员接管",
                "备注": note,
            })
    # 同一安全目标取所有相关危害事件中的最高 ASIL，与模板一致。
    goal_level: dict[str, str] = {}
    for row in rows:
        goal_id = row["Safety Goal"][1:5]
        current = goal_level.get(goal_id)
        if current is None or ASIL_RANK[row["ASIL"]] > ASIL_RANK[current]:
            goal_level[goal_id] = row["ASIL"]
    for row in rows:
        goal_id = row["Safety Goal"][1:5]
        goal_text = row["Safety Goal"].split("（ASIL")[0]
        level = goal_level[goal_id]
        row["Safety Goal"] = f"{goal_text}（ASIL {level.replace('ASIL ', '') if level != 'QM' else 'QM'}）"
    return rows


def validate(task: str, data: dict, source_ids: list[str]) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list) or not data["rows"]:
        raise ValueError(f"{task} 未生成分析条目。")
    fields = FIELDS[task]
    if task == "HARA" and len(data["rows"]) < HARA_MIN_ROWS:
        raise ValueError(f"HARA 需至少 {HARA_MIN_ROWS} 条，当前仅 {len(data['rows'])} 条。")
    for index, row in enumerate(data["rows"], 1):
        for field in fields:
            if field == "对应分析编号" and not source_ids:
                row.setdefault(field, "不适用（独立测试任务）")
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{task} 第 {index} 条缺少必填字段：{field}")
        if task == "HARA":
            for field, allowed in {
                "Severity": {"S1", "S2", "S3"},
                "Exposure": {"E1", "E2", "E3", "E4"},
                "Controllability": {"C1", "C2", "C3"},
            }.items():
                row[field] = row[field].strip().upper()
                if row[field] not in allowed:
                    raise ValueError(f"HARA 第 {index} 条的 {field} 值无效：{row[field]}")
            # ASIL 由 S/E/C 唯一决定，统一按 ISO 26262 查表覆盖，避免评级不一致。
            row["ASIL"] = _asil_for(row["Severity"], row["Exposure"], row["Controllability"])
        if task == "TEST" and source_ids and row["对应分析编号"] not in source_ids:
            raise ValueError(f"测试用例第 {index} 条引用了不存在的分析编号：{row['对应分析编号']}")
    return data


def template(task: str, feature: str, instruction: str, source_rows: list[dict]) -> dict:
    """Conservative offline drafts; no source documents are accepted here."""
    lane = feature.upper() in {"LCC", "ALC", "LKA"}
    curve = "弯道" in instruction
    worn = "磨损" in instruction or "模糊" in instruction
    scenario = "高速公路弯道，车道线磨损" if curve and worn else "高速公路正常车道行驶"
    steering = "横向控制" if lane else "功能控制"
    event = "车辆偏离本车道，与相邻车辆或道路边界发生碰撞" if lane else "车辆未按预期执行功能，可能引发碰撞"
    if task == "HARA":
        rows = hara_rows(feature, instruction)
    elif task == "FMEA":
        rows = [
            ["F-001", f"{feature} 横向控制命令接口（逻辑边界，待架构确认）", "向执行端提供期望控制命令", "命令卡滞或重复", "通信或计算故障（待架构确认）", "控制命令与当前场景不匹配", event, "是", "待确认：命令时效性与合理性监测", "增加超时、序列号及幅值合理性校验，异常时抑制控制并告警"],
            ["F-002", f"{feature} 功能状态接口（逻辑边界，待架构确认）", "传递功能可用与激活状态", "状态错误或延迟", "状态更新故障（待架构确认）", "功能状态与实际控制不一致", "驾驶员可能误判控制状态，延迟接管", "是", "待确认：状态一致性监测", "状态不一致时退出功能并提示驾驶员"],
        ]
        if not lane:
            rows[0][1] = f"{feature} 控制命令接口（逻辑边界，待架构确认）"
    elif task == "CTA":
        rows = ([
            ["C-001", "车辆未保持安全横向轨迹", "车道边界识别置信度不足仍维持控制", "车道线磨损或低对比度", "弯道曲率增大 + 车道线磨损 + 驾驶员暂未接管", event, "识别置信度门限、曲率适用边界与退出提示联合设计"],
            ["C-002", "车辆错误趋向相邻车道", "对道路边界的语义理解不足", "施工临时标线与旧标线并存", "临时标线冲突 + 高速行驶", event, "冲突标线检测、降低控制权限并提示接管"],
        ] if lane else [
            ["C-001", "功能输出与真实场景不匹配", "对运行场景适用性判断不足", "环境观测质量下降", "观测质量下降 + 功能仍激活 + 驾驶员暂未接管", event, "定义适用性置信度门限，超出边界时限制功能并提示接管"],
            ["C-002", "功能在复杂场景中给出错误响应", "对场景语义的理解不足", "临时交通组织或异常目标", "场景语义冲突 + 高速行驶", event, "检测场景冲突，降低控制权限并提示接管"],
        ])
    elif task == "STPA":
        rows = [
            ["S-001", "驾驶员→功能控制器→横向执行端→车辆，环境感知反馈至控制器（抽象结构）", "发送横向控制命令", "车道边界不可靠时仍给出转向命令", "车道线磨损使边界估计偏移，控制器未及时降低权限", "SC-01：边界置信度不足时不得维持危险横向控制"],
            ["S-002", "驾驶员→功能控制器→横向执行端→车辆，状态反馈至驾驶员（抽象结构）", "发出退出及接管提示", "控制能力下降时提示过晚", "状态评估延迟导致驾驶员可用接管时间不足", "SC-02：控制能力下降时及时退出并提供可感知接管提示"],
        ]
        if not lane:
            rows[0] = ["S-001", "驾驶员→功能控制器→执行端→车辆，环境与车辆状态反馈至控制器（抽象结构）", "发送功能控制命令", "场景适用性不足时仍发送控制命令", "环境或目标理解偏差，控制器未及时降低权限", "SC-01：场景适用性不足时不得维持危险控制"]
            rows[1][1] = "驾驶员→功能控制器→执行端→车辆，状态反馈至驾驶员（抽象结构）"
    else:
        if source_rows:
            rows = []
            for index, source in enumerate(source_rows, 1):
                concern = next((source[key] for key in ("Potential Effect", "危险事件", "整车影响", "顶层危险行为", "不安全控制动作及其发生情境") if source.get(key)), "功能危险行为")
                stimulus = next((source[key] for key in ("失效原因", "触发条件", "致因场景", "车辆级失效行为") if source.get(key)), "模拟对应异常条件")
                measure = next((source[key] for key in ("安全措施", "针对性措施", "安全约束", "安全目标", "Safety Goal", "Safe State") if source.get(key)), "限制危险控制并提示驾驶员")
                rows.append([f"T-{index:03d}", str(source["编号"]), f"验证 {concern} 的风险控制", source.get("运行场景", scenario), f"施加或模拟：{stimulus}", "1. 建立前置场景并激活功能；2. 注入所述条件；3. 记录功能状态、车辆轨迹和提示；4. 对照来源分析条目判定", f"系统执行对应安全响应：{measure}", f"未发生所述危险事件；按项目限值验证响应时间和效果：{measure}"])
        else:
            rows = [
                ["T-001", "不适用（独立测试任务）", "验证边界识别不可靠时系统安全响应", scenario, "注入低置信度车道边界输入或使用磨损标线场景", "1. 建立场景并激活功能；2. 逐步降低边界质量；3. 记录轨迹、功能状态和告警", "系统限制危险控制，必要时退出并提示驾驶员", "不出现非预期车道偏离；在项目规定时间内退出或抑制控制并发出可感知提示"],
                ["T-002", "不适用（独立测试任务）", "验证控制状态异常时的响应", "高速公路稳态行驶，功能已激活", "注入控制命令卡滞或状态延迟（如接口允许）", "1. 激活功能；2. 注入异常；3. 记录命令、车辆响应与提示", "异常被检测，功能停止危险控制并提示接管", "无持续异常控制；检测、退出和提示满足项目规定时间阈值"],
            ]
            if not lane:
                rows[0] = ["T-001", "不适用（独立测试任务）", "验证场景适用性下降时系统安全响应", "功能激活，环境观测质量逐步下降", "注入低置信度环境或目标输入", "1. 建立场景并激活功能；2. 逐步降低输入质量；3. 记录车辆响应、功能状态和告警", "系统限制危险控制，必要时退出并提示驾驶员", "不出现非预期危险响应；在项目规定时间内退出或抑制控制并发出可感知提示"]
    if task == "HARA":
        return {"rows": rows}
    return {"rows": [dict(zip(FIELDS[task], row)) for row in rows]}
