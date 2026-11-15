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
from fusa_harness.core import parse_request, read_analysis_ids


class HarnessTests(unittest.TestCase):
    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(list(args))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_hara_only_text_and_ratings(self):
        code, output, error = self.invoke("分析高速 LCC 功能，生成 HARA，直接在终端回复。", "--backend", "template")
        self.assertEqual(code, 0, error)
        self.assertIn("## HARA", output)
        self.assertIn("S：S3", output)
        self.assertIn("E：E3", output)
        self.assertIn("C：C3", output)
        self.assertIn("ASIL：ASIL D", output)
        self.assertNotIn("## FMEA", output)
        self.assertNotIn("## TEST", output)

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
            self.assertEqual(read_analysis_ids(source), ["H-001", "H-002"])
            target = Path(tmp) / "tests.xlsx"
            code, _, error = self.invoke("根据 HARA 生成 LCC 测试用例，输出 XLSX", "--backend", "template", "--source-analysis", str(source), "-o", str(target))
            self.assertEqual(code, 0, error)
            with ZipFile(target) as book:
                sheet = book.read("xl/worksheets/sheet1.xml").decode()
            self.assertIn("H-001", sheet)
            self.assertIn("H-002", sheet)
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


if __name__ == "__main__":
    unittest.main()
