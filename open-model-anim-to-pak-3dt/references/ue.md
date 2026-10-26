# UE 控制、碰撞及验收

## 导入与控制

已批准的 FBX 导入 Skeletal Mesh / Skeleton / Animation Sequence。逐模型骨架无需复用 AnimBP；使用 SingleNode 模式、SetAnimation 后 Stop，以 SetPosition 绝对取样。更换模型要验证骨架匹配、压缩小件误差、材质和展开包围盒。

同一个编辑器插件可通过运行参数处理多个 objectId，不为每个物件重新替换源码。每个逻辑物件保持独立 Actor 蓝图和资产命名。大模型分组导入后可在同一蓝图内放置多个 SkeletalMeshComponent，使用匹配的共享骨架及动画，由一份 Progress 同步取样；详见 [大模型](large-models.md) 与 [多模型](multi-model.md)。多个组件的初始化、播放位置设置展开在同一个 ControlAssembly 中，不增加自定义函数嵌套。

最终运行资产无 UMG，无 CameraActor 强制视角，不设置固定 ViewTarget。默认自动接收 Player 0 的 N 键，用户指定其他键则替换。ControlAssembly 和 UserVariable 契约以 SKILL.md 为准。浮点进度 UI 范围 0–1；用户可在 PIE 的运行实例 Details 编辑，不能误改编辑器世界中的另一个实例。

`UserVariable` 只赋值，下一 Tick 应用姿态。不要在该函数内部调用 ControlAssembly；前端若需要同一调用栈刷新，可由外部调用者顺序调用两个函数，并先明确语义。不存在 UI 时也保留 Progress 的 Details 暴露。

## 必做碰撞步骤

检查 PrimitiveComponent 的碰撞是否启用，以及是否确有可用形状；骨骼网格不能仅因存在 PhysicsAsset 引用就认定有效，需要检查其 bodies。已有合适碰撞保留并检查用途。如果没有有效碰撞，在蓝图组件树下增加一个 Box Collision，覆盖**装配、过渡和展开全过程**。不为每个零件自动生成物理模拟；不能把单盒当作精确零件表面。

随附 `-EnsureCollision` 命令对 LOD0 蒙皮顶点进行全时段取样，转到根组件坐标后合并边界，添加安全余量。默认每秒 120 个样本、1 cm 加 1% 余量；旋转剧烈、路径弯曲、快速局部动作应加密取样并检查中间极值。抽样加余量不是连续运动的数学证明；视觉或加密检查发现越界就扩大盒体，直到覆盖全过程。

有多个内部组件时将所有组件的相对变换和全程运动纳入联合包络，不能只检查第一个网格。报告需记录实际组件/顶点/样本数量。盒体用于交互和碰撞代理，不证明各零件运动中互不穿插。

默认 Profile 为 BlockAllDynamic，QueryAndPhysics，支持 Visibility 查询；根据用户的实际用途调整通道。记录最终中心、Extent、取样密度、Profile 和验证结果。`BoundsScale` 仅影响渲染裁剪，不是碰撞，不能代替 Box Collision。Scale/父级变换变化后重新检查盒体范围。

已有盒体或 PhysicsAsset 不合适时说明修正依据，不能静默删除原物理设置。随附命令可添加缺失碰撞，并复验/更新本流程创建的运动盒体；其他已有碰撞保留，只有完整覆盖且通道检查通过才记为通过。不能因为 `existingCollision=true` 就跳过复验，也不能把此工具称作任意 PhysicsAsset 修复器。

## 验收

- 0、0.1…1、任意小数、随机顺序重复跳转；同进度同姿态，无累计误差。
- 两端精确到位；N 或指定键中途反向不跳变；手动进度接管；中间再按键继续展开。
- 时长改变有效；短剩余路程按比例缩短时间；动画不自行播放。
- UserVariable 正确赋值；两函数互不调用，Control 只有 DeltaSeconds/Toggle 输入，UserVariable 含所有暴露业务参数。
- 检查刚性、材质、明显穿插、细小零件压缩误差、展开裁剪、镜头自由度。数值通过不能代替视觉审查。
- 检查盒体包围全程，碰撞通道和实际射线或角色行为符合用途。
- 至少在实际 PIE 运行实例中验证键盘和 Details 编辑。默认使用后台自动化，不为检查截图而自动申请屏幕控制。已有 harness 的路径和按键需随配置生成，不能拿旧报告替代新任务。

多模型同屏时检查输入是否被某个 Actor 消耗；默认同键响应需符合本次演示需求，前端也可逐实例调用 UserVariable。姿态取样耗时仅反映 CPU 更新，不等于实际渲染 FPS；大模型实际流畅度需用对应场景实测。

导入修订使用 ImportOnly 保留用户蓝图编辑；完整重建只用于明确要重新生成的工作副本。打包前再次检查上述内容，碰撞变化也要验证并保存后再迁移。
