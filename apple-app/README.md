# 🍎 TraceTodo：纯原生 SwiftUI 跨端应用（macOS + iPhone iOS）

本目录包含 **100% 纯 SwiftUI 跨端通用工程**（[`TraceTodo.xcodeproj`](TraceTodo.xcodeproj) 与 [`Sources/TraceTodo/`](Sources/TraceTodo)），已使用 Xcode 26.3 工具链完成 **macOS 原生版、iOS 模拟器版与 iPhone 真机版三端零警告编译校验**，并全面融合 **DuoDo 风格马卡龙卡片、AI 拆解清单、周期重复/顺延、沉浸专注锁屏与好友监工小队**。

---

## 一、 核心升级特性（Mac + iPhone + Web/PWA 全端同步）

1. **DuoDo 风格新建/编辑卡片抽屉（6 色马卡龙色板）**：
   - 支持 **薰衣草紫 / 樱花粉 / 天空蓝 / 奶油黄 / 薄荷绿 / 蜜桃橙** 实时换肤。
   - **「个人任务 | 多人任务」** 分段切换：未开启监督的个人任务严格私有隔离；开启监督或切换多人任务后同步进入 **「👥 监工小队」**。
2. **📋 子任务清单 + `✨ AI 拆解`**：
   - 点击 `✨ AI 拆解` 时，后端自动检索 `trace-memory` 中的个人偏好、事实与背景记忆，由 `qwen3.8-flash` 拆解出 3～6 条个性化可执行子步骤，并在外层四象限卡片实时展示完成进度条。
3. **📅 日期 / 时间 / 周期重复 / 本地提醒通知**：
   - 支持「今天 + 具体时间」动态计算剩余小时数自动提权象限；
   - 支持 `每天 / 工作日 / 每周 / 每月` 周期重复（完成本期后自动归档并生成下一期）；
   - 原生 Mac / iPhone 端接入系统 `UNUserNotificationCenter` 准点提醒，Web/PWA 端接入浏览器通知与提示音。
4. **↻ 未完成跨日自动顺延 + AI 防拖延诊断**：
   - 默认开启「顺延任务」开关，跨日未完成自动顺延入今日并累计 `rollover_count`；连续顺延 ≥ 3 天触发 AI 诊断建议拆解或精简。
5. **🔒 沉浸专注锁屏模式 & 👥 监工小队**：
   - 专注倒计时全屏遮罩支持在锁屏页直接勾选子步骤；未完成提前退出需记录中断原因（iOS 原生端已在 `FocusLockView.swift` 预留 `ScreenTimeLockCapability` 扩展位以接入 `FamilyControls`）。
   - 「监工小队」支持计算监督完成率、按成员筛选，并可点击 **「⚡ 催一催 / 👍 点赞鼓励」**。

---

## 二、 在 Mac 上直接运行纯 SwiftUI 原生版

已经为你编译生成纯原生 SwiftUI 的 [`dist/TraceTodo.app`](dist/TraceTodo.app)，直接运行：

```bash
open ./apple-app/dist/TraceTodo.app
```

- **自动启动后端**：打开 App 时若检测到本地 `127.0.0.1:18090` 未运行，会自动在后台以 `--lan` 局域网模式拉起 `trace-memory` 引擎。
- **Mac 顶部状态栏（`MenuBarExtra`）**：屏幕右上角常驻 `🔥 待办数` 图标，随时点击即可一句话录入待办、勾选任务或一键新建彩色卡片。

---

## 三、 用 Xcode 26.3 运行到 iPhone 模拟器或真机 iPhone

### 1. 打开多平台工程 [`TraceTodo.xcodeproj`](TraceTodo.xcodeproj)

```bash
open -a /Applications/Xcode.app ./apple-app/TraceTodo.xcodeproj
```

### 2. 在 iPhone 模拟器或真机运行
1. **iPhone 模拟器**：顶部设备栏选任意 iPhone 模拟器，按 `⌘R` 启动（默认连接本机 `http://127.0.0.1:18090`）。
2. **真机 iPhone**：
   - 数据线连接 iPhone，在 Xcode 左侧点 `TraceTodo` → **Signing & Capabilities** → **Team** 登录并选择你的个人 Apple ID（如提示 Bundle ID 重复，将 `com.tracememory.tracetodo` 末尾改为你的缩写即可）。
   - Mac 终端运行 `python3 run_web.py --lan` 启动服务，打开手机端 **TraceTodo → 设置** 将服务端地址设为终端打印的 Mac 局域网地址即可实时同步。
