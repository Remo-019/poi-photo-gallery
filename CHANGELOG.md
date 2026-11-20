# 更新记录

本项目的版本号遵循 `主版本.次版本.修订号`。

## v1.0.0 — 2026-11-20

首个公开版本。

**采集能力**

- 高德 Web 服务 API：关键词检索 POI、逐机构下载照片、写 `manifest.csv`
- 百度网页接口：`qt=s` 检索 + `qt=inf` 详情/相册，免 Key
- 原图还原：高德 CDN 补 `limit_1`；百度相册去掉 `x-bce-process` 整段 query；`qt=pr3d` 全景图过滤
- 真原图判定：对同一 URL 递增请求宽度，尺寸不再增长处即为原图，避免把插值放大当成高清
- 双源分目录存放，便于交叉比对；同名机构实测高德胜出居多，默认以高德为主源、百度为补充

**工具链**

- `cli.py`：`--city --keywords --source --max-poi --photos --out --amap-key --types --delay --no-best --dry-run`，Key 支持 `--amap-key` 与 `AMAP_KEY` 环境变量
- `gui.py`：tkinter 桌面界面，参数与 Key 存用户配置目录，按钮区含停止 / 挑最优图 / 生成总览图 / 打开文件夹 / 历史记录
- `tools/baidu_poi.py`：百度单源单文件版
- `tools/select_best.py`：按 `manifest.csv` 挑最优图并盘点分辨率
- `tools/make_contact_sheet.py`：拼总览大图
- `build.spec`：PyInstaller 单文件打包

**工程**

- 运行时仅依赖标准库，Pillow 可选
- GitHub Actions：三平台 × Python 3.10/3.13 语法检查与入口自检
- 配置目录首次启动自动迁移旧版程序名下的 `config.json`
