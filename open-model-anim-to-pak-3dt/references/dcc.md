# DCC 制作与预览

## 发现与输入

运行 `scripts/workflow.py discover`，结合用户指定路径和本机安装记录确认可执行文件。发现结果只证明文件存在，还要验证批处理可启动、许可可用、所需导入导出模块可用。Maya 可用则默认使用；否则选择本机可用替代 DCC 并说明选择。没有可用软件才询问用户，不能自动下载或购买软件。

Maya 后端 `assets/Tools/Maya/explode_pipeline.py` 由 mayapy 运行：

```text
inspect SOURCE --output INSPECTION.json
create-config SOURCE --output MOTION.json --name OBJECT_ID
build MOTION.json --output GENERATED_DIRECTORY
fixture --output NEW_FIXTURE.ma
```

支持 `.ma/.mb/.fbx/.obj`。每个逻辑零件应有独立网格变换。检查 cm 单位、负缩放、非均匀缩放、材质、层级和重复名称。源场景禁用脚本节点执行；不保存覆盖源文件。

`create-config` 的空间布局只是初稿。没有参考视频时以小件先动、主体错峰、缓起缓停为默认节奏，按结构编辑配置后构建。默认 30 fps / 90 帧（3 秒）。随附 Maya 后端目前固定 30 fps；用户要求其他帧率时同时适配导出、取样和验证，不只更改配置数字。

每个运动零件一根骨骼，全部顶点权重 1；保存稳定 partId、骨骼名、偏移、旋转、start/end 和源哈希。模型改变时重新检查映射，不直接更新哈希绕过不一致。

输出独立 Maya 工作文件、FBX 2018、数值验证样本及预览用几何。只导出完整到展开；预览视频可以包含停留和回装，不能把整段往返直接映射为 Progress 0–1。

## 预览确认

优先使用已有 DCC 的批渲染或可用的后台渲染器。随附 `render_animation_preview.py` 是 Blender 后台渲染已制作 FBX 的实现，**不是 Blender 动画制作后端**。没有 Blender 时使用 Maya 可用渲染器，不把渲染器缺失误报为没有 Maya。

```text
blender --background --factory-startup --python render_animation_preview.py -- GENERATED_DIRECTORY OBJECT_ID
```

查看输出视频的完整性与清晰度，再给用户播放。固定观察视角覆盖全过程；观察视角动画与拆解进度分离。缩略图使用同一确认版本在 Progress=1 的真实渲染图，避免 AI 编造零件结构。

只有收到用户对已展示版本的明确认可，才记录 `animation_approval.json`：`status=approved`、真实 `userConfirmation`、`fbxSha256`、`mayaFileSha256`、`previewSha256`。用 `workflow.py check-approval` 核验版本。制作修改后旧确认自动失效。不要携带历史试跑的 `PilotRunThrough` 例外。
