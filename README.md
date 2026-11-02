# astrbot_plugin_mijia

<p align="center">
  <img src="logo.png" width="120" alt="米家智能家居">
</p>

<p align="center">
  在 QQ / Telegram / 飞书等聊天平台里，用一句话控制你家的米家设备。
</p>

<p align="center">
  <img alt="AstrBot" src="https://img.shields.io/badge/AstrBot-%3E%3D4.10-6f42c1">
  <img alt="Python" src="https://img.shields.io/badge/Python-%3E%3D3.10-3776ab">
  <img alt="License" src="https://img.shields.io/badge/License-GPL--3.0-blue">
</p>

---

## 简介

`astrbot_plugin_mijia` 是 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的米家智能家居插件。
它基于 [mijiaAPI](https://github.com/Do1e/mijia-api) 调用米家云接口，不依赖 Home Assistant、不要求设备处于同一局域网。

- 手机扫码一次即可登录，登录态保存在本地，`serviceToken` 自动刷新
- 支持开关、属性读写、设备动作、手动场景
- 同时提供**斜杠指令**和**自然语言工具**：既可以 `/米家 开 客厅灯`，也可以直接说「把客厅灯调暗一点」
- 默认只允许管理员私聊使用，高危设备需要二次确认，避免群友误操作

## 功能

| 能力 | 说明 |
| --- | --- |
| 设备列表 | 按家庭分组列出所有设备，显示离线状态 |
| 设备状态 | 一次性批量读取设备所有可读属性（开关、亮度、温度、电量…） |
| 开关控制 | `开` / `关` 快捷指令，等价于把 `on` 置为真/假 |
| 属性设置 | 按属性名设置亮度、色温、目标温度、风速、模式等，自动做类型与范围校验 |
| 枚举值 | 支持用中文描述设置枚举属性，例如 `mode=制冷` |
| 设备动作 | 执行设备自定义动作（如「切换」「开始充电」） |
| 手动场景 | 列出并执行米家 App 里创建的手动场景 |
| 自然语言 | 向模型注册 4 个工具，支持「帮我关掉所有灯」这类多步指令 |
| 安全控制 | 仅管理员、仅私聊、高危设备二次确认、别名映射 |

## 效果示例

```text
你： /米家 开 客厅灯
Bot：✅ 已设置：客厅灯 · on = True

你： /米家 状态 客厅灯
Bot：📊 客厅灯
     状态：在线
     · on = 开
     · brightness = 80
     · color-temperature = 4000

你： 客厅有点暗，把灯调亮一点
Bot：（调用 mijia_control_device）
     ✅ 已设置：客厅灯 · brightness = 90

你： /米家 执行 回家模式
Bot：✅ 已执行场景：回家模式
```

## 安装

### 前置条件

- AstrBot `>= 4.10`（已在 `4.29.3` 上验证）
- Python `>= 3.10`
- 一个能登录米家 App 的小米账号

### 方式一：从插件市场安装

插件发布到 AstrBot 插件市场后，可在 WebUI →「插件」→「插件市场」搜索 `米家` 直接安装。

### 方式二：手动安装

```bash
cd AstrBot/data/plugins
git clone https://github.com/TamakoTuan/astrbot_plugin_mijia.git
```

然后重启 AstrBot，或在 WebUI 插件页点击「重载」。

### 依赖

插件会在首次加载时自动安装依赖（`requirements.txt`）：

```text
mijiaAPI>=5.0.0
```

手动安装：

```bash
pip install mijiaAPI
```

## 首次使用

1. 用**管理员账号**私聊机器人，发送：

   ```text
   /米家 登录
   ```

2. 机器人会发来一张二维码，用**米家 App → 我的 → 右上角扫一扫**扫描，并在手机上确认登录。
3. 登录成功后发送 `/米家 设备`，确认能读到你的设备列表。
4. 之后就可以用指令或自然语言控制了。

> 登录凭证保存在 `data/plugin_data/astrbot_plugin_mijia/auth.json`，**不会**上传到任何地方，也不会随插件更新丢失。
> `serviceToken` 会自动刷新；`passToken` 大约一个月有效，过期后重新执行 `/米家 登录` 即可。

## 指令

所有指令以 `/米家` 开头，也可使用别名 `/mj`、`/mijia`。

| 指令 | 说明 |
| --- | --- |
| `/米家` / `/米家 帮助` | 查看帮助 |
| `/米家 登录` | 扫码登录米家账号 |
| `/米家 状态` | 查看登录状态、家庭/设备/场景数量 |
| `/米家 设备 [关键词]` | 列出设备，可按名称过滤 |
| `/米家 刷新` | 重新拉取设备与场景列表（默认缓存 5 分钟） |
| `/米家 详情 <设备>` | 查看设备可读/可控属性与动作 |
| `/米家 状态 <设备>` | 读取设备当前状态 |
| `/米家 状态 <设备> <属性>` | 读取单个属性 |
| `/米家 开 <设备>` | 打开设备 |
| `/米家 关 <设备>` | 关闭设备 |
| `/米家 设置 <设备> <属性> <值>` | 设置属性，例如 `/米家 设置 客厅灯 brightness 60` |
| `/米家 动作 <设备> <动作>` | 执行设备动作 |
| `/米家 场景` | 列出手动场景 |
| `/米家 执行 <场景>` | 执行手动场景 |
| `/米家 确认` / `/米家 取消` | 高危设备操作的二次确认 |
| `/米家 退出登录` | 清除本地登录凭证 |

设备名称支持**模糊匹配**：`客厅灯` 能匹配到 `客厅灯-主灯`；匹配到多个时会提示你写得更具体。
也可以在插件配置里用「设备别名」把 `大灯` 映射到 `客厅主灯`。

## 自然语言

插件向 AstrBot 注册了 4 个工具，模型会自动选择合适的调用：

- `mijia_list_devices` —— 列出设备
- `mijia_device_status` —— 读取设备状态
- `mijia_control_device` —— 开关或设置属性
- `mijia_run_scene` —— 执行场景

例如：

- 「客厅灯太亮了，调到 30%」
- 「空调开到 26 度，制冷」
- 「我要睡了，执行晚安场景」
- 「家里现在多少度？」

> 自然语言控制同样受权限限制，并且**高危设备不允许模型直接操作**，只能用指令 + 二次确认。

## 配置

在 AstrBot WebUI →「插件」→「米家智能家居」→「配置」中修改：

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `admin_only` | bool | `true` | 仅管理员可用。关闭后所有能私聊机器人的人都能控制家电，**不建议**。 |
| `private_only` | bool | `true` | 仅限私聊。开启后群聊中不响应指令，也不向模型暴露控制工具。 |
| `danger_keywords` | text | `门锁,门,锁,燃气,阀门,热水器,电热,安防,摄像头` | 设备名命中这些关键词时，指令需要 `/米家 确认` 二次确认，且模型无法直接调用。留空则关闭该保护。 |
| `device_aliases` | text | 空 | 一行一条，格式 `别名=米家设备名`，例如 `大灯=客厅主灯`。 |

## 工作原理

```text
QQ ──▶ SnowLuma/OneBot ──▶ AstrBot ──▶ 本插件 ──▶ 米家云 API ──▶ 设备
                                    └─▶ LLM 工具调用
```

- 全部请求走米家云（`api.mijia.tech`），因此机器人不需要和智能设备在同一局域网
- 设备的属性模型来自 [miot-spec.org](https://miot-spec.org/)，按 `spec_type` 解析并缓存到插件数据目录
- 同步 HTTP 请求统一放进线程池执行，不会阻塞 AstrBot 事件循环
- 设备列表缓存 5 分钟，避免频繁请求云端

## 常见问题

**扫码后提示超时？**
二维码有效期约 2 分钟。重新发送 `/米家 登录` 换一张即可；扫码后记得在手机上点「确认登录」。

**提示登录失效？**
`passToken` 大约一个月过期。重新执行 `/米家 登录`，通常会自动续期而无需扫码。

**设备列表是空的？**
确认账号在米家 App 里确实绑定了设备；共享设备可能需要用主账号登录。发送 `/米家 刷新` 强制重新拉取。

**某台设备提示「缺少规格信息」？**
部分蓝牙、虚拟设备或第三方接入设备没有公开的 MIoT 规格，无法通过云 API 控制。

**控制有延迟？**
云控链路通常是 1~3 秒，属正常现象。极少数走网关的设备只返回「指令已下发」，不回报执行结果。

**性能与安全**
插件默认只允许管理员私聊；请勿对公网暴露 AstrBot 面板，也不要把 `auth.json` 提交到 Git 仓库。

## 目录结构

```text
astrbot_plugin_mijia/
├── main.py              # 插件入口：指令 + LLM 工具
├── mijia_service.py     # 米家云封装：登录、设备、属性、场景
├── metadata.yaml        # 插件元信息
├── _conf_schema.json    # 插件配置项定义
├── requirements.txt     # 依赖
├── logo.png
├── tests/
│   └── test_service.py  # 离线单元测试
├── README.md
├── CHANGELOG.md
└── LICENSE
```

## 开发

```bash
git clone https://github.com/TamakoTuan/astrbot_plugin_mijia.git
cd astrbot_plugin_mijia
pip install mijiaAPI

# 离线单元测试（不需要米家账号）
python tests/test_service.py
```

`mijia_service.py` 不依赖 AstrBot，可以单独导入做单元测试。修改后把目录放到 `AstrBot/data/plugins/` 下，在 WebUI 里重载插件即可。

## 致谢

- [mijiaAPI](https://github.com/Do1e/mijia-api) —— 米家云 API 的 Python 实现，本插件的能力来源
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) —— 插件框架
- [miot-spec.org](https://miot-spec.org/) —— 设备规格数据

## 许可证

[GPL-3.0](LICENSE)

由于本项目依赖 GPL-3.0 的 `mijiaAPI`，插件同样以 GPL-3.0 发布。使用、修改、分发时请遵守该许可证。
