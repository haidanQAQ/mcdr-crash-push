# crash_push

> **MCDR plugin** — push a Bark / ServerChan / Webhook notification to your phone when the Minecraft server crashes.
> **MCDR 插件** —— Minecraft 服务端崩溃时，把告警推到你的手机（Bark / Server酱 / 通用 Webhook）。

[English](#english) | [中文](#中文)

---

## English

### Why this exists

MCDR's plugin catalogue has **no** Bark / ServerChan / generic push plugin. I checked all 235 plugins in the official catalogue (id + name + description) and searched GitHub for `mcdr+serverchan`, `mcdr+bark`, `mcdreforged+crash+notify`, `mcdr+plugin+webhook+notify` — **zero results** for every push-service keyword.

The two closest things are not what you need:

| Plugin | Does | Doesn't |
| --- | --- | --- |
| [crash_restart](https://github.com/TISUnion/CrashRestart) | detects a crash, restarts the server | no notification at all |
| mcdr2restic | multi-channel async notifications | backup only, never watches for crashes |

So the crash **detection** is already a solved, tiny problem (it is literally two event handlers, see [Detection logic](#detection-logic)) and this plugin reuses it. The **delivery** layer is what doesn't exist — so this repo contains just that, in one file, with zero third-party dependencies.

### Features

- **Single file, standard library only** — drop `crash_push.py` into `plugins/`, done.
- **Three channels at once** — Bark (incl. self-hosted), ServerChan (Turbo / ³), and a generic webhook with a templated body (Feishu, DingTalk, WeCom, Slack, n8n, …).
- **Useful payload** — crash-report absolute path, return code (translated to a signal name when the server was killed), and the tail of the log.
- **No false alarms on your own stops** — `stop` / `restart` / `!!MCDR stop` from the console are recognised as intentional.
- **No notification lost on shutdown** — the crash case kills the MCDR process right after the stop event; the worker thread is deliberately non-daemon so the HTTP request still completes.
- **Rate limiting per notification type** — a crash loop won't flood you, but a crash right after a recovery notice still gets through.
- **Recovery notice** — if the server was down for more than 30 s, the next startup reports the downtime.
- **Test command** — `!!crashpush test` verifies the whole chain before you trust it.

### Install

1. Copy `crash_push.py` into your MCDR `plugins/` folder (it coexists happily with `crash_restart.py`).
2. `!!MCDR reload plugin`, or restart MCDR.
3. `config/crash_push/config.json` is generated on first load. Edit it, then `!!crashpush reload`.
4. Run `!!crashpush test` — you should get a notification within a second or two.

Requires MCDR 2.x (tested on 2.16.3). No pip packages needed.

### Configuration

`config/crash_push/config.json` — save as UTF-8 (a BOM is tolerated, the plugin reads with `utf-8-sig`).

#### Bark (iOS)

```json
{
    "bark": {
        "enabled": true,
        "server_url": "https://api.day.app",
        "device_key": "your-device-key",
        "group": "Minecraft",
        "sound": "alarm",
        "level": "timeSensitive"
    }
}
```

`level`: `active` / `timeSensitive` (breaks through Focus) / `critical` (bypasses silent mode, needs the in-app permission). Point `server_url` at your own instance if you self-host Bark.

#### ServerChan

```json
{
    "serverchan": {
        "enabled": true,
        "sendkey": "SCTxxxxxxxx",
        "api": "https://sctapi.ftqq.com/{sendkey}.send"
    }
}
```

`api` is a template — `{sendkey}` is substituted. Turbo uses `sctapi.ftqq.com/{sendkey}.send`; ServerChan³ has its own send URL, put it here.

#### Generic webhook

```json
{
    "webhook": {
        "enabled": true,
        "url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx",
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "body_template": "{\"msg_type\": \"text\", \"content\": {\"text\": \"{title}\\n{body}\"}}"
    }
}
```

`{title}` and `{body}` are JSON-escaped before substitution, so you don't need to escape quotes yourself.

#### Other switches

```json
{
    "notify_on_normal_stop": false,
    "notify_on_start": false,
    "log_tail_lines": 12,
    "timeout_seconds": 8,
    "rate_limit_seconds": 20
}
```

| Key | Meaning |
| --- | --- |
| `notify_on_normal_stop` | also notify on an intentional stop |
| `notify_on_start` | notify on every startup (default: only after a crash, as a recovery notice) |
| `log_tail_lines` | how many log lines to attach, `0` disables |
| `timeout_seconds` | HTTP timeout |
| `rate_limit_seconds` | minimum gap between two notifications **of the same type** |

A full example lives in [`examples/config.example.json`](examples/config.example.json).

### Detection logic

1. `on_info` — a log line containing `This crash report has been saved to:`, `Crash report saved to:` or `A detailed crash report can be found at` sets `crash_detected` and remembers the report path.
2. `on_info` — an intentional stop command (`stop`, `restart`, `!!MCDR stop`, …) sets `expected_stop`, which suppresses the alarm.
3. `on_server_stop(server, return_code)`:
   ```python
   is_crash = crash_detected or (return_code != 0 and not expected_stop)
   ```
   A negative `return_code` means the process was killed by a signal (`-9` = SIGKILL, `-15` = SIGTERM) and is shown as such in the notification.

### Commands

| Command | Effect |
| --- | --- |
| `!!crashpush test` | send a test notification asynchronously |
| `!!crashpush reload` | re-read `config.json` |

Both require OWNER-level permission (`PHYSICAL_SERVER_CONTROL_LEVEL`).

### Gotchas worth knowing

These cost me real debugging time; they are handled here so you don't repeat them:

1. **`on_server_stop` is dispatched with `block=True`** — never do the HTTP request synchronously in the handler, you would stall the stop sequence.
2. **The notification thread must not be a daemon.** After a crash the MCDR process exits immediately, and a daemon thread is killed with it: the log says "push submitted" but no request ever leaves. Use `daemon=False` (and rely on the HTTP timeout as the bound).
3. **Don't skip on `server.is_server_running()`.** `crash_restart` listens to the same event; if it restarts first, the guard would swallow your crash notification entirely. Deduplicate by timestamp instead.
4. **`Literal.requires()` takes a *callable* failure message**, not a string — passing a string raises `TypeError`.
5. **The permission constant is `PermissionLevel.PHYSICAL_SERVER_CONTROL_LEVEL`** — there is no `PHYSICAL_SERVER_CONTROL`.

### Tested

End-to-end against MCDR 2.16.3 with a fake server process and a local HTTP mock:

- crash (`exit 1` + crash-report line) → notification delivered, `HTTP 200`, correct Bark JSON (`device_key` / `title` / `body` / `group` / `sound` / `level` / `isArchive`), absolute report path in the body;
- normal stop (`exit 0`) → nothing sent;
- `!!crashpush test` → notification delivered;
- two crashes in a row → two notifications (the limiter only applies to the same type within 20 s).

### Coexistence with crash_restart

Both plugins listen to `on_server_stop` and don't interfere:

- `crash_restart` restarts the server;
- `crash_push` notifies — even if the server has already been restarted inside the same event.

### License

MIT — see [LICENSE](LICENSE).

---

## 中文

### 为什么写这个

MCDR 插件目录里**没有** Bark / Server酱 / 通用推送插件。我把官方目录的 235 个插件（id + 名称 + 描述）全过了一遍，又用 GitHub 仓库搜索了 `mcdr+serverchan`、`mcdr+bark`、`mcdreforged+crash+notify`、`mcdr+plugin+webhook+notify` —— 所有推送服务关键词**全部 0 命中**。

最接近的两个都不是你要的：

| 插件 | 做了什么 | 没做什么 |
| --- | --- | --- |
| [crash_restart](https://github.com/TISUnion/CrashRestart) | 检测崩溃并自动重启 | 完全不推送 |
| mcdr2restic | 多通道异步通知 | 只用于备份场景，不监听崩服 |

也就是说，**崩溃判定**已经是解决过的极小问题（就是两个事件回调，见[崩溃判定逻辑](#崩溃判定逻辑)），本插件直接沿用；**推送这一层**才是没人做过的部分——这个仓库就只做这一层，一个文件，零第三方依赖。

### 功能

- **单文件、纯标准库** —— `crash_push.py` 丢进 `plugins/` 就能用。
- **三个通道可同时开** —— Bark（支持自建服务端）、Server酱（Turbo / ³）、以及模板化的通用 Webhook（飞书、钉钉、企业微信、Slack、n8n 都行）。
- **推送内容有用** —— 崩溃报告绝对路径、返回码（被信号杀死时翻译成信号名）、日志尾部。
- **自己停服不误报** —— 控制台的 `stop` / `restart` / `!!MCDR stop` 会被识别成主动停服。
- **崩服后推送不会丢** —— 崩溃时 MCDR 进程会立刻退出，所以通知线程刻意是非守护线程，HTTP 请求一定能跑完。
- **按类型限流** —— 崩溃循环不会刷屏，但「恢复通知」之后紧接一次崩溃仍然推得出去。
- **恢复通知** —— 宕机超过 30 秒，下次启动会告诉你宕机了多久。
- **测试命令** —— `!!crashpush test` 先验证链路，别等真崩了才发现 key 填错。

### 安装

1. 把 `crash_push.py` 放进 MCDR 的 `plugins/` 目录（和 `crash_restart.py` 放一起互不影响）。
2. 执行 `!!MCDR reload plugin`，或重启 MCDR。
3. 首次加载会生成 `config/crash_push/config.json`，改完执行 `!!crashpush reload` 生效。
4. 先跑一遍 `!!crashpush test`，一两秒内手机上应该能收到。

依赖 MCDR 2.x（在 2.16.3 上验证）。不需要任何 pip 包。

### 配置

`config/crash_push/config.json`，保存为 UTF-8（带 BOM 也能读，插件用 `utf-8-sig` 打开）。

#### Bark（iOS）

```json
{
    "bark": {
        "enabled": true,
        "server_url": "https://api.day.app",
        "device_key": "你的设备 key",
        "group": "Minecraft",
        "sound": "alarm",
        "level": "timeSensitive"
    }
}
```

`level` 可选 `active` / `timeSensitive`（会突破专注模式）/ `critical`（无视静音，需要 App 内授权）。自建 Bark 服务端就把 `server_url` 换成自己的地址。

#### Server酱

```json
{
    "serverchan": {
        "enabled": true,
        "sendkey": "SCTxxxxxxxx",
        "api": "https://sctapi.ftqq.com/{sendkey}.send"
    }
}
```

`api` 是模板，`{sendkey}` 会被替换。Turbo 用 `sctapi.ftqq.com/{sendkey}.send`；Server酱³ 有自己 send 地址，填在这里。

#### 通用 Webhook

```json
{
    "webhook": {
        "enabled": true,
        "url": "https://open.feishu.cn/open-apis/bot/v2/hook/xxxx",
        "method": "POST",
        "headers": {"Content-Type": "application/json"},
        "body_template": "{\"msg_type\": \"text\", \"content\": {\"text\": \"{title}\\n{body}\"}}"
    }
}
```

`{title}` / `{body}` 会先做 JSON 转义再替换，模板里的引号不用自己处理。

#### 其它开关

```json
{
    "notify_on_normal_stop": false,
    "notify_on_start": false,
    "log_tail_lines": 12,
    "timeout_seconds": 8,
    "rate_limit_seconds": 20
}
```

| 键 | 含义 |
| --- | --- |
| `notify_on_normal_stop` | 主动停服也推一条 |
| `notify_on_start` | 每次启动都推（默认只在上次是崩溃时推「已恢复」） |
| `log_tail_lines` | 附带多少行日志，`0` 表示不带 |
| `timeout_seconds` | HTTP 超时 |
| `rate_limit_seconds` | **同类**通知之间的最小间隔 |

完整示例见 [`examples/config.example.json`](examples/config.example.json)。

### 崩溃判定逻辑

1. `on_info` —— 日志命中 `This crash report has been saved to:` / `Crash report saved to:` / `A detailed crash report can be found at`，记 `crash_detected` 并保存报告路径。
2. `on_info` —— 命中主动停服指令（`stop`、`restart`、`!!MCDR stop` …）记 `expected_stop`，用来压掉误报。
3. `on_server_stop(server, return_code)`：
   ```python
   is_crash = crash_detected or (return_code != 0 and not expected_stop)
   ```
   `return_code` 为负表示被信号杀死（`-9` = SIGKILL，`-15` = SIGTERM），推送里会显示出来。

### 命令

| 命令 | 作用 |
| --- | --- |
| `!!crashpush test` | 异步发一条测试推送 |
| `!!crashpush reload` | 重读 `config.json` |

两者都需要 OWNER 权限（`PHYSICAL_SERVER_CONTROL_LEVEL`）。

### 踩过的坑

这几条都是实际调试撞出来的，插件里已经处理好，你不必再踩一遍：

1. **`on_server_stop` 是 `block=True` 派发的** —— HTTP 请求绝不能同步跑在回调里，否则会卡住停服流程。
2. **通知线程不能是守护线程。** 崩溃后 MCDR 进程立刻退出，`daemon=True` 的线程会被连坐杀掉：日志写着「已提交推送」，其实一个请求都没发出去。必须 `daemon=False`，用 HTTP 超时兜底。
3. **不要用 `server.is_server_running()` 做跳过判断。** `crash_restart` 监听同一个事件，如果它先把服务器拉起来，这个判断会把崩溃通知整个吞掉。应该用时间戳去重。
4. **`Literal.requires()` 的失败提示只接受 Callable**，传字符串会 `TypeError`。
5. **权限常量是 `PermissionLevel.PHYSICAL_SERVER_CONTROL_LEVEL`**，不存在 `PHYSICAL_SERVER_CONTROL`。

### 验证情况

在 MCDR 2.16.3 上用假服务端 + 本地 HTTP mock 做过端到端测试：

- 崩溃（`exit 1` + 崩溃报告行）→ 推送成功，`HTTP 200`，Bark JSON 字段正确（`device_key` / `title` / `body` / `group` / `sound` / `level` / `isArchive`），正文是崩溃报告绝对路径；
- 正常停服（`exit 0`）→ 不推送；
- `!!crashpush test` → 推送成功；
- 连续两次崩溃 → 推两条（限流只作用于 20 秒内的同类通知）。

### 和 crash_restart 一起用

两个插件都监听 `on_server_stop`，互不干扰：

- `crash_restart` 负责重启；
- `crash_push` 负责推送 —— 哪怕服务器已经在同一次事件里被拉起来，崩溃那条照样推。

### 许可

MIT，见 [LICENSE](LICENSE)。
