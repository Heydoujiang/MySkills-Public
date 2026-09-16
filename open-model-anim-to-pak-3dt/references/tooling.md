# 随附工具

`scripts/workflow.py` 使用 Python 3.11+ 标准库。所有子进程参数以列表传递，Windows 使用 CREATE_NO_WINDOW。配置文件使用 UTF-8 JSON；路径由当前任务填入，不沿用旧机器路径。

## 配置字段

| 字段 | 含义 |
|---|---|
| objectId / hotkey | 如 Gun / N；objectId 用 ASCII 资产标识，hotkey 为 UE EKeys 名称 |
| project | 独立工作工程 `.uproject`，生产源工程先复制并备份，不能直接在唯一源工程执行迁移重命名 |
| engine / customEngine | UE4.26 编辑引擎根目录 / 指定的交付自定义引擎根目录，均含 Engine 子目录 |
| targetProject | 本次预设工程 `.uproject`，保留其插件和配置 |
| reports | 当前版本的独立日志目录，不覆盖旧验证证据 |
| fbx / dccScene / preview / approval / samples | 确认版本的文件与 approval JSON、Maya validation_samples.json |
| thumbnail | 确认动画 Progress=1 的真实渲染 PNG，用于 .pak |
| map | 默认 `/Game/<objectId>/NoUMG/Demo_ExplodedAssembly_FreeView` |
| collisionReport | 碰撞检查输出 JSON |
| format | 用户明确选择的 `pak` 或 `3dt`；缺失时脚本拒绝打包 |
| profileFile / profileName | 找到的 `.ulp2` 文件和名称，默认名称 New Profile 0 |
| blueprintFile / validatedBlueprintSha256 | 已通过控制与碰撞验证的磁盘 BP 文件及 SHA256；变更后重新验证和记录 |
| requiredPakPaths | 从本次 migration.json 推导全部必需的包内路径，不只列主蓝图 |
| forbiddenPakPaths | 上一分支不应出现的模型路径，不把预设正常引擎资源误判为残留 |

`importOnly` 和 `rebuildControls` 默认 false。仅在明确重导动画或明确重建控制副本时设置。已有用户修改须先审查备份，不能自动翻转开关绕过已有资产检查。

## 生产顺序

1. `discover`；用 Maya 后端检查、制作和预览。`check-approval CONFIG` 只验证真实确认，不生成确认。
2. 将工作工程放在独立目录，先备份。`materialize CONFIG` 将 editor-only 插件模板复制到工作工程并替换 objectId/按键；有同名插件时拒绝覆盖，需要先比对与备份。
3. `ue-build-plugin CONFIG` 编译（本模板针对 Windows、UE4.26、VS2019；其他工具链需实机适配）。`ue-import CONFIG` 导入获批 FBX。
4. `ue-controls CONFIG` 生成最终两函数无 UMG 蓝图，`ue-free-camera CONFIG` 输出自由视角演示地图，`ue-collision CONFIG` 检查/添加运动盒体，`ue-validate CONFIG` 检查姿态和交互。
5. 阅读源码中的 `OpenModelNoUMGTest.cpp`，在该工作工程运行后台 PIE 自动化测试 `OpenModel.NoUMG.PIE`，报告输出目录单独指定。materialize 已替换模型包路径和实际按键；测试名称保留固定名称，不是模型依赖。首次着色器编译可能占用时间，要区分准备时间和运行超时。
6. .pak 迁移前，在工作工程创建与目标同名的 JC_CustomAssets 内容插件挂载点，只复制 `.uplugin` 描述并创建空 Content；不要将目标整个库复制进工作副本。检测目标重名资产并处理，再运行 `ue-prepare CONFIG`。该命令会重命名工作副本的资产；之后使用 `ue-validate-relocated CONFIG`，不要继续按旧包路径验证。与原工作资产不同的磁盘位置要更新 blueprintFile/hash 记录。
7. .3dt 用 `ue-prepare CONFIG` 只清理工作副本中的 FreeView 关卡、复制依赖，保留蓝图文件内容。确认目标 BP 哈希与已验证版本一致、地图一个业务 Actor 且位置归零。
8. 从迁移清单构造 requiredPakPaths：.3dt 前缀 `<ProjectName>/Content/`；.pak 前缀 `<ProjectName>/Plugins/JC_CustomAssets/Content/`。逐个核对目标资产存在并哈希一致，检查无旧源路径依赖；报告应包含碰撞检查。
9. `package CONFIG` 检查确认、碰撞记录、蓝图哈希，备份并设置 profile，在后台启动 AutomationTool；`verify CONFIG` 检查成功和新输出，执行 UnrealPak Verify/List，核对清单并按分支改名或保留。

命令行例子：`python scripts/workflow.py ue-collision task.json`。脚本不会自动安装 DCC、引擎、构建依赖或启用安全权限；缺少环境时先报告缺口。

## 模板实现边界

- `ue-import` 的历史内部工厂先产生基础装配 BP/演示模板，`ue-controls` 生成最终 NoUMG 两函数资产并移除固定摄像机。只迁移最终关卡或最终蓝图闭包；内部 UMG 模板不是交付物，也不应暴露为最终演示。
- `OpenModelUserVariable.inl` 仅用于已有旧蓝图的一次性升级。新工厂直接产生两个函数，不再调用 SplitUserVariable。
- materialize 当前参数化名称、资源路径和按键；更换接口名称、资产类别或层级组织需要修改模板并重新验证。
- PhysicsAsset 等额外模型依赖需纳入分类；未知依赖会停止，避免静默漏包。多个逻辑零件不独立时不能直接套用刚性绑定后端。
- 装配体模板默认一个 SkeletalMesh。额外可视组件或多个动画网格需要扩展碰撞取样与依赖验证，不能仅包围第一个网格后宣称覆盖全部物体。
- `.pak` 后续单独复用前需要宿主加载器验证。两个打包分支生成成功不代表任意 UE4.26 构建都兼容，必须使用目标指定自定义引擎。

正式入口为 workflow.py；早期试跑的 UE 包装脚本不随 Skill 分发。不要恢复历史跳过预览的参数。修改脚本时保持真实审批、版本核验、后台执行和失败退出。
