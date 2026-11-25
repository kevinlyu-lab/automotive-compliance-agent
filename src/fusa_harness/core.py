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

from .safety import FSR_FIELDS, TSR_FIELDS, default_notes, describe, fsr_draft, tsr_draft
from .sotif import SECTIONS as SOTIF_SECTIONS
from .sotif import cut_sets_from_tree, fields_for as sotif_fields, sotif_draft


FIELDS = {
    "HARA": [
        "Hazard", "编号", "位置", "道路条件", "环境条件", "本车车速", "本车操作", "相关项工作状态",
        "Potential Effect", "Severity", "Severity Comment", "Exposure", "Exposure Comment",
        "Controllability", "Controllability Comment", "ASIL", "Safety Goal", "Safe State", "备注",
    ],
    "FMEA": sotif_fields("FMEA"),
    "CTA": sotif_fields("CTA"),
    "STPA": sotif_fields("STPA"),
    "TEST": ["编号", "对应分析编号", "测试目标", "场景与前置条件", "输入或故障注入", "执行步骤", "预期表现", "通过准则"],
    "FSR": FSR_FIELDS,
    "TSR": TSR_FIELDS,
}
ORDER = list(FIELDS)
PREFIX = {"HARA": "H", "FMEA": "F", "CTA": "C", "STPA": "S", "TEST": "T", "FSR": "FSR", "TSR": "TSR"}
TASK_PATTERNS = {
    "HARA": r"HARA|危害分析(?:和风险评估)?",
    "FMEA": r"FMEA|失效模式(?:与影响)?分析",
    "CTA": r"CTA|触发条件分析",
    "STPA": r"STPA|系统理论过程分析",
    "TEST": r"测试用例|测试案例|test cases?",
    "FSR": r"FSR|功能安全要求|Functional\s+Safety\s+Requirements",
    "TSR": r"TSR|技术安全要求|Technical\s+Safety\s+Requirements",
}
ROW_ID_FIELDS = ("编号", "FSR 编号", "TSR 编号")
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


_SOURCE_MARKER = re.compile(
    r"根据|基于|依据|参考|结合|参照|按照|利用|使用|\b(?:based\s+on|according\s+to|from|using)\b", re.I
)
_ACTION = re.compile(
    r"生成|编写|推导|制定|设计|输出|导出|得到|形成|给出|开展|进行|整理|做|"
    r"\b(?:generate|derive|create|write|produce|develop|output|export)\b",
    re.I,
)
_DEFER = re.compile(r"的")


def _clause_mentions(clause: str) -> list[tuple[int, str]]:
    return sorted(
        (match.start(), task)
        for task in ORDER
        for match in re.finditer(TASK_PATTERNS[task], clause, re.I)
    )


def _requested_tasks(task_text: str) -> list[str]:
    """Detect requested tasks, treating analysis named after 根据/基于 as upstream input.

    The upstream rule only applies once FSR/TSR are present, so routing of the original five
    task types is unchanged.
    """
    found = [task for task in ORDER if re.search(TASK_PATTERNS[task], task_text, re.I)]
    if not (set(found) & {"FSR", "TSR"}):
        return found
    kept: list[str] = []
    for clause in re.split(r"[，,。；;：:\n]", task_text):
        mentions = _clause_mentions(clause)
        if not mentions:
            continue
        clause_tasks = [task for _, task in mentions]
        markers = list(_SOURCE_MARKER.finditer(clause))
        if not markers or not (set(clause_tasks) & {"FSR", "TSR"}):
            kept.extend(clause_tasks)
            continue
        action = _ACTION.search(clause)
        requested: list[str] = []
        if action:
            for position, task in mentions:
                marker = next((item for item in markers if item.end() <= position), None)
                # 来源标记与任务之间若没有生成类动词，该任务属于上游输入。
                if marker and not (marker.end() <= action.start() < position):
                    continue
                requested.append(task)
        else:
            defer = _DEFER.search(clause)
            requested = [task for position, task in mentions if defer and position > defer.start()] if defer else []
        kept.extend(requested)
    kept = [task for task in ORDER if task in kept]
    return kept or found


