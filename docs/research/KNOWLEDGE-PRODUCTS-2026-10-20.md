# 智能文档与知识工作台：产品能力调研

调研日期：2026-10-20。本文件保留调研时的候选建议与官方观察；用户随后要求将选定能力纳入 V3，当前范围和顺序以 [V3 规划](../implementation/V3-PLAN.md) 为准。新增能力尚未开始实现。

## 结论

可以继续 V3；不需要把调研发现全部补齐后再开始。
相比增加更多生成按钮，当前更值得形成的是“接入 → 读懂 → 提问 → 沉淀 → 产出 → 更新”的连续过程。
建议近期集中在三组能力：有范围的连续问答、可核对的阅读与搜索、能沉淀复用的报告和分析成果。
来源扩展、学习辅助、资料更新是下一批候选能力，按真实使用频率选择，避免一口气扩成通用办公平台。

## 调研方法与限制

- 只使用实际检索和打开的厂商官方帮助、官方产品文档；没有试用这些产品，也没有做准确率或性能比较。
- “官方已文档化”只代表厂商给出的功能说明，不代表本项目需要照搬，也不代表在所有套餐、地区、账户中均可用。
- NotebookLM 帮助入口本次已重定向到 Gemini Notebook；下面用“NotebookLM／Gemini Notebook”指所查阅的这一产品资料，避免沿用过期功能说明。
- Notion 的旧 `research-mode` 帮助地址已跳转到 Notion Agent；以下按当前 Enterprise Search 和 Agent 文档记录。
- RAGFlow 所用详细文档版本为 `1.0.0-rc2`，不能把该候选版本能力泛化为所有已部署版本都支持。
- 空白或“未在本次来源中确认”不等于产品没有该功能。
- 本项目现状以主线程仓库核查为准；本节产品调研不替代代码审查和质量验证。

## 产品的启发

| 产品 | 官方文档能确认的能力 | 对本项目最有价值的启发 |
| --- | --- | --- |
| NotebookLM／Gemini Notebook | 选择来源问答、引用跳回上下文、来源摘要；网页与 Drive 资料发现；笔记用于后续问答和生成；报告、卡片与测验。[G1–G5] | 把“资料阅读、个人理解、生成成果”接成一个持续使用的工作空间 |
| Notion AI／Enterprise Search | 跨工作区及连接应用检索，指定页面或来源，引用来源；Agent 可把文件与检索结果整理为可编辑页面、数据库和下载文件。[N1–N2] | 查到信息后直接变成长期成果，减少重复复制与重新整理 |
| Microsoft Copilot Notebooks／Pages | 在自选参考资料范围内问答和起草；云端参考资料更新；聊天转为可保存、直接编辑及协作的 Page。[M1–M2] | 项目材料与成果持续关联；成果的寿命长于一次聊天 |
| RAGFlow | 自然语言搜索与文档过滤；解析片段与原文联动核对、纠正；外部资料同步。[R1–R3] | 用户需要直接找到和核对内容，也需要解决知识缺失的实际原因 |
| Dify | 资料维护、元数据过滤、基于资料生成报告；可修改并保存常见问题的固定回答。[D1–D2] | 让人工确认的内容可以复用；不直接把一次生成答案当成权威知识 |
| AnythingLLM | 区分全文附件与检索部分片段；全文仍受上下文容量限制，截断会丢失资料。[A1] | “问到相关片段”和“读完全文”是不同产品能力，导读与报告需说明材料覆盖 |

## 已文档化的具体观察

### 资料接入、发现与更新

- Google 支持文件、粘贴文字、网页等来源，并允许先检索、查看结果说明，再选择导入；这与不经确认地把网络资料混入答案不同。[G2]
- Google 的 Drive 来源文档说明自动同步，也说明网页导入主要提取该页正文，不递归导入嵌套页面；“支持网页”不应被理解为完整站点采集。[G2]
- Notion 将工作区、连接应用与网页搜索放在可调整的检索范围中，支持进一步限定页面或团队空间。[N1]
- Copilot Notebooks 使用用户加入的参考资料；官方说明云端参考资料可随内容变化更新。[M1]
- Copilot 官方 FAQ 也明确，生成物可能只使用部分参考资料，不会使用或引用每份材料；所选资料数量不等于实际覆盖数量。[M3]
- RAGFlow 明确区分“保存连接配置”和“把数据源加入知识库并同步”；新增、修改内容通常在下一刷新周期同步，删除同步需启用对应选项。[R3]
- 启发：本项目可以先支持网页正文、粘贴文字；真正发生反复重传同类资料后，再选择一个实际常用来源做同步。

### 有范围的问答、搜索与原文核对

