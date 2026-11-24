import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

from mcdreforged.api.all import *

# 崩溃标志：不同版本 / 不同 mod loader 的写法不一样，命中任意一条即视为崩溃
CRASH_MARKERS = (
	'This crash report has been saved to:',
	'Crash report saved to:',
	'A detailed crash report can be found at',
)

# 用户主动停服时的指令（用来压掉“我自己停的”这种误报）
USER_STOP_COMMANDS = ('stop', 'stop now', 'restart', '!!stop', '!!mcdr stop', '!!mcdr restart', '!!mcdr stop_exit')

SIGNALS = {
	2: 'SIGINT',
	3: 'SIGQUIT',
	6: 'SIGABRT',
	9: 'SIGKILL',
	11: 'SIGSEGV',
	15: 'SIGTERM',
}


class BarkConfig(Serializable):
	enabled: bool = False
	server_url: str = 'https://api.day.app'  # 自建 Bark 服务端改成自己的地址
	device_key: str = ''
	group: str = 'Minecraft'
	sound: str = 'alarm'
	level: str = 'timeSensitive'  # active / timeSensitive / critical / passive


class ServerChanConfig(Serializable):
	enabled: bool = False
	sendkey: str = ''
	# Server酱 Turbo：https://sctapi.ftqq.com/{sendkey}.send
	# Server酱³：把这里换成你自己的 .../send/{sendkey}.send 地址
	api: str = 'https://sctapi.ftqq.com/{sendkey}.send'


class WebhookConfig(Serializable):
	enabled: bool = False
	url: str = ''
	method: str = 'POST'
	headers: dict = {'Content-Type': 'application/json'}
	# 飞书/钉钉/企业微信/Slack 都能用，改 body_template 即可；{title} {body} 会被替换
	body_template: str = '{"title": "{title}", "body": "{body}"}'


class Config(Serializable):
	bark: BarkConfig = BarkConfig()
	serverchan: ServerChanConfig = ServerChanConfig()
	webhook: WebhookConfig = WebhookConfig()
	notify_on_normal_stop: bool = False  # 手动停服也推一条
	notify_on_start: bool = False  # 每次启动推一条
	log_tail_lines: int = 12  # 推送里附带的日志尾部行数，0 表示不带
	timeout_seconds: int = 8
	rate_limit_seconds: int = 20  # 两条推送之间的最小间隔，防崩溃循环刷屏
	test_cooldown_seconds: int = 30  # !!crashpush test 的冷却，防止连点刷推送


config: Config = Config()
crash_path: Optional[str] = None
crash_detected = False
expected_stop = False
last_notify_times = {}  # 标题 -> 上次推送时间，同类通知才做限流
last_stop_notify_ts = 0.0  # 上一次处理停服事件的时间，用于去重
last_test_ts = 0.0  # 上一次 !!crashpush test 的时间，用于冷却
state = {'last_stop_ts': None, 'last_stop_was_crash': False}


# ---------------------------------------------------------------- 推送

def _http(url: str, data: bytes, headers: dict, method: str = 'POST'):
	req = urllib.request.Request(url, data=data, headers=headers, method=method)
	with urllib.request.urlopen(req, timeout=config.timeout_seconds) as resp:
		return resp.status, resp.read().decode('utf-8', 'replace')[:300]


def _send_bark(title: str, body: str) -> str:
	cfg = config.bark
	payload = {
		'device_key': cfg.device_key,
		'title': title,
		'body': body,
		'group': cfg.group,
		'sound': cfg.sound,
		'level': cfg.level,
		'isArchive': 1,
	}
	status, text = _http(
		cfg.server_url.rstrip('/') + '/push',
		json.dumps(payload, ensure_ascii=False).encode('utf-8'),
		{'Content-Type': 'application/json; charset=utf-8'},
	)
	return 'bark http {} {}'.format(status, text)


def _send_serverchan(title: str, body: str) -> str:
	cfg = config.serverchan
	url = cfg.api.format(sendkey=cfg.sendkey)
	status, text = _http(
		url,
		urllib.parse.urlencode({'title': title, 'desp': body}).encode('utf-8'),
		{'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8'},
	)
	return 'serverchan http {} {}'.format(status, text)


def _send_webhook(title: str, body: str) -> str:
	cfg = config.webhook
	raw = cfg.body_template.replace('{title}', json.dumps(title, ensure_ascii=False)[1:-1])
	raw = raw.replace('{body}', json.dumps(body, ensure_ascii=False)[1:-1])
	status, text = _http(
		cfg.url,
		raw.encode('utf-8'),
		dict(cfg.headers),
		cfg.method.upper(),
	)
	return 'webhook http {} {}'.format(status, text)


