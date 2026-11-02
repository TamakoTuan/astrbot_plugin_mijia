"""AstrBot 米家插件：在 QQ / 其他平台里查询与控制米家设备。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools, register

from .mijia_service import Device, MijiaError, MijiaService, NeedLoginError

PLUGIN_NAME = "astrbot_plugin_mijia"
VERSION = "1.0.0"

HELP_TEXT = """🏠 米家智能家居

【账号】
/米家 登录　　　扫码登录米家账号
/米家 状态　　　查看登录与设备状态
/米家 刷新　　　重新拉取设备/场景列表
/米家 退出登录　清除本机保存的登录信息

【查看】
/米家 设备 [关键词]　列出设备
/米家 详情 <设备>　　查看可读/可控属性
/米家 状态 <设备>　　读取设备当前状态
/米家 场景　　　　　 列出手动场景

【控制】
/米家 开 <设备>
/米家 关 <设备>
/米家 设置 <设备> <属性> <值>
/米家 动作 <设备> <动作>
/米家 执行 <场景名>

【其他】
/米家 确认 / 取消　　高危设备的二次确认
/米家 帮助

也可以直接对我说：「把客厅灯调暗一点」「空调开到 26 度」。"""


COMMAND_NAMES = {"米家", "mj", "mijia", "/米家", "/mj", "/mijia"}


def _strip_command(text: str) -> str:
    """去掉消息开头的指令词，返回参数部分。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    head, _, rest = raw.partition(" ")
    head = head.strip()
    if head.lower() in COMMAND_NAMES or head in COMMAND_NAMES:
        return rest.strip()
    return raw


