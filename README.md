# Ai-Link A.O. Smith 热水器集成

用于 AI 家智控燃气热水器及 LL1GBQ24-E10 壁挂炉的 Home Assistant 自定义集成。通过史密斯云端控制设备，支持 HomeKit Bridge。

## LL1GBQ24-E10 壁挂炉

支持类别 `24`、型号 `LL1GBQ24-E10`，与原有类别 `19` 的燃气热水器分别解析和控制。配置仍使用 `access_token`、`user_id`、`family_id`，Cookie 可选。其他壁挂炉型号尚未开放。

| 实体 | 作用 |
|---|---|
| 生活热水 `water_heater` | 设置生活热水温度，范围取设备上报；当前 E10 为 35–60°C，整度调节 |
| 采暖供水 `climate` | 采暖开关与供水设定温度；当前 E10 为 30–85°C，整度调节 |
| 总电源 `switch` | 整台壁挂炉开关，同时影响生活热水和采暖 |
| 温度、流量 `sensor` | 生活热水设定/进水/出水温度、采暖设定/出水/回水温度及生活热水流量 |

采暖实体的当前温度是**采暖出水温度**，不是室温；关闭采暖实体只关闭采暖。生活热水实体只提供调温，整机启停使用独立总电源开关。整机关闭时，先打开总电源再操作采暖或调温。

温度设定依据新读取的设备状态校验。SHC 模式禁止手动调节采暖温度，正在使用生活热水时禁止把水温继续调高到 50°C 以上；三联供等组合系统暂不提供写入。控制命令使用类别 `24`、`CommandValue`，发送后需状态回读匹配才能确认成功，不会自动重发物理命令。

E10 不创建普通燃气热水器的增压和零冷水时长控件。速热洗、定时、房间温控及能耗统计仍使用官方 App；未确认火焰属于哪个回路时，不推断采暖正在燃烧。

