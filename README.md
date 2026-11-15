# FuSa/SOTIF 安全分析 Harness

面向自动驾驶功能的终端初稿生成器。按自然语言指令独立运行 HARA、系统 FMEA、SOTIF CTA、STPA、测试用例，支持一次指定多项任务。输出为终端文本或可编辑 XLSX。初稿供工程师修改与复核，不能直接替代项目安全结论。

## 安装

需要 Python 3.9+，无第三方运行依赖。

```bash
python3 -m pip install -e .
```

也可直接运行：`PYTHONPATH=src python3 -m fusa_harness "分析高速 LCC 功能，生成 HARA，直接在终端回复。"`

## 使用

```bash
fusa-harness "分析高速 LCC 功能，生成 HARA，直接在终端回复。"
fusa-harness "对 LCC 生成功能安全 HARA 和系统 FMEA，以文本回复。"
fusa-harness "针对 LCC 在弯道且车道线磨损的情况生成测试用例，输出 XLSX。" -o lcc_tests.xlsx
fusa-harness "根据提供的系统架构图，对 ALC 开展系统 FMEA，导出 XLSX。" -r ./architecture.png -o alc_fmea.xlsx
```

也可以省略指令参数，从标准输入读取。参考资料通过 `-r/--reference` 显式提供，可多次传入；不会扫描项目目录。支持 TXT、Markdown、JSON、CSV、TSV、DOCX、可提取文字的 PDF（需安装 `pdftotext`）及 PNG/JPEG/WebP 图片。图片和参考文件分析需要设置 `OPENAI_API_KEY`。

```bash
export OPENAI_API_KEY="..."
fusa-harness "对 ALC 开展系统 FMEA，导出 XLSX" -r ./architecture.png -o alc_fmea.xlsx
```

默认 `--backend auto`：有密钥时调用 OpenAI Responses API 按任务生成结构化分析；无密钥且无参考资料时生成明确标记通用假设的离线初稿。可用 `--backend openai` 强制模型模式，`--model` 选择模型。指定参考资料但没有密钥时会报错，避免把资料驱动分析伪装成已读取资料的结果。未指定格式时输出终端文本。可用 `--format text|xlsx` 覆盖指令格式；`-o` 保存文件；`--save-json` 额外保存可编辑 JSON。

根据已有分析生成测试用例：

```bash
fusa-harness "分析 LCC，生成 HARA" --save-json hara.json
fusa-harness "根据 HARA 生成 LCC 测试用例，输出 XLSX" --source-analysis hara.json -o tests.xlsx
```

`--source-analysis` 支持本工具的 JSON 或 XLSX，测试用例保留对应分析编号。只有用户要求测试用例时才生成测试用例。FMEA 的故障链与 CTA 的功能不足、触发条件分开处理。CTA 不包含 ASIL 字段。

## 验证

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

模型输出会检查每项任务的必填字段、HARA 的 S/E/C 与 ASIL 值，以及来源测试用例的编号引用。评级是可修改的初稿，须按具体 ODD、车辆架构、控制策略和项目证据复核。
