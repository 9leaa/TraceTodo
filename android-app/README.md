# TraceTodo Android Native Client

面向 Android 8.0+（`minSdk 26` / `targetSdk 34`）的原生轻量客户端工程：
- 默认直连线上正式 HTTPS 域名：`https://todo.531party.cn`（网络受限时自动回退 `http://43.228.79.133:18090`，并支持应用内环境切换弹窗）；
- 支持全站 15 套亮/暗主题的状态栏与导航栏沉浸式动态变色同步；
- 原生相册多图选择（记忆/随记图片上传）、麦克风权限动态授权（语音极速录入）与物理返回键智能关闭弹窗。

## 一键打包正式版 / 测试版 APK

```bash
cd android-app
chmod +x build_android_apk.sh

# 正式版 (com.tracememory.tracetodo -> dist/TraceTodo-Android.apk)
./build_android_apk.sh

# 测试版 (com.tracememory.tracetodo.test -> dist/TraceTodo-Test-Android.apk)
./build_android_apk.sh --test
```
