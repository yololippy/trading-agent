> 历史归档（2026-10-24）：保留原始调研与来源，不再维护本文中的旧 TODO。当前需求以 [项目大纲](../PROJECT_OUTLINE.md) 为准，活跃任务见 [TODO](../TODO.md)；下文推荐不代表当前选型。

# NautilusTrader 能力与替代产品调研

首次调研：2026-10-21；源码补充：2026-10-22  
关联需求：[整体目标与任务大纲](../PROJECT_OUTLINE.md)  
性质：官方文档、发布记录、适配文档及部分源代码调研；未安装、未连接 IBKR、未做性能/故障实测。

## 1. 结论

- NautilusTrader 是可嵌入自建系统的交易引擎，对多策略、行情、回测、订单生命周期和对账有较完整的抽象；不是现成手机交易产品。
- 对「Apple Silicon Mac 自托管＋IBKR＋自建手机端＋人工交易后同步」需求，它仍是强候选，但处于 v2 候选发布迁移期，不能直接贴上“最稳妥”标签。
- LumiBot 是本次最值得与它正面对比的替代路线：Python 策略、AI、回测和执行整合度高，但 IBKR REST 与 Legacy socket 两条路径的认证、恢复、外部订单归属必须验证。
- LEAN 是重要的综合量化平台，但其官方 CLI 本地 IBKR 路线明确有 ARM 限制，而且算法运行期间的券商外部人工操作存在约束；不能作为这台 Mac 上的无障碍替代。
- VeighNa 提供中文生态、交易桌面、IB 接口和基础 Web API，能减少部分界面/控制服务工作；Mac 完整部署和外部订单同步仍须验证。
- 没有发现已经证实能够无改造满足全部需求的产品。最关键的选型标准是订单同步、人工/Agent 切换和恢复，而非吞吐量或 Star。

## 2. NautilusTrader 的版本状态

本次 GitHub releases 列表最新为 v2.0.0rc7，发布于 2026-10-15 UTC，明确标记为预发布。项目默认 develop 分支已围绕 Rust/PyO3 v2，迁移文档说明 v1 进入有限的关键安全回补期，不再做新功能对齐。

这不是项目停止维护；相反近期更新活跃。但意味着：
- 旧教程的类名、配置、导入路径不能直接用于 v2。
- “latest 文档”“develop 代码”“稳定版安装结果”未必是同一套接口。
- 后续要固定版本，并明确为候选发布的测试/修复预留时间。

