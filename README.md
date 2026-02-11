# 知微多模态RAG-Agent电商客服中台

## 一. 项目描述

聚焦电商场景咨询意图杂、PDF 商品和规则手册信息提取难以及核心业务办理依赖人工等痛点，搭建全栈电商智能客服中台。系统采用 **"路由拦截 + 数据预注 + Agent 兜底"** 架构，实现简单意图秒回、意图识别触发下的多路业务数据预取及 Agent 复杂场景自适应推理闭环。项目构建起从多模态数据解析、多路混合检索到自动量化评测的完整链路，显著提升企业在复杂咨询环境下的响应速度与回复精度。

---

## 二. 技术栈

| 层级 | 技术选型 |
|------|---------|
| **大模型** | Gemini 3 Flash Preview（意图推理 + 生成 + LLM-as-a-Judge 评测） |
| **Embedding** | 通义 text-embedding-v2（DashScope API，1536 维） |
| **Reranker** | BAAI/bge-reranker-base（本地 CrossEncoder 推理，无需 API） |
| **向量库** | ChromaDB 0.4.24（HNSW + cosine 距离） |
| **全文检索** | Elasticsearch 8.11（BM25 + jieba 分词扩展） |
| **关系数据库** | MySQL 8.0（订单、商品、物流、用户数据） |
| **缓存 & 会话** | Redis 7（对话历史窗口 + RAG 结果缓存） |
| **后端框架** | FastAPI + Uvicorn（SSE 流式输出 + 同步/异步双接口） |
| **前端** | Tailwind CSS 单页应用（聊天界面 + 管理后台） |
| **Agent 框架** | LangChain + ChatOpenAI（ReAct 循环，最多 5 轮工具调用） |
| **文档解析** | PyMuPDF 文本提取 + 视觉大模型 OCR 兜底 |
| **容器化** | Docker Compose 编排 5 个服务（App / MySQL / ChromaDB / ES / Redis） |

---

## 三. 功能特性

**1. 意图识别与路由拦截**
- 规则引擎通过正则 + 关键词对用户输入打标签，覆盖 **闲聊、订单查询、取消订单、退款申请、物流追踪、库存查询、商品对比、RAG 知识问答** 等 11 类意图
- 闲聊类命中后直接返回预置回复，零 LLM 调用
- RAG 类命中后走检索生成链路，检索结果作为上下文注入 Agent 返回
- 业务办理类命中后，RAG 同步检索相关知识，检索结果与业务数据一并作为上下文注入 Agent
- 未命中任何标签的请求默认进入 RAG 检索，检索结果注入 Agent，由 Agent 进入 ReAct 循环自主分析和工具调用

**2. 多模态文档处理**
- PDF 解析采用 **文本层优先 + 视觉模型兜底** 双策略，文本量低于 50 字符自动切换为图片 OCR
- 滑动窗口切片：chunk_size=500，overlap=100，保证语义连续性
- 支持 PDF / TXT / 图片上传，统一进入向量库 + ES 双写

**3. 多路混合检索**
- **召回阶段**：ChromaDB 向量检索与 ES 关键词检索通过线程池并行执行，各取 Top 15，超时 2 秒兜底
- **粗排阶段**：RRF 倒数排名融合，ES 权重 1.5 × 向量权重 1.0，输出 Top 10
- **精排阶段**：本地 bge-reranker-base CrossEncoder 重排序，输出 Top 3 送入生成

**4. 多轮对话**
- Redis 存储对话历史，窗口大小 10 轮，TTL 3600 秒
- Agent 每次请求从 Redis 拉取历史消息并注入 Prompt，支持指代消解和上下文延续

**5. 流式响应**
- SSE 逐 token 推送，前端实时渲染 + 打字机动画
- 图片消息走同步接口，文本消息走流式接口

**6. 自动化评测体系**
- 40 条覆盖全意图类型的评测样本
- 三维度评分：意图准确率（精确匹配）、检索召回率（关键词命中）、生成质量（LLM-as-a-Judge 0-100 打分）
- Web 端一键触发评测，实时展示每条 PASS/FAIL 和各意图维度准确率

---

## 四. 流程图

```
用户输入
   │
   ▼
┌───────────────────┐
│  意图识别引擎(打标签) │ ← 正则规则 + 关键词匹配
└────────┬──────────┘
         │
    ┌────┼──────────────┐
    │    │              │
  有标签  有标签          无标签
    │    │              │
    ▼    ▼              │
  闲聊  业务办理/RAG     │
    │    │              │
    ▼    ▼              ▼
  直接  RAG 检索 ◄──────┘
  返回  (多路召回→RRF→Reranker→LLM生成)
    │    │
    │    ▼
    │  检索结果作为上下文注入
    │    │
    │    ▼
    │  Agent (ReAct 循环)
    │    │
    │    ┌──────────────┤
    │    │  ① 分析意图   │
    │    │  ② 选择工具   │
    │    │  ③ 执行调用   │
    │    │  ④ 整合结果   │
    │    │  (最多5轮)    │
    │    └──────────────┤
    │                   │
    ▼                   ▼
┌─────────────────────────┐
│       返回结果            │
│  (SSE流式 / 同步响应)     │
└─────────────────────────┘

  ┌─────────────────────────────────┐
  │  Redis 对话历史                   │
  │  · 存储每轮 user/assistant 消息   │
  │  · 每次请求注入 Agent Prompt      │
  │  · 窗口10轮，TTL 3600s           │
  └─────────────────────────────────┘
```

