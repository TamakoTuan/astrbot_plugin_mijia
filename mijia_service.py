"""米家云 API 封装。

对 mijiaAPI（https://github.com/Do1e/mijia-api）做一层薄封装：
- 全部同步请求丢到线程池，避免阻塞 AstrBot 事件循环
- 统一错误信息（中文、可直接发到聊天里）
- 设备/场景列表缓存，设备属性按 spec 做类型校验
"""

from __future__ import annotations

import asyncio
import difflib
import io
import json
import time
from pathlib import Path
from typing import Any


class MijiaError(Exception):
    """可以直接展示给用户的错误。"""


class NeedLoginError(MijiaError):
    """登录态缺失或过期。"""


def _import_module():
    try:
        import mijiaAPI
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
        raise MijiaError(
            "缺少依赖 mijiaAPI，请重启 AstrBot 让插件自动安装，"
            "或手动执行：pip install mijiaAPI"
        ) from exc
    return mijiaAPI


_TRUE_WORDS = {"开", "打开", "开启", "启动", "true", "on", "1", "是", "yes"}
_FALSE_WORDS = {"关", "关闭", "关掉", "停止", "false", "off", "0", "否", "no"}


class Device:
    """单个米家设备（按 spec 缓存读写能力）。"""

    def __init__(self, api, dev: dict, spec: dict):
        self.api = api
        self.did = str(dev["did"])
        self.name = str(dev.get("name") or "未命名设备")
        self.model = str(dev.get("model") or "")
        self.spec_type = str(dev.get("spec_type") or "")
        self.raw = dev
        self.spec_name = str(spec.get("name") or "")

        self.props: dict[str, dict] = {}
        for prop in spec.get("properties", []):
            self.props[prop["name"]] = prop
            if "-" in prop["name"]:
                self.props[prop["name"].replace("-", "_")] = prop

        self.actions: dict[str, dict] = {}
        for act in spec.get("actions", []):
            self.actions[act["name"]] = act
            if "-" in act["name"]:
                self.actions[act["name"].replace("-", "_")] = act

    # ---- 展示 ----
    @property
    def online(self) -> bool | None:
        for key in ("isOnline", "online", "is_online"):
            if key in self.raw:
                return bool(self.raw[key])
        return None

    def prop(self, name: str) -> dict:
        key = (name or "").strip()
        if key in self.props:
            return self.props[key]
        lowered = key.lower()
        for candidate, prop in self.props.items():
            if candidate.lower() == lowered:
                return prop
        raise MijiaError(
            f"「{self.name}」没有属性 {name}。可用属性：{self.prop_names()}"
        )

    def prop_names(self, mode: str = "all") -> str:
        seen: list[str] = []
        for name, prop in self.props.items():
            if "-" in name or "_" in name:
                continue
            rw = prop.get("rw", "")
            if mode == "readable" and "r" not in rw:
                continue
            if mode == "writable" and "w" not in rw:
                continue
            seen.append(name)
        return "、".join(seen) if seen else "（无）"

    @staticmethod
    def describe_prop(prop: dict) -> str:
        units = {
            "bool": "开/关",
            "int": "整数",
            "uint": "正整数",
            "float": "数值",
            "string": "文本",
        }
        parts = [f"{prop['name']}（{units.get(prop.get('type'), prop.get('type'))}"]
        if prop.get("range"):
            rng = prop["range"]
            parts.append(f"，{rng[0]}~{rng[1]}")
        if prop.get("value-list"):
            mapping = "，".join(
                f"{item['value']}={item.get('desc_zh_cn') or item['description']}"
                for item in prop["value-list"]
            )
            parts.append(f"，可选：{mapping}")
        parts.append("）")
        return "".join(parts)

    def describe(self) -> str:
        lines = [f"📦 {self.name}"]
        if self.model:
            lines.append(f"型号：{self.model}")
        if self.online is not None:
            lines.append("在线：是" if self.online else "在线：否")

        readable = [
            self.describe_prop(p)
            for name, p in self.props.items()
            if "-" not in name and "_" not in name and "r" in p.get("rw", "")
        ]
        writable = [
            self.describe_prop(p)
            for name, p in self.props.items()
            if "-" not in name and "_" not in name and "w" in p.get("rw", "")
        ]
        if readable:
            lines.append("可读属性：" + "；".join(readable))
        if writable:
            lines.append("可控属性：" + "；".join(writable))
        if self.actions:
            acts = "、".join(
                a["name"] for n, a in self.actions.items() if "-" not in n and "_" not in n
            )
            lines.append(f"可执行动作：{acts}")
        return "\n".join(lines)

    @staticmethod
    def _options_hint(options: list[dict]) -> str:
        return "，".join(
            f"{item.get('desc_zh_cn') or item['description']}={item['value']}"
            for item in options
        )

    # ---- 读写 ----
    def _coerce(self, prop: dict, value: Any) -> Any:
        ptype = prop.get("type")
        text = value.strip() if isinstance(value, str) else value
        options = prop.get("value-list") or []

        # 枚举属性优先按中文/英文描述匹配，例如 mode="制冷"
        if options and isinstance(text, str) and text:
            for item in options:
                labels = {
                    str(item.get("desc_zh_cn") or "").strip(),
                    str(item.get("description") or "").strip(),
                }
                if text in labels:
                    return item["value"]

        if ptype == "bool":
            if isinstance(text, bool):
                return text
            low = str(text).strip().lower()
            if low in _TRUE_WORDS:
                return True
            if low in _FALSE_WORDS:
                return False
            raise MijiaError(f"{prop['name']} 只接受 开/关")

        if ptype in ("int", "uint"):
            try:
                num: Any = int(float(str(text)))
            except (TypeError, ValueError) as exc:
                if options:
                    raise MijiaError(
                        f"{prop['name']} 取值无效，可选：{self._options_hint(options)}"
                    ) from exc
                raise MijiaError(f"{prop['name']} 需要一个整数，收到「{text}」") from exc
        elif ptype == "float":
            try:
                num = float(str(text))
            except (TypeError, ValueError) as exc:
                if options:
                    raise MijiaError(
                        f"{prop['name']} 取值无效，可选：{self._options_hint(options)}"
                    ) from exc
                raise MijiaError(f"{prop['name']} 需要一个数字，收到「{text}」") from exc
        elif ptype == "string":
            num = str(text)
        else:
            raise MijiaError(f"不支持的属性类型：{ptype}")

        if options:
            if num in [item["value"] for item in options]:
                return num
            mapping = "，".join(
                f"{item.get('desc_zh_cn') or item['description']}={item['value']}"
                for item in options
            )
            raise MijiaError(f"{prop['name']} 取值无效，可选：{mapping}")

        rng = prop.get("range")
        if rng and ptype in ("int", "uint", "float"):
            low, high = rng[0], rng[1]
            if num < low or num > high:
                raise MijiaError(f"{prop['name']} 超出范围，应在 {low}~{high} 之间")
        return num

    async def set_prop(self, service: "MijiaService", name: str, value: Any) -> str:
        prop = self.prop(name)
        if "w" not in prop.get("rw", ""):
            raise MijiaError(
                f"「{self.name}」的 {prop['name']} 是只读属性。"
                f"可控属性：{self.prop_names('writable')}"
            )
        cast = self._coerce(prop, value)
        payload = dict(prop["method"])
        payload["did"] = self.did
        payload["value"] = cast
        result = await service.call(self.api.set_devices_prop, payload)
        code = 0
        if isinstance(result, dict):
            code = int(result.get("code", 0))
        if code == 1:
            return f"✅ 指令已下发到网关：{self.name} · {prop['name']} = {cast}（网关不回报执行结果）"
        if code != 0:
            raise MijiaError(f"设置失败，错误码 {code}")
        return f"✅ 已设置：{self.name} · {prop['name']} = {cast}"

    async def run_action(self, service: "MijiaService", name: str) -> str:
        key = (name or "").strip()
        act = self.actions.get(key)
        if act is None:
            lowered = key.lower()
            for candidate, item in self.actions.items():
                if candidate.lower() == lowered:
                    act = item
                    break
        if act is None:
            raise MijiaError(
                f"「{self.name}」没有动作 {name}。可用动作："
                + ("、".join(self.actions) or "（无）")
            )
        payload = dict(act["method"])
        payload["did"] = self.did
        result = await service.call(self.api.run_action, payload)
        code = 0
        if isinstance(result, dict):
            code = int(result.get("code", 0))
        if code == 1:
            return f"✅ 动作已下发：{self.name} · {act['name']}"
        if code != 0:
            raise MijiaError(f"执行失败，错误码 {code}")
        return f"✅ 已执行：{self.name} · {act['name']}"

    async def snapshot(self, service: "MijiaService") -> list[tuple[str, Any]]:
        """一次性批量读取所有可读属性。"""
        payload = []
        names: list[str] = []
        for name, prop in self.props.items():
            if "-" in name or "_" in name:
                continue
            if "r" not in prop.get("rw", ""):
                continue
            item = dict(prop["method"])
            item["did"] = self.did
            payload.append(item)
            names.append(name)
        if not payload:
            return []
        result = await service.call(self.api.get_devices_prop, payload)
        if isinstance(result, dict):
            result = [result]
        out: list[tuple[str, Any]] = []
        for name, item in zip(names, result or []):
            if not isinstance(item, dict):
                continue
            if int(item.get("code", 0)) != 0:
                continue
            out.append((name, self._prettify(self.props[name], item.get("value"))))
        return out

    def _prettify(self, prop: dict, value: Any) -> Any:
        for item in prop.get("value-list") or []:
            if item["value"] == value:
                return f"{item.get('desc_zh_cn') or item['description']}（{value}）"
        if isinstance(value, bool):
            return "开" if value else "关"
        return value


