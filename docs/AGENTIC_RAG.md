# 多平台售后知识与经营分析

RetailOps 现在提供独立的 `ask` 入口和服务端 `assistant` 模式。它使用平台、渠道和政策日期筛选知识，将知识检索与原有经营分析工具组合执行。原来的 `run` 命令和默认分析 API 保持兼容。

## 知识库

首批语料位于 `knowledge/retailops-policies.json`：京东、苏宁共 5 份官方公开资料的 10 条人工核对摘要，另有 2 份项目规格/演示操作规范的 4 条条款。来源及适用范围见 `knowledge/SOURCES.md`。

平台编码为 `jd`、`suning`、`retailops`。京东自营与第三方分别检索；苏宁当前资料是平台通用运费争议规范；内部数据分析口径使用独立范围。缺少平台/渠道、文字与参数冲突时返回澄清。明确的多平台比较分别检索、分别引用。

每份文档包含来源、版本、采集时间、有效期和状态。采集时间不代表政策生效时间；未知生效日期保留为空，不能支持历史有效性判断。条款保留内容类型、标题、ID、内容哈希和来源 URL。官方页面使用精简摘要，引用可跳到原页面核对。

## 执行流程

1. 区分知识查询、指标计算和混合任务，保留原始计算请求及其禁止操作约束。
2. 先过滤平台、渠道和有效期，再执行中文 BM25、向量检索或两路 RRF 融合。
3. 模型检查现有证据是否覆盖问题；缺材料时澄清，缺条款时改写查询或读取已命中条款附近的上下文。
4. 通过查询去重、无新增证据退出、最多 2 轮检索（可配置上限 4 轮）限制补检索。默认最多 3 次模型逻辑调用；HTTP 重试沿用模型客户端策略，另行统计实际请求次数。
5. 需要计算时调用已有 `RetailOpsAgent`，使用原有业务校验与插件运行时。知识缺口不会直接触发指标插件生成。
6. 生成带引用的回答，验证引用 ID 存在、平台及渠道一致；同时保留原始条款和计算证据。引用语义支持通过人工标注与模型实验评估。

向量模型使用 FastEmbed 的 ONNX 运行时，默认候选为 `BAAI/bge-small-zh-v1.5`，CPU 推理。当前小型语料使用内存索引，向量按需缓存；未配置编码器时，`hybrid` 会明确报告 BM25 回退。文档文件是持久源，修改并验证后重启服务重建索引，语料哈希随内容和元数据改变。

## 命令行

在项目根目录使用 `retail` 环境安装可选依赖：

```bash
python -m pip install -e '.[rag,service,test]'
export RETAILOPS_EMBEDDING_CACHE=/home/ljh/.cache/retailops-embeddings

# 无需模型密钥的检索浏览
python -m retailops_agent.cli ask \
  --task '京东第三方包邮商品七天无理由退货，寄回运费由谁承担？' \
  --platform jd --scope marketplace \
  --embedding-model BAAI/bge-small-zh-v1.5

# DeepSeek 证据检查与回答；密钥读取现有环境配置
python -m retailops_agent.cli ask \
  --task '比较京东第三方质量问题退货与苏宁质量问题退货的运费承担规则' \
  --platform jd --platform suning --planner model \
  --embedding-model BAAI/bge-small-zh-v1.5 --output runs/policy-comparison.json

# 原有数据计算与内部指标解释组合
python -m retailops_agent.cli ask \
  --task '计算退款率，并解释退款率分母的口径' \
  --platform retailops --scope internal \
  --orders data/orders.csv --refunds data/refunds.csv
```

结果状态：`completed` 表示模型回答或纯计算完成；`retrieved` 表示检索浏览或引用校验失败后的摘录回退；`needs_clarification` 表示需要补充信息；`insufficient_evidence` 表示现有证据不足；`failed` 表示执行失败。模型账户余额不足会返回可定位的错误码，日志不输出密钥或原始服务商错误正文。

## 服务

```bash
python -m retailops_agent.cli serve \
  --knowledge knowledge/retailops-policies.json \
  --embedding-model BAAI/bge-small-zh-v1.5 \
  --data-root data --state state/tasks.sqlite3
```

请求示例：

```json
{"mode":"assistant","task":"苏宁商品有质量问题，退换货运费由谁承担？","platforms":["suning"],"planner":"model"}
```

纯知识任务不需要 CSV；计算任务仍需订单和退款文件。服务复用持久任务、鉴权和 SSE，澄清任务状态为 `needs_input`，知识不足为 `insufficient_evidence`；两者均会正常结束事件流。补充信息后提交带完整范围的新请求。

## 验证与对照

开发评测集位于 `benchmarks/knowledge/questions.json`，30 题覆盖检索、范围澄清、比较和无证据。答案标签不进入知识索引；其中精确金额与到账时效题专门检查“召回相关文段，但没有答案”的情况。

```bash
PYTHONPATH=src python experiments/run_knowledge_checks.py \
  --output artifacts/agentic-rag-20261120/tests.json
PYTHONPATH=src python experiments/evaluate_knowledge.py \
  --output artifacts/agentic-rag-20261120/retrieval-comparison.json

# 导出仅含官方公开摘要的模型测试集
python experiments/prepare_public_policy_eval.py
PYTHONPATH=src python experiments/evaluate_knowledge.py \
  --corpus runs/public-policy-eval/corpus.json \
  --questions runs/public-policy-eval/questions.json \
  --modes hybrid --with-model --public-policy-only \
  --output artifacts/agentic-rag-20261120/model-public.json
```

检索报告比较 BM25、向量、混合三种配置的条款 Recall@3、必要条款覆盖和平台泄漏。`retrieved` 状态不作为模型答案正确率。真实模型实验使用独立的公开政策子集，通过 `--public-policy-only` 校验语料范围；引用正确性还应检查具体结论是否被条款支持。当前语料规模用于验证链路，扩充资料及冻结独立测试集后再评估优化收益。

## 设计参考

本次在现有 Python 执行框架内实现机制，未引入上游完整平台代码或其服务依赖：

- [Ragent 知识检索工具](https://github.com/nageoffer/ragent/blob/main/agent/src/main/java/com/nageoffer/ai/ragent/agent/tool/KnowledgeSearchTool.java)：只读工具边界与查询处理。
- [LangGraph Agentic RAG](https://github.com/langchain-ai/langgraph/blob/main/examples/rag/langgraph_agentic_rag.ipynb)：检索反馈与条件转移；本项目补充业务范围和退出条件。
- [WeKnora 文档阅读工具](https://github.com/Tencent/WeKnora/blob/main/internal/agent/tools/read_document.go)：命中后按需扩读。
- [RAGFlow 证据登记](https://github.com/infiniflow/ragflow/blob/main/internal/agentic_rag/evidence_registry.go)：稳定证据标识、去重与引用映射。

下一阶段可以评测重排序、可持久向量索引和文档增量刷新；首版未启用联网自动检索、知识图谱或真实退款交易操作。
