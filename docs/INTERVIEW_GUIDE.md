# 🎯 Trace-Memory 项目简历包装与面试高频追问指南

这份文档专门帮你把 `trace-memory` 转化为简历上的核心亮点项目，并准备好面试官从浅入深的所有追问弹药。

---

## 一、 简历项目经历写法模板（可直接参考调整）

> **项目名称**：Trace-Memory —— 可追溯、自演化的多模态 Agent 长期记忆与智能待办引擎  
> **技术栈**：Python、FastAPI、MCP (Model Context Protocol)、SQLite (WAL + FTS5)、Qdrant (HNSW Vector DB)、RRF Hybrid Search  
> **项目背景与职责**：针对传统对话助手“跨会话遗忘、向量黑盒不可控、模型重写导致原话证据丢失”三大痛点，融合主流开源记忆方案（Basic Memory、阿里 ReMe、随心记）设计并独立实现了一套支持 CLI / Web / MCP / Obsidian 多端协同的长期记忆与艾森豪威尔四象限任务管理系统。
> - **可追溯记忆底座与统一变更守门**：设计 `RawNote`（原话证据）与四类结构化记忆（任务/偏好/事实/经历）分离架构；抽象统一的 `ChangeProposal` 变更契约，基于数据库级 **CAS 乐观锁（`expected_version`）**、**时间因果顺序栅栏**与**用户编辑锁（`user_locked`）**，彻底解决晚到旧历史覆盖当前事实、并发覆写及 AI 自动整理覆盖人工排期的问题。
> - **混合向量检索与向量生命周期闭环**：实现 **SQLite FTS5 (CJK 分词 BM25) + Qdrant 稠密向量检索的 RRF 倒数秩融合**，并在双链关系表上实现前向悬空引用自动连线与有预算（跳数/节点/字符上限）的多跳上下文展开；建立 `VectorLifecycle` 机制，在原文修订或片段级遗忘时级联清理过期向量与派生记忆，实现零脏数据召回。
> - **记忆驱动的四象限待办与睡眠归纳（Dream）**：构建联动长期偏好记忆校准重要度、结合截止时间动态升级紧急度的艾森豪威尔四象限待办分析引擎；实现带 Token/调用次数熔断器（`BudgetGuard`）的后台归纳器，严格区分原话陈述（`stated`）与模型推断（`inferred`）。
> - **MCP 读写分离与 Markdown 受控双向同步**：开发带安全行为注解的 MCP Server 接入外部编程 Agent，并基于 `.manifest.json` 校验和实现本地 Markdown 知识视图的单向可重建导出与冲突保留草稿式编辑回流；构建覆盖 12 类边界不变量（C01–C12）的自动化回归测试集。

---

## 二、 面试官必问的 6 道深度追问与满分回答

### Q1：普通 RAG 和你们做的 Agent 长期记忆系统，本质区别是什么？
**回答要点**（对应代码：[`vector_store.py`](../src/trace_memory/storage/vector_store.py)、[`change_service.py`](../src/trace_memory/services/change_service.py)）：
1. **静态文档 vs 动态状态演进**：普通 RAG 的文档切片是只增不改的静态知识；而长期记忆是有生命周期的——比如用户去年住杭州、今年搬到上海，或者任务从 `todo` 变成 `done`。如果像普通 RAG 一样只往向量库里塞，检索“我住哪”就会把杭州和上海同时搜出来打架。
2. **我们的解法**：
   - 状态层做**因果合并与版本推进**（旧状态转为历史或 `previous_values`，主卡片保持唯一最新 `active` 态）；
   - 向量层做**`VectorLifecycle` 同步生命周期管理 + Metadata 前置过滤（`status='active'`）**，当记忆失效或被片段级遗忘时，立即物理清除或重算向量；
   - 问答层（[`ask_service.py`](../src/trace_memory/services/ask_service.py)）做**意图分流**——问“现在住哪”只看当前 `active` 态，问“以前住过哪”才展开 `memory_versions` 演化链。

---

### Q2：你融合了 Basic Memory、ReMe 和随心记，具体是怎么取舍的？为什么不直接拿其中一个改？
**回答要点**：
- **`basic-memory` 的长处与局限**：它的 MCP 工具表面（读写分离注解）和 Markdown 双链语法非常优雅，但它把裸 Markdown 文件作为唯一底座，一旦大模型编辑改坏了文件，用户的原始对话证据就彻底丢了，也没法做细粒度的任务状态流转控制。
- **`ReMe` 的长处与局限**：阿里 `ReMe` 的 `Dream` 后台归纳和双链检索扩展思路很好，但如果让后台归纳 Agent 直接改写正式记忆，不仅容易把“模型的猜测”当成“用户说过的原话”，定时跑还可能 Token 失控。
- **我的融合方案**：
  1. 用 **SQLite 关系库 + 原话 `RawNote` 溯源** 做唯一权威底座；
  2. 把 **Markdown 目录（`knowledge/`）定位为可随时删掉重建的“派生视图”**，外部编辑通过 `.manifest.json` 比对哈希后转成带 `expected_version` 的 `ChangeProposal` 回流；
  3. 给 `Dream` 归纳加上 **`BudgetGuard` 预算熔断器**，且归纳产物强制打上 `assertion_kind="inferred"` 标签并保留底层原话证据链。