def _notify_worker(server: PluginServerInterface, title: str, body: str):
	now = time.time()
	last = last_notify_times.get(title, 0.0)
	if now - last < config.rate_limit_seconds:
		server.logger.info('[crash_push] 同类通知 {} 距上次不足 {} 秒，跳过本次推送'.format(title, config.rate_limit_seconds))
		return
	last_notify_times[title] = now
	if config.bark.enabled and config.bark.device_key:
		try:
			server.logger.info('[crash_push] {}'.format(_send_bark(title, body)))
		except Exception as e:
			server.logger.error('[crash_push] Bark 推送失败: {!r}'.format(e))
	if config.serverchan.enabled and config.serverchan.sendkey:
		try:
			server.logger.info('[crash_push] {}'.format(_send_serverchan(title, body)))
		except Exception as e:
			server.logger.error('[crash_push] Server酱 推送失败: {!r}'.format(e))
	if config.webhook.enabled and config.webhook.url:
		try:
			server.logger.info('[crash_push] {}'.format(_send_webhook(title, body)))
		except Exception as e:
			server.logger.error('[crash_push] Webhook 推送失败: {!r}'.format(e))


def _notify(server: PluginServerInterface, title: str, body: str):
	# on_server_stop 是 block=True 派发的，HTTP 请求绝不能同步跑在这里
	threading.Thread(
		target=_notify_worker, args=(server, title, body),
		name='crash_push_notifier', daemon=False,
	).start()


# ---------------------------------------------------------------- 文本

def _read_tail(path: str, lines: int) -> list:
	try:
		with open(path, 'r', encoding='utf-8', errors='replace') as f:
			content = f.readlines()
		return [line.rstrip() for line in content[-lines:]]
	except Exception:
		return []


def _server_log_path(server: PluginServerInterface) -> Optional[str]:
	# working_directory 是相对 MCDR 运行目录的，进程 cwd 就是 MCDR 根目录
	try:
		working_dir = server.get_mcdr_config().get('working_directory', 'server')
		path = os.path.join(working_dir, 'logs', 'latest.log')
		return path if os.path.isfile(path) else None
	except Exception:
		return None


def _crash_report_path(server: PluginServerInterface) -> Optional[str]:
	# 原版日志里给的是相对服务端工作目录的路径，转成绝对路径手机上更好找
	if not crash_path:
		return None
	path = crash_path
	if not os.path.isabs(path):
		try:
			working_dir = server.get_mcdr_config().get('working_directory', 'server')
			path = os.path.abspath(os.path.normpath(os.path.join(working_dir, path)))
		except Exception:
			pass
	return path