来源：[v2.0.0rc7](https://github.com/nautechsystems/nautilus_trader/releases/tag/v2.0.0rc7)、[迁移说明](https://github.com/nautechsystems/nautilus_trader/blob/develop/MIGRATION_V2.md)。

## 3. 它能提供哪些能力

| 能力 | 对本项目的作用 | 边界 |
| --- | --- | --- |
| Rust 核心、Python 策略控制 | 自建策略和应用接口 | 复杂底层修改可能涉及 Rust；使用预编译包不要求自己写 Rust |
| 多资产、多策略、事件驱动 | 股票、ETF及后续其他资产的统一运行结构 | 具体支持取决于券商适配器和账户权限 |
| 历史回测与实盘共用策略逻辑 | 减少双份策略实现 | 相同代码不保证仿真与实盘完全一致 |
| Quote/Trade/Bar 等行情与历史请求 | 获取策略输入 | IBKR 数据权限、历史覆盖和请求限速仍然有效 |
| 订单提交、改单、撤单、状态事件 | 统一订单执行流程 | 引擎支持不等于 IBKR 支持所有组合 |
| 风控、组合、缓存和消息机制 | 连接账户规则、策略与手机后台 | 自定义业务风控与远程权限仍需开发 |
| 状态持久化及对账 | 重启、掉线后核对券商状态 | 无法凭空恢复 API 不提供的历史成交 |
| 外部订单识别与策略归属机制 | 表示非本系统产生的交易 | 读取、展示、归属、修改权限是不同问题 |
| 可扩展适配器 | 后续增加数据源或券商 | 需要额外开发与测试 |

来源：[项目 README](https://github.com/nautechsystems/nautilus_trader)、[执行对账](https://nautilustrader.io/docs/latest/concepts/execution/reconciliation/)。

## 4. 针对 IBKR，已核对的具体能力

以 v2.0.0rc7 的适配文档为主要版本依据：

- 通过 TWS API 连接 TWS 或 IB Gateway。
- 行情和历史请求、合约识别与加载、订单/成交/账户事件。
- 市价、限价、止损、止损限价、触价和跟踪止损等订单映射。
- 下单、关联订单、改单、撤单、查询和整体状态报告。
- 未完成及已完成订单产生订单报告，成交及佣金产生 fill 报告，账户持仓产生 position 报告。
- `fetch_all_open_orders` 默认 False，可请求当前会话能看到的全部挂单；这不意味着读取其他登录会话的所有订单有无条件保证。
- 文档包含期权 Greeks、价差/组合等能力，但期权业务仍按用户要求后置。

来源：[固定版本 IBKR 适配文档](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc7/docs/integrations/interactive_brokers.md)。

### 需要纠正的常见假设

1. **不能直接套用 client ID 0 绑定人工订单的教程。**该版本文档明确：execution client ID 不能是 1000 的倍数，因此也包括 0。若需求涉及绑定，须确认适配器设计和其他读取途径，不能简单把配置设为 0。
2. **外部持仓恢复不等于恢复完整成交历史。**引擎可以通过内部对账记录修正持仓；这样的内部记录不能冒充券商真实成交用于历史绩效和审计。
3. **连接端口成功不等于协议相容。**该版本文档要求 TWS/IB Gateway 服务端协议版本至少 213；这是协议号，不是软件发行号。安装时需要按固定版本实际握手验证。
4. **自动恢复不等于自动重新认证。**IBKR 登录失效、官方端会话切换仍需单独处理。

以上前三项来自固定版本适配文档；认证限制需结合实际账户和 IBKR 文档验证。

## 5. 替代产品比较

| 方案 | 更突出之处 | IBKR 与人工订单 | 手机/管理界面 | 对当前 Mac 自托管需求的判断 |
| --- | --- | --- | --- | --- |
| NautilusTrader | 模块化事件引擎、回测/实盘统一、订单与对账抽象 | 有专门适配与对账；外部订单跨会话需验证 | 需要自建应用层 | 继续作为执行层基准候选 |
| LumiBot | Python 易集成，内置 AI runtime，研究/回测/执行连接较少 | 有 REST 和 Legacy socket 实现；外部订单归属与恢复未完成验证 | 开源包仍需应用层；BotSpot 为另一产品/服务 | 最强的一体化竞争路线 |
| LEAN / QuantConnect | 多资产研究、数据、回测、算法框架和云/本地工具链 | 有独立 IB 插件；官方提示外部人工操作可使算法停止 | 有平台与控制工具，不能等同自建手机 App | ARM 本地 IB 路线存在官方限制，当前不作为直接替换首选 |
| VeighNa / vn.py | 中文文档、桌面终端、模块生态、REST/WebSocket 基础 | vnpy_ib 提供专门连接；人工订单跨会话仍需验证 | 有基础 webtrader，可复用部分后台 | 值得比较，但 Apple Silicon 完整依赖链待验证 |

### 5.1 LumiBot：整合度更高，但必须选择清楚 IBKR 路线

本次核对 v4.6.23：
- 有普通 Strategy 内部创建与运行 AI Agent 的示例，不只是让 AI 帮忙生成策略代码。
- 同一策略类可用于回测和券商运行，但直接运行示例仅进入回测；不能只改环境变量就认为完成实盘接入。
- 源码包含 `interactive_brokers.py`（socket）与 `interactive_brokers_rest.py`（REST）。
- socket 源码有挂单/持仓查询；REST 源码有持仓同步等流程。这些证明相关功能入口存在，但不是“完整同步所有手工订单”的验收结果。
- 当前主 IBKR 文档面向 Client Portal 路线，要求个人用户浏览器认证和至少每日重新认证；另一页面标记 socket 为 Legacy。若要沿用 IB Gateway 的长期运行方案，要单独评估 socket 路线维护情况。
- 官方对比表把 LumiBot 与 BotSpot 托管能力放在一起，不能据此认定开源包包含所有托管监控、手机和部署能力。

我的判断：若主要是低频 AI 决策与 Python 策略，LumiBot 可能比手工连接 TradingAgents＋NautilusTrader 少一些整合工作；收益是开发复杂度降低，不是已证实交易效果更好。若外部订单同步或恢复不满足需求，这种优势不足以支持替换。

来源：
- [v4.6.23](https://github.com/Lumiwealth/lumibot/releases/tag/v4.6.23)
- [固定版本 Agent 示例](https://github.com/Lumiwealth/lumibot/blob/v4.6.23/lumibot/example_strategies/ai_researcher_trader.py)
- [socket 实现](https://github.com/Lumiwealth/lumibot/blob/v4.6.23/lumibot/brokers/interactive_brokers.py)
- [REST 实现](https://github.com/Lumiwealth/lumibot/blob/v4.6.23/lumibot/brokers/interactive_brokers_rest.py)
- [IBKR 接入与认证说明](https://lumibot.lumiwealth.com/brokers.interactive_brokers.html)

### 5.2 LEAN：强平台，但你的机器与使用习惯存在匹配问题

- LEAN 引擎开源，核心为 C#，可编写 Python/C# 算法。
- QuantConnect CLI 提供研究、回测、优化、本地/云实盘等工具；官方文档要求使用 CLI 的组织处于付费层级。开源引擎与 CLI/云服务条件必须分开理解。
- 官方 CLI IBKR 文档说明 ARM 镜像不包含 IB Gateway，不支持该路线的本地 IB 实盘。文档以 M1/M2/M3 举例，不能由此推断 M6 已获支持；本次没有取得 M6 支持证据。
- 这项限制是 QuantConnect 部署路线的限制，不是说 IBKR 官方 Gateway 不支持 Apple Silicon。
- 官方 Algorithm Control 文档建议：若要在券商端手工调整持仓，停止算法、手动交易，再重新部署。仅让 Agent 不发新单而让执行算法继续运行，不等同于遵循这套流程。
- 自编译、自建网关桥接或改用 x86/云端可能构成其他路线，但不能在未验证前称为现成解决方案。

来源：[LEAN](https://github.com/QuantConnect/Lean)、[CLI 条件](https://www.quantconnect.com/docs/v2/lean-cli/key-concepts/getting-started)、[本地 IBKR/ARM 限制](https://www.lean.io/docs/v2/lean-cli/live-trading/brokerages/interactive-brokers)、[人工干预规则](https://www.quantconnect.com/docs/v2/lean-cli/live-trading/algorithm-control)。

### 5.3 VeighNa：可以减少部分 Web 服务工作

- 有交易桌面、策略、回测、风控、数据管理等模块；IBKR 接口是独立 vnpy_ib 包。
- vnpy_webtrader 提供 REST 主动调用与 WebSocket 推送，通过 RPC 与交易进程分离。
- README 明确仅提供基础交易和管理接口，其他策略应用接口需要扩展；这不是完整手机交易产品。
- 核心 README 的环境推荐偏 Windows/Ubuntu，但也列出 macOS 安装方式，IB/Web 模块标记支持 macOS。不能简单说“只支持 Windows”，也不能未经验证宣称 Apple Silicon 整套部署开箱即用。
- 项目的 AI 量化/因子模块主要是机器学习研究，不能直接当成 TradingAgents 那种 LLM 研究团队。

来源：[VeighNa](https://github.com/vnpy/vnpy)、[IB 接口](https://github.com/vnpy/vnpy_ib)、[Web 模块](https://github.com/vnpy/vnpy_webtrader)。

## 6. 维护、许可证和部署成本快照

| 项目 | Star | 最近 push 日期（UTC） | 许可证标识 | 需要额外区分 |
| --- | ---: | --- | --- | --- |
| NautilusTrader | 30,474 | 2026-10-21 | LGPL-3.0 | v2 候选发布、v1 迁移状态 |
| LumiBot | 2,142 | 2026-10-21 | GPL-3.0 | 开源包与 BotSpot 托管能力 |
| LEAN | 22,132 | 2026-10-21 | Apache-2.0 | 引擎开源与 CLI/数据/云服务费用 |
| VeighNa | 45,969 | 2026-10-06 | MIT | 开源核心与商业产品/数据服务 |

数据来自各项目 GitHub API 查询。活跃度不代表稳定性；许可证标识不是商业分发建议。

本次没有同机运行测试，因此不提供虚构的 CPU、内存排名或“性能快多少倍”。24GB/512GB 的实际余量仍取决于标的数、并发 Agent、回测和存储保留策略。

## 7. 对本项目的选择建议

### 方案 A：TradingAgents＋NautilusTrader

适合保持清晰的研究/执行分工、自建完整控制台、后续扩展多策略。现有大纲继续适用；主要成本是桥接、订单归属、账户同步、手机与运维层。当前突出风险是 v2 版本迁移和特定 IBKR 行为适配。

### 方案 B：LumiBot 内置 Agent＋执行，按需接其他研究组件

适合减少跨框架连接、主要采用 Python 低频策略。可以作为完整系统路线，而非“简化版起步”。成立前提是固定版本下的 IBKR 路线、外部订单同步、重启恢复和程序风控通过验证。不要同时引入第二个执行引擎管理同一组订单。

### 其他路线

- LEAN：若愿意改变执行主机/采用云服务并接受其人工交易切换规则，可提高优先级。
- VeighNa：若中文桌面操作与已有 Web 接口比跨市场引擎结构更重要，可提高优先级。
- 当前仍不应只因某个项目更热门或宣称支持更多资产，就替换已讨论架构。

## 8. 决定替换前必须回答的 TODO

以下是完整系统的选型验收，不是部署简化系统的建议。

- [ ] Mac ARM 原生依赖、固定版本与 Gateway 协议能否相容。
- [ ] 用户在 IBKR Mobile/TWS 下单后：未成交、部分成交、已成交、已撤销是否都能同步。
- [ ] 人工交易导致 API 会话中断时，恢复后能否补齐状态并正确标注未知历史。
- [ ] 切换 Agent 模式前，如何确认挂单、持仓与资金已完成对账。
- [ ] 外部订单是否被错误归入某个策略、被自动修改或被重复执行。
- [ ] 下单超时、进程重启和网络中断后，能否先核查而非盲目重发。
- [ ] 手机查询与控制可复用多少现有接口，哪些必须自建。
- [ ] AI 决策执行是否有程序强制检查，不能只依赖提示词。
- [ ] 后续期权功能建立支持矩阵，但不阻塞当前股票/ETF 主流程选型。
- [ ] 分别估算方案 A/B 在同一验收范围内的开发与维护工作量，再确定最终引擎。

## 9. 调研范围与未覆盖项

重点比较与当前个人 IBKR、自托管、AI、手机需求最相关的开源方案，同时区分 QuantConnect/BotSpot 商业服务。不是穷尽全部交易软件市场，也没有评测真实收益。

本次已完成：能力与边界比较、发布和维护快照、IBKR/ARM/人工订单关键限制识别。未完成：安装运行、账户联调、性能测试、外部订单端到端验收。上述未完成项属于后续实施验证，不应在本次调研中伪称已通过。

## 10. 第二轮：外部订单与恢复源码核查（2026-10-22）

本轮固定 NautilusTrader v2.0.0rc7、LumiBot v4.6.23，阅读实现和测试。下面的“确认”指源码中存在该路径，不代表 IBKR 账户端到端验证通过。

### 10.1 NautilusTrader 能查询什么？

IBKR 的订单报告路径调用 `all_open_orders()`；当请求不限定未完成订单时，也查询 `completed_orders(false)`。代码按账户筛选，并可按合约筛选。成交报告使用 executions 查询并处理佣金配对，持仓报告独立查询。批量状态报告组合这三类结果。

这支持把“券商订单、成交、持仓”作为恢复依据，但查询范围受 IBKR 会话、用户名、权限与历史窗口约束。源码明确说明，未配置回看起点时，不能宣称历史报告集合完整；配置回看也不能让 IBKR 返回本来不可用的数据。

对当前需求：切回 Agent 时应先恢复当前账户事实，再决定是否需要补齐历史。系统为对齐持仓而生成的合成事件必须与真实成交记录分开，不能用于伪造某次人工交易的成交价、时间或佣金。

来源：[订单、成交与持仓报告](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc7/crates/adapters/interactive_brokers/src/execution/reports.rs)、[批量报告与历史边界](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc7/crates/adapters/interactive_brokers/src/execution/core.rs#L1385)。

### 10.2 同步手工订单，不等于接管手工订单

IBKR 官方说明：修改/撤销手工 TWS 订单需要 client ID 0 进行绑定；`reqAllOpenOrders` 读取不执行绑定。绑定可能造成订单撤回重报并影响排队位置。

NautilusTrader 当前核查版本的执行连接禁止 client ID 为 1000 的整数倍，包含 0。因此，不能把“已读取外部订单”推导成“当前适配器可以直接绑定、接管任意手工 TWS 订单”。这是结合两份文档得出的适配限制判断；没有实际执行绑定实验。IBKR Mobile 订单在具体登录安排下的可见性及修改权限还需单独核验，不能用 TWS 结论替代测试。

本项目当前不要求接管人工挂单。建议继续定义为：人工订单同步展示，计入账户敞口与挂单占用；默认仍在 IBKR 官方端修改。Agent 恢复前对已有挂单明确选择保留并预留额度，或由用户处理后再恢复，避免与同方向新订单叠加。

来源：[固定版本连接要求](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc7/docs/integrations/interactive_brokers.md#L30)、[IBKR 绑定与修改规则](https://www.interactivebrokers.com/docs/tws-api/doc/orders/modifying-orders)。

### 10.3 LumiBot 不是没有同步和恢复机制

已确认通用执行器会同步余额、持仓和券商订单，更新本地订单属性；首次运行会处理券商返回的已有终态订单，后续不会把旧终态订单直接提升为新挂单。IBKR REST 适配器也有轮询订单和持仓的实现。

当挂单从列表中消失，通用 Broker 会尝试按 ID 查询；查询异常或无法解析时保留未解决状态，部分路径会标记 unknown。因此不能简单说它会把所有查询不到的订单当成撤单，也不能把通用代码视为 IBKR Mobile 同步已经验证。

尚需核验：IBKR 路线实际上返回多大范围、人工订单如何归属策略、余额是否新鲜、未知状态如何阻断下一次交易。执行器在某些限流/失败场景会沿用缓存余额或旧值，这进一步说明“读取过账户”与“拿到足够新的账户信息”需要分开判断。

来源：[通用同步执行器](https://github.com/Lumiwealth/lumibot/blob/v4.6.23/lumibot/strategies/strategy_executor.py#L623)、[缺失订单复查](https://github.com/Lumiwealth/lumibot/blob/v4.6.23/lumibot/brokers/broker.py#L1199)、[IBKR REST 轮询](https://github.com/Lumiwealth/lumibot/blob/v4.6.23/lumibot/brokers/interactive_brokers_rest.py#L1200)。

### 10.4 测试证据的边界

NautilusTrader IB 适配器测试涉及撤单事件、佣金后成交事件、迟到成交、断线报告查询、持仓报告去重等。LumiBot 测试涉及交易工具权限、完整账户读取、下单后刷新，以及超时后 unknown。

这些是可复用的验证起点。本轮没有运行测试，也没有找到足以替代我们实际验收的“IBKR Mobile 手工下单→会话切换→系统恢复→Agent 继续”的完整结果。

来源：[NautilusTrader 适配器测试](https://github.com/nautechsystems/nautilus_trader/blob/v2.0.0rc7/crates/adapters/interactive_brokers/src/execution/core/tests.rs)、[LumiBot Agent 测试](https://github.com/Lumiwealth/lumibot/blob/v4.6.23/tests/test_agent_tool_permissions.py)。

### 10.5 选型验收场景（TODO，尚未执行）

| 场景 | 通过标准 |
| --- | --- |
| IBKR 官方端人工挂限价单 | 系统显示账户、合约、价格、数量、来源，不自动绑定或修改。 |
| 人工订单部分成交 | 挂单剩余量与账户持仓同时对齐，不重复累计成交。 |
| 人工订单撤销/完全成交 | 系统最终状态与券商一致；历史查询不到时明确未知。 |
| 人工交易期间 Gateway 离线 | 恢复后先核对持仓/挂单，再开放 Agent 执行；旧快照不得被当成最新账户。 |
| 人工→Agent 切换时已有挂单 | 明确挂单归属和占用；不产生重复目标仓位。 |
| 下单请求超时或进程退出 | 先查询结果；不能仅因没收到确认便重复提交。 |
| 历史窗口无法覆盖离线期间 | 当前持仓可恢复；未知历史单独标注，必要时用报表补充。 |
| AI 提交超限或数据过时的订单 | 程序拒绝，不依赖 Agent 自觉遵守提示词。 |

按你的使用方式，本轮仍保留 TradingAgents＋NautilusTrader 为主候选，LumiBot 为完整替代候选。这个排序依据模块边界和已看到的对账实现，不是性能或可靠性实测排名。最终比较应围绕上表，不能只比支持多少券商或有多少 Agent 示例。

