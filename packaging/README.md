# AnyLoc macOS 发布包

## 构建

在 Apple Silicon Mac 上运行：

```bash
zsh packaging/build-macos.sh
```

产物：

- `dist/AnyLoc.app`
- `dist/AnyLoc-macOS-0.1.0-arm64.dmg`

## 同事使用

1. 双击 DMG，把 `AnyLoc.app` 拖到 Applications。
2. 第一次打开如果被 macOS 拦截，在 Finder 中右键 AnyLoc.app，选择“打开”。
3. 连接已开启“开发者模式”的 iPhone，点“信任此电脑”。
4. 打开 AnyLoc 后，网页控制台会自动出现；选择地图位置并点击“设为模拟定位”。

再次双击 AnyLoc 会复用已有服务并重新打开网页，不会重复占用 USB/DVT 连接。

该包针对 Apple Silicon（arm64）构建。地图使用 Leaflet/OpenStreetMap，首次使用需要网络访问地图资源。

## macOS 提示

当前包使用 PyInstaller 的 ad-hoc 签名，未做 Developer ID 签名和公证。首次打开如果 macOS 拦截，请在“系统设置 → 隐私与安全性”中找到 AnyLoc 并选择“仍要打开”。
