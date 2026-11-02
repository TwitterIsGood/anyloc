# AnyLoc

在 Mac 网页地图上切换 iPhone 定位，方便开发调试和场景模拟。

支持 USB 连接、地图选点、恢复真实定位、路线播放和定位保活。重复打开 App 会复用后台服务。

## 使用

1. USB 连接 iPhone，信任电脑并开启开发者模式。
2. 打开 `AnyLoc.app`，进入自动打开的网页。
3. 点击地图选点，再点“设为模拟定位”；点恢复按钮可恢复真实定位。

当前打包版本面向 Apple Silicon Mac；已在 iOS 27.0.1 真机验证。地图需要联网。

## 源码运行

安装 Python 3.12 和 [uv](https://docs.astral.sh/uv/)，在项目目录执行：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
./.venv/bin/anyloc web
```

## 打包 macOS App / DMG

```bash
zsh packaging/build-macos.sh
```

产物位于 `dist/`。当前使用 ad-hoc 签名，未做 Apple 公证。

蓝牙适配代码为实验功能，需要额外运行时和认证材料；USB 模式不依赖它们。