---

### Q3：待办事项与四象限页面大家都会做，你的四象限设计有什么技术亮点？
**回答要点**（对应代码：[`task_matrix_service.py`](../src/trace_memory/services/task_matrix_service.py)）：
1. **长期记忆联动校准（Memory-Calibrated Importance）**：抽取新待办时，不是孤立打分，而是检索当前空间用户的 `preference` 偏好记忆（例如用户长期关注“向量检索性能优化”），命中偏好主题的任务会自动提权重要度并写明提权依据。
2. **读写分离的动态紧急度升级（Dynamic Deadline Escalation）**：紧急度随时间流逝而变化。我们在读取/构建矩阵时结合 `due_at` 实时计算 `effective_urgency`——一个上周记的“重要不紧急（Q2）”事项，当距离截止时间不足 48 小时时会自动升入“重要且紧急（Q1）”，无需后台跑海量定时写库任务。
3. **人工排期防 AI 覆盖（`user_locked` + CAS）**：用户在 Web 看板手动把某个任务调到指定象限后，系统通过 CAS 校验版本号并置 `user_locked=True`；后续后台自动整理或重算时严格尊重用户手工锁定的象限。

---

### Q4：如果用户在 Obsidian 里改了导出的 Markdown 文件，同时通过 MCP 或 Web 也改了同一条记忆，会发生什么？
**回答要点**（对应代码：[`markdown_sync.py`](../src/trace_memory/services/markdown_sync.py)）：
- 我们在导出每个 `.md` 文件时，会在 `.manifest.json` 中记录当时的基准版本号（比如 `version: 2`）和 `sha256` 摘要。
- 如果服务端先通过 Web 升到了 `v3`，随后用户保存了基于 `v2` 的本地文件修改并执行 `sync`：
  1. `MarkdownSyncService` 构造 `expected_version=2` 的 `ChangeProposal` 提交给 `ChangeService`；
  2. 数据库层原子更新发现当前已是 `v3`，抛出 `VersionConflictError(expected=2, actual=3)`；
  3. 同步器捕获冲突后，**绝不覆盖服务端的 `v3`**，也**绝不丢弃用户辛苦写的修改**，而是将用户的本地文件自动另存为 `<文件名>.conflict-v2.md` 草稿，再将权威的 `v3` 重新导出到原路径，做到零数据丢失。

---

### Q5：通过 MCP 暴露给外部大模型时，怎么防止 Prompt 注入（比如网页里有恶意指令让 AI 偷偷删记忆或改任务状态）？
**回答要点**（对应代码：[`mcp_server/server.py`](../src/trace_memory/mcp_server/server.py)、[`ask_service.py`](../src/trace_memory/services/ask_service.py)）：
1. **查询链路绝对只读**：`tm_ask`、`tm_search`、`tm_read`、`tm_context` 全部走纯只读服务路径（即使提问内容写着“把周报任务标为已完成”，`AskService` 也绝不调用任何状态变更逻辑，这在 `test_c12` 中有专门的回归测试）。
2. **MCP 工具注解分级**：所有写操作（`tm_record`、`tm_propose_change`）显式标注 `destructiveHint=True`，触发宿主客户端（如 Claude Code / Cursor）的人工确认弹窗。
3. **高危动作服务端硬拦截 + 空间绑定**：`MCPToolDispatcher` 的 `space_id` 由服务端可信配置绑定（不允许外部 Agent 传参越权跨租户访问），且在协议层直接拦截 `forget` / `approve` 等仅限人类可信界面执行的操作。

---

### Q6：双链图谱做多跳扩展（GraphRAG）时，如果出现环路（A 链向 B，B 链向 A）或者某条关联记忆已经被用户“遗忘”了怎么办？
**回答要点**（对应代码：[`retrieval_service.py`](../src/trace_memory/services/retrieval_service.py) 中的 `build_graph_context`）：
1. **环路防护与三维预算**：BFS 遍历维护 `visited` 集合防止环形死循环，并同时受 `max_hops`（跳数）、`max_nodes`（最大节点数）、`max_chars`（最大字符数）三重预算约束，超限立即截断并在返回元数据中标记 `budget_truncated=True`。
2. **逐跳二次权限与遗忘校验**：一方面在节点被遗忘时，`clear_inbound_on_inactive` 会清空入向边的 `to_memory_id`；另一方面在图谱遍历的**每一跳**，都会重新核验目标节点的 `space_id` 归属和 `status == 'active'`，确保任何已遗忘或跨空间的隐私绝不可能顺着图谱边“借尸还魂”。
