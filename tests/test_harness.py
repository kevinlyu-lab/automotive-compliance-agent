import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fusa_harness.cli import main
from fusa_harness.core import FSR_FIELDS, parse_request, read_analysis_ids


class HarnessTests(unittest.TestCase):
    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_hara_only_text_framework_and_count(self):
        code, output, error = self.invoke("分析高速 LCC 功能，生成 HARA，直接在终端回复。", "--backend", "template")
        self.assertEqual(code, 0, error)
        self.assertIn("## HARA", output)
        self.assertEqual(output.count("\n[HE"), 50)
        self.assertIn("Hazard：[H001]", output)
        self.assertIn("Potential Effect：", output)
        self.assertIn("Severity Comment：", output)
        self.assertIn("Controllability Comment：", output)
        self.assertIn("Safety Goal：[G001]", output)
        self.assertIn("ASIL：ASIL D", output)
        self.assertNotIn("关键假设", output)
        self.assertNotIn("## FMEA", output)
        self.assertNotIn("## TEST", output)

    def test_hara_xlsx_columns_and_fifty_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hara.xlsx"
            code, output, error = self.invoke("分析 LCC，生成 HARA，输出 XLSX。", "--backend", "template", "-o", str(path))
            self.assertEqual(code, 0, error)
            self.assertTrue(path.exists())
            with ZipFile(path) as book:
                sheet = book.read("xl/worksheets/sheet1.xml").decode()
            for header in ("Hazard", "编号", "位置", "道路条件", "环境条件", "本车车速", "本车操作",
                           "相关项工作状态", "Potential Effect", "Severity", "Severity Comment",
                           "Exposure", "Exposure Comment", "Controllability", "Controllability Comment",
                           "ASIL", "Safety Goal", "Safe State", "备注"):
                self.assertIn(header, sheet)
            self.assertEqual(sheet.count("<row "), 51)
            self.assertIn("HE0050", sheet)

    def test_fmea_only_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alc.xlsx"
            code, output, error = self.invoke("根据提供的系统架构图，对 ALC 开展系统 FMEA，导出 XLSX。", "--backend", "template", "-o", str(path))
            self.assertEqual(code, 0, error)
            self.assertTrue(path.exists())
            with ZipFile(path) as book:
                names = book.read("xl/workbook.xml").decode()
                sheet = book.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn('name="FMEA"', names)
            self.assertNotIn('name="HARA"', names)
            # SOTIF 导向 FMEA：列名必须对应「性能局限 → 触发条件 → 输出不足 → 危害行为 → 措施 → 验证」链条
            for header in ("功能定义不足或性能局限", "触发条件", "输出不足", "整车危害行为",
                           "危害事件", "SOTIF 措施", "验证方式"):
                self.assertIn(header, sheet)
            self.assertIn("F-001", sheet)
            self.assertNotIn("失效模式", sheet)

    def test_independent_test_case_xlsx(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tests.xlsx"
            code, output, error = self.invoke("针对 LCC 在弯道且车道线磨损的情况生成测试用例，输出 XLSX。", "--backend", "template", "-o", str(path))
            self.assertEqual(code, 0, error)
            with ZipFile(path) as book:
                names = book.read("xl/workbook.xml").decode()
                sheet = book.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn('name="TEST"', names)
            self.assertNotIn('name="HARA"', names)
            self.assertIn("车道线磨损", sheet)
            self.assertIn("通过准则", sheet)

    def test_hara_and_fmea_only(self):
        code, output, error = self.invoke("对 LCC 生成功能安全 HARA 和系统 FMEA，以文本回复。", "--backend", "template")
        self.assertEqual(code, 0, error)
        self.assertIn("## HARA", output)
        self.assertIn("## FMEA", output)
        self.assertNotIn("## CTA", output)
        self.assertNotIn("## STPA", output)
        self.assertNotIn("## TEST", output)

    def test_cta_stpa_independent_and_no_asil_in_cta(self):
        for task in ("CTA", "STPA"):
            code, output, error = self.invoke(f"分析 LCC，生成 {task}", "--backend", "template")
            self.assertEqual(code, 0, error)
            self.assertIn(f"## {task}", output)
            self.assertNotIn("ASIL：", output)
            self.assertNotIn("## TEST", output)

    def test_source_analysis_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "hara.json"
            code, _, error = self.invoke("分析 LCC，生成 HARA", "--backend", "template", "--save-json", str(source))
            self.assertEqual(code, 0, error)
            ids = read_analysis_ids(source)
            self.assertEqual(len(ids), 50)
            self.assertEqual(ids[0], "HE0001")
            self.assertEqual(ids[-1], "HE0050")
            target = Path(tmp) / "tests.xlsx"
            code, _, error = self.invoke("根据 HARA 生成 LCC 测试用例，输出 XLSX", "--backend", "template", "--source-analysis", str(source), "-o", str(target))
            self.assertEqual(code, 0, error)
            with ZipFile(target) as book:
                sheet = book.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("HE0001", sheet)
            self.assertIn("HE0002", sheet)
            self.assertEqual(parse_request("根据 HARA 生成 LCC 测试用例").tasks, ["TEST"])

    def test_model_receives_explicit_reference_and_only_requested_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "architecture.txt"
            source.write_text("图示：车道线识别模块 -> ALC 控制逻辑 -> 转向命令接口", encoding="utf-8")
            row = {
                "编号": "F-101", "分析对象或功能": "转向命令接口", "要素层级": "系统要素",
                "功能定义不足或性能局限": "接口未定义指令时效性", "触发条件": "高负载总线",
                "输出不足": "指令到达过晚", "整车危害行为": "横向控制响应滞后",
                "危害事件": "车辆偏离本车道", "现有控制措施": "资料未说明",
                "SOTIF 措施": "定义超时与序列号校验", "验证方式": "总线负载压力测试",
                "残余风险说明": "待确认",
            }
            captured = []

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return None

                def read(self):
                    return json.dumps({"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps({"assumptions": ["参考图未给出监测阈值"], "rows": [row]}, ensure_ascii=False)}]}]}).encode()

            def fake_urlopen(request, timeout):
                captured.append(json.loads(request.data))
                return FakeResponse()

            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                code, output, error = self.invoke("根据提供的系统架构图，对 ALC 开展系统 FMEA，导出 XLSX。", "-r", str(source), "-o", str(Path(tmp) / "result.xlsx"))
            self.assertEqual(code, 0, error)
            self.assertEqual(len(captured), 1)
            self.assertIn("车道线识别模块", json.dumps(captured[0], ensure_ascii=False))
            self.assertEqual(captured[0]["text"]["format"]["name"], "analysis_fmea")
            self.assertIn("result.xlsx", output)

    def test_reference_requires_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "architecture.txt"
            source.write_text("感知系统 -> 控制器 -> 执行端", encoding="utf-8")
            code, output, error = self.invoke("对 ALC 开展系统 FMEA", "--backend", "template", "-r", str(source))
            self.assertEqual(code, 2)
            self.assertIn("OPENAI_API_KEY", error)
            self.assertFalse(output)

    def test_fsr_tsr_routing_and_keywords(self):
        self.assertEqual(parse_request("终端输出高速 车辆非预期转向风险的FSR").tasks, ["FSR"])
        self.assertEqual(parse_request("终端输出高速 车辆非预期转向风险的TSR").tasks, ["TSR"])
        self.assertEqual(parse_request("functional safety requirements for unintended steering").tasks, ["FSR"])
        self.assertEqual(parse_request("technical safety requirements for braking loss").tasks, ["TSR"])
        self.assertEqual(parse_request("分析 LCC 功能安全要求").tasks, ["FSR"])
        self.assertEqual(parse_request("生成 FSR 和 TSR").tasks, ["FSR", "TSR"])
        # 根据/基于之后的分析是上游输入，不应被同时当成要生成的任务。
        self.assertEqual(parse_request("根据 HARA 推导高速车辆非预期转向风险的 FSR").tasks, ["FSR"])
        self.assertEqual(parse_request("根据 FSR 生成 TSR").tasks, ["TSR"])
        request = parse_request("终端输出高速 车辆非预期转向风险的FSR")
        self.assertIn("车辆非预期转向", request.feature)
        self.assertEqual(request.output, "text")
        with self.assertRaises(ValueError) as caught:
            parse_request("随便聊聊")
        self.assertIn("FSR", str(caught.exception))
        self.assertIn("TSR", str(caught.exception))

    def test_fsr_single_sentence_draft(self):
        code, output, error = self.invoke("终端输出高速 车辆非预期转向风险的FSR", "--backend", "template")
        self.assertEqual(code, 0, error)
        self.assertIn("## FSR", output)
        self.assertIn("FSR-001", output)
        self.assertIn("FSR-006", output)
        self.assertIn("SG → FSR 追溯关系", output)
        self.assertIn("安全状态（Safe State）", output)
        self.assertIn("ASIL 等级：待确认", output)
        self.assertIn("待确认", output)
        self.assertIn("## 待补充资料与确认事项", output)
        self.assertNotIn("## HARA", output)
        self.assertNotIn("## TSR", output)

    def test_tsr_single_sentence_draft(self):
        code, output, error = self.invoke("终端输出高速 车辆非预期转向风险的TSR", "--backend", "template")
        self.assertEqual(code, 0, error)
        self.assertIn("## TSR", output)
        self.assertIn("TSR-001", output)
        self.assertIn("相关 FSR：", output)
        self.assertIn("候选 FSR", output)
        self.assertIn("FSR → TSR 追溯关系", output)
        self.assertIn("## 待补充资料与确认事项", output)
        self.assertIn("未提供 FSR/FSC", output)
        self.assertNotIn("## FSR", output)
        self.assertNotIn("## HARA", output)

    def test_fsr_xlsx_sheet_and_pending_sheet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fsr.xlsx"
            code, output, error = self.invoke("终端输出高速 车辆非预期转向风险的FSR，输出 XLSX。", "--backend", "template", "-o", str(path))
            self.assertEqual(code, 0, error)
            self.assertTrue(path.exists())
            with ZipFile(path) as book:
                names = book.read("xl/workbook.xml").decode()
                sheet = book.read("xl/worksheets/sheet1.xml").decode()
                notes = book.read("xl/worksheets/sheet2.xml").decode()
            self.assertIn('name="FSR"', names)
            self.assertIn('name="FSR-待确认"', names)
            self.assertIn("FSR 编号", sheet)
            self.assertIn("FSR-001", sheet)
            self.assertEqual(sheet.count("<row "), 7)
            self.assertIn("待补充资料", notes)

    def test_fsr_from_hara_source_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            hara = Path(tmp) / "hara.json"
            code, _, error = self.invoke("分析高速 LCC 功能，生成 HARA", "--backend", "template", "--save-json", str(hara))
            self.assertEqual(code, 0, error)
            code, output, error = self.invoke(
                "根据 HARA 推导高速车辆非预期转向风险的 FSR", "--backend", "template", "--source-analysis", str(hara)
            )
            self.assertEqual(code, 0, error)
            self.assertIn("## FSR", output)
            self.assertIn("[G001]", output)
            self.assertIn("ASIL 等级：ASIL D", output)
            self.assertIn("G001 → FSR-001", output)
            self.assertIn("已依据提供的 HARA 分析结果", output)
            self.assertNotIn("未提供 HARA 分析结果", output)

    def test_tsr_from_fsr_source_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            fsr = Path(tmp) / "fsr.json"
            code, _, error = self.invoke("终端输出高速 车辆非预期转向风险的FSR", "--backend", "template", "--save-json", str(fsr))
            self.assertEqual(code, 0, error)
            self.assertEqual(read_analysis_ids(fsr), [f"FSR-{index:03d}" for index in range(1, 7)])
            code, output, error = self.invoke("根据 FSR 生成 TSR", "--backend", "template", "--source-analysis", str(fsr))
            self.assertEqual(code, 0, error)
            self.assertIn("## TSR", output)
            self.assertIn("FSR-003 → TSR-001", output)
            self.assertIn("已依据提供的 FSR", output)

    def test_reference_allowed_for_fsr_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "architecture.txt"
            reference.write_text("感知 -> 控制器 -> 转向执行器", encoding="utf-8")
            code, output, error = self.invoke("分析高速车辆非预期转向风险的 FSR", "--backend", "template", "-r", str(reference))
            self.assertEqual(code, 0, error)
            self.assertIn("已接收参考资料：architecture.txt", output)

    def test_reference_allowed_for_tsr_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference = Path(tmp) / "fsc.txt"
            reference.write_text("FSC：横向控制功能 -> 转向执行器", encoding="utf-8")
            code, output, error = self.invoke("分析高速车辆非预期转向风险的 TSR", "--backend", "template", "-r", str(reference))
            self.assertEqual(code, 0, error)
            self.assertIn("## TSR", output)
            self.assertIn("已接收参考资料：fsc.txt", output)

    def test_tsr_from_hara_marks_candidate_fsr(self):
        with tempfile.TemporaryDirectory() as tmp:
            hara = Path(tmp) / "hara.json"
            code, _, error = self.invoke("分析高速 LCC 功能，生成 HARA", "--backend", "template", "--save-json", str(hara))
            self.assertEqual(code, 0, error)
            code, output, error = self.invoke(
                "根据 HARA 推导高速车辆非预期转向风险的 TSR", "--backend", "template", "--source-analysis", str(hara)
            )
            self.assertEqual(code, 0, error)
            self.assertIn("## TSR", output)
            self.assertIn("候选 FSR-003", output)
            self.assertIn("ASIL 等级：ASIL D", output)
            self.assertIn("候选 FSR 为待确认的上游要求", output)

    def test_model_fsr_prompt_schema_and_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            hara = Path(tmp) / "hara.json"
            code, _, error = self.invoke("分析高速 LCC 功能，生成 HARA", "--backend", "template", "--save-json", str(hara))
            self.assertEqual(code, 0, error)
            row = {field: "待确认（示例）" for field in FSR_FIELDS}
            row["FSR 编号"] = "FSR-001"
            captured = []

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return None

                def read(self):
                    return json.dumps({"status": "completed", "output": [{"content": [{"type": "output_text", "text": json.dumps({"rows": [row], "notes": ["待补充 HARA 确认记录"]}, ensure_ascii=False)}]}]}).encode()

            def fake_urlopen(request, timeout):
                captured.append(json.loads(request.data))
                return FakeResponse()

            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), patch("urllib.request.urlopen", side_effect=fake_urlopen):
                code, output, error = self.invoke(
                    "根据 HARA 推导高速车辆非预期转向风险的 FSR", "--source-analysis", str(hara)
                )
            self.assertEqual(code, 0, error)
            self.assertEqual(captured[0]["text"]["format"]["name"], "analysis_fsr")
            self.assertIn("notes", captured[0]["text"]["format"]["schema"]["required"])
            prompt = json.dumps(captured[0], ensure_ascii=False)
            self.assertIn("ISO 26262-3:2018 Clause 7", prompt)
            self.assertIn("HE0001", prompt)
            self.assertIn("## FSR", output)
            self.assertIn("待补充 HARA 确认记录", output)


    def test_cta_text_sections_gates_and_default_scenario(self):
        code, output, error = self.invoke("分析 LCC，生成 CTA", "--backend", "template")
        self.assertEqual(code, 0, error)
        self.assertIn("## CTA", output)
        for index, name in enumerate(("顶事件", "因果树", "底事件", "最小割集"), 1):
            self.assertIn(f"### {index}. {name}", output)
        # AND/OR 必须同时出现，割集编号可读
        self.assertIn("逻辑门：AND", output)
        self.assertIn("逻辑门：OR", output)
        self.assertIn("MCS-001", output)
        self.assertIn("阶数：1 阶", output)
        # 未给场景时自主补全，并在分析边界中声明
        self.assertIn("待项目确认", output)
        self.assertIn("不包含随机硬件失效", output)
        self.assertNotIn("## STPA", output)
        self.assertNotIn("## FMEA", output)

    def test_cta_cut_sets_are_minimal_and_derived_from_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cta.json"
            code, _, error = self.invoke("分析高速 LCC，生成 CTA", "--backend", "template", "--save-json", str(path))
            self.assertEqual(code, 0, error)
            data = json.loads(path.read_text(encoding="utf-8"))
            sections = {item["name"]: item["rows"] for item in data["analyses"][0]["sections"]}
            leaves = {row["底事件编号"] for row in sections["底事件"]}
            cuts = []
            for row in sections["最小割集"]:
                cut = set(row["布尔表达式"].replace(" ", "").split("·"))
                self.assertTrue(cut <= leaves, cut)
                self.assertEqual(row["阶数"], f"{len(cut)} 阶")
                cuts.append(cut)
            # 最小性：不存在两个割集互为真子集
            for first in cuts:
                for second in cuts:
                    if first != second:
                        self.assertFalse(first < second, f"{first} 是 {second} 的真子集，割集未消减")
            # AND 分支（≥2 阶）与 OR 分支（1 阶）都应出现
            self.assertEqual(min(len(cut) for cut in cuts), 1)
            self.assertGreaterEqual(max(len(cut) for cut in cuts), 2)

    def test_stpa_text_sections_and_four_uca_types(self):
        code, output, error = self.invoke("分析高速 ALC，生成 STPA", "--backend", "template")
        self.assertEqual(code, 0, error)
        self.assertIn("## STPA", output)
        for index, name in enumerate(("损失与危害", "控制结构", "不安全控制行为", "原因场景", "安全约束"), 1):
            self.assertIn(f"### {index}. {name}", output)
        for kind in ("未提供", "提供导致危害", "时序错误", "持续时间错误"):
            self.assertIn(kind, output)
        self.assertIn("UCA-1", output)
        self.assertIn("不足条件（IC）", output)
        self.assertIn("因果因素（CF）", output)
        self.assertIn("SC-1", output)
        self.assertNotIn("## CTA", output)
        self.assertNotIn("ASIL", output)

    def test_fmea_rows_follow_sotif_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fmea.json"
            code, _, error = self.invoke("针对 LCC 在逆光高速场景开展 SOTIF FMEA", "--backend", "template", "--save-json", str(path))
            self.assertEqual(code, 0, error)
            rows = json.loads(path.read_text(encoding="utf-8"))["analyses"][0]["rows"]
            self.assertGreaterEqual(len(rows), 6)
            for row in rows:
                for field in ("功能定义不足或性能局限", "触发条件", "输出不足", "整车危害行为", "危害事件", "SOTIF 措施", "验证方式"):
                    self.assertTrue(row[field].strip(), field)
                # 性能局限不得写成硬件损坏
                self.assertNotIn("硬件损坏", " ".join(row.values()))
                self.assertNotIn("元器件失效", " ".join(row.values()))
            self.assertTrue(any("逆光" in row["触发条件"] for row in rows))

    def test_cta_schema_is_sectioned_by_analysis_step(self):
        from fusa_harness.core import schema_for

        schema = schema_for("CTA")
        names = [item["properties"]["name"]["enum"][0] for item in schema["properties"]["sections"]["items"]["anyOf"]]
        self.assertEqual(names, ["顶事件", "因果树", "底事件", "最小割集"])
        self.assertIn("notes", schema["required"])
        stpa_names = [item["properties"]["name"]["enum"][0] for item in schema_for("STPA")["properties"]["sections"]["items"]["anyOf"]]
        self.assertEqual(stpa_names, ["损失与危害", "控制结构", "不安全控制行为", "原因场景", "安全约束"])

    def test_cta_from_hara_source_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            hara = Path(tmp) / "hara.json"
            code, _, error = self.invoke("分析高速 LCC 功能，生成 HARA", "--backend", "template", "--save-json", str(hara))
            self.assertEqual(code, 0, error)
            code, output, error = self.invoke(
                "根据 HARA 生成高速 LCC 的 CTA", "--backend", "template", "--source-analysis", str(hara)
            )
            self.assertEqual(code, 0, error)
            self.assertIn("## CTA", output)
            self.assertIn("已关联来源分析条目", output)
            self.assertIn("HE0001", output)

    def test_sotif_method_xlsx_sheets_follow_analysis_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            expected = {
                "CTA": ["CTA-顶事件", "CTA-因果树", "CTA-底事件", "CTA-最小割集", "CTA-待确认"],
                "STPA": ["STPA-损失与危害", "STPA-控制结构", "STPA-不安全控制行为", "STPA-原因场景", "STPA-安全约束", "STPA-待确认"],
                "FMEA": ["FMEA", "FMEA-待确认"],
            }
            for task, sheets in expected.items():
                path = Path(tmp) / f"{task.lower()}.xlsx"
                code, output, error = self.invoke(f"分析高速 LCC，生成 {task}，输出 XLSX", "--backend", "template", "-o", str(path))
                self.assertEqual(code, 0, error)
                with ZipFile(path) as book:
                    names = book.read("xl/workbook.xml").decode()
                for name in sheets:
                    self.assertIn(f'name="{name}"', names)
                for other in ("CTA", "STPA", "FMEA"):
                    if other != task:
                        self.assertNotIn(f'name="{other}-', names)


if __name__ == "__main__":
    unittest.main()
