# WealthLens 调研：多券商资产看板与当前 IBKR 系统的关系

核查日期：2026-10-22。仓库：[ejjcc/wealthlens](https://github.com/ejjcc/wealthlens)。

源码基线：`33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f`。本次读取 README、完整目录树、IBKR 微服务、聚合与快照、网页、CLI、部署配置及部分测试。**只做静态阅读，没有安装依赖、运行项目、连接券商或测试手机。** 下文区分作者描述、代码事实和工程判断。

## 1. 结论

WealthLens 是一个**把券商、加密交易所和手工资产集中展示的个人财富看板**。它确实有 IBKR 接入代码，读取 IB Gateway 的账户资金与持仓；不是只有截图，也不是仅导入 IBKR 报表。

它适合补充我们系统的**资产总览、历史快照、图表和聊天查账户入口**，不能替代 TradingAgents 的投资研究，也不能替代 NautilusTrader 的订单执行与状态管理。当前代码没有我们需要的 IBKR 委托、成交、手续费、撤改单和断线后订单对账接口。

**对当前项目的建议：纳入“界面及资产聚合参考”，暂不列为账户数据或执行核心依赖。** 原因不仅是功能不足，还包括已确认的字段不一致、默认资产数值、手机 API 地址与安装文档问题。先解决这些，才能判断复用整个项目是否比自建看板节省工作。

## 2. 它可以做什么

| 能力 | 核查结果 | 对我们项目的价值 |
|---|---|---|
| 多账户资产汇总 | 作者列出 Futu、IBKR、OKX、Binance、Bitget；代码有相应客户端/采集路径 | 将来确有多券商或加密资产时有价值 |
| 股票持仓、现金、未实现盈亏 | IBKR 微服务有资金/持仓端点；聚合口径存在待修问题 | 能参考展示结构，不能直接当风控输入 |
| 网页仪表盘 | Next.js/React + Chart.js；分布图、持仓表、趋势图、手工资产页面 | 是当前两个交易框架未提供的界面层 |
| CLI | Python 命令 `wl`，可查询后端或直接访问各采集服务，支持刷新与缓存 | 方便诊断或接聊天机器人 |
| 历史资产曲线 | SQLite 快照，定时保存；不是完整委托/成交账本 | 适合资产变化展示 |
| AI/聊天 | 外部 Agent 运行 CLI，再把结果返回聊天 | 查账户助手入口；非独立投研 Agent |
| 手机 | 有响应式布局代码，但远程 API 连接需要改造 | 可作为手机网页基础，尚非可直接用的手机交易端 |
| 自动交易 | 在所检查的 IBKR 服务与应用结构中没有订单执行实现 | 不满足执行层需求 |

主要依据：[项目说明](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/README.md)、[网页](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/frontend/app/dashboard/page.tsx)、[CLI](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/wealthlens)、[快照调度](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/scheduler/snapshot.scheduler.ts)。

## 3. IBKR 接入到底实现到哪里

### 已有的代码路径

`IB Gateway → ib_async → Python FastAPI :8001 → NestJS :3001 → 网页/CLI`

Python 服务第一次被请求时建立持久连接，默认端口 4001、clientId 10，选择 `managedAccounts()[0]` 订阅账户更新。账户资金来自 `accountSummaryAsync()`，持仓来自 `portfolio()`。所以属于**API 账户读取**，不是 CSV/Flex 报表导入。

但 HTTP 接口只有健康检查、`/api/funds` 和 `/api/positions`。在这份 IBKR 服务中没有开放订单、已完成订单、成交、佣金或订单提交端点，也没有订单事件持久化逻辑。底层库是否自己缓存部分交易事件，不代表 WealthLens 已将它们提供给我们的系统。

当用户在 IBKR 官方端成交后，连通状态下资产/持仓更新有机会反映最终结果；**这无法回答“哪个订单成交了多少、剩余多少、被谁撤销、成交价和费用是什么”**。因此不能完成我们要求的人工交易同步与模式切换对账。

[IBKR 服务源码](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend-ibkr/main.py)

### 会话与账户范围

- 根 README 推荐 IB Gateway + IBC，并提示手机与 Gateway 会话冲突。
- 它把“第二用户名”表述为“sub-account”，不能据此认为新用户名等于新资金子账户，或保证自动拥有相同权限与行情。
- 代码直接选择第一个 managed account，未提供我们需要的账户选择与账户隔离配置；资金返回值也没有保留账户标识。
- 代码没有实现“停 Agent → 人工交易 → 重连 → 完整对账 → 恢复 Agent”的切换流程。

TODO：结合真实账户权限与 IBKR 官方规则核查同一用户名/第二用户名的会话行为。此处不把作者建议视为已验证的接入方案。

## 4. 与当前系统直接相关的源码问题

以下是本次静态检查发现，并非已运行复现的测试结果。

| 问题 | 代码证据 | 可能影响 / 后续动作 |
|---|---|---|
| IBKR 现金字段不一致 | Python 返回 `cash`；Nest 类型及资产聚合读取 `total_cash` | 该聚合路径将缺失字段按 0 处理，网页/历史总资产可能漏算 IBKR 现金；CLI 另取 net_liquidation，口径又不同。统一数据契约并测试 |
| 卖空市值符号丢失 | 持仓服务将 marketValue 取绝对值，但 qty 仍保留正负号，网页也展示 Qty | 并非完全看不到空头；问题是正值市值进入资产加总，不能直接作为净资产或带方向估值 |
| 多币种不完整 | 微服务把市值原样标成 market_val_usd；Nest 仅转换 HKD、CNY，其他币种沿用原值 | 多币种资产可能被误当 USD；须保存原币种金额并统一换汇 |
| 合约与账户身份不足 | 持仓响应没有 conId、账户 ID、secType、到期日、行权价、乘数等完整字段 | 同标的不同合约难可靠关联；尤其不能用于期权订单及对账 |
| 错误可变成空仓 | Nest IBKR client 获取持仓失败返回 []、资金失败返回 null | 缺失不等于持仓为零；必须保留来源状态、时间与完整性 |
| 快照完整性粒度不足 | 已有 fallback/missing 标记，但按 stocks/cash 等类别判断；资产接口失败子源可先被转成空数组 | 外层成功不代表 IBKR 子源成功；交易前不能只检查总体 HTTP 成功 |
| 有非零资产默认值 | snapshot-buckets 定义默认 USD 12687 与 CNY 基金 40000，缺失对应输入时参与汇总 | 新部署不得把这些默认数值当用户资产；移除或显式配置，缺失应显示未知 |
| 安装依赖不完整 | main.py import ib_async，但 requirements.txt 没列它 | 按当前清单全新安装不能保证启动 IBKR 连接 |
| IBKR 子目录文档过时 | 子目录 README 说 Client Portal、不需要 IB Gateway；实际代码连接 IB Gateway socket | 按子文档部署会走错接入方式 |
| 快照文档和代码不一致 | README/SETUP 写每 6 小时；源码默认每日 06:00 和 20:00 | 需要统一频率、时区和刷新语义 |

证据：[IBKR 微服务](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend-ibkr/main.py)、[Nest IBKR 客户端](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/ibkr/ibkr.client.ts)、[资产聚合](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/assets/assets.service.ts)、[资产默认值](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/snapshot/snapshot-buckets.ts)、[完整性处理](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/snapshot/snapshot-integrity.ts)、[依赖清单](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend-ibkr/requirements.txt)、[IBKR 子目录文档](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend-ibkr/README.md)。

<!-- 以下关键证据使用固定提交和行号，方便后续开发复核。 -->

现金与空头结论的精确边界：Python 的 [cash 输出，L79](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend-ibkr/main.py#L79) 经过 Nest [直接返回 funds，L67](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/ibkr/ibkr.client.ts#L67)，没有改名，然后 [聚合读取 total_cash，L119](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/assets/assets.service.ts#L119)。网页 [L488](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/frontend/app/dashboard/page.tsx#L488)、summary 控制器和快照调度均继续使用该聚合现金，已读路径未见修补；不能把这一问题泛化成 CLI 净清算总额也漏现金，因为 CLI 的 [总额公式 L250](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/wealthlens#L250) 单独使用 ibkr_net。卖空方面，Python [L107](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend-ibkr/main.py#L107) 取绝对值，Nest [L103](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/assets/assets.service.ts#L103) 加总市值，但网页 [L1040](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/frontend/app/dashboard/page.tsx#L1040) 仍显示带符号的 qty。

## 5. “AI chat”具体是什么意思

仓库提供的 SKILL.md 本质上是告诉 OpenClaw、Claude Code 或其他 Agent：用户说“看板”等触发词时，执行 `wl`，把终端结果回给用户。这次仅把该文件作为研究对象阅读，没有安装或执行它。

没有看到 TradingAgents 那样的基本面/新闻分析、多角色辩论、交易计划生成，也没有独立的内置 LLM 研究服务。Telegram 等聊天通道依赖外部 Agent 平台。

因此：

- 可以让云端模型解释资产组成或回答账户问题，但需要我们设计上下文、权限与分析流程。
- 不能因名称里有 AI 就认为具备自主研究、风险决策或自动交易。
- README 的“数据都在本地”不能直接延伸到云端聊天：一旦外部 Agent 把持仓输出交给云端 LLM 或 Telegram，相关数据就离开本机。应只传所需字段，并明确哪些账户信息允许进入模型上下文。

[Agent 技能定义](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/skills/SKILL.md)

## 6. 手机与自托管

### 手机

源码有 `md:` / `lg:` 响应式网格及表格横向滚动，说明网页有适应小屏的基础；未做真实手机浏览器验证，也未看到原生手机应用或 PWA 配置。

**当前网页不能原样远程访问账户 API：** 页面和趋势图把 API 地址写成 `http://localhost:3001`。在手机里 localhost 指向手机本身，且后端只监听服务器 127.0.0.1。PM2 配置虽有 NEXT_PUBLIC_API_URL，已读网页并未使用它。

需要改同源 API 路由/代理，加入身份验证与受控远程访问，再验证手机显示与操作。手机下单还需另外开发订单表单、确认流程和执行接口。

### 部署与硬件

技术栈为 Next.js、NestJS、Python FastAPI、SQLite、IB Gateway/IBC；PM2 配置管理进程。README 给了 macOS 的 Gateway/IBC 命令，说明项目面向本地自托管，但没有 M6/24GB 的实测报告。

工程判断：这种资产看板通常主要消耗少量常驻内存、网络请求和 SQLite/缓存读写，本身不是 GPU/本地模型负载。不能据此承诺整个 TradingAgents + NautilusTrader + 回测系统的容量；需测完整组合的峰值及后台任务干扰。

不需要为了这个看板另外运行本地 AI 模型。

依据：[前端](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/frontend/app/dashboard/page.tsx)、[趋势图](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/frontend/app/components/TrendChart.tsx)、[后端监听](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/main.ts)、[进程配置](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/ecosystem.config.js)。

## 7. 认证、权限与账户数据边界

这是本地个人工具的设计，不能直接视为已经具备远程交易产品的访问控制。

- 已读 Nest 启动、模块、账户/历史/手工记录控制器未见登录、角色或认证 Guard；CORS 不是身份认证。
- Nest 默认仅监听 127.0.0.1；IBKR start.sh 也只监听本机，但直接运行 main.py 的入口监听 0.0.0.0。部署入口不同会改变暴露面。
- FastAPI IBKR 服务允许全部 CORS origin，健康端点返回 managed account 列表，异常可带底层错误文本。
- 密钥是本地 JSON，IB Gateway 登录凭据按文档配置在 IBC 文件；git-ignore 仅防止误提交，不等于密钥加密或访问控制。
- README 建议 IBKR ReadOnlyApi=yes 与交易所只读密钥。只读是实际配置/权限约束，应用代码本身没有证明这些外部权限已生效。
- 有创建/删除历史快照与手工资产记录的接口，虽然不是券商交易，也需要远程访问权限保护。
- Crypto 服务支持可选 OKX webToken。它与只读 API key 的权限边界不同；当前 IBKR 项目无须引入这个浏览器会话凭据路径。

证据：[IBKR 服务](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend-ibkr/main.py)、[Nest 启动](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/main.ts)、[历史接口](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/snapshot/history.controller.ts)、[手工记录接口](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/feishu/feishu.controller.ts)、[Crypto 服务](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/crypto/crypto.service.ts)。

## 8. 维护与许可

核查时 GitHub API 显示 2 stars、0 forks，release 列表为空。默认分支最新提交是 2026-06-02 的 Bitget 接口修复。仓库 pushed_at 显示 2026-10-22，但这是推送时间，不等于当天有新代码提交。项目较小，有近期接口修复历史，但没有证据证明长期维护、完整测试或交易系统成熟度。

目录中有行情计算、行情服务、快照 fallback 等测试，没看到 IBKR 实盘/模拟盘集成测试或订单恢复测试；本次没有运行任何测试。

许可存在冲突：README 自称 MIT，但目录树没有 LICENSE 文件，backend/package.json 标记 UNLICENSED。**不要直接把它列为许可已确认的 MIT 可复用依赖；TODO 向作者确认并补齐明确许可文件。** 这是仓库元数据核查结果，不是法律结论。

依据：[仓库元数据](https://api.github.com/repos/ejjcc/wealthlens)、[最新提交](https://github.com/ejjcc/wealthlens/commit/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f)、[Releases](https://github.com/ejjcc/wealthlens/releases)、[后端 package.json](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/package.json)、[快照测试](https://github.com/ejjcc/wealthlens/blob/33e2beb0a88912c3a56c0e55d9ad4bf4a8c41f8f/backend/src/snapshot/snapshot-integrity.spec.ts)。

## 9. 在我们的系统中怎么放

| 组件 | 主要职责 | WealthLens 能否替代 |
|---|---|---|
| TradingAgents | 基于信息与账户上下文进行研究、产出投资建议 | 不能；可提供整理后的账户展示数据，研究编排仍需独立 |
| NautilusTrader | 策略执行、订单状态、成交/持仓处理及恢复 | 不能；WealthLens 缺订单模型与执行接口 |
| 自建后端 | 账户真源、人工/Agent 模式、权限、风控与 API | 不能完整替代；部分展示/快照模块可参考 |
| 手机网页 | 看账户、看建议、确认及可选下单 | 看板部分可参考，远程访问及交易操作需开发 |
| 多券商财富总览 | 包含不参与策略交易的其他资产 | 是最直接的用处 |

若最终复用，推荐的连接方向是：**由我们的账户同步服务提供统一、带完整性和时间戳的数据，再让看板展示**。避免给交易核心同时接入多套互相独立的账户汇总口径。原有 IBKR 小服务可作为研究样例，不宜直接承担订单对账。

## 10. TODO（保持大纲，后续逐项展开）

- [ ] 确认许可证；确定是参考产品布局、复用部分模块，还是维护分支。
- [ ] 建立 IBKR 账户/现金/持仓/合约统一数据契约，修复 cash / total_cash 不一致。
- [ ] 修正带方向市值、多币种、默认资产数值与账户选择；与 IBKR 对账。
- [ ] 所有数据携带来源账户、时间戳、连接状态、完整性，断开不能变成零仓位。
- [ ] 将手机网页 API 改为受控同源入口，设计认证和远程访问。
- [ ] 展示手工 IBKR 委托/成交/持仓同步状态，数据由独立账户同步层提供。
- [ ] 接入 TradingAgents 建议展示、人工确认和模式切换状态；不是只把 CLI 文本转发给 LLM。
- [ ] 订单提交、撤改、未知结果处理、恢复对账仍由交易后端实现。
- [ ] 修复依赖与文档，验证 Mac 原生部署及进程恢复。
- [ ] 为现金字段、空仓与断线、多币种、卖空、同标的多合约建立有意义的数据测试。
- [ ] 在隔离环境用构造数据/模拟盘做端到端验证；本次尚未实施。
- [ ] 期权合约与多腿操作继续低优先级，但数据模型预留完整合约身份。

