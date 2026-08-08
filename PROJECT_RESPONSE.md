# 知微 Agent 电商客服系统交付说明

## 项目概述

本项目面向电商客服中的订单查询、物流查询、售后处理和商品知识问答场景，搭建了一个前后端一体的智能客服 MVP。系统采用意图分流、受控 ReAct、Function Calling、RAG 检索和 Harness 编排，降低客服重复查询成本，提高商品知识问答和业务处理的稳定性。

## 核心能力

### 文档解析入库

- 支持 Osmo Pocket 系列多份 PDF 批量上传。
- 使用 MinerU 将 PDF 解析为 Markdown，并保留型号、章节、小节、页码等元数据。
- 以完整小节作为父块存入 PostgreSQL。
- 正文按 500 字符、重叠 80 字符切分为子块，使用 BAAI/bge-small-zh-v1.5 向量化后写入 pgvector。
- 检索命中子块后，可通过父子关联补充完整上下文，支持型号过滤与页码回溯。

### 意图与工具调用

- 采用规则优先、LLM 兜底的意图识别与槽位填充。
- 接入订单、物流、售后 3 类业务工具。
- 通过 Function Calling 调度业务工具，调用前校验订单号等必要参数。
- 售后提交需要二次确认，避免直接执行写操作。
- 读取类工具支持超时重试，异常调用会记录审计日志并返回受控兜底结果。

### 检索记忆增强

- 向量检索与 BM25 关键词检索各召回 Top30。
- 使用 RRF 融合后取 Top15，再由 BGE-Reranker-Base 精排选取 Top3。
- 将高相关上下文交给 LLM 生成客服回答。
- 构建短期记忆、摘要记忆、长期记忆三层机制，支持多轮会话中的上下文恢复和槽位补全。

### Harness 编排评测

- Harness 统一编排客服 Agent 处理链路。
- 覆盖会话状态恢复、工具观察记录、异常重试、兜底和转人工交接信息生成。
- 内置 100 条业务评测集，从路由、工具调用、召回、回答质量、任务完成率和平均耗时六个维度评估。
- 支持逐条查看评测记录、失败原因、耗时和成本。

## 技术栈

- 后端：Python、FastAPI、Pydantic
- 前端：React、TypeScript、Vite
- 模型：DeepSeek-V4-Flash、BAAI/bge-small-zh-v1.5、BGE-Reranker-Base
- 检索与存储：PostgreSQL、pgvector、BM25、RRF
- 文档解析：MinerU
- 工程能力：Function Calling、受控 ReAct、Harness、Docker Compose

## 启动方式

### 后端

```powershell
cd "D:\客服 agent\apps\api"
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 前端

```powershell
cd "D:\客服 agent\apps\web"
npm run dev
```

前端默认访问地址为 `http://localhost:5173`。

### PostgreSQL

```powershell
docker compose -f docker-compose.mvp.yml up -d postgres
```

如果 Docker Desktop 未启动，需要先启动 Docker Desktop 后再执行。

## 主要页面

- 电商客服：支持日常咨询、订单查询、物流查询、售后确认和知识问答。
- 手册入库：支持批量上传 PDF，展示解析状态、页数、父块、子块、解析器和更新时间。
- 自动评测：支持执行 100 条评测，展示质量门禁、各项指标、平均耗时、成本和逐条记录。

## 验收重点

- 批量导入 5 份 Osmo Pocket 手册后，文档状态应显示为 `ready`。
- 文档解析结果应显示 `mineru-cli`，表示使用 MinerU 完成解析。
- 知识问答应能按 Pocket、Pocket 2、Pocket 3、Pocket 4、Pocket 4 Pro 区分型号。
- 工具调用应走受控 Function Calling，不直接拼接未校验参数。
- 评测页面应展示真实逐条记录，不使用伪造评测结果。