def parse_request(instruction: str, output_override: str | None = None) -> Request:
    text = instruction.strip()
    if not text:
        raise ValueError("请输入分析指令。")
    # A named analysis after 根据/基于 is source material for a test request, not another requested task.
    task_text = re.sub(r"(?:根据|基于)[^，,。；;]*?(?:HARA|FMEA|CTA|STPA|危害分析|失效分析)[^，,。；;]*?(?:生成|编写|设计)[^，,。；;]*?测试用例", "生成测试用例", text, flags=re.I)
    tasks = _requested_tasks(task_text)
    if not tasks:
        raise ValueError("未识别到任务类型；请指定 HARA、系统 FMEA、CTA、STPA、测试用例、FSR 或 TSR。")
    match = re.search(r"\b(LCC|ALC|ACC|AEB|LKA|NOA)\b", text, re.I)
    if match:
        feature = match.group(1).upper()
    elif (match := re.search(r"(?:针对|对|分析|评估)\s*([^，,。；;]{2,40}?)(?:功能)?\s*(?:在|生成|开展|进行|做|的|，|,)", text)):
        feature = match.group(1).strip()
    elif set(tasks) <= {"FSR", "TSR"}:
        feature = describe(text)["feature"]
    else:
        feature = "用户描述的自动驾驶功能"
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


def _row_number(row: dict) -> str:
    for field in ROW_ID_FIELDS:
        value = row.get(field)
        if value:
            return str(value)
    # CTA/STPA 的分段表格用「节点编号」「UCA 编号」等字段标识行，统一按后缀识别。
    for field, value in row.items():
        if value and str(field).endswith("编号"):
            return str(value)
    return ""


def read_analysis_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"分析文件不存在：{path}")
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return [row for section in data.get("analyses", []) for row in section.get("rows", []) if _row_number(row)]
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
                id_header = next((item for item in header if "编号" in item), None)
                if id_header is None:
                    continue
                for row in rows[1:]:
                    cells = row.findall("m:c", ns)
                    values = ["".join(cell.itertext()).strip() for cell in cells]
                    item = dict(zip(header, values))
                    if _row_number(item):
                        analysis_rows.append(item)
        return analysis_rows
    raise ValueError("来源分析仅支持本工具导出的 JSON 或 XLSX 文件。")


def read_analysis_ids(path: Path) -> list[str]:
    return [_row_number(row) for row in read_analysis_rows(path)]


def schema_for(task: str) -> dict:
    if task in {"CTA", "STPA"}:
        section_schemas = []
        for name, fields in SOTIF_SECTIONS[task]:
            section_schemas.append({
                "type": "object", "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "enum": [name]},
                    "rows": {"type": "array", "items": {
                        "type": "object", "additionalProperties": False,
                        "properties": {field: {"type": "string"} for field in fields},
                        "required": fields,
                    }},
                },
                "required": ["name", "rows"],
            })
        return {
            "type": "object", "additionalProperties": False,
            "properties": {
                "sections": {"type": "array", "items": {"anyOf": section_schemas}},
                "notes": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["sections", "notes"],
        }
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
    required = ["rows"]
    if task in {"FSR", "TSR"}:
        required.append("notes")
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "rows": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                     "properties": properties, "required": fields}},
            **({"notes": {"type": "array", "items": {"type": "string"}}} if task in {"FSR", "TSR"} else {}),
        },
        "required": required,
    }


