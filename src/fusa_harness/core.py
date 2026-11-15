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
    "HARA": ["编号", "功能", "运行场景", "车辆级失效行为", "危害", "危险事件", "S", "E", "C", "ASIL", "安全目标"],
    "FMEA": ["编号", "分析对象或接口", "功能", "失效模式", "失效原因", "局部影响", "整车影响", "是否可能形成危害", "现有控制", "安全措施"],
    "CTA": ["编号", "顶层危险行为", "相关功能不足", "触发条件", "条件组合", "可能后果", "针对性措施"],
    "STPA": ["编号", "控制结构", "控制动作", "不安全控制动作及其发生情境", "致因场景", "安全约束"],
    "TEST": ["编号", "对应分析编号", "测试目标", "场景与前置条件", "输入或故障注入", "执行步骤", "预期表现", "通过准则"],
}
ORDER = list(FIELDS)
PREFIX = {"HARA": "H", "FMEA": "F", "CTA": "C", "STPA": "S", "TEST": "T"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}
TEXT_TYPES = {".txt", ".md", ".json", ".csv", ".tsv"}


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
        for name, values in {"S": ["S1", "S2", "S3"], "E": ["E1", "E2", "E3", "E4"], "C": ["C1", "C2", "C3"], "ASIL": ["QM", "ASIL A", "ASIL B", "ASIL C", "ASIL D"]}.items():
            properties[name] = {"type": "string", "enum": values}
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                     "properties": properties, "required": fields}},
        },
        "required": ["assumptions", "rows"],
    }


def call_model(task: str, request: Request, references: list[Path], source_rows: list[dict], model: str) -> dict:
    source_ids = [str(row["编号"]) for row in source_rows]
    content: list[dict] = [{"type": "input_text", "text": (
        f"用户指令：{request.instruction}\n分析对象：{request.feature}\n只生成任务：{task}。\n"
        f"来源分析编号：{', '.join(source_ids) if source_ids else '无'}\n"
        "请用中文生成专业但可修改的初稿，至少两条具有不同情境的具体分析条目。所有字段写出实质内容。无资料时明确列出关键通用假设。资料只支持其实际陈述的结构；不要猜测 ECU、传感器、接口名称。基于来源分析生成测试时保留对应编号。\n"
        "HARA 必须提供 S/E/C 和 ASIL，按真实危险事件审慎评级；FMEA 只分析随机硬件/系统故障链；CTA 只分析预期功能不足与触发条件，不给 CTA 行赋 ASIL；STPA 使用控制动作和约束；测试用例需可执行、通过准则可观察。" )}]
    if source_rows:
        content.append({"type": "input_text", "text": "来源分析条目（测试用例应逐项对应其风险、原因与措施）：\n" + json.dumps(source_rows, ensure_ascii=False)[:60000]})
    for path in references:
        body, image = read_reference(path)
        content.append({"type": "input_text", "text": f"参考资料 {path.name}：\n{body}"} if image is None else {"type": "input_text", "text": f"参考架构图 {path.name}："})
        if image:
            content.append({"type": "input_image", "image_url": image})
    payload = {
        "model": model,
        "instructions": "你是自动驾驶功能安全与预期功能安全分析工程师。区分证据、假设与待确认项。不得编造未提供的具体架构。只返回符合 schema 的 JSON。",
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


def validate(task: str, data: dict, source_ids: list[str]) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list) or not data["rows"]:
        raise ValueError(f"{task} 未生成分析条目。")
    fields = FIELDS[task]
    for index, row in enumerate(data["rows"], 1):
        for field in fields:
            if field == "对应分析编号" and not source_ids:
                row.setdefault(field, "不适用（独立测试任务）")
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{task} 第 {index} 条缺少必填字段：{field}")
        if task == "HARA":
            for field, allowed in {"S": {"S1", "S2", "S3"}, "E": {"E1", "E2", "E3", "E4"}, "C": {"C1", "C2", "C3"}, "ASIL": {"QM", "ASIL A", "ASIL B", "ASIL C", "ASIL D"}}.items():
                if row[field].upper() not in allowed:
                    raise ValueError(f"HARA 第 {index} 条的 {field} 值无效：{row[field]}")
        if task == "TEST" and source_ids and row["对应分析编号"] not in source_ids:
            raise ValueError(f"测试用例第 {index} 条引用了不存在的分析编号：{row['对应分析编号']}")
    data["assumptions"] = [str(value) for value in data.get("assumptions", [])]
    return data


