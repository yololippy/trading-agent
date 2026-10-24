# Codex 额度恢复脚本

`codex-quota-watch.py` 使用 Python 标准库和已登录的 Codex CLI，通过官方 app-server 协议查询套餐额度。查询和等待不调用模型；发送“继续”后产生的正常执行仍使用套餐额度。

## 用法

在仓库根目录执行，要求 Python 3.10+、Codex CLI 在 PATH 中，并已通过 ChatGPT 账号登录。

```sh
# 默认只查询一次，不发送消息，也不加载会话
python3 bin/codex-quota-watch.py

# 每 15 分钟检查；观察到恢复后继续监控，仍不发消息
python3 bin/codex-quota-watch.py --watch --interval 900

# 每个受限→恢复周期向 main 发一次“继续”，随后继续监控
python3 bin/codex-quota-watch.py --watch --interval 900 \
  --continue-thread 01a16c79-0f8f-7d53-b0dd-e36b607d9094

# 用唯一的停止标记表达“整体任务完成”或“用户叫停”
task_monitor_dir="$(mktemp -d "${TMPDIR:-/tmp}/trading-agent-monitor.XXXXXX")"
printf '停止标记：%s\n' "$task_monitor_dir/stop"
python3 bin/codex-quota-watch.py --watch --interval 900 \
  --continue-thread 01a16c79-0f8f-7d53-b0dd-e36b607d9094 \
  --stop-file "$task_monitor_dir/stop"
# 在另一个终端或 main 完成整体任务后，对上面显示的实际路径执行 touch
# touch /实际临时目录/stop

# 有共享 app-server 控制 socket 时，使用原运行时续跑
python3 bin/codex-quota-watch.py --watch --interval 900 \
  --server-socket /path/to/app-server-control.sock \
  --continue-thread 01a16c79-0f8f-7d53-b0dd-e36b607d9094

# 有限次数的只读检查，方便验证；检查结果按 JSONL 输出到终端
python3 bin/codex-quota-watch.py --watch --interval 2 --max-checks 3
```

脚本不自动启动、不注册系统服务、不创建自动化、不改 Codex 配置，也不创建业务进度文件。需要常驻时在终端手动运行；Ctrl-C／SIGTERM 可停止。续跑模式仅锁定同一会话的其他脚本实例，锁文件保存在系统临时目录。

正常监控持续到整体任务完成或用户叫停。一次额度恢复或 main 的一次回复完成不会停止监控。脚本不能从一次回复自动判定整个项目完成：main 验收完当前已授权开发任务后，创建 `--stop-file` 指定的停止标记；它只是一个停止信号。等待间隔中约一秒内响应；正在续跑时等待该轮结束后退出。已经存在的停止文件会让脚本直接退出，新一轮使用新的唯一停止路径。

## 检查与重试语义

- 依赖服务端明确的 `ordinaryUsageAllowed`；不把百分比下降、预计重置时间已过或请求成功当成额度恢复。未知字段、网络失败、超时分别记录，继续等待。
- 优先检查 `rateLimitsByLimitId` 中的 `codex`，可用 `--limit-id` 指定其他额度桶。显式的限额/支出限制仍会阻止续跑；不会购买额度、消耗重置券或切换计费方式。
- 启动时额度可用只会监控，不发送“继续”。必须先观察到受限，再观察到明确恢复；脚本重启后重新观察，不根据上次的业务进度猜测。
- 每轮续跑成功后重新等待下一次受限；持续可用期间不重复发送。可连续处理多轮耗尽/恢复，直到停止信号或人为限定的检查次数上限。
- 启用 `--continue-thread` 才读取/恢复指定会话；目标正在运行时等待，不主动打断。发送的正文只有“继续”，脚本不向其他会话分派任务。
- 发送后等待 `turn/completed` 的真实结束状态。若仍明确报 `usageLimitExceeded` / `rateLimitExceeded`，继续检查并重试；不能以提交被接受代替成功结果。
- 提交或执行结果不明时报告 `continue_unknown`，继续只读检查，但暂停自动发送，避免重复执行。默认单次请求超时 30 秒，续跑等待上限 3600 秒，分别用 `--timeout` / `--turn-timeout` 调整。
- 需要用户回答/审批或出现非额度执行错误时，记录 `needs_user_input` / `continue_error` 和 `resume_paused`；监控进程继续，自动发送保持暂停，不自动同意审批。处理并核实先前结果后，可以停止并重新启动监控；不会擅自解除暂停。
- 日志只包含检查状态、额度窗口、请求/执行结果和目标标识，不主动输出凭据、聊天正文或工具输出。可自行重定向到文件。

## 运行时边界与本次验证

默认启动独立的 `codex app-server --stdio` 进程，复用当前 Codex 登录态。续跑使用现有 thread ID 和已有历史，但执行环境由当前 CLI 配置决定，不能假定与桌面 App 的专属插件/工具完全一致；独立服务也不能可靠判断另一个运行时里正在进行的任务。

如果需要在桌面原运行时继续并保留其工具环境，需使用该运行时已开放的标准控制 socket（`--server-socket`）。脚本不会自行开启监听或更改桌面配置。普通 `~/.codex/ipc/ipc.sock` 和桌面内部 app-tools socket 不是此接口，不应填入。

验证包括：本机真实额度只读查询，以及模拟服务下的多轮受限/恢复、持续可用不重复发送、停止信号、网络错误、超时、忙碌会话、发送与最终结果、仍然限额、结果不明后暂停发送和重复实例场景。未向真实会话发送“继续”，未启动常驻监控，未验证真实额度耗尽/重置或桌面原运行时续跑。

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s bin/tests -v
```

退出码：`0` 单次查询可用或收到停止文件；`1` 单次查询/启动错误；`2` 单次检查明确受限；`3` 单次状态未知或达到明确设置的检查次数上限；`5` 单次请求需要用户处理；`130` Ctrl-C／SIGTERM。监控模式下的自动发送暂停以日志事件表示，不因一次恢复或这些运行异常退出。

协议依据：[OpenAI 官方 App Server 文档](https://learn.chatgpt.com/docs/app-server)。具体字段以本机 `codex app-server generate-json-schema` 生成的协议为准。
