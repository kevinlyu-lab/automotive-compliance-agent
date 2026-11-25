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
fusa-harness "分析高速 LCC，生成 CTA" -o lcc_cta.xlsx
fusa-harness "分析高速 ALC，生成 STPA，输出 XLSX" -o alc_stpa.xlsx
fusa-harness "针对高速 AEB 逆光场景开展 SOTIF FMEA" -o aeb_fmea.xlsx
```

## CTA / STPA / SOTIF FMEA 说明

三种方法可分别执行，也可同时要求（如“生成 CTA 和 STPA”）。未提供功能、场景或系统架构时，按通用感知/决策/执行/人机分层补全，并在结果的“待补充资料与确认事项”中声明假设。

- **CTA（因果树分析，ISO 21448:2022 Clause 7）**：从整车危害（顶事件）向下展开功能不足、输出不足与触发条件，区分 AND/OR 逻辑门，输出因果树、底事件，并由因果树推导最小割集（阶数越低优先级越高）。只展开 SOTIF 因果链，不包含随机硬件失效与系统性软件缺陷（那属于 ISO 26262 FTA/FMEA）。
- **STPA（系统理论过程分析）**：按“损失与整车危害 → 系统边界与控制结构 → 控制动作与反馈 → 不安全控制行为（未提供 / 提供导致危害 / 时序错误 / 持续时间错误）→ 原因场景（不足条件 IC + 因果因素 CF）→ 安全约束”顺序输出，每条 UCA 均有对应安全约束。
- **SOTIF 导向 FMEA**：按功能或系统要素逐项分析“功能定义不足或性能局限 → 触发条件 → 输出不足 → 整车危害行为 → 危害事件 → 措施 → 验证方式”。性能局限（如摄像头逆光下能力受限、雷达对静止目标信噪比不足）不得写成硬件损坏或元器件失效。

导出 XLSX 时按分析方法分表，不共用通用表：

| 方法 | 工作表 |
|------|--------|
| CTA | `CTA-顶事件`、`CTA-因果树`、`CTA-底事件`、`CTA-最小割集`、`CTA-待确认` |
| STPA | `STPA-损失与危害`、`STPA-控制结构`、`STPA-不安全控制行为`、`STPA-原因场景`、`STPA-安全约束`、`STPA-待确认` |
| SOTIF FMEA | `FMEA`、`FMEA-待确认` |

## FSR / TSR 说明

- FSR 依据 ISO 26262-3:2018 Clause 7，TSR 依据 ISO 26262-4:2018 Clause 6，二者是独立任务，不要求先执行 HARA 或 FSR。
- 仅提供一句功能或风险描述时输出初步草案；缺少依据的 SG、ASIL、FTTI/FHTI、接口与阈值均标注“待确认”，并在结果末尾列出待补充资料。
- 传入本工具导出的 HARA 或 FSR 分析结果（`--source-analysis`，支持 JSON/XLSX）时，会沿用已有 SG/ASIL 并建立 SG → FSR、FSR → TSR 追溯。
- 参考资料仍可用 `-r` 传入；无模型密钥时 FSR/TSR 生成离线初稿，并在结果中提示参考资料未解析。