class MijiaService:
    DEVICE_TTL = 300
    SCENE_TTL = 300

    def __init__(self, data_dir: Path, config: Any = None):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.auth_path = self.data_dir / "auth.json"
        self.config = config
        self._api = None
        self._lock = asyncio.Lock()
        self._devices: list[dict] | None = None
        self._devices_ts = 0.0
        self._homes: list[dict] | None = None
        self._scenes: list[dict] | None = None
        self._scenes_ts = 0.0
        self._handles: dict[str, Device] = {}

    # ---------- 基础 ----------
    @property
    def api(self):
        if self._api is None:
            module = _import_module()
            self._api = module.mijiaAPI(str(self.auth_path))
        return self._api

    @property
    def logged_in(self) -> bool:
        if not self.auth_path.exists():
            return False
        try:
            data = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        required = ("serviceToken", "ssecurity", "userId", "cUserId", "passToken")
        return all(key in data for key in required)

    async def call(self, fn, *args, timeout: float = 30.0, **kwargs):
        async with self._lock:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(fn, *args, **kwargs), timeout=timeout
                )
            except asyncio.TimeoutError as exc:
                raise MijiaError(
                    f"米家云端响应超时（{timeout:.0f} 秒），请稍后重试"
                ) from exc
            except MijiaError:
                raise
            except Exception as exc:  # noqa: BLE001
                raise self._map_error(exc) from exc

    @staticmethod
    def _map_error(exc: Exception) -> MijiaError:
        name = type(exc).__name__
        text = str(exc)
        lowered = text.lower()
        if "LoginError" in name or "登录" in text or "auth" in lowered:
            return NeedLoginError(
                "米家登录状态已失效，请发送「/米家 登录」重新扫码。"
            )
        if "DeviceNotFound" in name:
            return MijiaError(f"米家返回：找不到该设备（{text}）")
        if "MultipleDevices" in name:
            return MijiaError("有多个同名设备，请先在米家 App 里改名区分。")
        if "GetDeviceInfo" in name:
            return MijiaError("获取设备规格失败（miot-spec.org 可能不可达），请稍后再试。")
        return MijiaError(f"米家接口出错：{text or name}")

    async def ensure_login(self) -> None:
        if not self.logged_in:
            raise NeedLoginError("还没有登录米家，请先发送「/米家 登录」扫码。")
        try:
            ok = await self.call(lambda: self.api.available, timeout=25)
        except MijiaError as exc:
            raise NeedLoginError(str(exc)) from exc
        if not ok:
            raise NeedLoginError("米家登录状态已过期，请发送「/米家 登录」重新扫码。")

    # ---------- 登录 ----------
    async def prepare_login(self) -> tuple[bytes | None, dict | None]:
        """返回 (二维码 PNG, 登录数据)；若 token 仍有效则 PNG 为 None。"""
        api = self.api
        getter = getattr(api, "_get_qr_login_data", None)
        if getter is None:  # 兼容旧版本
            raise MijiaError(
                "当前 mijiaAPI 版本不支持在聊天里扫码登录，请升级：pip install -U mijiaAPI"
            )
        data = await self.call(getter, timeout=30)
        if data.get("refreshed"):
            self._api = None
            return None, None
        png = self._render_qr(data["loginUrl"])
        return png, data

    async def complete_login(self, login_data: dict, timeout: float = 180.0) -> None:
        api = self.api
        await self.call(api._complete_qr_login, login_data, timeout=timeout)
        self._api = None
        self._devices = None
        self._handles.clear()

    @staticmethod
    def _render_qr(text: str) -> bytes:
        import qrcode

        qr = qrcode.QRCode(border=2, box_size=9)
        qr.add_data(text)
        qr.make(fit=True)
        image = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def logout(self) -> None:
        for path in (self.auth_path, self.auth_path.with_suffix(".json.bak")):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise MijiaError(f"删除登录信息失败：{exc}") from exc
        self._api = None
        self._devices = None
        self._scenes = None
        self._homes = None
        self._handles.clear()

    # ---------- 数据 ----------
    async def homes(self, refresh: bool = False) -> list[dict]:
        if refresh or self._homes is None:
            self._homes = await self.call(self.api.get_homes_list, timeout=40)
        return self._homes or []

    async def devices(self, refresh: bool = False) -> list[dict]:
        if (
            refresh
            or self._devices is None
            or time.time() - self._devices_ts > self.DEVICE_TTL
        ):
            devices = await self.call(self.api.get_devices_list, timeout=60)
            self._devices = [
                d for d in devices if isinstance(d, dict) and d.get("did")
            ]
            self._devices_ts = time.time()
        return self._devices or []

    async def scenes(self, refresh: bool = False) -> list[dict]:
        if (
            refresh
            or self._scenes is None
            or time.time() - self._scenes_ts > self.SCENE_TTL
        ):
            self._scenes = await self.call(self.api.get_scenes_list, timeout=40)
            self._scenes_ts = time.time()
        return self._scenes or []

    # ---------- 解析 ----------
    def _aliases(self) -> dict[str, str]:
        raw = ""
        if self.config is not None:
            raw = str(self.config.get("device_aliases", "") or "")
        mapping: dict[str, str] = {}
        for line in raw.replace("；", "\n").replace(";", "\n").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("=", "＝", ":", "："):
                if sep in line:
                    alias, _, target = line.partition(sep)
                    if alias.strip() and target.strip():
                        mapping[alias.strip()] = target.strip()
                    break
        return mapping

    def resolve(self, query: str) -> dict:
        text = (query or "").strip()
        if not text:
            raise MijiaError("请告诉我要操作哪个设备，例如：客厅灯")
        text = self._aliases().get(text, text)
        devices = self._devices or []
        if not devices:
            raise MijiaError("设备列表为空，请先发送「/米家 刷新」。")

        lowered = text.casefold()
        for dev in devices:
            if str(dev.get("name", "")).casefold() == lowered:
                return dev

        partial = [d for d in devices if lowered in str(d.get("name", "")).casefold()]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = "、".join(str(d.get("name")) for d in partial)
            raise MijiaError(f"「{text}」匹配到多个设备：{names}，请写得更具体一些。")

        names = [str(d.get("name", "")) for d in devices]
        close = difflib.get_close_matches(text, names, n=1, cutoff=0.55)
        if close:
            raise MijiaError(f"没找到「{text}」，你是不是想找「{close[0]}」？")
        preview = "、".join(names[:15])
        more = "" if len(names) <= 15 else f" 等 {len(names)} 个"
        raise MijiaError(f"没找到「{text}」。现有设备：{preview}{more}")

    async def device(self, dev: dict) -> Device:
        did = str(dev["did"])
        cached = self._handles.get(did)
        if cached is not None:
            return cached

        module = _import_module()
        spec_type = dev.get("spec_type")
        if not spec_type:
            # 少数设备列表里没有 spec_type，回退到 devices 模块补全
            for item in await self.devices():
                if str(item.get("did")) == did and item.get("spec_type"):
                    spec_type = item["spec_type"]
                    break
        if not spec_type:
            raise MijiaError(
                f"「{dev.get('name')}」缺少规格信息，暂不支持控制（可能是蓝牙/虚拟设备）。"
            )

        spec = await self.call(
            module.get_device_info,
            spec_type,
            cache_path=str(self.data_dir),
            timeout=30,
        )
        handle = Device(self.api, dev, spec)
        self._handles[did] = handle
        return handle

    def home_name(self, home_id: Any) -> str:
        for home in self._homes or []:
            if str(home.get("id")) == str(home_id):
                return str(home.get("name") or "我的家")
        if str(home_id) == "shared":
            return "共享设备"
        return "我的家"

    # ---------- 文本渲染 ----------
    def format_devices(self, devices: list[dict], keyword: str = "") -> str:
        if not devices:
            return "没有找到设备。"
        keyword = (keyword or "").strip().casefold()
        shown = [
            d for d in devices if not keyword or keyword in str(d.get("name", "")).casefold()
        ]
        if not shown:
            return f"没有名字包含「{keyword}」的设备。"
        grouped: dict[str, list[dict]] = {}
        for dev in shown:
            grouped.setdefault(self.home_name(dev.get("home_id")), []).append(dev)

        lines = [f"🏠 共 {len(shown)} 个设备"]
        for home, items in grouped.items():
            lines.append(f"\n【{home}】")
            for dev in items:
                flag = ""
                if dev.get("isOnline") is False:
                    flag = "（离线）"
                lines.append(f"· {dev.get('name')}{flag}")
        lines.append("\n用法：/米家 开 <设备>｜/米家 状态 <设备>｜/米家 详情 <设备>")
        return "\n".join(lines)

    def format_snapshot(self, handle: Device, rows: list[tuple[str, Any]]) -> str:
        if not rows:
            return f"「{handle.name}」没有可读属性。"
        lines = [f"📊 {handle.name}"]
        if handle.online is not None:
            lines.append("状态：" + ("在线" if handle.online else "离线"))
        for name, value in rows:
            lines.append(f"· {name} = {value}")
        return "\n".join(lines)
