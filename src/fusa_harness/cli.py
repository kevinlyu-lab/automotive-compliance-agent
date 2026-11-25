"""Terminal entry point."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from .core import FIELDS, Request, _row_number, call_model, parse_request, read_analysis_rows, template, validate
from .xlsx import write_xlsx

SOURCE_TASKS = {"TEST", "FSR", "TSR", "CTA", "STPA", "FMEA"}
SECTION_TITLES = {
    "HARA": "## HARA",
    "FMEA": "## FMEA（SOTIF 导向归纳分析，ISO 21448:2022）",
    "CTA": "## CTA（因果树分析，ISO 21448:2022 Clause 7）",
    "STPA": "## STPA（系统理论过程分析，MIT STPA Handbook / ISO 21448:2022）",
    "FSR": "## FSR（ISO 26262-3:2018 Clause 7）",
    "TSR": "## TSR（ISO 26262-4:2018 Clause 6）",
}


def render_text(request: Request, analyses: list[dict]) -> str:
    blocks = [f"分析对象：{request.feature}", "状态：可修改的初稿，需由项目安全团队复核"]
    for section in analyses:
        task = section["task"]
        blocks.extend(["", SECTION_TITLES.get(task, f"## {task}")])
        if section.get("sections"):
            multiple = len(section["sections"]) > 1
            for index, part in enumerate(section["sections"], 1):
                if multiple:
                    blocks.extend(["", f"### {index}. {part['name']}"])
                for row in part["rows"]:
                    blocks.append("")
                    number = _row_number(row)
                    if number:
                        blocks.append(f"[{number}]")
                    blocks.extend(f"{field}：{row[field]}" for field in part["fields"])
        else:
            for row in section["rows"]:
                blocks.append("")
                blocks.append(f"[{_row_number(row)}]")
                blocks.extend(f"{field}：{row[field]}" for field in FIELDS[task] if field != "编号")
    gaps = [(section["task"], note) for section in analyses for note in section.get("notes", [])]
    if gaps:
        blocks.extend(["", "## 待补充资料与确认事项"])
        blocks.extend(f"- {task}：{note}" for task, note in gaps)
    return "\n".join(blocks)


def run(request: Request, references: list[Path], source_analysis: Path | None, backend: str, model: str) -> list[dict]:
    if source_analysis and not (set(request.tasks) & SOURCE_TASKS):
        raise ValueError("--source-analysis 仅用于测试用例、FSR、TSR、CTA、STPA 或 FMEA 任务。")
    source_rows = read_analysis_rows(source_analysis) if source_analysis else []
    source_ids = [_row_number(row) for row in source_rows]
    if source_analysis and not source_rows:
        raise ValueError("来源分析文件中没有可用编号。")
    use_model = backend == "openai" or (backend == "auto" and bool(os.getenv("OPENAI_API_KEY")))
    if use_model and not os.getenv("OPENAI_API_KEY"):
        raise ValueError("模型模式需要设置 OPENAI_API_KEY。")
    if references and not use_model and not set(request.tasks) <= {"FSR", "TSR"}:
        raise ValueError("分析参考资料需要 OPENAI_API_KEY；无密钥时仅支持无参考资料的通用初稿。")
    analyses = []
    for task in request.tasks:
        task_source_rows = source_rows if task in SOURCE_TASKS else []
        task_source_ids = source_ids if task in SOURCE_TASKS else []
        generated = call_model(task, request, references, task_source_rows, model) if use_model else template(task, request.feature, request.instruction, task_source_rows)
        generated = validate(task, generated, task_source_ids)
        if references and not use_model and task in {"FSR", "TSR"}:
            names = "、".join(path.name for path in references)
            generated["notes"].append(f"已接收参考资料：{names}；离线初稿未解析资料内容，相关结论仍需结合资料确认。")
        analyses.append({"task": task, **generated})
    return analyses


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FuSa/SOTIF 安全分析初稿生成器")
    parser.add_argument("instruction", nargs="?", help="自然语言指令；省略时从标准输入读取")
    parser.add_argument("--reference", "-r", type=Path, action="append", default=[], help="显式提供参考资料，可重复使用")
    parser.add_argument("--source-analysis", type=Path, help="生成测试用例、FSR 或 TSR 所依据的本工具 JSON/XLSX 分析文件")
    parser.add_argument("--format", choices=["text", "xlsx"], help="覆盖指令中的输出格式")
    parser.add_argument("--output", "-o", type=Path, help="XLSX 输出路径；也可用于保存文本")
    parser.add_argument("--save-json", type=Path, help="同时保存可编辑 JSON 分析数据")
    parser.add_argument("--backend", choices=["auto", "openai", "template"], default="auto", help="auto: 有密钥用模型，否则生成通用初稿")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"), help="OpenAI Responses API 模型")
    args = parser.parse_args(argv)
    try:
        instruction = args.instruction if args.instruction is not None else sys.stdin.read()
        request = parse_request(instruction, args.format)
        analyses = run(request, args.reference, args.source_analysis, args.backend, args.model)
        document = {"instruction": request.instruction, "feature": request.feature, "analyses": analyses}
        if request.output == "xlsx":
            path = args.output or Path(f"{request.feature.replace(' ', '_')}_analysis_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
            sheets = []
            for section in analyses:
                task = section["task"]
                if section.get("sections"):
                    # 每种方法按分析步骤分表，不把多种方法塞进同一张通用表。
                    for part in section["sections"]:
                        name = part["name"] if task == "FMEA" else f"{task}-{part['name']}"
                        sheets.append((name[:31], part["fields"], [[row[field] for field in part["fields"]] for row in part["rows"]]))
                else:
                    fields = FIELDS[task]
                    sheets.append((task, fields, [[row[field] for field in fields] for row in section["rows"]]))
                if section.get("notes"):
                    title = f"{task}-待确认"
                    sheets.append((title[:31], ["待补充资料与确认事项"], [[note] for note in section["notes"]]))
            write_xlsx(path, sheets)
            print(f"已生成：{path.resolve()}")
        else:
            output = render_text(request, analyses)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(output + "\n", encoding="utf-8")
                print(f"已生成：{args.output.resolve()}")
            else:
                print(output)
        if args.save_json:
            args.save_json.parent.mkdir(parents=True, exist_ok=True)
            args.save_json.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"已生成：{args.save_json.resolve()}", file=sys.stderr)
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