- Google 支持勾选本次问答来源；引用可展开摘录并跳到对应原文上下文；聊天历史保留且对本人可见。[G1]
- Google 还提供单份资料的自动摘要，可作为阅读入口，而不要求用户先想好问题。[G2]
- Notion 可指定具体页面、人员和来源；答案引用让用户返回来源核对。[N1]
- RAGFlow 有独立 Search：直接找知识库内容，支持范围过滤，并提供相关问题等进一步探索入口。[R2]
- RAGFlow 的解析结果可与支持定位的原文预览联动，也可纠正片段；文档不支持定位时有明确限制。[R1]
- 启发：搜索命中原文、阅读上下文和针对选定资料提问，是核心产品能力；无需把所有问题都包装成 AI 长答案。
- AnythingLLM 文档区分全文输入与部分片段检索；不能用普通问答的若干相关片段，冒充全文摘要或全量比较。[A1]
- 本次没有据此推断任何产品采用何种跨轮上下文算法；聊天历史存在，也不能独立证明上下文处理质量。

### 成果沉淀、报告与协作

- Google 可把回答保存为笔记，笔记可选择为问答和生成来源，并导出 Google Docs；保存回答形成的笔记需移除引用后才能编辑，导出的 Docs 修改不会回写原笔记。[G3] 因此“编辑时仍保留原始依据”是本项目建议，不能归为已确认的 Google 同等能力。
- Google 报告可以选择或调整生成格式，并分享已有报告；其文档同时要求核对 AI 结果。[G4]
- Notion Agent 能创建和编辑页面、处理文件并生成可下载成果；其多步执行属于厂商能力，不是本项目现在建设通用 Agent 的理由。[N2]
- Copilot Pages 能把一次回答变为长期保存的页面，人工或 AI 持续修改，并与同事共同编辑。[M2]
- 启发：V3 的“生成”只是开始；要让用户能再次打开、修改、整理并使用成果。多人协作先从共享查看和评论价值出发，无须立刻实现实时共同编辑。
- Dify 将人工修正的常见回答保存为 curated responses；可借鉴“人工确认后复用”，无需照搬固定答案替换整个问答机制。[D2]
- 需注意：本项目不能把 AI 生成的报告默认为新的原始事实。若用于后续分析，应区分原文事实、模型归纳与人工补充，并保留来源关系。这是本报告建议，不是上述厂商能力的实测结论。

### 学习辅助和结构化导出

- Google 提供学习引导风格、资料卡片和测验；可调整难度或关注点，解释答案、记录学习进度，卡片支持 CSV 下载。[G1、G5]
- Google 的实验性聊天功能列出 CSV、JSON、Excel、Word 等输出，并明确处于实验早期阶段；不据此承诺一般性可靠导出。[G1]
- Notion Agent 文档包含从文件整理结构化页面、数据库及下载文件的用法。[N2]
- 启发：本项目已有“知识学习”场景，近期可先用文档导读与章节解释覆盖；测验、卡片等专门模式暂缓。
- 提取表能被 CSV／Excel 继续使用，是 V2 的自然延伸；它的优先级应由实际使用决定，不必排在更多视觉生成格式之后。

## 当前实现中需区分的限制

以下为静态代码观察，尚未复现为用户实际质量故障，不需要因此阻塞整个 V3。

- `backend/app/conversations.py` 的检索与回答流程围绕本轮问题组织，保存历史与把前文用于追问不是一回事。
- `backend/app/parsing.py:115` 的 DOCX 提取只遍历 `document.paragraphs`，未遍历表格；含表格材料是明确需要核查的资料类型。
- `backend/app/retrieval.py:59` 先取向量候选，再做词项评分和排序，缺少独立的关键词候选召回；编号、专名可能因此漏检，当前只是风险判断。
- Dify 的官方技术说明将专名、缩写、编号列为关键词检索擅长的场景，并区分召回与重排。[D3] 这提供了验证方向，不能替代本项目实际测试。

## 本项目的建议：按价值分批选择

以下是调研推导和产品判断，不是已批准需求，也不是应全部立即完成的待办。

| 建议 | 用户能得到什么 | 建议位置与取舍 |
| --- | --- | --- |
| 1. 有范围的连续问答 | 选定几份资料后，持续追问同一问题，不必反复说明来源和前文 | 近期优先；先支持明确文档选择，暂不扩跨团队／跨所有知识库问答 |
| 2. 文档导读、搜索和阅读核对 | 摘要、目录、关键概念、建议问题帮助开始阅读；按术语找到正文并阅读上下文 | 与 V3 配合；导读需实际覆盖全文或说明未覆盖部分，不用 Top K 片段冒充全量概括 |
| 3. 可保存和复用的成果 | 问答、知识笔记、分析进入报告；报告再打开、修改、导出，保留依据 | V3 主线；首版手工修改加 Word，即可实际使用，其他格式按需求增加 |
| 4. 可编辑知识笔记和可复用要求 | 保存原文摘录、问答结论与自己的理解；复用常用提取字段和报告要求 | 近期候选；笔记默认私人，显式分享属于待定新范围；区分原始来源与人工确认内容 |
| 5. 增加文字与网页来源 | 临时笔记、公开网页不必先制作成文档才能进入工作台 | V3 之后或实际需要时；先单页正文和粘贴文字，暂缓全网深度研究与批量爬站 |
| 6. 资料更新与时效提示 | 更新资料后知道哪些成果可能受影响，选择重新生成 | 来源经常变动时；不擅自覆盖人工修改成果，不先做大量连接器和通知平台 |
| 7. 待核实清单与分析修正 | 汇总缺失字段、相互矛盾说法和依据不足的结论；逐项查原文与修正 | 有差异化价值的候选；只提示本次分析发现的缺口，不承诺全局无遗漏或代做业务判定 |
| 8. 自然语言提出分析要求 | 输入“比较这些材料的条件和限制”，系统先建议字段／维度，用户确认后再提取 | 降低 V2 使用门槛的候选；复用现有分析，不先建设任意自主规划器 |

