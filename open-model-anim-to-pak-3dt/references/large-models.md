# 大模型导入与多组件回退

仅在实测导入内存、耗时或 UE4 的单块序列化限制需要时读此文件。普通模型保持单个 SkeletalMesh，不能把某一次大模型的优化开关套用到所有项目。

## 先诊断，保持已确认的动画

- 记录模型面数、零件数、骨骼数、可用内存、失败日志与实际卡住的阶段。内存不足、三角形索引排序缓慢、`FMemoryWriter` 单块超过 2 GB 是不同问题。
- 不擅自关闭用户正在使用的软件，不把减面作为默认解法。需要清理重复顶点时只处理导出副本，验证面角位置、法线、UV、材质、刚性权重及动画一致；不得覆盖已确认的 DCC 主工程。
- 已验证过精确重合顶点清理、保留显式法线并省略 smoothing groups、分组导入等手段。是否采用取决于本模型数据和失败证据。
- 对姿态和几何保真验证通过的导出处理，记录从获批工作工程/FBX 到处理后 FBX、分组清单的哈希与验证链；保留原始用户确认，不编造新的确认。若运动路径、节奏、分组呈现或可见几何发生变化，则重新拍屏请用户确认。
- `r.TriangleOrderOptimization=2` 只在诊断确认 UE4 三角索引排序成为瓶颈后，考虑在隔离制作工程中使用并记录。不要修改全局引擎或用户其它工程配置。

## 可复用工具及边界

`assets/Tools/Unreal/complete_fbx_tangents.py SOURCE NEW_FBX REPORT` 为缺失切线且没有 UV 的网格补充正交切线基。要求二进制 FBX 7500+、直接按面角存储的法线；已有 UV、只缺半套切线、不同法线映射会明确失败。它逐个比对原有节点属性的字节，不能用来修复普通带 UV 的法线贴图切线。使用 Python 3.11+ 与 NumPy。

`assets/Tools/Unreal/partition_rigid_fbx.py SOURCE NEW_DIRECTORY GROUP_COUNT --object-id PumpA [--max-triangles N]` 按整块网格平衡分组，完整保留所有骨骼与动画。要求二进制 FBX 7500+、导出副本已经三角化；不会把单个超大网格切成碎片。输出目录必须不存在，避免覆盖已验证文件。每个源网格只进入一组；所有保留几何、权重、骨骼、动画属性都逐项验证，报告为 `partition_manifest.json`。分组数和面数预算根据当前失败原因决定，不固定为 8 组。

manifest 的 `groups` 按顺序提供 `fbx`、`sha256`/`fbxSha256`、`assetName`、`componentName`、`geometryIds`、`triangles` 与 `vertices`。第一组为 `SK_<Id>` / `AssemblyMesh`，后续为 `SK_<Id>_Group02` / `AssemblyMesh02` 等。不要手动删改分组列表后沿用旧校验结果。

## UE 顺序导入

同一模型所有命令使用相同的 `-ObjectId` 与 `-Root`：

1. 首组：`-run=OpenModelBuild -Name=<Id> -Fbx=<first.fbx> -ImportOnly`，生成共享 Skeleton 和完整动画。
2. 后续各组：`-run=OpenModelBuild -Name=<Id>_Group02 -Fbx=<group.fbx> -MeshOnly -SharedSkeleton=<full skeleton object path>`。顺序执行，避免同时占用大块内存。
3. 组齐后：`-run=OpenModelBuild -Name=<Id> -SkipImport -PartitionManifest=<manifest.json>`，创建多个 SkeletalMeshComponent 的源蓝图。
4. 运行 controls、collision、validate，生成最终两函数蓝图。`ControlAssembly` 内直接逐组件执行原生动画操作，没有新增自定义函数嵌套；外部仍只控制一个 Actor 的 `Progress` 与 `FullTravelDuration`。

核对每组几何恰好一次、骨架和动画一致、所有组件同步、随机跳转一致、无压缩偏差。碰撞应覆盖所有组件的完整运动范围。UE 导入可能移除零面积三角形，报告差异而不是误称完全逐三角相同。保留源数据和差异证据。命令行 CPU 姿态更新时间不能当作实际渲染帧率。
