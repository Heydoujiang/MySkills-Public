# 打包分支

先确定格式、预设工程 `.uproject`、自定义引擎根目录（其下应有 `Engine/Binaries/Win64/UE4Editor-Cmd.exe`），以及 profile 名称。默认 New Profile 0。找不到指定 profile 时询问或读取实际可用配置，不自动改选其他配置。

备份预设 `.uproject`、Config、目标 profile 和旧输出；对目标同名资产进行碰撞检查。UE 版本切换用 DesktopPlatform 注册真实引擎根目录并设置项目关联。无需通过可见编辑器打开项目来完成后台 Cook；Cook 日志应证明指定引擎实际运行。

## .3dt

迁移目标演示关卡及依赖到预设 Content，保留包路径。保留最新版两函数结构，不向 ControlAssembly 插入旧版 Setter。

仅在打包工作副本清理关卡：只保留需要交付的蓝图 Actor，默认世界位置归零；WorldSettings / LevelScriptActor 属于必要系统对象。不把清理后的地图覆盖回用户演示工程。

设 profile 的工程和 CookedMaps 为本次工程、目标关卡，清除旧任务地图选择。启用 Pak、关闭分块以得到单个交付包。若用户显式提供特殊 profile 设置，先核对用途再变更，不覆盖无关选项。

## .pak

只迁移蓝图依赖，不迁移演示关卡。通过 UE AssetTools 移动/重命名和更新引用，不用文件管理器直接搬运未更新引用的 uasset。

物件目录规则（ObjectId 由当前物体确定，不固定为 Gun）：

```text
Plugins/JC_CustomAssets/Content/ObjectLibrary/
  Exhibition/OpenModel/OpenModel_<ObjectId>/
    OpenModel_<ObjectId>.uasset
    OpenModel_<ObjectId>_T.uasset
  Material/     材质、实例、函数、参数集
  StaticMesh/   静态或骨骼网格、骨架、动画、压缩配置等模型依赖
  Texture/      普通贴图依赖
```

例如 Car 使用 `Exhibition/OpenModel/OpenModel_Car/OpenModel_Car.uasset`；名称不明确时询问。缩略图从确认版本的实际展开状态生成并作为 Texture2D 导入同一个物件文件夹，`_T` 是普通贴图分类规则的明确例外。已有资产名冲突时核对同一资产与版本，不能覆盖其他物件依赖；必要时给依赖名加物件前缀。

新的 PhysicsAsset 或其他依赖也必须纳入分类和引用检查。未知类型先检查用途；不能丢掉依赖以满足固定分类。引擎自带依赖通常保留引擎引用，插件/项目依赖需确认目标可用。

取消上一 .3dt 的 CookedMaps。保留预设用于资源库的 `/JC_CustomAssets` always-cook 配置。启用 Advanced Settings 的 **Store all content in a single file (UnrealPak)**：序列化字段 `DeployWithUnrealPak=true`，UAT `pak=true`；`GenerateChunks=false`。不能只改 UI 文案或只看旧 profile 已勾选。

## 后台执行与交付

支持使用指定引擎 AutomationTool 执行包含 `scripts/BuildCookRun` 的 `.ulp2` JSON。普通旧 `.ulp` 不能直接当作 `-profile` 参数。保持既有 Shipping、压缩、unversioned 等相关预设；校验命令实际指向正确工程和地图。

输出路径根据 `.uproject` 的工程名推导：`Saved/StagedBuilds/WindowsNoEditor/<ProjectName>/Content/Paks`。必须同时满足 UAT exit 0、BUILD SUCCESSFUL、新于本次启动时间、非空；再用同引擎 UnrealPak `-Verify`（检查文件哈希）和 `-List` 检查关卡/蓝图/全部依赖。`-Test` 只检查可读性，不替代 `-Verify`。

检查无旧分支资产、漏包或残留原路径。确认原工程内容哈希未改动。`.3dt` 校验后只改扩展名；`.pak` 不改名为 .3dt。不得把过期包或仅新建空文件当作成功。

交付绝对路径、时间、大小和实际测试范围；用户未指定按键时明确说“默认 N 键展开/回装”。同时说明 Progress 可由参数控制。无前端加载测试时写明尚未测试，不能把包完整性等同于宿主兼容性。预设自带警告与当前资产错误分开报告。
