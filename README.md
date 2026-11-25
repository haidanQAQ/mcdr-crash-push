# crash_push

服务端崩了，手机响一声。

MCDReforged 插件。服务端非正常退出时，往 Bark / Server酱 / 自定义 Webhook 推一条消息，说明时间、返回码、崩溃报告在哪、日志最后几行是什么。重启交给 crash_restart 之类的插件，这里只负责通知。

需要 MCDReforged 2.0 或更高版本。

## 安装

从 [Releases](https://github.com/haidanQAQ/mcdr-crash-push/releases) 下载 `.mcdr` 放进 `plugins`，然后执行：

```text
!!MCDR plugin reload crash_push
```

首次加载会生成 `config/crash_push/config.json`，三个通道默认都是关的，填好一个再执行 `!!crashpush reload` 即可。插件被目录站收录后，也可以直接 `!!MCDR plugin install crash_push`。

## 配置

开一个通道就够了。Bark 最少填两行：

```json
{
	"bark": {
		"enabled": true,
		"device_key": "你的设备 key"
	}
}
```

Server酱：

```json
{
	"serverchan": {
		"enabled": true,
		"sendkey": "SCT..."
	}
}
```

飞书、钉钉、企业微信这类有自定义机器人的，用 webhook 通道，把 `body_template` 改成对方要求的格式就行，`{title}` 和 `{body}` 会被替换。

全部字段：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `bark.enabled` | `false` | 开启 Bark 通道 |
| `bark.server_url` | `https://api.day.app` | 自建 Bark 服务端改成自己的地址 |
| `bark.device_key` | 空 | Bark App 里那串 key |
| `bark.group` | `Minecraft` | 通知分组 |
| `bark.sound` | `alarm` | 提示音 |
| `bark.level` | `timeSensitive` | `active` / `timeSensitive` / `critical` / `passive`，`critical` 能穿透静音和专注模式 |
| `serverchan.enabled` | `false` | 开启 Server酱通道 |
| `serverchan.sendkey` | 空 | Server酱的 SendKey |
| `serverchan.api` | `https://sctapi.ftqq.com/{sendkey}.send` | Server酱³ 的接口不同，换成自己的地址模板 |
| `webhook.enabled` | `false` | 开启通用 Webhook |
| `webhook.url` | 空 | 目标地址 |
| `webhook.method` | `POST` | 请求方法 |
| `webhook.headers` | `Content-Type: application/json` | 请求头 |
| `webhook.body_template` | `{"title": "{title}", "body": "{body}"}` | 请求体模板 |
| `notify_on_normal_stop` | `false` | 手动停服也推一条 |
| `notify_on_start` | `false` | 每次启动都推一条 |
| `log_tail_lines` | `12` | 推送里带几行日志，`0` 表示不带 |
| `timeout_seconds` | `8` | 单次 HTTP 超时 |
| `rate_limit_seconds` | `20` | 同类通知之间的最小间隔 |
| `test_cooldown_seconds` | `30` | `!!crashpush test` 的冷却 |

## 命令

两个命令都要求 MCDR 管理员（admin）或更高权限，普通玩家的 `!!help` 里也不会显示。

| 命令 | 说明 |
| --- | --- |
| `!!crashpush test` | 发一条测试推送，验证配置通不通 |
| `!!crashpush reload` | 重新读取配置，不用重载插件 |

## 什么时候会推

- 进程返回码不是 0
- 日志里出现崩溃报告保存的提示，原版和部分 mod loader 的写法不同，常见三种都认
- 被信号杀死时返回码是负数，通知里会写出 `SIGSEGV` 之类的名字
- 玩家自己敲 `stop`、`!!MCDR stop`、`restart` 时不推，除非打开 `notify_on_normal_stop`
- 上次是崩溃、这次启动成功时，补一条「已恢复，本次宕机约 X」，宕机超过 30 秒才补；关掉 `notify_on_start` 也照推

推送内容形如：

```text
时间: 2026-11-25 17:40:12
返回码: 1
崩溃报告: /Volumes/KP230Pro_1T/MinecraftProject/server/crash-reports/crash-2026-11-25_17.40.12-server.txt

最近日志:
...最后 12 行...
```

## 说明

推送在后台线程里发，不会卡住停服流程；HTTP 超时由 `timeout_seconds` 控制，`rate_limit_seconds` 用来防止崩溃循环把手机刷爆。

崩溃报告如果是相对路径，会按 MCDR 的 `working_directory` 拼成绝对路径再发，方便直接去翻文件。

和 crash_restart 一起装没问题：它负责重启，本插件负责通知。本插件不会因为「服务器已经被拉起来了」就跳过推送，两个插件同时工作时也不会漏消息。

## 许可

MIT，见 [LICENSE](LICENSE)。
