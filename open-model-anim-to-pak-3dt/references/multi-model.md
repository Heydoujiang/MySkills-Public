# 多模型制作与联合交付

当一次交付包含多个独立拆解对象时读此文件。每个模型各有稳定 `objectId`、拆解配置、DCC 主工程、审核记录、FBX、蓝图及缩略图；共用已编译的 OpenModelBuilder，不再替换 C++ 源码中的物件名。

## 运行时参数

- 全部制作命令传 `-ObjectId=<Id>`；workflow 配置使用 ASCII 英文字母开头，后续字母/数字/下划线；大小写不同不得当作两个 UE 资产标识。
- `-Root=/Game/<Id>` 指制作资产根目录，也支持 `/Game/Project/<Id>`。省略 Root 时采用 `/Game/<Id>`；ObjectId 与 Root 均省略时保留 `/Game/OpenModel` 的旧单模型路径。
- `-Hotkey=N` 默认 N；用户指定其它键时，在生成蓝图和 PIE 测试传同一个 UE Key 名称。
- **验证命令例外**：`OpenModelValidate -Root` 是蓝图所在文件夹，例如 `/Game/<Id>/NoUMG`，分类迁移后为 `/JC_CustomAssets/ObjectLibrary/Exhibition/OpenModel/OpenModel_<Id>`，并传 `-BPName=OpenModel_<Id>`。
- PIE 测试支持 `-Blueprint=<完整蓝图对象路径>` 与 `-Map=<关卡包路径>`，可验证分类迁移后的蓝图。省略则使用当前对象的 NoUMG 演示关卡。所选关卡中被测蓝图实例必须唯一。

各对象依次制作和验证，名称前缀隔离材质、骨架、动画等依赖。已有目标资产同名时停止并检查，不能静默覆盖其它模型。

## `.pak`

对每个对象依次运行 `OpenModelNoUMG -PreparePakBranch`，传该对象的参数、缩略图与目标插件 Content 目录。它通过 AssetTools 移动依赖并修复引用：

```text
JC_CustomAssets/Content/ObjectLibrary/
  Exhibition/OpenModel/OpenModel_<Id>/
    OpenModel_<Id>.uasset
    OpenModel_<Id>_T.uasset
  Material/<Id>_*.uasset
  StaticMesh/<Id>_*.uasset
  Texture/<Id>_*.uasset
```

迁移的是蓝图和依赖，不迁移展示关卡。对每个对象执行迁移后验证，确认插件资产闭包不再引用原 `/Game/` 资产。所有对象共同进入目标 New Profile 0，并开启单文件存储后再打包一次。检查每个模型的 `.uasset`、`.uexp` 及实际需要的 bulk 文件，不能仅以包文件存在为完成。

## `.3dt` 集合关卡

单模型仍沿用 `-PreparePackage`。多个模型使用：

```text
-run=OpenModelNoUMG -PrepareCollectionPackage
-ObjectIds=PumpA+MotorB
-Map=/Game/OpenModelCollection/Demo_ExplodedAssembly_FreeView
-Destination=<目标工程Content目录> -Report=<迁移报告.json>
```

默认优先查找上述插件目录中的蓝图，再查找 `/Game/<Id>/NoUMG`。非默认制作根目录或其它命名时传 `-CollectionManifest=<json>`：

```json
{"objects":[
  {"objectId":"PumpA","blueprint":"/Game/Project/PumpA/NoUMG/BP_ExplodedAssembly_NoUMG.BP_ExplodedAssembly_NoUMG"},
  {"objectId":"MotorB","blueprint":"/JC_CustomAssets/ObjectLibrary/Exhibition/OpenModel/OpenModel_MotorB/OpenModel_MotorB.OpenModel_MotorB"}
]}
```

集合关卡只保留选中模型的业务 Actor 及引擎必要的系统对象。第一件默认归零，其余依据已验证的完整运动盒体沿世界 X 轴错开放置，避免互相遮挡；不更改各蓝图局部原点和模型尺度。若用户指定布局则采用其布局并重新验收。集合自动排布需要完整运动盒体，不能把现有任意碰撞体当作已验证运动包络。

集合迁移会复制 `/Game` 关卡及依赖，报告 `pluginDependencies`，**不会代替插件资产迁移**。若蓝图已经分类到插件，先确保所有报告内插件资产及其文件已在目标预设工程，缺一不可。使用自定义引擎和目标 profile 完成 cook，再做 UnrealPak Verify/List。`.3dt` 只在检查成功后把新生成的 `.pak` 改扩展名；不可把旧文件改名冒充最新结果。
