# ADHD 眼动实验

使用前置 RGB 摄像头采集注视实验，按实验编号关联两段记录。默认方案包含两段各 8 分钟的注视任务，中间休息 15 分钟，每段前后各做一次 9 点校准。两段视频和四组校准 JPEG 帧可交给处理端。

| 程序 | 入口 | 功能 |
| --- | --- | --- |
| CPU 采集端 | `app.py` | 采集实验、运行测试，使用 AFF-Net 校准及处理数据 |
| 纯采集端 | `raw_capture_app.py` | 保存原始视频和校准帧，供后处理 |
| 数据处理端 | `processor_app.py` | 使用 AFF-Net 批量处理，导出 TXT、散点图和回放 |

两种采集端都会发布 LSL 流 `ADHD_EyeTracker_Markers`，默认双段流程发送 13 个标记。完整标记表、局域网接收和时间同步见 [LSL 接收说明](lsl_receiver/局域网接收说明.txt)，Python 示例为 [receive_triggers.py](lsl_receiver/receive_triggers.py)。

处理端为每段独立导出 20 Hz TXT，列顺序为 `time_s`、`timestamp_utc`、`x_cm`、`y_cm`、`distance_cm`。每段的人员信息、设备参数与采集时间线保存在该段 `session.json`。

## 从源码运行

Windows x64 下可使用 Python 3.12。以下命令在项目目录的 PowerShell 中运行。首次启动前可分别设置 `ADHD_EYETRACK_DATA_DIR`、`ADHD_EYETRACK_RAW_DATA_DIR` 或 `ADHD_EYEPROCESSOR_DATA_DIR`，将各程序的数据保存到项目外。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -X utf8 raw_capture_app.py
```

纯采集源码启动使用 `capture_model_identity.json` 中的校验信息。CPU 采集端和数据处理端启动或处理前，须从实验室获准的共享位置取得以下模型文件。

- `models/affnet_mixed/backbone.pt`
- `models/affnet_mixed/model_cpu.ts`
- `models/face_landmarker.task`

AFF-Net 文件可按 [模型清单](models/manifest.json) 校验 SHA-256。人脸关键点文件为 [MediaPipe Face Landmarker](https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task)。取得模型后，可把启动命令的入口替换为 `app.py` 或 `processor_app.py`。CPU 版使用 CPU 版 PyTorch 构建。需要 GPU 的处理端可使用与机器驱动兼容的 CUDA 版 PyTorch 环境。

## 构建 Windows 安装包

构建环境需要 PyInstaller 和 Inno Setup 6。三个安装包共用经过验证的模型清单，构建前须备齐上述模型文件。纯采集安装包保留模型标识，供处理端匹配素材。

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe build_tool.py --project . --output .\release_installer\capture_raw --edition capture_raw --python .\.venv\Scripts\python.exe
```

CPU 采集端使用 `--edition capture_cpu`，数据处理端使用 `--edition processor`，各自使用独立输出目录及相应的 Python 环境。构建程序会在生成安装包前运行自检。运行时依赖声明收录在 `licenses/`。
