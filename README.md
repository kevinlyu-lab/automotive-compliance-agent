# FuSa/SOTIF 安全分析 Harness

面向自动驾驶功能的安全分析工具，支持 HARA、系统 FMEA、SOTIF CTA、STPA、测试用例，以及 FSR（功能安全要求）和 TSR（技术安全要求）生成。

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
fusa-harness "终端输出高速 车辆非预期转向风险的FSR"
fusa-harness "终端输出高速 车辆非预期转向风险的TSR"
fusa-harness "根据 HARA 推导高速车辆非预期转向风险的 FSR" --source-analysis hara.json
fusa-harness "根据 FSR 生成 TSR" --source-analysis fsr.json
```