@register(
    PLUGIN_NAME,
    "DSH",
    "通过米家云 API 在聊天中查询与控制米家智能家居设备。",
    f"v{VERSION}",
)
class MijiaPlugin(Star):
    CONFIRM_TTL = 120

    def __init__(self, context: Context, config=None):
        super().__init__(context)
        self.config = config
        self.data_dir: Path = StarTools.get_data_dir(PLUGIN_NAME)
        self.service = MijiaService(self.data_dir, config)
        self._login_lock = asyncio.Lock()
        self._pending: dict[str, dict] = {}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------------ 工具
    def _conf(self, key: str, default=None):
        if self.config is None:
            return default
        try:
            value = self.config.get(key, default)
        except Exception:  # noqa: BLE001
            return default
        return default if value is None else value

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        if not self._conf("admin_only", True):
            return True
        try:
            admins = getattr(self.context.get_config(), "admins_id", None) or []
        except Exception:  # noqa: BLE001
            admins = []
        return str(event.get_sender_id()) in {str(item) for item in admins}

    def _guard(self, event: AstrMessageEvent) -> str | None:
        if not self._is_admin(event):
            return "🚫 米家控制只对机器人管理员开放。"
        if self._conf("private_only", True) and not event.is_private_chat():
            return "🚫 为了安全，米家控制只允许在私聊中使用。"
        return None

    def _is_dangerous(self, device_name: str) -> bool:
        raw = str(self._conf("danger_keywords", "") or "")
        keywords = [
            item.strip()
            for item in raw.replace("，", ",").replace("、", ",").split(",")
            if item.strip()
        ]
        return any(keyword in device_name for keyword in keywords)

    def _save_qr(self, png: bytes) -> str:
        path = self.data_dir / "login_qr.png"
        path.write_bytes(png)
        return str(path)

    def _remember_task(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------- 指令入口
    @filter.command("米家", alias={"mj", "mijia"})
    async def mijia(self, event: AstrMessageEvent):
        """米家智能家居控制入口。"""
        denied = self._guard(event)
        if denied:
            yield event.plain_result(denied)
            return

        args = _strip_command(event.message_str)
        if not args:
            yield event.plain_result(HELP_TEXT)
            return

        head, _, rest = args.partition(" ")
        head = head.strip()
        rest = rest.strip()

        try:
            if head in {"帮助", "help", "?", "？"}:
                yield event.plain_result(HELP_TEXT)
            elif head in {"登录", "login"}:
                async for result in self._cmd_login(event):
                    yield result
            elif head in {"退出", "注销", "登出", "logout"}:
                self.service.logout()
                yield event.plain_result("✅ 已清除本机保存的米家登录信息。")
            elif head in {"状态", "status"} and not rest:
                yield event.plain_result(await self._status_text())
            elif head in {"设备", "列表", "list", "devices"}:
                yield event.plain_result(await self._cmd_devices(rest, refresh=False))
            elif head in {"刷新", "refresh"}:
                yield event.plain_result(await self._cmd_devices(rest, refresh=True))
            elif head in {"详情", "info"}:
                yield event.plain_result(await self._cmd_detail(rest))
            elif head in {"查询", "状态", "query", "status"}:
                yield event.plain_result(await self._cmd_query(rest))
            elif head in {"开", "打开", "on"}:
                yield event.plain_result(await self._cmd_switch(event, rest, True))
            elif head in {"关", "关闭", "off"}:
                yield event.plain_result(await self._cmd_switch(event, rest, False))
            elif head in {"设置", "set"}:
                yield event.plain_result(await self._cmd_set(event, rest))
            elif head in {"动作", "action"}:
                yield event.plain_result(await self._cmd_action(rest))
            elif head in {"场景", "scenes"}:
                yield event.plain_result(await self._cmd_scenes())
            elif head in {"执行", "run"}:
                yield event.plain_result(await self._cmd_run_scene(rest))
            elif head in {"确认", "confirm", "y"}:
                yield event.plain_result(await self._cmd_confirm(event))
            elif head in {"取消", "cancel", "n"}:
                self._pending.pop(str(event.unified_msg_origin), None)
                yield event.plain_result("已取消。")
            else:
                yield event.plain_result(f"未知的子指令「{head}」。\n\n{HELP_TEXT}")
        except NeedLoginError as exc:
            yield event.plain_result(f"🔐 {exc}")
        except MijiaError as exc:
            yield event.plain_result(f"❌ {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.exception("米家指令执行失败")
            yield event.plain_result(f"❌ 出错了：{exc}")

    # ------------------------------------------------------------------ 登录
    async def _cmd_login(self, event: AstrMessageEvent):
        if self._login_lock.locked():
            yield event.plain_result("⏳ 已经有一个扫码登录在进行中，请先在米家 App 里完成它。")
            return

        async with self._login_lock:
            try:
                png, login_data = await self.service.prepare_login()
            except MijiaError as exc:
                yield event.plain_result(f"❌ {exc}")
                return

            if png is None:
                yield event.plain_result("✅ 米家登录状态仍然有效，无需重新扫码。")
                return

            yield event.image_result(self._save_qr(png))
            yield event.plain_result(
                "请用「米家」App → 我的 → 右上角扫一扫 扫描上面的二维码。\n"
                "扫码后请在手机上确认登录，有效期约 2 分钟。"
            )

            umo = str(event.unified_msg_origin)
            task = asyncio.create_task(self._finish_login(login_data, umo))
            self._remember_task(task)

    async def _finish_login(self, login_data: dict, umo: str) -> None:
        message = ""
        try:
            await self.service.complete_login(login_data)
            message = (
                "✅ 米家登录成功，可以开始控制设备了。"
                "发送「/米家 设备」看看有哪些设备。"
            )
        except MijiaError as exc:
            message = f"❌ 登录失败：{exc}"
        except Exception as exc:  # noqa: BLE001
            logger.exception("米家登录失败")
            message = f"❌ 登录失败：{exc}"
        try:
            await self.context.send_message(umo, MessageChain([Plain(message)]))
        except Exception:  # noqa: BLE001
            logger.exception("推送米家登录结果失败")

    async def _status_text(self) -> str:
        lines = ["🏠 米家状态"]
        if not self.service.logged_in:
            lines.append("登录：未登录（发送「/米家 登录」）")
            return "\n".join(lines)
        lines.append("登录：已保存凭证")
        try:
            await self.service.ensure_login()
            lines.append("云端校验：正常")
        except MijiaError as exc:
            lines.append(f"云端校验：异常（{exc}）")
            return "\n".join(lines)
        try:
            devices = await self.service.devices()
            scenes = await self.service.scenes()
            homes = await self.service.homes()
            lines.append(f"家庭：{len(homes)} 个")
            lines.append(f"设备：{len(devices)} 个")
            lines.append(f"手动场景：{len(scenes)} 个")
        except MijiaError as exc:
            lines.append(f"拉取数据失败：{exc}")
        return "\n".join(lines)

    # ------------------------------------------------------------------ 查询
    async def _cmd_devices(self, keyword: str, refresh: bool) -> str:
        await self.service.ensure_login()
        devices = await self.service.devices(refresh=refresh)
        await self.service.homes(refresh=refresh)
        return self.service.format_devices(devices, keyword)

    async def _cmd_detail(self, name: str) -> str:
        if not name:
            return "用法：/米家 详情 <设备名>"
        await self.service.ensure_login()
        await self.service.devices()
        handle = await self.service.device(self.service.resolve(name))
        return handle.describe()

    async def _cmd_query(self, args: str) -> str:
        if not args:
            return "用法：/米家 状态 <设备名> [属性]"
        name, _, prop = args.partition(" ")
        await self.service.ensure_login()
        await self.service.devices()
        handle = await self.service.device(self.service.resolve(name))
        prop = prop.strip()

        if prop:
            target = handle.prop(prop)
            rows = await handle.snapshot(self.service)
            for item_name, value in rows:
                if item_name == target["name"]:
                    return f"📊 {handle.name} · {item_name} = {value}"
            return f"「{handle.name}」的 {target['name']} 当前不可读取。"

        rows = await handle.snapshot(self.service)
        return self.service.format_snapshot(handle, rows)

    # ------------------------------------------------------------------ 控制
    async def _cmd_switch(self, event: AstrMessageEvent, name: str, turn_on: bool) -> str:
        if not name:
            return f"用法：/米家 {'开' if turn_on else '关'} <设备名>"
        return await self._apply(event, name, "on", turn_on)

    async def _cmd_set(self, event: AstrMessageEvent, args: str) -> str:
        parts = args.split()
        if len(parts) < 3:
            return "用法：/米家 设置 <设备名> <属性> <值>\n例如：/米家 设置 客厅灯 brightness 60"
        name = parts[0]
        prop = parts[1]
        value = " ".join(parts[2:])
        return await self._apply(event, name, prop, value)

    async def _cmd_action(self, args: str) -> str:
        parts = args.split()
        if len(parts) < 2:
            return "用法：/米家 动作 <设备名> <动作名>"
        await self.service.ensure_login()
        await self.service.devices()
        handle = await self.service.device(self.service.resolve(parts[0]))
        return await handle.run_action(self.service, parts[1])

    async def _apply(
        self, event: AstrMessageEvent, name: str, prop: str, value
    ) -> str:
        await self.service.ensure_login()
        await self.service.devices()
        device = self.service.resolve(name)

        if self._is_dangerous(str(device.get("name", ""))):
            key = str(event.unified_msg_origin)
            self._pending[key] = {
                "device": str(device.get("name")),
                "prop": prop,
                "value": value,
                "expire": time.time() + self.CONFIRM_TTL,
            }
            return (
                f"⚠️「{device.get('name')}」属于高敏感设备，需要二次确认。\n"
                f"即将执行：{prop} = {value}\n"
                f"{self.CONFIRM_TTL} 秒内发送「/米家 确认」执行，或「/米家 取消」放弃。"
            )

        handle = await self.service.device(device)
        return await handle.set_prop(self.service, prop, value)

    async def _cmd_confirm(self, event: AstrMessageEvent) -> str:
        key = str(event.unified_msg_origin)
        pending = self._pending.pop(key, None)
        if not pending:
            return "没有等待确认的操作。"
        if pending["expire"] < time.time():
            return "⌛ 确认已超时，请重新发起操作。"

        await self.service.ensure_login()
        await self.service.devices()
        handle = await self.service.device(
            self.service.resolve(str(pending["device"]))
        )
        return await handle.set_prop(self.service, pending["prop"], pending["value"])

    # ------------------------------------------------------------------ 场景
    async def _cmd_scenes(self) -> str:
        await self.service.ensure_login()
        scenes = await self.service.scenes()
        if not scenes:
            return "没有找到手动场景。可以在米家 App → 智能 → 添加手动控制 里创建。"
        await self.service.homes()
        lines = ["🎬 手动场景"]
        for scene in scenes:
            lines.append(f"· {scene.get('name')}")
        lines.append("\n执行：/米家 执行 <场景名>")
        return "\n".join(lines)

    async def _cmd_run_scene(self, name: str) -> str:
        if not name:
            return "用法：/米家 执行 <场景名>"
        await self.service.ensure_login()
        scenes = await self.service.scenes()
        if not scenes:
            return "没有可执行的手动场景。"

        target = None
        lowered = name.casefold()
        for scene in scenes:
            if str(scene.get("name", "")).casefold() == lowered:
                target = scene
                break
        if target is None:
            partial = [
                s for s in scenes if lowered in str(s.get("name", "")).casefold()
            ]
            if len(partial) == 1:
                target = partial[0]
        if target is None:
            names = "、".join(str(s.get("name")) for s in scenes)
            return f"没找到场景「{name}」。现有场景：{names}"

        ok = await self.service.call(
            self.service.api.run_scene,
            str(target.get("scene_id")),
            str(target.get("home_id")),
            timeout=30,
        )
        if ok is False:
            return f"❌ 场景「{target.get('name')}」执行失败。"
        return f"✅ 已执行场景：{target.get('name')}"

    # -------------------------------------------------------------- LLM 工具
    def _tool_guard(self, event: AstrMessageEvent) -> str | None:
        return self._guard(event)

    @filter.llm_tool(name="mijia_list_devices")
    async def tool_list_devices(self, event: AstrMessageEvent, keyword: str = ""):
        """列出用户家里的米家智能家居设备，用于确认设备名称。

        Args:
            keyword(string): 可选，按设备名称过滤的关键词，比如「灯」「空调」
        """
        denied = self._tool_guard(event)
        if denied:
            return denied
        try:
            await self.service.ensure_login()
            devices = await self.service.devices()
            return self.service.format_devices(devices, keyword)
        except MijiaError as exc:
            return f"查询失败：{exc}"

    @filter.llm_tool(name="mijia_device_status")
    async def tool_device_status(self, event: AstrMessageEvent, device: str):
        """读取某个米家设备的当前状态（开关、亮度、温度等）。

        Args:
            device(string): 设备名称，尽量使用设备列表里的完整名称
        """
        denied = self._tool_guard(event)
        if denied:
            return denied
        try:
            await self.service.ensure_login()
            await self.service.devices()
            handle = await self.service.device(self.service.resolve(device))
            rows = await handle.snapshot(self.service)
            return self.service.format_snapshot(handle, rows)
        except MijiaError as exc:
            return f"读取失败：{exc}"

    @filter.llm_tool(name="mijia_control_device")
    async def tool_control_device(
        self,
        event: AstrMessageEvent,
        device: str,
        property: str,
        value: str,
    ):
        """控制米家设备：开关或设置属性，例如把灯打开、把亮度设为 60、把空调设为 26 度。

        Args:
            device(string): 设备名称，尽量使用设备列表里的完整名称
            property(string): 属性名，开关用 on，亮度用 brightness，温度一般用 target-temperature
            value(string): 目标值，开/关可写 true/false，数值直接写数字
        """
        denied = self._tool_guard(event)
        if denied:
            return denied
        try:
            await self.service.ensure_login()
            await self.service.devices()
            dev = self.service.resolve(device)
            if self._is_dangerous(str(dev.get("name", ""))):
                return (
                    f"「{dev.get('name')}」属于高敏感设备，出于安全考虑不能由我直接操作，"
                    "请让用户手动发送 /米家 设置 指令并二次确认。"
                )
            handle = await self.service.device(dev)
            return await handle.set_prop(self.service, property, value)
        except MijiaError as exc:
            return f"操作失败：{exc}"

    @filter.llm_tool(name="mijia_run_scene")
    async def tool_run_scene(self, event: AstrMessageEvent, scene: str):
        """执行米家的手动场景，比如「回家模式」「晚安」。

        Args:
            scene(string): 场景名称
        """
        denied = self._tool_guard(event)
        if denied:
            return denied
        try:
            return await self._cmd_run_scene(scene)
        except MijiaError as exc:
            return f"执行失败：{exc}"

    # ------------------------------------------------------------------ 收尾
    async def terminate(self):
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
