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
            self.assertIn("失效模式", sheet)
            self.assertIn("F-001", sheet)

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
                "编号": "F-101", "分析对象或接口": "转向命令接口", "功能": "传递转向命令",
                "失效模式": "命令丢失", "失效原因": "通信中断", "局部影响": "指令无法到达",
                "整车影响": "横向控制中断", "是否可能形成危害": "是",
                "现有控制": "资料未说明", "安全措施": "监测超时并提示接管",
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


if __name__ == "__main__":
    unittest.main()