def call_model(task: str, request: Request, references: list[Path], source_rows: list[dict], model: str) -> dict:
    source_ids = [_row_number(row) for row in source_rows]
    minimum = HARA_MIN_ROWS if task == "HARA" else 6 if task in {"FSR", "TSR"} else 8 if task in {"CTA", "STPA", "FMEA"} else 2
    if task == "HARA":
        framework = (
            "HARA 按 HARA Report 模板逐行输出：Hazard（[Hxxx] 编号与危害描述）、编号（HE 开头的唯一标识）、位置、道路条件、"
            "环境条件、本车车速、本车操作、相关项工作状态、Potential Effect、Severity（S1~S3）、Severity Comment、"
            "Exposure（E1~E4）、Exposure Comment、Controllability（C1~C3）、Controllability Comment、ASIL、"
            "Safety Goal、Safe State、备注。ASIL 必须与 S/E/C 组合的 ISO 26262 查表结果一致。"
        )
        rules = (
            "资料只支持其实际陈述的结构；不要猜测 ECU、传感器、接口名称。基于来源分析生成测试时保留对应编号。"
            "HARA 必须逐条给出 S/E/C 评级理由，按真实危险事件审慎评级；FMEA 只分析随机硬件/系统故障链；"
            "CTA 只分析预期功能不足与触发条件，不给 CTA 行赋 ASIL；STPA 使用控制动作和约束；测试用例需可执行、通过准则可观察。"
        )
        source_hint = "来源分析条目（测试用例应逐项对应其风险、原因与措施）"
    elif task == "FSR":
        framework = (
            "FSR 依据 ISO 26262-3:2018 Clause 7 生成，逐条输出字段：FSR 编号（FSR-001 起）、功能或风险描述、"
            "相关安全目标（SG）、ASIL 等级、FSR 具体要求、故障检测与控制策略、安全状态（Safe State）、"
            "功能降级和应急运行策略、安全时间要求（FTTI/FHTI）、FSR 分配对象、验证和验收准则、SG → FSR 追溯关系、"
            "依据与待确认事项。每条要求使用“系统应……”的系统安全需求语言，单条独立、清晰、可追溯、可验证。"
        )
        rules = (
            "未提供 HARA、SG 或 ASIL 依据时，SG 与 ASIL 只能标注为候选/待确认，不得给出确定等级；"
            "不得虚构 FTTI、故障检测时间、ECU、传感器或接口名称；缺少依据处写“待确认”并说明需要补充的资料。"
            "用户仅给出功能或风险描述时，结果注明为初步草案；提供 HARA 时，逐条建立 SG → FSR 追溯。"
        )
        source_hint = "来源分析条目（提供 HARA 时应沿用其安全目标与 ASIL，并逐条建立 SG → FSR 追溯）"
    elif task == "TSR":
        framework = (
            "TSR 依据 ISO 26262-4:2018 Clause 6 生成，为系统、硬件和软件开发提供输入。逐条输出字段：TSR 编号（TSR-001 起）、"
            "相关安全目标（SG）、相关 FSR、ASIL 等级、TSR 具体要求、技术安全机制、故障检测及诊断方式、"
            "故障响应与安全状态切换、安全时间约束、系统接口和通信安全要求、分配对象（系统/硬件/软件）、"
            "验证方法和验收准则、FSR → TSR 追溯关系、依据与待确认事项。TSR 必须比 FSR 更具体，给出可实现的机制、"
            "诊断手段与接口约束。"
        )
        rules = (
            "未提供 FSR 或 FSC 时，可先推导候选 FSR 并据此生成 TSR，但必须在相关 FSR 与追溯字段注明其为待确认的上游要求；"
            "未提供 ASIL 依据时不得给出确定等级；不得虚构 FTTI、故障检测时间、ECU、传感器或接口名称；"
            "接口、阈值与时间数值缺少依据处写“待确认”并说明需要补充的资料。"
        )
        source_hint = "来源分析条目（提供 FSR 时应逐条建立 FSR → TSR 追溯；提供 HARA 时应先推导候选 FSR）"
    elif task == "CTA":
        framework = (
            "CTA 依据 ISO 21448:2022 第 7 章生成，按四个分段输出，分段名必须是："
            "①「顶事件」：编号、顶事件（整车危害）、危害事件、相关功能、运行场景、危害后果、SOTIF 适用性、分析边界与假设；"
            "②「因果树」：节点编号、父节点、节点类型（顶事件/中间事件/底事件）、逻辑门（AND/OR/—）、节点描述、不足类型、证据或假设；"
            "③「底事件」：底事件编号、事件类别（功能定义不足/性能局限/触发条件/合理可预见误用）、事件描述、所属系统要素、"
            "关联触发条件、导致的输出不足、是否属于 SOTIF 范围、处理措施、验证方式；"
            "④「最小割集」：割集编号、阶数、布尔表达式、组成底事件、对应顶事件、SOTIF 处理优先级、说明、缓解与验证要点。"
            "因果树必须同时出现 AND 与 OR 节点，最小割集由因果树推导（一阶优先）。"
        )
        rules = (
            "CTA 是 SOTIF 演绎分析，只展开功能定义不足、性能局限、触发条件与合理可预见误用导致的因果链；"
            "不得引入随机硬件失效、元器件失效或系统性软件缺陷（那属于 ISO 26262 FTA/FMEA）。"
            "顶事件必须是整车层面的危害，不能写成“传感器不准”这类要素级问题。"
            "未提供的架构信息按通用感知/决策/执行/人机分层补全，并在分析边界与假设中声明。"
        )
        source_hint = "来源分析条目（顶事件与危害事件应与来源危害保持一致）"
    elif task == "STPA":
        framework = (
            "STPA 依据 MIT STPA Handbook 与 ISO 21448:2022 生成，按五个分段输出，分段名必须是："
            "①「损失与危害」：编号、损失（Loss）、整车危害（Hazard）、危害事件、涉及利益相关者、初步安全约束；"
            "②「控制结构」：层级、控制器或要素、控制动作（Control Action）、受控过程、反馈（Feedback）、接口与环境、边界假设；"
            "③「不安全控制行为」：UCA 编号、控制器、控制动作、UCA 类型、不安全控制行为描述、关联整车危害、发生情境（最坏条件）、对应安全约束；"
            "④「原因场景」：场景编号、关联 UCA、不足条件（IC）、因果因素（CF）、因素类型、说明与证据；"
            "⑤「安全约束」：约束编号、关联 UCA、安全约束、约束类型、分配到要素、验证方式。"
            "UCA 类型必须逐一覆盖四类：未提供、提供导致危害、时序错误、持续时间错误。"
        )
        rules = (
            "UCA 必须写成“控制器 + 控制动作 + 类型 + 情境”的形式，并关联至少一个整车危害；每条 UCA 必须有对应安全约束。"
            "原因场景按“不足条件（IC）→ 因果因素（CF）”展开，CF 类型只能是功能不足、输出不足或触发条件。"
            "控制结构需包含驾驶员与 HMI 反馈，不得只画控制器到执行器的直线。"
        )
        source_hint = "来源分析条目（整车危害应覆盖来源危害，UCA 应能追溯到来源条目编号）"
    elif task == "FMEA":
        framework = (
            "SOTIF 导向 FMEA 依据 ISO 21448:2022 生成，逐条输出：编号（F-001 起）、分析对象或功能、要素层级（功能/系统/硬件单元/软件单元）、"
            "功能定义不足或性能局限、触发条件、输出不足、整车危害行为、危害事件、现有控制措施、SOTIF 措施、验证方式、残余风险说明。"
            "每条必须构成完整链条：功能定义不足或性能局限 → 触发条件 → 输出不足 → 整车危害行为 → 危害事件 → 措施 → 验证方式。"
        )
        rules = (
            "只分析 SOTIF 范畴的触发条件与性能局限，禁止把“摄像头在逆光下能力受限、雷达对静止目标信噪比不足”写成“硬件损坏/元器件失效”；"
            "随机硬件失效属于 ISO 26262 FMEA，不得混入。措施应区分设计改进、限制运行条件与提示/培训三类，并给出可执行的验证方式。"
        )
        source_hint = "来源分析条目（危害事件可继承来源分析的危害与影响）"
    else:
        framework = "按各任务字段逐条写出实质内容。"
        rules = (
            "资料只支持其实际陈述的结构；不要猜测 ECU、传感器、接口名称。基于来源分析生成测试时保留对应编号。"
            "HARA 必须逐条给出 S/E/C 评级理由，按真实危险事件审慎评级；测试用例需可执行、通过准则可观察。"
        )
        source_hint = "来源分析条目（测试用例应逐项对应其风险、原因与措施）"
    content: list[dict] = [{"type": "input_text", "text": (
        f"用户指令：{request.instruction}\n分析对象：{request.feature}\n只生成任务：{task}。\n"
        f"来源分析编号：{', '.join(source_ids) if source_ids else '无'}\n"
        f"请用中文生成专业但可修改的初稿，至少 {minimum} 条具有不同情境的具体分析条目"
        f"{'，并按分段组织为 sections' if task in {'CTA', 'STPA'} else ''}。所有字段写出实质内容。"
        f"{framework}"
        f"{rules}"
    )}]
    if source_rows:
        content.append({"type": "input_text", "text": f"{source_hint}：\n" + json.dumps(source_rows, ensure_ascii=False)[:60000]})
    for path in references:
        body, image = read_reference(path)
        content.append({"type": "input_text", "text": f"参考资料 {path.name}：\n{body}"} if image is None else {"type": "input_text", "text": f"参考架构图 {path.name}："})
        if image:
            content.append({"type": "input_image", "image_url": image})
    payload = {
        "model": model,
        "instructions": "你是自动驾驶功能安全与预期功能安全分析工程师。区分证据与假设，不得编造未提供的具体架构。FSR/TSR 中缺少依据的信息必须标注为待确认。只返回符合 schema 的 JSON。",
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


def _validate_sections(task: str, data: dict) -> list[dict]:
    """CTA/STPA 按分析步骤分段校验，并保证分段名与字段与 ISO 分析步骤一致。"""
    expected = dict(SOTIF_SECTIONS[task])
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError(f"{task} 未生成分段结果（sections）。")
    by_name: dict[str, list[dict]] = {}
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError(f"{task} 的分段格式无效。")
        name = str(section.get("name") or "").strip()
        rows = section.get("rows")
        if name not in expected:
            raise ValueError(f"{task} 出现未知分段：{name or '（空）'}；允许的分段为：{'、'.join(expected)}")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{task} 分段「{name}」没有分析条目。")
        for index, row in enumerate(rows, 1):
            for field in expected[name]:
                if not isinstance(row.get(field), str) or not row[field].strip():
                    raise ValueError(f"{task} 分段「{name}」第 {index} 条缺少必填字段：{field}")
        by_name[name] = rows
    missing = [name for name in expected if name not in by_name]
    if missing:
        raise ValueError(f"{task} 缺少分段：{'、'.join(missing)}")
    if task == "CTA":
        # 最小割集必须由因果树推导，避免模型自说自话或与树不一致。
        derived = cut_sets_from_tree(by_name["因果树"])
        if not derived:
            raise ValueError("CTA 的因果树未包含可推导的底事件。")
        existing = by_name["最小割集"]
        if {row["布尔表达式"] for row in existing} != {row["布尔表达式"] for row in derived}:
            by_name["最小割集"] = derived
    if task == "STPA":
        kinds = " ".join(row["UCA 类型"] for row in by_name["不安全控制行为"])
        missing_kinds = [kind for kind in ("未提供", "提供导致危害", "时序错误", "持续时间错误") if kind not in kinds]
        if missing_kinds:
            raise ValueError(f"STPA 未覆盖以下 UCA 类型：{'、'.join(missing_kinds)}")
    ordered = [{"name": name, "fields": expected[name], "rows": by_name[name]} for name in expected]
    data["sections"] = ordered
    data["rows"] = by_name["底事件"] if task == "CTA" else by_name["不安全控制行为"]
    notes = data.get("notes")
    notes = [str(note).strip() for note in notes if str(note).strip()] if isinstance(notes, list) else []
    data["notes"] = notes
    return ordered


def validate(task: str, data: dict, source_ids: list[str]) -> dict:
    if task in {"CTA", "STPA"}:
        _validate_sections(task, data)
        return data
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
    if task in {"FSR", "TSR"}:
        notes = data.get("notes")
        notes = [str(note).strip() for note in notes if str(note).strip()] if isinstance(notes, list) else []
        # 结果末尾必须给出待补充资料；模型未给出时使用与任务对应的默认清单。
        data["notes"] = notes or default_notes(task)
    return data


def template(task: str, feature: str, instruction: str, source_rows: list[dict]) -> dict:
    """Conservative offline drafts; no source documents are accepted here."""
    if task == "FSR":
        return fsr_draft(feature, instruction, source_rows)
    if task == "TSR":
        return tsr_draft(feature, instruction, source_rows)
    if task in {"CTA", "STPA", "FMEA"}:
        return sotif_draft(task, feature, instruction, source_rows)
    lane = feature.upper() in {"LCC", "ALC", "LKA"}
    curve = "弯道" in instruction
    worn = "磨损" in instruction or "模糊" in instruction
    scenario = "高速公路弯道，车道线磨损" if curve and worn else "高速公路正常车道行驶"
    if task == "HARA":
        rows = hara_rows(feature, instruction)
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
