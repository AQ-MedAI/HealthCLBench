# HealthCLBench 使用指南

用于医疗上下文学习的评测框架：对比完整上下文（Full）与仅末轮问题（Query），
用每个 Case 的独立 Rubrics 判分，再计算簇分数。

[中文项目首页](../README_ZH.md) · [English](../README.md) · [技术说明](README.md) ·
[数据格式](DATA_FORMAT.md) · [裁判协议](JUDGE_PROTOCOL.md)

**论文：[HealthCLBench](https://arxiv.org/abs/2611.22362)（arXiv:2611.22362，2026）。**

## 研究概要

HealthCLBench 关注模型能否利用持续变化的健康对话上下文，而不只是回答
孤立的医学问题。论文通过**受控案例簇**组织原 Case 与相关变体：

- **改变关键条件或任务**：检查回答是否在应当改变时改变、不应改变时保持稳定，
  而非仅做同义改写。
- **Context–Rubric Co-Diffusion**：证据或任务变化时，同步保留、修改、删除或新增
  评分要求，确保新 Rubrics 与新 Case 对齐。
- **医学复核与安全门禁**：人机协作构建并进行医学复核，以 P0 表达关键要求。
- **单题与簇共同评价**：Full/Query 对照衡量上下文收益，簇分数揭示单题均分
  可能掩盖的薄弱成员。

## 数据与实验口径

最终数据版本 `release-20261029`：**1,103 个 Case、328 个簇、4,931 条 Rubrics**。
P0/P1/P2 分别为 1,028/3,648/255。簇成员以元数据清单为准，不能凭编号猜测。

论文表 4 的医学场景分布为：医学解释 41、健康评估 498、诊疗决策 273、
分诊就医 127、健康管理 164。每簇 1–5 个 Case（中位数 3.5），每个 Case
2–14 条 Rubrics（中位数 4）。这些是论文统计，不是运行时自动推断的场景标签。

[本地数据包](../data/release-20261029/README.md)包含原始 CSV、运行用 JSONL 和校验记录。
默认使用 Hugging Face 带标签 CSV，包含 `label` 和 `cluster_id`；固定文件哈希保护发布版完整性。
本地 JSONL 保留为离线备选，可将下方命令的数据路径替换为
`data/release-20261029/HealthCLBench.jsonl`。切换格式须新建运行目录。
命令仍需显式提供 `--dataset`，不会隐式下载。

- Full：完整发布的消息序列，不自动截断。
- Query：仅末轮问题，不含历史上下文。
- 两种条件使用相同 Case 和裁判；裁判均看到完整证据，只评价生成的最后回答。
- 被测模型看不到 Rubrics。当前仅支持字符串或文本块，不支持直接输入图片。

## 1. 安装并验证

以下命令统一从**仓库根目录**运行，需要 Python 3.11+。

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install './public_eval[hf]'
python -m unittest discover -s public_eval/tests -q
python public_eval/examples/demo.py --output /tmp/hclbench-demo-001
mkdir -p downloads
hclbench-download --config public_eval/hf.example.json --output downloads/HealthCLBench.csv
hclbench validate --dataset downloads/HealthCLBench.csv
```

合成 demo 不调用模型，预期 Full 两项分数 100、Query 两项 0、差分两项 100。
每次新实验使用新目录，不能覆盖已有结果。离线评分不需要 Key；真实调用需要。

## 2. 配置模型并跑小样本

把 `public_eval/config.example.json` 复制到仓库外。配置至少一个被测模型、
三名不同裁判、HTTPS 服务地址、对应主机白名单、参数及密钥环境变量名。
真实 Key 通过环境变量或密钥管理工具提供，不写入 JSON 或 Git。

```sh
hclbench-run --dataset downloads/HealthCLBench.csv --config /path/to/run.json --output-dir /tmp/hclbench-run-001 --clusters cluster-0072 --dry-run
hclbench-run --dataset downloads/HealthCLBench.csv --config /path/to/run.json --output-dir /tmp/hclbench-run-001 --clusters cluster-0072
```

该版本的 `cluster-0072` 含 3 个 Case。选簇会保留全部成员，不要截取 JSONL 前 N 行。
不传 `--clusters` 才是全量。执行器自动运行 Full 和 Query 两种条件。
dry-run 不调用接口，仅检查配置、Key 是否存在和请求数量，不能证明 Key 有效。

一个被测模型跑全量需要 2,206 次推理、最多 29,586 次裁判调用，重试另计；
这不是 token 或费用估算。建议先小样本。

示例默认并发 4、socket 超时 180 秒、推理 4,096 / 裁判 2,048 token、等待 10 秒
重试一次。推理模型可能在生成最终 JSON 前耗尽预算，需按服务调整。
本次成功复测使用并发 3、240 秒、8,192 token；不是所有模型的推荐默认值。
参数变化后必须使用新运行目录，不可冒充原实验续跑。

## 3. 报告与续跑

```sh
hclbench-report --run-dir /tmp/hclbench-run-001 --output-dir /tmp/hclbench-report-001
hclbench-run --dataset downloads/HealthCLBench.csv --config /path/to/run.json --output-dir /tmp/hclbench-run-001 --clusters cluster-0072 --resume
```

报告输出 JSON、CSV 和 Markdown，从原始裁判票独立重算。
缺失分数留空；部分报告退出码为 2，不把缺失当零。
运行结束后再导出报告，报告目录必须不存在。

续跑复用成功调用，补失败裁判票；已固定为失败的推理记录仍记零，重跑需新目录。
数据、配置或裁判协议变化会拒绝续跑。鉴权失败会终止，不作为模型质量零分。
进程意外结束留下 `.lock` 时，先确认无活动进程，才能手动移除该锁。

## 评分规则

三名裁判独立投票：至少两票 Met 或两票 Not Met 才形成结论，否则未决。
正向 Rubric 的 Met 为满足；负向 Rubric 的 Met 为**禁止行为发生**，不是遵守禁令。

- 权重 P0/P1/P2 = 3/2/1；任一 P0 明确失败，整题为零。
- 其它情况：`max(0, (通过权重 − 失败 P1/P2 权重) / 总权重)`。
- 未决不加不扣，但保留分母；单题按 HALF_EVEN 保留四位小数。
- 簇内几何平均，任一成员零分则整簇零分；簇间等权算术平均。
- 同时报告 Case 均分与 Full−Query 差分。

明确推理执行失败可计零；缺记录报错。技术性裁判失败阻止完整出分，
不能转成 Unknown。成功调用后的零分可能是评分结果，不一定是程序错误。

## Hugging Face

仓库地址：[AQ-MedAI/HealthCLBench](https://huggingface.co/datasets/AQ-MedAI/HealthCLBench)。
支持直接下载并评测 Hub 的带标签 CSV，也继续支持 JSONL。下载校验 SHA-256，保留原文件字节；CSV 的标签不会加入模型输入。

`public_eval/hf.example.json` 已填写当前公开 CSV 的仓库 ID、固定 revision、文件名和文件 SHA-256：

```sh
python -m pip install './public_eval[hf]'
mkdir -p downloads
hclbench-download --config public_eval/hf.example.json --output downloads/HealthCLBench.csv
hclbench validate --dataset downloads/HealthCLBench.csv
```

### 进阶数据配置

离线评测可将运行命令中的 `downloads/HealthCLBench.csv` 替换为随仓库附带的
`data/release-20261029/HealthCLBench.jsonl`，无需下载。切换格式时须新建运行目录；
命令始终需要显式提供 `--dataset`。

如需下载其他 JSONL 版本，请另建配置并填写对应的 `repo_id`、固定 `revision`、
`filename` 和文件字节的 `sha256`：

```sh
python -m pip install './public_eval[hf]'
mkdir -p downloads
hclbench-download --config /path/to/hf-source.json --output downloads/HealthCLBench.jsonl
hclbench validate --dataset downloads/HealthCLBench.jsonl
```

私有或受限数据需在下载命令中显式增加 `--token-env HF_TOKEN`，并通过该环境变量提供令牌。
下载器不会默认发送缓存凭证，会校验文件哈希、格式和簇完整性，拒绝覆盖已有文件，也不执行远程数据脚本。
不要把本地校验和直接当作远端已验证值。

## 验证状态与边界

2026-11-18：46 项单元测试通过；随仓库发布的 CSV 和 JSONL 均校验通过，
各包含 1,103 个案例、328 个簇、4,931 条评分项。这是本地验证结果，不代表托管 CI 已通过或论文结果已复现。

2026-10-29：44 项单元测试通过；全量加载、无损转换和 Full/Query 请求构造通过；
四种模拟投票完成 4,412 次逐题算分对照及簇聚合校验。

真实复测：1 个完整簇、3 条 Case，Qwen3.6-35B-A3B 推理，
Kimi K2.6 / Qwen3.6-35B-A3B / GLM-5.2 裁判，6 份回答、54 张裁判票全部成功，
1 次调用重试后成功；报告独立重算通过。首轮更大样本出现网络失败和输出截断，
未完整出分。不能据此宣称全量无失败，或将小样本得分用作排行榜。
本次被测模型与裁判重叠，正式结果需披露该设置。

框架支持非流式 OpenAI 兼容 Chat Completions，不自动部署模型，
不支持 Responses API 或 `reasoning_effort`；其它服务可接外部执行器。
不自动修订医学内容，不宣称已复现论文或其它平台的裁判协议。
详细参数与故障处理见[技术说明](README.md)。

代码采用 [Apache-2.0](LICENSE)，数据集采用 [CC BY-NC-SA 4.0](../data/release-20261029/LICENSE)。
正式开源审批、公开论文引用和基准结果的验证边界见[发布清单](RELEASE_CHECKLIST.md)。报告虽不含问答原文，运行目录仍包含
输入及回答，不能连同私有配置、凭证一起公开。

## 论文主结果与引用

已在[项目首页主结果表](../README_ZH.md#论文结果)摘录论文表 6 的八系统结果。
论文对同一组 1,103 个 Case / 328 个簇进行 Full、Query 配对评测；
七个通用模型使用 thinking/high，Ant Afu 使用蚂蚁集团 AI 健康助手服务。主要结论是：

- 完整上下文与仅当前问题相比，Case 分差异为 **+11.83 至 +31.76**，
  簇分差异为 **+10.87 至 +38.97**。
- GPT-6 Astra 的 Full 单题均分为 **75.03**，簇均分为 **60.42**。
- Full 条件下八个系统的 Case 与 Cluster 排名一致，Ant Afu 位列第三。
- 论文 §6.1 对原七模型评测中的 700 份回答（每模型 100 份）进行人工核验，Rubric 判断
  一致率为 **90.5%**；这不是回答准确率。

以上为**论文报告结果，不是当前框架的小样本跑测结果**。数量一致不代表论文数据
快照与协议已完全对齐；内置执行器尚不支持 `reasoning_effort`，对应实验需使用
支持该设置的外部执行器，并补充精确版本与参数记录后才能宣称复现。
论文公开链接：[arXiv:2611.22362](https://arxiv.org/abs/2611.22362)。请使用以下 BibTeX 引用：

```bibtex
@misc{yang2026healthclbenchevaluatingcontextlearninghealth,
  title={HealthCLBench:Evaluating Context Learning in Health Conversations through Controlled Case Clusters},
  author={Ting Yang and Zijing Hong and Chengjie Wang and Peng Wei and Chunxiao Guo and Yun Yue and Xurui Li and Jinqun Guan and Zixiao Chen and Ant Group},
  year={2026},
  eprint={2611.22362},
  archivePrefix={arXiv},
  primaryClass={cs.AI},
  url={https://arxiv.org/abs/2611.22362},
}
```