**流程说明**：
1. 意图识别引擎对用户输入打标签，命中 **闲聊** 直接返回预置回复
2. 命中 **业务办理** 或 **RAG** 类标签时，均先执行 RAG 检索，检索结果作为上下文注入 Agent
3. **未命中任何标签** 的请求默认进入 RAG 检索，检索结果同样注入 Agent
4. Agent 进入 ReAct 循环，自主分析、选择工具、执行调用、整合结果，最多 5 轮迭代
5. Redis 对话历史在每次 Agent 调用前被拉取并注入到 Prompt 中，支持多轮上下文理解

---

## 五. 项目结构模块说明

```
rag_project/
├── main.py              # FastAPI 入口，定义 /chat/stream、/chat/sync、/upload、/eval 等接口
├── config.py            # Pydantic Settings 统一配置，读取 .env 环境变量
├── dialog.py            # 对话管理器，协调图片处理、RAG 检索、Agent 调用
├── agent.py             # ReAct Agent，SystemPrompt + 历史注入 + 工具绑定 + 循环执行
├── intent.py            # 规则意图识别引擎，11 类意图 + FAQ/闲聊/RAG 关键词库
├── skills.py            # 7 个结构化工具定义 + Mock 数据库 + 快速路径路由器
├── rag.py               # RAG 管线：FAQ匹配 → 并行召回 → RRF粗排 → Reranker精排 → LLM生成
├── llms.py              # LLM / Embedding / Reranker 三合一封装，含缓存和重试
├── vectorstore.py       # ChromaDB 向量库操作：去重写入、cosine 检索、集合管理
├── es_client.py         # Elasticsearch 操作：索引创建、jieba 分词、混合查询
├── memory.py            # Redis 对话历史管理，滑动窗口 + 自动过期
├── multimodal.py        # 多模态处理器，调用视觉大模型描述/OCR 图片
├── ocr_processor.py     # PDF/TXT/图片文档解析，文本层提取 + 视觉兜底 + 滑动窗口切片
├── evaluation.py        # 评测引擎：意图精确匹配 + 关键词召回 + LLM-as-a-Judge 打分
├── import_kb.py         # 知识库 JSON 导入脚本，双写 ChromaDB + ES
├── import_pdf.py        # PDF 批量导入脚本，OCR 解析后双写
├── clean_kb.py          # 数据清理脚本，重置 ChromaDB / ES / Redis
├── run_eval.py          # 命令行评测脚本，输出完整报告
├── deploy.sh            # 一键部署脚本
├── Dockerfile           # Python 3.10-slim 镜像，安装系统依赖和 pip 包
├── docker-compose.yml   # 5 服务编排：App / MySQL / ChromaDB / ES / Redis
├── requirements.txt     # Python 依赖清单
├── init.sql             # MySQL 建表 DDL：products / orders / logistics / refunds 等 8 张表
├── init_data.sql        # MySQL 初始数据：5 款商品、3 笔订单、物流轨迹
├── .env                 # 环境变量配置
├── frontend/
│   ├── index.html       # 聊天界面：SSE 流式渲染、图片上传预览、会话管理
│   └── admin.html       # 管理后台：评测触发、指标看板、知识库上传
└── test_data/
    ├── eval_cases.json          # 40 条评测用例，覆盖 8 类意图
    ├── knowledge_base.json      # 30 条结构化知识条目（运费/售后/DJI参数等）
    └── *.pdf                    # 4 份帮助文档 PDF
```

> > **说明**：test_data/ 中 knowledge_base.json 和 eval_cases.json 已内置全部运费、售后、退款等业务数据，通过 `import_kb.py` 直接导入即可使用。DJI Mini 4 Pro 用户手册因文件超过 25MB 未包含在仓库中（[下载地址](https://dl.djicdn.com/downloads/DJI_Mini_4_Pro/DJI_Mini_4_Pro_User_Manual_CHS.pdf)），需自行放入 test_data/ 目录后通过 `import_pdf.py` 进行 OCR 解析导入知识库。
---

## 六. 评测结果

| 指标 | 数值 | 说明 |
|------|------|------|
| **意图准确率** | **97.5%** | 40 条样本中 39 条意图识别正确 |
| **检索召回率** | **0.87** | 基于关键词命中率衡量文档召回质量 |
| **生成质量** | **89.5 / 100** | LLM-as-a-Judge 对回答相关性和准确性打分 |
| **平均延迟** | **3.01s** | 含意图识别 + 检索 + 生成全链路耗时 |

各意图维度准确率均达到 **80%** 以上，RAG 类问题覆盖运费规则、退款流程、DJI 产品参数等 **20+** 细分场景。

---

## 七. 快速开始

**1. 克隆项目并配置环境变量**

```bash
cd rag_project
cp .env.example .env   # 按需修改 API Key 和数据库连接
```

**2. 一键部署**

```bash
chmod +x deploy.sh
./deploy.sh
```

部署完成后 5 个容器自动启动：App（8000）、MySQL（3307）、ChromaDB（8001）、ES（9200）、Redis（6380）

**3. 导入知识库**

```bash
# 导入结构化知识条目
docker exec -it rag-app python import_kb.py

# 导入 PDF 文档
docker exec -it rag-app python import_pdf.py
```

**4. 运行评测**

```bash
docker exec -it rag-app python run_eval.py
```

**5. 访问服务**

- 聊天界面：http://localhost:8000
- 管理后台：http://localhost:8000/admin
- 健康检查：http://localhost:8000/health
- API 文档：http://localhost:8000/docs