协议依据：2026-09-30 的脱敏 E10 状态样例及官方 [壁挂炉页面脚本](https://ailink-appservice-h5-prd.hotwater.com.cn/dist/js/wallHung.5ab6256c.js)、[系统参数页面脚本](https://ailink-appservice-h5-prd.hotwater.com.cn/dist/js/4913.a6f8bc72.js)。生活热水使用 `waterTEMP` / `waterOutTEMP`，采暖使用 `warmTemp` / `warmOutTEMP`，分别对应 `SetTemperature` 的 `CommandType=0` / `1`。

已在 Home Assistant 2026.9.4 / Python 3.14 下通过自动化回归，并使用真实账号完成只读认证、发现和状态读取。**尚未向真实壁挂炉发送开关或调温指令，也未在用户的 Home Assistant 实例部署验收**；控制报文已按官方源码核对并使用模拟响应测试。仓库只包含合成设备标识和脱敏状态值，不包含真实认证信息或抓包文件。

更新时将本仓库的 `custom_components/ailink_aosmith` 覆盖到 Home Assistant 的同名目录并重启；随后添加集成或重新加载现有配置。原有燃气热水器实体标识保持不变。

## v1.4.0 功能

| 功能 | Home Assistant | Apple 家庭（通过 HomeKit Bridge） |
|---|---|---|
| 设定水温、电源 | 热水器实体 + 温控实体 | 恒温器界面，选择 `climate` 实体 |
| 实际出水温度、加热状态 | 温控及传感器 | 恒温器当前温度与加热/待机状态 |
| 零冷水、节能半管零冷水 | 独立开关 | 独立开关 |
| 增压开关、1/2/3 档 | 单个增压滑条 | 单个风扇样式滑条，33/67/100% 对应三档 |
| 零冷水时长 | 1–99 分钟滑条及 ±1 按钮 | 单个百分比滑条，1% = 1 分钟 |
| 燃气消耗（JSQ31-VJS） | m³ 累计传感器、能源统计、每日/月报表 | 在 Home Assistant 查看 |
| 风机转速、出水流量 | rpm、L/min 传感器 | Apple 家庭无原生数值显示类型 |

增压控件控制水泵增压，不控制燃烧风机。任意滑条位置会就近落到设备的 1/2/3 档，并回显实际值；0% 关闭增压。

HomeKit 时长适配器借用风扇的百分比控件，名称明确标注“1%=1分钟”。步进 1%，有效值 1–99%；0%/关闭操作设为最小 1 分钟，100% 设为最大 99 分钟。它始终表示已设置的时长，不能关闭，也不会启动或停止零冷水。零冷水启停使用独立开关。旧版时长预设仍保留以兼容已有自动化，新配置不再导出这些开关。

### 范围与状态来源

根据官方 GasWater 控制页面及 JSQ31-VJS 抓包核对：

- `waterTemp` 是**设定温度**；`outWaterTemp` 是**实际出水温度**。
- 支持最低 35°C 的设备范围为 35–70°C；`minTemp35=0` 时最低 37°C。
- 普通设备步进 1°C；HomeKit 传入的小数温度就近对齐设备步进并回显实际设定值。支持半度的设备在 50°C 以下步进 0.5°C，50°C 及以上为整数。
- 按官方 App 逻辑，在使用热水时禁止继续调高到 50°C 以上。
- 零冷水时长 1–99 分钟，不是累计运行时间。设备自身负责零冷水运行逻辑。
- 增压仅 1、2、3 档。
- 待机时可能有设定温度但未燃烧，界面根据 `heating` 回报显示实际加热状态。

### 本次修复

- 修正电源命令为 `SetDeviceOnOff`。
- 修正零冷水时长命令为 `WaterCruiseTimer`，参数名同名。
- 增压开关使用 `PressurizeOnOff` / `pressurize`，档位使用 `SetPressurizeLevel` / `pressurizeLevel`。
- 详细设备状态优先于可能过时的首页快照，避免控制后读回旧状态。
- HTTP/业务错误不再当作成功；命令串行执行，并等待设备状态回报确认。未确认会向调用方报错。
- 未知、离线状态不再伪装成默认温度或关闭。
- 出水温度传感器增加 HomeKit 所需的温度设备类别。

## 认证与协议限制

- 请求体按紧凑 UTF-8 JSON 序列化后，使用同一组字节计算 `md5data` 与签名；认证失败会进入 Home Assistant 原生重新认证流程。
- 云端在响应 `Authorization` 头中返回轮换后的令牌，集成会立即采用并安全写回配置。
- JWT 的 `exp` 仅用于安排同步时间，不直接判定令牌失效。临近 `exp` 时使用 `getLastToken` 同步账号最新令牌，过期后还会尝试只读端点获取云端重新签发的令牌。
- 云端可能以 HTTP 200 加空设备记录表示令牌不再被接受。集成会先同步令牌并重试；失败后保持定时轮询，不会让配置项永久停止，从而能在官方 App 更新令牌后自行恢复。
- 原生“重新认证/重新配置”仍可手动替换令牌；新令牌验证成功前不会修改已有凭据。物理控制命令不会因鉴权错误被自动重放。
- 回归测试使用合成响应，不连接真实云端、不包含真实账号或设备数据；特定型号和设备仍需单独的用户授权验证。
- 签名研究致谢 Doker9527 与 xiaoyawei 的公开研究。

## 安装与更新

在 HACS 自定义仓库中添加 `https://github.com/mopocv/Ai-Link_A.O.Smith`，类别选 Integration，安装后重启 Home Assistant。也可以将 `custom_components/ailink_aosmith` 复制到 HA 的 `custom_components` 目录后重启。

在“设置 → 设备与服务 → 添加集成”搜索 Ai-Link A.O. Smith，填写从 AI 家智控抓包获取的 `access_token`、`user_id`、`family_id`，可选 Cookie 与手机号。令牌可带或不带 `Bearer ` 前缀。首次抓取后，集成会自动同步云端轮换的令牌；若云端无法从旧令牌恢复，仍可在集成设置中手动替换。

默认每 60 秒轮询，可在集成选项中调整。控制操作会另外主动查询状态确认。

## HomeKit 配置

在 HomeKit Bridge 中选择：热水器**温控**、零冷水、节能半管、三档增压、**零冷水时长（1%=1分钟）**和实际出水温度。温控与原有 `water_heater` 只选一个；增压只选择 `fan`，不要重复导出增压开关或旧版时长预设。已有配对只需修改原桥过滤器并重载，不需要删除桥或重新扫码。

HomeKit 没有普通 `number` 配件，百分比适配器是为主界面滑条提供的明确映射。若希望原生时间单位，可另外配置 valve + linked_valve_duration，但 Apple 家庭的编辑入口和控件与百分比滑条不同。

风机 rpm、流量 L/min 和任意文字状态不能通过标准 HomeKit Bridge 原样展示在 Apple 家庭中，请使用 Home Assistant 仪表盘查看。不要将其伪装成湿度、照度等错误类型。

容器部署建议使用 host 网络，确保 mDNS 与 HomeKit 端口可被同一局域网访问。首次配对需在已登录目标 Apple 账号的 iPhone“家庭 → 添加配件”中扫描 HA 提供的二维码。

参考：[HomeKit Bridge 官方文档](https://www.home-assistant.io/integrations/homekit/)。协议核对来源：官方 AI 家智控 [GasWater 网页入口](https://ailink-appservice-h5-prd.hotwater.com.cn/dist/index.html#/GasWater)，核对日期 2026-09-02。

## 燃气统计与仪表盘

JSQ31-VJS 新增独立的标准燃气累计传感器：按官方能耗页面的 0.1 m³ 计数单位换算，原始计数 2549 显示为 254.9 m³；原始字段和倍率保留在属性中。其他型号需另行核对单位后再开放统计。旧版原始传感器不改单位、不混入新统计，以免历史值发生十倍跳变。

新传感器使用 `device_class: gas`、`state_class: total_increasing`、`m³`，可在“能源 → 燃气消耗”选择**累计燃气**。零冷水累计是其中的一部分，不能重复相加。统计仅覆盖热水器，不代表家庭燃气总表。未设置单价时只统计体积。

[配置示例](examples/homeassistant.yaml) 提供桥过滤器和每日、每月 utility meter；[仪表盘视图](examples/lanyuewan-views.json) 提供热水器控制和燃气报表两个视图。实体 ID 要按自己环境对应调整；示例使用 Mushroom、mini-graph-card 和原生卡片。将两个视图追加到已有仪表盘，保留其他视图。

新接入的每日、每月用量从接入时开始积累，不会把设备历史累计当成今日消费。长期趋势需要等待首个小时统计生成；不会伪造缺失的历史日/月数据。

能耗换算来源：官方 [能耗页面脚本](https://ailink-appservice-h5-prd.hotwater.com.cn/dist/js/39.8c81819f.js)，`gas_water_heater_gas_consumption_*` 显示值除以 10 后以 m³ 展示。卡片参考 [Home Assistant 卡片功能](https://www.home-assistant.io/dashboards/features) 和 [Utility Meter](https://www.home-assistant.io/integrations/utility_meter/)。

## 验证

安装 Home Assistant 测试依赖后运行 `python -m unittest discover -s tests -v`，覆盖签名认证、协议范围、非法输入、旧快照优先级、状态回读、原燃气热水器及 E10 分路控制。仅检查独立协议函数时可运行 `python -m unittest discover -s tests -p test_protocol.py -v`，无需 Home Assistant。

## 致谢

特别感谢 [@RainySat](https://github.com/RainySat) 在 [issue #11 的详细研究](https://github.com/mopocv/Ai-Link_A.O.Smith/issues/11#issuecomment-5691595668)中验证令牌轮换、`getLastToken`、响应头更新及 HTTP 200 空记录的失效表现，并提供同云平台的[参考实现](https://github.com/RainySat/ha-ailink-electric-water-heater)。这些结论构成 v1.4.0 自动令牌同步与自恢复机制的基础。

感谢 [@xiaoyaner0201](https://github.com/xiaoyaner0201) 贡献请求签名、错误分类和 Home Assistant 原生重新认证流程。

第三方项目，与 A.O. Smith 官方无隶属关系。许可证见 LICENSE。
