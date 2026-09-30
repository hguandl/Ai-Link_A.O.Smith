# Ai-Link A.O. Smith：Home Assistant 集成

[![在 HACS 中打开此仓库](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mopocv&repository=Ai-Link_A.O.Smith&category=integration)

## 项目背景

本项目是面向 A.O. 史密斯 AI-LiNK 智慧家（原“AI 家智控”）设备的 Home Assistant 自定义集成。它通过设备使用的云端接口读取状态和发送控制指令，让热水与采暖设备可以在 Home Assistant 中查看、控制，并按需通过 HomeKit Bridge 接入 Apple 家庭。

这是社区维护的非官方项目。设备型号、固件和云端接口可能存在差异，因此下表区分已核对的型号与同类设备的适用范围。

## 支持的设备

| 设备 | 云端类别 | 支持情况 |
| --- | --- | --- |
| AI-LiNK 燃气热水器 | `19` | 支持该类别的基础热水器控制；已针对 **JSQ31-VJS** 核对温控、零冷水、增压和燃气计数。其他型号的功能及数据单位需按实机确认。 |
| **LL1GBQ24-E10** 壁挂炉 | `24` | 由 [PR #13](https://github.com/mopocv/Ai-Link_A.O.Smith/pull/13) 引入，仅识别这一型号；已完成只读发现和状态读取验证，开关及调温仍待真实设备验收。使用前请确认安装版本包含该 PR。 |

其他类别和未核对的壁挂炉型号不在当前支持范围内。

## 功能

| 功能 | 燃气热水器（类别 `19`） | LL1GBQ24-E10 壁挂炉 |
| --- | --- | --- |
| 温度与开关 | 热水器及温控实体：设定水温、查看实际出水温度和加热状态、控制电源 | 生活热水实体：设定水温；采暖实体：控制采暖开关及供水温度；独立总电源开关控制整机 |
| 设备状态 | 出水流量、风机转速等传感器，具体取决于设备上报 | 生活热水进/出水温度与流量、采暖出/回水温度等传感器 |
| 附加控制 | 零冷水、节能半管零冷水、三级增压、1–99 分钟零冷水时长 | 暂不提供燃气热水器的零冷水及增压控件；速热洗、定时和房间温控仍使用官方 App |
| 能源统计 | **JSQ31-VJS** 提供换算为 m³ 的燃气累计传感器，可用于 Home Assistant 能源面板 | 暂不提供能耗统计 |
| Apple 家庭 | 可经 HomeKit Bridge 导出温控、开关和适配后的增压/时长滑条 | 可经 HomeKit Bridge 导出相应温控与开关实体；采暖实体显示的是**采暖出水温度**，不是室温 |

### 使用说明

- 燃气热水器的可设温度范围取设备上报值：最低 35°C 或 37°C，最高 70°C。支持半度的设备在 50°C 以下按 0.5°C 调节，其余情况按整度调节。增压滑条的 33% / 67% / 100% 对应 1 / 2 / 3 档，0% 为关闭。
- HomeKit 中的零冷水时长滑条采用 **1% = 1 分钟** 的映射，实际范围为 1–99 分钟；它只设定时长，零冷水启停使用独立开关。旧版时长预设保留，以兼容已有自动化。
- E10 的生活热水和采暖分别控制。生活热水温度范围取设备上报值，已核对样例为 35–60°C；采暖供水为 30–85°C，均按整度调节。整机关闭后须先开启总电源。SHC 模式不允许手动调节采暖温度；三联供等组合系统暂不支持控制指令。
- 控制命令会等待设备状态回报确认；云端或设备未确认时，Home Assistant 会报告失败。认证令牌由云端轮换时，集成会尝试自动同步；无法恢复时可在集成设置中手动更新。

## 安装与配置

点击顶部的 HACS 按钮，可在自己的 Home Assistant 实例中打开本仓库并按提示添加、安装。也可在 HACS 中手动将 `https://github.com/mopocv/Ai-Link_A.O.Smith` 添加为 **Integration** 类别的自定义仓库，或将 `custom_components/ailink_aosmith` 复制到 Home Assistant 的 `custom_components` 目录。重启 Home Assistant 后，在“设置 → 设备与服务 → 添加集成”中搜索 **Ai-Link A.O. Smith**。

配置需要从自己的 AI-LiNK 账号获取 `access_token`、`user_id`、`family_id`；Cookie 和手机号可选。默认每 60 秒轮询一次，间隔可在集成选项中调整。请勿在问题反馈中公开令牌、Cookie 或设备标识。

使用 HomeKit Bridge 时，燃气热水器建议只导出一个温控实体，并使用 `fan` 实体表示增压，避免重复控件。[配置示例](examples/homeassistant.yaml)和[仪表盘示例](examples/lanyuewan-views.json)可作为起点，实体 ID 需按自己的环境修改。

## 致谢

- 感谢 [@RainySat](https://github.com/RainySat) 在 [issue #11](https://github.com/mopocv/Ai-Link_A.O.Smith/issues/11) 中研究令牌轮换、`getLastToken` 和空设备记录，并提供[参考实现](https://github.com/RainySat/ha-ailink-electric-water-heater)。
- 感谢 [@xiaoyaner0201](https://github.com/xiaoyaner0201) 贡献请求签名、错误分类及 Home Assistant 原生重新认证流程。
- 感谢 PR #13 的贡献者为 LL1GBQ24-E10 壁挂炉提供协议研究、实现与测试；请求签名的早期资料来自 Doker9527 与 xiaoyawei 的公开研究。

## 免责声明

本项目与 A.O. 史密斯及其关联公司没有隶属或合作关系，也不保证所有型号、固件版本或云端接口持续可用。壁挂炉的物理控制尚未完成真实设备验收；请在有人看护、确认设备状态和安全设置的条件下首次测试。使用本集成及处理账号凭据的责任由使用者自行承担。许可证见 [LICENSE](LICENSE)。
