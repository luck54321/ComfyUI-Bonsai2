# ComfyUI Bonsai 2 27B

本地运行 PrismML Ternary Bonsai 2 27B，支持文字问答、图片理解和思考开关。输出为回答文本与思考文本，可连接其他文本节点。它不负责直接生成图片。

## 使用

1. 完成安装后重启 ComfyUI，双击空白处搜索 `Bonsai 2`。
2. 加入 `Bonsai 2 27B - Local Chat`，填写 `prompt`。将 `text` 输出连接 ComfyUI 自带的 `Preview as Text`，查看回答。
3. 需要看图时，把 `Load Image` 的 IMAGE 输出连接 `image`。多张图片会放入同一个问题。
4. 点击运行。首次加载需要等待；每次执行结束都会退出本节点启动的运行程序，释放模型内存。再次运行会重新加载。取消任务也会停止本节点启动的程序。

`examples/Bonsai2-text.json` 可直接拖入 ComfyUI。

## 参数

- `thinking`：默认关闭；打开后，思考内容从 `reasoning` 输出。复杂问题建议提高 `max_tokens`。
- `gpu_layers`：新节点默认 -1，由运行程序按空闲显存自动选择显卡层数；99 尽量全显卡；0 为 CPU。旧工作流里的 24 不会自动改变，需要手动改成 -1。CPU 参与越多通常越慢。
- `release_comfy_models`：默认开启，运行前卸载 ComfyUI 已加载模型的显卡权重并清理空闲显存；后续绘图节点按需重新加载。不关闭其他应用程序。
- `image_max_tokens`：默认 0，保留运行程序原有图片细节设置。看图速度优先时可设 1024，代价是小字识别和细节判断可能变差；不修改原图文件。
- `context_size`：上下文容量，越大占用越多；测试可先用 4096。模型文件约 5.95GB 不代表运行时只占这些内存。
- `max_tokens`：输出长度上限；思考也会占用输出预算。
- `seed`：固定值便于重复测试。

生成阶段显示进度条，按已生成 token 数与 `max_tokens` 上限计算，不是预计剩余时间。正常提前结束时进度条直接完成。图片传输使用低压缩 PNG，像素不变，也不产生图片临时文件。每次执行仍会重新加载模型，以保证结束后释放内存。

## 安装及目录

适用于 Windows x64、支持 CUDA 13.3 的 NVIDIA 驱动和新版 ComfyUI V3 节点接口。使用 ComfyUI 已有的 requests、numpy、Pillow，不升级 Python 环境。

下载仓库并将文件夹命名为 `ComfyUI-Bonsai2`，放入 ComfyUI 的 `custom_nodes`。在这个节点文件夹打开 PowerShell，执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\install_windows.ps1
```

仓库只包含源码、安装脚本和示例，不包含模型和运行程序。安装后专用运行程序约占 721 MiB，主要是 NVIDIA CUDA 计算库；模型另外约占 6.58GB。当前安装脚本面向 Windows x64，未验证 Linux、macOS、AMD 或 Intel 显卡。

已有模型时，先将 `model_paths.example.json` 复制为 `model_paths.json`，把示例路径改成自己的绝对路径，再执行安装脚本。安装脚本会验证已有文件，校验成功后直接复用。如果只安装运行程序，可添加 `-SkipModels`。

如果 GitHub 下载速度很慢，可加 `-UseGithubMirror`；这是第三方下载中转，安装时仍逐个校验固定 SHA256。模型下载来自 ModelScope 的 prism-ml 同名仓库。下载中断后可重复运行，脚本会继续下载并验证。已有文件校验失败时会保留原文件并报错。

- 模型默认目录：`ComfyUI/models/LLM/Bonsai-2-27B/`。可通过 `model_paths.json` 指定已有模型目录，直接读取，不复制。也可在 ComfyUI 的 extra_model_paths 中配置 `bonsai2`，但安装脚本仅读取 `model_paths.json` 或使用默认目录。
- 专用程序：本节点目录的 `runtime/`
- 运行日志：成功运行或取消后自动删除；发生错误时保留在 `runtime/server-*.log`，供排查。
- 安装脚本先校验现有文件，缺失时才下载 PTQ1_0 主模型和 Q8_0 图片投影模型，合计约 6.58GB。程序压缩包安装后自动删除。模型下拉菜单只显示已安装的版本；如果一个也没找到，会显示 PTQ1_0，运行时提示检查模型路径。

程序只监听本机临时端口并使用随机密钥，不调用云端推理，也不修改 Ollama。不要用旧版普通 llama.cpp 替代随附专用程序：PTQ1_0 需要 PrismML 分支支持。

## 来源

- 模型说明：https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf
- 专用程序：https://github.com/PrismML-Eng/llama.cpp
- 示例参数：https://github.com/PrismML-Eng/Bonsai-demo
- 固定程序版本：`prism-b12128-d7b2896`，Windows CUDA 13.3。

模型与运行程序各自遵循上游许可；本节点不重新打包模型。

## 常见问题

- 提示上下文不足：提高 `context_size`。长提示词加大图可先尝试 16384，并保持 `thinking` 关闭。
- 生成慢：将 `gpu_layers` 设为 -1，并开启 `release_comfy_models`。其他程序占用显存时仍可能降速。
- 提示缺少文件：检查模型路径；看图还需要 `Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf`。
- 安装或更新后找不到新参数：关闭并重新启动 ComfyUI，再刷新网页。
