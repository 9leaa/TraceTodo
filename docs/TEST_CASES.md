# Trace-Memory 测试用例设计与执行手册（对标三大 GitHub 仓库）

本文档对照本地及上游三大开源仓库（**[suixinji](https://github.com/9leaa/suixinji)**、**[basic-memory](https://github.com/basicmachines-co/basic-memory)**、**[ReMe](https://github.com/agentscope-ai/ReMe)**）的核心测试套件，为 `trace-memory` 设计了 **分层自动化回归测试用例（共 33 项）** 与 **Web 控制台真实大模型手工验收脚本**。

---

## 一、 测试分层与自动化执行命令

```bash
cd /path/to/trace-memory
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

| 测试套件文件 | 覆盖模块 | 用例数 | 状态 |
| :--- | :--- | :---: | :---: |
| [`tests/test_invariants_c01_c12.py`](../tests/test_invariants_c01_c12.py) | 核心业务不变量（时间因果、版本失效、条件偏好、部分遗忘、CAS、跨租户隔离、模型降级、只读安全问答） | 11 | ✅ 通过 |
| [`tests/test_github_reference_cases.py`](../tests/test_github_reference_cases.py) | 对标 `suixinji` / `basic-memory` / `ReMe` 三大仓库的经典边界回归测试 | 12 | ✅ 通过 |
| [`tests/test_task_quadrants.py`](../tests/test_task_quadrants.py) | 待办四象限归类、多维排序、临期动态升象限、偏好校准、同音错字状态对账 | 4 | ✅ 通过 |
| [`tests/test_retrieval_vector_and_graph.py`](../tests/test_retrieval_vector_and_graph.py) | 跨字面稠密向量召回、向量生命周期清理、双链前向悬空引用连线、环路与预算截断 | 3 | ✅ 通过 |
| [`tests/test_markdown_dream_mcp_api.py`](../tests/test_markdown_dream_mcp_api.py) | Markdown 导出自愈与冲突草稿保护、Dream 调用上限、MCP JSON-RPC 协议、FastAPI 路由 | 3 | ✅ 通过 |

---

## 二、 参考三大 GitHub 仓库设计的专项测试用例矩阵

### 2.1 对标 `suixinji`（任务状态对账、主题门控拒答、多源证据演进）

| ID | 优先级 | 模块 | 参考上游仓库测试文件 | 前置条件 | 操作步骤 / 输入数据 | 预期结果 | 本次结果 |
| :--- | :---: | :--- | :--- | :--- | :--- | :--- | :---: |
| **TC-SXJ-01** | P0 | 任务生命周期对账 | `suixinji/tests/test_task_state_reconciliation.py` (`test_task_lifecycle_merges_action_wording`) | 空空间 | 依次录入：<br>1. `记得制作系统首页消息路径图`<br>2. `需要继续制作系统首页消息路径图`<br>3. `系统首页消息路径图做完了` | 1. 全程仅维护 **1 张**任务卡（不分裂为 3 张）；<br>2. 第 2 步“进行中”严格保持二态 `todo`（v2）；<br>3. 第 3 步流转为 `done/completed`（v3），且 `evidences` 完整绑定 3 条原话 | **通过** |
| **TC-SXJ-02** | P0 | 问答主题兼容门控 | `suixinji/tests/test_layer3_v2_no_answer_gate.py` (`test_topic_gate_rejects_same_type_different_preference_attribute`) | 已录入偏好：`我平时非常喜欢喝冰美式咖啡` | 调用 `/ask` 提问：`我最喜欢的科幻电影是什么？` | 不会因为共现泛化词“喜欢”而错误召回咖啡偏好作答；返回 `has_sufficient_evidence=False` 且不引用咖啡记忆 | **通过** |
| **TC-SXJ-03** | P0 | 多证据共享存活 | `suixinji/tests/test_fact_memory_source_revision.py` | Note A（`我现在住在上海`）与 Note B（`目前常住在上海`）共同支撑同一张居住地事实卡 | 将 Note A 原文修改为无事实信息的 `你好` | 居住地事实卡**不失效**（仍保持 `active: 上海`），仅剥离 Note A 的旧证据引用并保留 Note B 证据；对比单证据场景（`C02`）则自动置为 `superseded` | **通过** |
| **TC-SXJ-04** | P0 | 时间线因果栅栏 | `suixinji` C01 不变量 | 先录入 2025 住北京、2026 搬到上海 | 补录更早历史：`2023年的时候我住在深圳` | 当前生效值仍为 `上海`（绝不被晚录入的旧事件覆盖），`北京` 与 `深圳` 归档进 `previous_values` | **通过** |
| **TC-SXJ-05** | P0 | 局部跨度精准遗忘 | `suixinji` C07 遗忘遮蔽 | 同一条 Note 含两件事：`我住在上海。平时喜欢喝拿铁咖啡。` | 对该 Note 执行局部遗忘 `span="上海"` | `上海` 事实卡及其向量、Markdown 导出被彻底抹除，原文展示脱敏为 `[REDACTED]`；但同条 Note 抽出的**拿铁咖啡偏好卡完整保留可查** | **通过** |

---

### 2.2 对标 `basic-memory`（锁定保护、路径安全、唯一固定链接、双向文件同步）

| ID | 优先级 | 模块 | 参考上游仓库测试文件 | 前置条件 | 操作步骤 / 输入数据 | 预期结果 | 本次结果 |
| :--- | :---: | :--- | :--- | :--- | :--- | :--- | :---: |
| **TC-BM-01** | P0 | Markdown 边界与路径穿越防护 | `basic-memory/test-int/test_locked_notes.py` & 安全规范 | 已导出 `knowledge/<space>/` 目录 | 1. 构造含 `../../etc/passwd` 的相对路径；<br>2. 将导出的 `.md` 顶部改为未闭合的损坏 YAML `---` 后执行 `sync` | 路径穿越与损坏的 Frontmatter 均在修改数据库前抛出 `ValidationError` 拦截，正式记忆零污染 | **通过** |
| **TC-BM-02** | P0 | 锁定卡片防后台覆写 | `basic-memory/test-int/test_locked_notes.py` (`test_locked_note_rejects_append`) | 用户已手动调整或创建 `user_locked=True` 的记忆/四象限任务（v1） | 后台抽取器或 Dream 归纳器对该卡片发起自动 `update` | 返回 `skipped_user_locked`，卡片内容、象限与版本号 `v1` 严格保持不变 | **通过** |
| **TC-BM-03** | P1 | 同名记忆 Permalink 自动消歧 | `basic-memory/test-int/test_mandatory_permalinks_integration.py` | 存在一条标题为 `架构设计纪要` 的记忆 | 再创建另一条不同 `canonical_key` 但同名 `架构设计纪要` 的记忆 | 自动生成唯一固定链接 `facts/架构设计纪要` 与 `facts/架构设计纪要-2`，均可通过 `memory://` 独立寻址 | **通过** |
| **TC-BM-04** | P1 | 视图文件误删自愈 | `basic-memory/docs/DOMAIN_MODEL.md` & 融合计划 §7.2 | 已导出某条偏好的 `.md` 视图文件 | 在磁盘上直接删除该 `.md` 文件后运行 `sync` | **不触发数据库遗忘**，正式记忆仍为 `active`，且 `sync` 末尾自动重导出自愈恢复该 `.md` 文件 | **通过** |
| **TC-BM-05** | P0 | 并发编辑冲突保留草稿 | `basic-memory` CAS 版本控制 | 导出 `v2` 文件后，Web 端先将记忆更新至 `v3` | 用户修改本地旧版 `v2` 文件并运行 `sync` | 服务端 `v3` 不被覆盖；用户的本地修改自动另存为 `*.conflict-v2.md` 草稿，正式文件更新为 `v3` | **通过** |

---

### 2.3 对标 `ReMe`（Dream 快照幂等、防幻觉自我放大、预算熔断、图谱展开）

| ID | 优先级 | 模块 | 参考上游仓库测试文件 | 前置条件 | 操作步骤 / 输入数据 | 预期结果 | 本次结果 |
| :--- | :---: | :--- | :--- | :--- | :--- | :--- | :---: |
| **TC-REME-01** | P0 | Dream 快照摘要幂等防重 | `ReMe/tests/unit/test_auto_dream.py` (`_snapshot_digest`) | 空间内有 ≥2 条同主题 `stated` 记忆 | 连续执行两次 `run_dream()`，期间无新增或修改记忆 | 第 1 次生成 1 条 `inferred` 洞察；第 2 次命中证据签名幂等缓存，`used_llm_calls == 0`、`skipped_unchanged == 1` | **通过** |
| **TC-REME-02** | P0 | 防 `inferred` 卡片递归二次归纳 | `ReMe` 自演化防幻觉原则 | 已运行过一次 Dream 生成了 `inferred` 洞察卡 | 再次触发 `run_dream()` | 候选池严格过滤仅限 `assertion_kind == 'stated'`，绝不把上一次模型猜出来的 `inferred` 卡片再拿去二次归纳放大幻觉 | **通过** |
| **TC-REME-03** | P1 | Token 与调用次数双熔断 | `ReMe` 后台任务预算治理 | 空间内存在 ≥2 个待归纳主题簇（如经历簇 + 偏好簇） | 设置 `max_tokens=100`（或 `max_llm_calls=1`）执行 `run_dream()` | 首个簇完成后检测到预算触顶，立即置 `budget_halted=True` 停止后续簇请求，无半成品脏写入 | **通过** |
| **TC-REME-04** | P1 | 双链前向引用连线与环路截断 | `ReMe` & `basic-memory` 图谱检索 | 记忆 A 含悬空双链 `[[Architecture Spec]]` | 1. 后创建《Architecture Spec》并反向链回 A（构成环路）；<br>2. 设 `max_nodes=1` 展开上下文；<br>3. 遗忘 B 后再展开 | 1. 创建 B 时 A→B 前向引用自动解析连线；<br>2. 环路遍历不死循环，触达预算返回 `budget_truncated=True`；<br>3. 遗忘 B 后图谱展开严格拦截 B | **通过** |

---

### 2.4 融合创新专项（四象限动态提权、同音错字完成对账、混合 RRF 检索）

| ID | 优先级 | 模块 | 测试场景说明 | 前置条件 | 操作步骤 / 输入数据 | 预期结果 | 本次结果 |
| :--- | :---: | :--- | :--- | :--- | :--- | :--- | :---: |
| **TC-FUS-01** | P0 | 四象限逾期预警与自然语言消警 | 截止时间动态升级 + 完成闭环 | 存在一条基础紧急度较低（`urgency=2`）但 `due_at` 已过期 5 小时的待办 | 1. 打开四象限矩阵查看诊断；<br>2. 输入自然语言：`我已经把提交季度差旅报销单搞定了` | 1. 矩阵实时提权至 `Q1` 并输出 `⚠️ 已超过截止时间` 预警；<br>2. 自然语言完成后，待办数与逾期数双双清零 | **通过** |
| **TC-FUS-02** | P0 | 同音字/Key 漂移完成对账 | 真实百炼 `qwen3.8-flash` 实战回归用例 | 存在 Q1 待办：`明天下午五点前必须把线上订单支付超时告警修好` | 录入带 1 字同音差异的完成句：`我已修复线上订单支付超时告急` | 上下文感知抽取 + CJK 二元 Dice 相似度对账识别为同一任务，原卡片升至 `v2 (done)` 并从待办象限移除，不产生重复卡 | **通过** |
| **TC-FUS-03** | P0 | 错误码精确匹配 + 跨字面语义双路召回 | BM25 + 向量相似度 RRF 互补验证 | 录入：`今天必须排查支付网关 ERR_504_GATEWAY 超时故障` | 1. 搜精确错误码 `ERR_504_GATEWAY`；<br>2. 搜零字面重合词 `线上结算异常隐患` | 1. 错误码查询由 `BM25 #1` 精准命中；<br>2. 语义泛化查询由 `Vector #1` 余弦相似度命中，RRF 融合稳定排第一 | **通过** |

---

## 三、 Web 控制台手工交互测试脚本（可直接复制体验）

启动 `python3 run_web.py` 打开 `http://127.0.0.1:18090` 后，你可以按顺序在顶部输入框粘贴以下 6 句话，直观验收 `qwen3.8-flash` + 四象限 + 混合检索全流程：

1. **测试偏好条件与例外共存（C03）**：
   > `我平时很喜欢喝冰美式咖啡，但是晚上八点以后绝对不喝咖啡；另外我非常关注分布式存储架构设计。`
2. **测试四象限自动分级（Q1 / Q2 / Q3 / Q4）**：
   - 录入 Q1： `明天上午十点前必须修复线上支付网关 ERR_504 超时故障`
   - 录入 Q2： `下个月系统梳理分布式存储架构演进方案`
   - 录入 Q3： `今天下午下班前帮同事转发技术周会腾讯会议链接`
   - 录入 Q4： `以后有空的时候顺便整理一下去年的旧书单`
3. **测试自然语言跨表述完成待办（状态对账）**：
   > `我已经帮同事把技术周会的会议链接转发好了`
   - **预期**：观察 Q3（紧急不重要）象限计数减 1，勾选「显示已完成/已取消」可见该卡片已划上删除线并升至 `v2`。
4. **测试手动锁定防覆盖（C06）**：
   - 在 Q4 卡片《整理去年的旧书单》上点击下拉框 **「移至 Q2 重要不紧急」**，观察出现 `🔒 手动锁定保护` 标签。
5. **测试向量库跨字面语义召回**：
   - 切到 **「🗂️ 四类记忆与混合向量检索」**，搜索 `线上交易接口稳定性风险`，观察凭借 `text-embedding-v3` 向量余弦相似度召回《修复线上支付网关 ERR_504 超时故障》。
6. **测试时间线因果与拒答门控（C01 & TC-SXJ-02）**：
   - 先录入 `我现在搬到上海定居了`，再录入 `2023年的时候我住在深圳`。
   - 切到 **「💬 双源证据问答 & Trace」**：
     - 问 `我现在住在哪个城市？` → 回答 **上海**；
     - 问 `我以前的居住城市历史变迁是什么？` → 展示上海与深圳的历史演进；
     - 问 `我最喜欢的科幻电影是什么？` → 诚实拒答，绝不拿“冰美式咖啡”凑数！

---

## 四、 实测验收记录（确定性回归 + 阿里百炼真机评测）

### 1. A 类：确定性回归测试集（33 项）
- **执行命令**：`PYTHONPATH=src python3 -m unittest discover -s tests -v`
- **执行结果**：**33 / 33 全部通过**（耗时 `0.26s`，零跳过、零失败）。

### 2. B 类：阿里百炼真机全链路评测（[`scripts/eval_live_qwen.py`](../scripts/eval_live_qwen.py)）
- **测试模型**：`qwen3.8-flash`（对话抽取/归纳/问答）+ `text-embedding-v3`（1024 维稠密语义向量）
- **运行开销**：共 `11` 次 LLM 调用，总计消耗 `8,538` tokens，总耗时 `47.71s`，**6 / 6 全场景通过**：
  - `LIVE-01` **偏好条件与例外抽取**：精准提取 `target="冰美式咖啡"`、`polarity="positive"`、`exceptions=["晚上八点以后绝对不喝"]`。
  - `LIVE-02` **四象限自动分级与排序**：4 条不同性质待办精准落入 `Q1 (131.0分)`、`Q2 (88.0分)`、`Q3 (85.0分)`、`Q4 (29.0分)` 各 1 项，排序完全符合艾森豪威尔矩阵预期。
  - `LIVE-03` **自然语言跨表述完成对账**：输入*“我已经帮同事把技术周会的腾讯会议链接转发好了”*后，`Q3` 待办从 `1 -> 0`、`total_done` 升至 `1`，无重复建卡。
  - `LIVE-04` **BM25 + 1024 维真向量混合召回**：搜 `"ERR_504"` 与跨字面搜 `"线上交易接口稳定性风险"`（向量余弦相似度 `0.5406`，排名第 1）双双精准命中《修复线上支付网关 ERR_504 超时故障》。
  - `LIVE-05` **时间因果栅栏问答**：先录 2026 定居上海、后补录 2023 住深圳，提问当前定居城市准确回答 **“上海”**。
  - `LIVE-06` **无依据拒答 + Dream 归纳 + Markdown 导出**：提问未记录的科幻电影导演返回 `has_sufficient_evidence=false` 诚实拒答；Dream 归纳消耗 `263` tokens 产出 1 条 `inferred` 洞察，稳定导出 `8` 篇 Markdown 视图文件。
