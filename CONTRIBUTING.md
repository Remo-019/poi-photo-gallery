# 参与开发

感谢你愿意花时间。这份文档说明怎么把改动提进来，以及代码里那些看起来奇怪的决定是为什么。

## 环境

```bash
git clone https://github.com/<your-name>/poi-photo-gallery.git
cd poi-photo-gallery
python -m pip install -r requirements.txt      # Pillow，挑最优图与总览图需要
python -m pip install -r requirements-dev.txt  # 打包用 PyInstaller
```

Python 3.10+。提交前本地跑一遍 CI 里做的事：

```bash
python -m compileall -q poi_core.py gui.py cli.py tools
python cli.py --help
python tools/baidu_poi.py --help
```

## 提 Issue

- 采集失败、图片不对、报错 → 用 Bug 模板，**贴完整命令与 traceback，Key 打码**。
- 新功能 → 用功能建议模板，先说场景再说做法。

## 提 Pull Request

1. 从 `main` 切分支：`git checkout -b feat/短描述`。
2. 改动范围尽量单一，一个 PR 解决一件事。
3. 提交信息用中文或英文均可，但要说清「改了什么 + 为什么」。
4. 在 PR 描述里贴出你实际跑过的命令与结果（哪怕是 `--dry-run` 的输出）。
5. 涉及采集逻辑的改动，请在 `--dry-run` 模式下验证，不要为了一次 PR 把配额刷光。

## 代码约定

- **采集全程不引入第三方依赖**。`poi_core.py` / `cli.py` / `tools/baidu_poi.py` 只用标准库；Pillow 只允许出现在 `tools/select_best.py` 与 `tools/make_contact_sheet.py`。
- 新增网络请求必须有超时、有重试上限、有可配置的 `--delay`。
- 所有落盘路径由调用方传入，不要在模块里写死目录。
- 日志走进度回调，不要在库里直接 `print`。
- 注释与文档用中文，与现有风格一致。

## 两处容易被改坏的地方

1. **高德 `limit_1` 不能去掉**。CDN 默认返回的是缩略图；`resize,w_N` 参数只是把缩略图放大，看着尺寸大了但细节是插值出来的假高清。判定真原图的办法见 [docs/技术要点.md](docs/技术要点.md)。
2. **百度相册 URL 的 `x-bce-process` 要整段去掉**，只删参数值不够。另外 `qt=pr3d` 是全景图，必须过滤掉。

## 行为准则

- 只讨论代码与事实，不针对人。
- 不提交他人的个人信息、密钥、Cookie、采集到的原始照片。
- 采集频率、版权与合规问题属于使用者责任，见 README「使用边界」。