def _build_body(server: PluginServerInterface, return_code: Optional[int]) -> str:
	parts = ['时间: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))]
	if return_code is None:
		parts.append('返回码: 未知')
	elif return_code < 0:
		parts.append('返回码: {} ({})'.format(return_code, SIGNALS.get(-return_code, 'signal')))
	else:
		parts.append('返回码: {}'.format(return_code))
	report = _crash_report_path(server)
	if report:
		parts.append('崩溃报告: {}'.format(report))
	if config.log_tail_lines > 0:
		source = report if report and os.path.isfile(report) else _server_log_path(server)
		tail = _read_tail(source, config.log_tail_lines) if source else []
		if tail:
			parts.append('')
			parts.append('最近日志:')
			parts.extend(tail)
	return '\n'.join(parts)


def _format_duration(seconds: float) -> str:
	seconds = int(seconds)
	if seconds < 60:
		return '{} 秒'.format(seconds)
	if seconds < 3600:
		return '{} 分钟'.format(seconds // 60)
	return '{} 小时 {} 分钟'.format(seconds // 3600, seconds % 3600 // 60)


# ---------------------------------------------------------------- 状态

def _state_file(server: PluginServerInterface) -> str:
	return os.path.join(server.get_data_folder(), 'state.json')


def _save_state(server: PluginServerInterface):
	try:
		with open(_state_file(server), 'w', encoding='utf-8') as f:
			json.dump(state, f)
	except Exception as e:
		server.logger.warning('[crash_push] 保存状态失败: {!r}'.format(e))


def _load_state(server: PluginServerInterface):
	try:
		with open(_state_file(server), 'r', encoding='utf-8') as f:
			data = json.load(f)
		if isinstance(data, dict):
			state.update(data)
	except Exception:
		pass


# ---------------------------------------------------------------- 事件

def on_load(server: PluginServerInterface, old):
	global config
	config = server.load_config_simple('config.json', target_class=Config, encoding='utf-8-sig')
	_load_state(server)
	server.register_help_message(
		'!!crashpush test', '发一条测试推送，验证 Bark / Server酱 配置',
		permission=PermissionLevel.ADMIN,  # 普通玩家连帮助里都看不到这条
	)
	server.register_command(
		Literal('!!crashpush').requires(
			_has_permission_to_manage,
			lambda src: RText('需要 MCDR 管理员（admin）或更高权限', RColor.red),
		).then(
			Literal('test').runs(_cmd_test)
		).then(
			Literal('reload').runs(_cmd_reload)
		)
	)
	server.logger.info('[crash_push] 已加载，bark={} serverchan={} webhook={}'.format(
		config.bark.enabled, config.serverchan.enabled, config.webhook.enabled))


def _has_permission_to_manage(source: CommandSource) -> bool:
	# 只允许 MCDR admin(3) 及以上（含 owner）；guest / user / helper 一律拒绝，
	# 否则普通玩家连点 !!crashpush test 会一直往服主手机上推消息
	return source.has_permission(PermissionLevel.ADMIN)


def _cmd_test(source: CommandSource):
	global last_test_ts
	# 命令树已经拦了一道，这里再查一次：以后改命令结构也不会把入口漏出去
	if not _has_permission_to_manage(source):
		source.reply('需要 MCDR 管理员（admin）或更高权限才能测试推送')
		return
	now = time.time()
	wait = config.test_cooldown_seconds - (now - last_test_ts)
	if wait > 0:
		source.reply('测试推送冷却中，还要等 {:.0f} 秒'.format(wait))
		return
	last_test_ts = now
	_notify(source.get_server(), 'Minecraft 崩服推送测试', '收到这条说明链路是通的。')
	source.reply('已异步发送测试推送，看手机和 MCDR 日志')



def _cmd_reload(source: CommandSource):
	_reload(source.get_server())
	source.reply('配置已重载')


def _reload(server: PluginServerInterface):
	global config
	config = server.load_config_simple('config.json', target_class=Config, encoding='utf-8-sig')
	server.logger.info('[crash_push] 配置已重载')


def on_info(server: PluginServerInterface, info: Info):
	global crash_path, crash_detected, expected_stop
	if info.is_user:
		if info.content.strip().lower() in USER_STOP_COMMANDS:
			expected_stop = True
		return
	if info.content is None:
		return
	for marker in CRASH_MARKERS:
		if marker in info.content:
			crash_detected = True
			after = info.content.split(marker, 1)[1].strip()
			if after:
				crash_path = after
			server.logger.info('[crash_push] 检测到崩溃报告: {}'.format(crash_path or '(路径未知)'))
			return


def on_server_startup(server: PluginServerInterface):
	global crash_detected, expected_stop, crash_path
	was_crash = state.get('last_stop_was_crash')
	last_ts = state.get('last_stop_ts')
	if last_ts and was_crash:
		downtime = time.time() - float(last_ts)
		if downtime > 30:
			_notify(server, 'Minecraft 服务端已恢复',
				'服务端重新启动，本次宕机约 {}。'.format(_format_duration(downtime)))
	elif config.notify_on_start:
		_notify(server, 'Minecraft 服务端已启动', '启动时间: {}'.format(datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
	crash_detected = False
	expected_stop = False
	crash_path = None
	state['last_stop_ts'] = None
	state['last_stop_was_crash'] = False
	_save_state(server)


def on_server_stop(server: PluginServerInterface, return_code: int):
	global crash_detected, expected_stop, crash_path, last_stop_notify_ts
	now = time.time()
	# 同一次停服被重复派发时只推一次；注意不要用 is_server_running() 判断，
	# crash_restart 可能在同一次事件里已经把服务器拉起来，那样会漏掉崩溃通知
	if now - last_stop_notify_ts < 2.0:
		return
	is_crash = crash_detected or (return_code != 0 and not expected_stop)
	if not is_crash and not config.notify_on_normal_stop:
		crash_detected = False
		expected_stop = False
		return
	last_stop_notify_ts = now
	state['last_stop_ts'] = now
	state['last_stop_was_crash'] = bool(is_crash)
	_save_state(server)
	title = 'Minecraft 服务端崩溃' if is_crash else 'Minecraft 服务端已停止'
	_notify(server, title, _build_body(server, return_code))
	server.logger.info('[crash_push] 已提交推送: {} (return_code={}, crash_report={})'.format(
		title, return_code, bool(crash_detected)))
	crash_detected = False
	expected_stop = False
	crash_path = None
