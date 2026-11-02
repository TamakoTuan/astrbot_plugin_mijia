"""离线单元测试：用假 API 验证 MijiaService 的解析、校验与渲染逻辑。

运行：
    python tests/test_service.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mijia_service as ms  # noqa: E402

SPEC = {
    "name": "yeelink.light.lamp4",
    "properties": [
        {
            "name": "on",
            "description": "开关",
            "type": "bool",
            "rw": "rw",
            "range": None,
            "value-list": None,
            "method": {"siid": 2, "piid": 1},
        },
        {
            "name": "brightness",
            "description": "亮度",
            "type": "uint",
            "rw": "rw",
            "range": [1, 100, 1],
            "value-list": None,
            "method": {"siid": 2, "piid": 2},
        },
        {
            "name": "mode",
            "description": "模式",
            "type": "uint",
            "rw": "rw",
            "range": [0, 2, 1],
            "value-list": [
                {"value": 0, "description": "auto", "desc_zh_cn": "自动"},
                {"value": 1, "description": "cool", "desc_zh_cn": "制冷"},
            ],
            "method": {"siid": 2, "piid": 3},
        },
        {
            "name": "temperature",
            "description": "温度",
            "type": "float",
            "rw": "r",
            "range": [-20, 60],
            "value-list": None,
            "method": {"siid": 3, "piid": 1},
        },
    ],
    "actions": [
        {"name": "toggle", "description": "切换", "method": {"siid": 2, "aiid": 1}}
    ],
}


class FakeModule:
    @staticmethod
    def get_device_info(spec_type, cache_path=None):
        return SPEC


class FakeAPI:
    available = True

    def __init__(self):
        self.writes = []

    def get_homes_list(self):
        return [{"id": "1", "name": "我的家"}]

    def get_devices_list(self):
        return [
            {
                "did": "d1",
                "name": "客厅台灯",
                "model": "yeelink.light.lamp4",
                "home_id": "1",
                "isOnline": True,
                "spec_type": "urn:test",
            },
            {
                "did": "d2",
                "name": "卧室台灯",
                "model": "yeelink.light.lamp4",
                "home_id": "1",
                "isOnline": False,
                "spec_type": "urn:test",
            },
            {
                "did": "d3",
                "name": "大门门锁",
                "model": "lumi.lock",
                "home_id": "1",
                "spec_type": "urn:test",
            },
        ]

    def get_scenes_list(self):
        return [{"scene_id": "s1", "name": "回家模式", "home_id": "1"}]

    def get_devices_prop(self, data):
        items = data if isinstance(data, list) else [data]
        return [
            {"code": 0, "value": True if i["siid"] == 2 and i["piid"] == 1 else 80}
            for i in items
        ]

    def set_devices_prop(self, data):
        self.writes.append(data)
        return {"code": 0, "value": data.get("value")}

    def run_action(self, data):
        return {"code": 0}

    def run_scene(self, scene_id, home_id):
        return True


async def main() -> None:
    tmp = Path(tempfile.mkdtemp())
    ms._import_module = lambda: FakeModule

    service = ms.MijiaService(tmp, config={"danger_keywords": "门锁,燃气"})
    service._api = FakeAPI()

    devices = await service.devices()
    assert len(devices) == 3
    await service.homes()
    listing = service.format_devices(devices)
    assert "客厅台灯" in listing and "离线" in listing

    assert service.resolve("客厅台灯")["did"] == "d1"
    try:
        service.resolve("台灯")
        raise AssertionError("同名设备应报歧义")
    except ms.MijiaError:
        pass

    dev = await service.device(devices[0])
    assert "brightness" in dev.describe()

    rows = dict(await dev.snapshot(service))
    assert rows["on"] == "开"

    assert "已设置" in await dev.set_prop(service, "on", "开")
    assert "已设置" in await dev.set_prop(service, "brightness", 60)
    assert "已设置" in await dev.set_prop(service, "mode", "制冷")
    assert service._api.writes[-1]["value"] == 1

    for name, value in (("brightness", 300), ("mode", "暴风"), ("on", "嗯")):
        try:
            await dev.set_prop(service, name, value)
            raise AssertionError(f"{name}={value} 应当被拒绝")
        except ms.MijiaError:
            pass

    try:
        await dev.set_prop(service, "temperature", 20)
        raise AssertionError("只读属性应当被拒绝")
    except ms.MijiaError:
        pass

    aliases = ms.MijiaService(
        tmp, {"device_aliases": "大灯=客厅台灯\nx=卧室台灯"}
    )._aliases()
    assert aliases["大灯"] == "客厅台灯"

    print("writes:", json.dumps(service._api.writes, ensure_ascii=False))
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