“资料更新后提示受影响成果”“人工确认后沉淀常用知识”等可成为后续差异化方向，但本次官方文档并未证明这些产品全面具备相同机制。
这些方向的前提是成果已经有人持续使用；不要先建设自动通知、复杂知识图谱或完整知识治理平台。

## 暂不建议照搬

- 音频播客、视频、幻灯片、信息图等多种表现形式：有学习价值，但目前报告、表格和原文阅读更接近已有任务。
- 全量外部连接器、跨企业统一搜索：应先识别最常见的一个资料来源。
- 通用自主 Agent、任意代码执行、多步骤编排：留给有具体执行目标的 V4，不作为 V3 的技术前置。
- 行业专用字段、合同条款库、专门业务流程：本项目是通用文档工作台，不能因为竞品提供就默认引入。
- 实时共同编辑、复杂审批与版本比较：等实际协作方式明确后再增加。
- OCR、复杂表格结构、外部资料连接：按常见资料类型选择一项补齐；先修正已确认的解析缺失，不先铺开所有输入方式。
- 自动记忆、测验卡片、定时摘要：先确认持续使用场景，暂不加入本轮主线。
- 安全加固、CI 扩展、部署验收、多屏精修：不因竞品调研重新进入当前核心功能主线；基本权限、保存和相关必要测试仍随功能完成。

## 官方来源

下列均于 2026-10-20 实际打开；帮助页可能持续更新。链接支持“官方如何描述该功能”，不构成效果背书。

- [G1：Google — Use chat in Gemini Notebook](https://support.google.com/gemininotebook/answer/16179559?hl=en)：来源选择、引用核对、聊天保存、学习引导及实验性输出。
- [G2：Google — Add or discover new sources](https://support.google.com/gemininotebook/answer/16215270?hl=en)：来源类型、资料摘要、发现资料、同步与导入限制。
- [G3：Google — Create & add notes](https://support.google.com/gemininotebook/answer/16262519?hl=en)：笔记、再利用与 Docs 导出。
- [G4：Google — Generate reports](https://support.google.com/gemininotebook/answer/18323649?hl=en)：报告格式、定制与分享。
- [G5：Google — Generate Flashcards or Quizzes](https://support.google.com/gemininotebook/answer/16958963?hl=en)：学习模式、解释、复习与 CSV 导出。
- [N1：Notion — Enterprise Search](https://www.notion.com/help/enterprise-search)：连接应用、来源范围、指定上下文与引用。
- [N2：Notion — Notion Agent](https://www.notion.com/help/notion-agent)：页面编辑、文件结构化、下载成果及能力限制。
- [M1：Microsoft — How Copilot Notebooks works](https://support.microsoft.com/en-us/microsoft-365-copilot/how-microsoft-365-copilot-notebooks-works)：限定参考资料、内容更新与起草。
- [M2：Microsoft — Get started with Copilot Pages](https://support.microsoft.com/en-us/microsoft-365-copilot/get-started-with-microsoft-365-copilot-pages)：聊天转成果、编辑、保存和协作。
- [M3：Microsoft — Frequently asked questions about Copilot Notebooks](https://support.microsoft.com/en-us/microsoft-365-copilot/frequently-asked-questions-about-microsoft-365-copilot-notebooks)：生成物可能仅使用部分参考资料。
- [R1：RAGFlow — Chunk: Parsing Results and Knowledge Fragment Management](https://ragflow.io/docs/v1.0.0-rc2/chunk_parsing_results_and_knowledge_fragment_management)：原文核对、解析内容纠正及定位限制。
- [R2：RAGFlow — Create a Search](https://ragflow.io/docs/v1.0.0-rc2/create_search)：独立搜索、范围过滤与相关问题。
- [R3：RAGFlow — Add a Data Source and Synchronization](https://ragflow.io/docs/v1.0.0-rc2/add_data_source/add_to_knowledge_base_and_sync)：接入、刷新和删除同步。
- [D1：Dify — Knowledge](https://docs.dify.ai/en/cloud/use-dify/knowledge/readme)：资料维护、元数据过滤和内容生成应用。
- [D2：Dify — Annotation System](https://docs.dify.ai/en/cloud/use-dify/monitor/annotation-reply)：人工修正并保存可复用回答。
- [D3：Dify — Hybrid Search and Rerank](https://dify.ai/blog/hybrid-search-rerank-rag-improvement)：关键词与向量召回互补，重排无法取代召回。
- [A1：AnythingLLM — Using Documents](https://docs.anythingllm.com/chatting-with-documents/introduction)：全文附件、片段检索、上下文与截断限制。