def template(task: str, feature: str, instruction: str, source_rows: list[dict]) -> dict:
    """Conservative offline drafts; no source documents are accepted here."""
    lane = feature.upper() in {"LCC", "ALC", "LKA"}
    curve = "弯道" in instruction
    worn = "磨损" in instruction or "模糊" in instruction
    scenario = "高速公路弯道，车道线磨损" if curve and worn else "高速公路正常车道行驶"
    steering = "横向控制" if lane else "功能控制"
    event = "车辆偏离本车道，与相邻车辆或道路边界发生碰撞" if lane else "车辆未按预期执行功能，可能引发碰撞"
    assumptions = [f"仅依据用户指令；假设 {feature} 为驾驶辅助功能，驾驶员持续监督且可接管。", "未提供车型、ODD、系统架构、既有安全机制或实测数据；所有评级与措施需项目团队复核。"]
    if task == "HARA":
        rows = [
            ["H-001", feature, scenario, f"{steering}产生非预期动作", "失去预期行驶轨迹", event, "S3", "E3", "C3", "ASIL D", "SG-01：防止非预期控制导致危险轨迹偏离"],
            ["H-002", feature, "高速公路邻车并行且驾驶员短时未及时接管", f"{steering}在需要时中断", "车辆无法保持预期轨迹", event, "S3", "E3", "C2", "ASIL C", "SG-02：控制能力丧失时及时告警并进入可控状态"],
        ]
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
                concern = next((source[key] for key in ("危险事件", "整车影响", "顶层危险行为", "不安全控制动作及其发生情境") if source.get(key)), "功能危险行为")
                stimulus = next((source[key] for key in ("失效原因", "触发条件", "致因场景", "车辆级失效行为") if source.get(key)), "模拟对应异常条件")
                measure = next((source[key] for key in ("安全措施", "针对性措施", "安全约束", "安全目标") if source.get(key)), "限制危险控制并提示驾驶员")
                rows.append([f"T-{index:03d}", str(source["编号"]), f"验证 {concern} 的风险控制", source.get("运行场景", scenario), f"施加或模拟：{stimulus}", "1. 建立前置场景并激活功能；2. 注入所述条件；3. 记录功能状态、车辆轨迹和提示；4. 对照来源分析条目判定", f"系统执行对应安全响应：{measure}", f"未发生所述危险事件；按项目限值验证响应时间和效果：{measure}"])
        else:
            rows = [
                ["T-001", "不适用（独立测试任务）", "验证边界识别不可靠时系统安全响应", scenario, "注入低置信度车道边界输入或使用磨损标线场景", "1. 建立场景并激活功能；2. 逐步降低边界质量；3. 记录轨迹、功能状态和告警", "系统限制危险控制，必要时退出并提示驾驶员", "不出现非预期车道偏离；在项目规定时间内退出或抑制控制并发出可感知提示"],
                ["T-002", "不适用（独立测试任务）", "验证控制状态异常时的响应", "高速公路稳态行驶，功能已激活", "注入控制命令卡滞或状态延迟（如接口允许）", "1. 激活功能；2. 注入异常；3. 记录命令、车辆响应与提示", "异常被检测，功能停止危险控制并提示接管", "无持续异常控制；检测、退出和提示满足项目规定时间阈值"],
            ]
            if not lane:
                rows[0] = ["T-001", "不适用（独立测试任务）", "验证场景适用性下降时系统安全响应", "功能激活，环境观测质量逐步下降", "注入低置信度环境或目标输入", "1. 建立场景并激活功能；2. 逐步降低输入质量；3. 记录车辆响应、功能状态和告警", "系统限制危险控制，必要时退出并提示驾驶员", "不出现非预期危险响应；在项目规定时间内退出或抑制控制并发出可感知提示"]
    return {"assumptions": assumptions, "rows": [dict(zip(FIELDS[task], row)) for row in rows]}
