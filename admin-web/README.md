# admin-web — km3 管理页面

管理页面（`docs/前后端/功能清单.md` §二 管-F01 至 管-F06）。
**设计权威是 `docs/架构设计/管理端Web程序架构.md`**，本文件只讲怎么跑。

## 职责

后台运维界面：总览、传感器历史、日志查询与实时流、用户与邀请、固件下发。

**不下发运动控制命令。** 命令签名要求用户侧 Ed25519 私钥，
私钥只存在于 Android 控制台的 Keystore 中，浏览器无法提供等价的密钥保护。
运动控制与紧急停止由控制台负责，理由见设计文档 §1.2。

## 依赖

| 运行时 | 版本 |
| --- | --- |
| Node | 20 或更高（本机在 24 上验证） |
| pnpm | **10 或更高**（本机在 11.7.0 上验证） |

pnpm 版本下限是硬要求，不是偏好：构建脚本白名单写在 `pnpm-workspace.yaml` 的
`allowBuilds` 中，pnpm 9 不识别该键，安装会以 `ERR_PNPM_IGNORED_BUILDS` 非零退出，
CI 第一步即失败。

部署**不需要 Node**：构建产物是静态文件，由既有的 nginx 提供。

## 开发

```sh
pnpm install
pnpm run dev          # 开发服务器，/api 反代到 127.0.0.1:8080
```

## 验证（与 CI 完全一致）

```sh
pnpm run typecheck    # vue-tsc --noEmit
pnpm run test:unit    # Vitest
pnpm run build        # 构建产物到 dist/
```

`pnpm run build` 不能省：类型检查通过不代表构建通过，
`.vue` 单文件组件的模板编译有自己的错误面。

## 结构

```
src/api/        唯一与服务器通信的层。只有这里允许出现 URL 与请求逻辑
src/stores/     Pinia：session / environment / devices
src/router/     路由表 + 权限守卫
src/permissions.ts   权限矩阵（数据，不是散落的 if）
src/views/      一页一个文件，与 管-F## 对应
src/components/ 环境横幅、权限门、空态、错误态
```

三条硬约束：

1. **只有 `src/api/` 允许出现 URL 与请求逻辑。** 组件与 store 一律经此调用。
2. **只有 `src/permissions.ts` 允许出现角色判断。**
3. **终端环境标识常驻可见，且刻意不在刷新后保持。**
   一个能跨刷新存活的"当前环境"，最容易在操作员以为还在演示环境时把固件推到生产。

## 已知问题（实测数据，非估计）

### 产物体积

构建产物约 1.6 MB（gzip 约 520 kB），大头是 Element Plus：

| chunk | 原始 | gzip |
| --- | --- | --- |
| `vendor-element` | 938.6 kB | 301.6 kB |
| `vendor-echarts` | 512.8 kB | 171.9 kB |
| `vendor-vue` | 111.9 kB | 43.6 kB |
| 全部文件合计 | 约 1560 kB | 约 520 kB |

> 体积口径：表中是**原始字节**（`ls` 求和）。用 `du -sh dist` 复核会看到约 1 MB 甚至更小，
> 那是文件系统压缩后的磁盘占用（本项目开发机为 ZFS），**不是**产物变小了。
> 要复核请用 `du -sh --apparent-size dist` 或直接求和文件大小。

**按需引入**（`unplugin-vue-components` + `unplugin-auto-import`）**没有解决 JS 体积**。
实测接入前后只差 1.4 kB，因为解析器给出的是
`import { ElMessage } from 'element-plus/es'`，而 `element-plus/es` 是**桶文件**，
re-export 整个库。已验证产物里存在 `cascader` / `calendar` / `carousel` / `colorpicker`
等从未使用的组件。

按需引入仍然保留，因为它负责**样式**的按需注入，且省去手写每个组件的 import。

要真正减体积需要直接引用组件子路径并自管样式，或换更轻的组件库；
两者都是需要单独评估的改造，**当前未做**。

对内部管理后台而言，520 kB gzip 首屏在局域网/办公网下可接受，
因此这不是阻塞项，但记录在此以免被当成已优化。

## 未实现项

| 项 | 状态 |
| --- | --- |
| Element Plus 的 JS 体积 | **未解决**，见上（样式按需已生效） |
| 工时统计（B10） | **占位页**。口径待业务确认，不做臆造聚合 |
| 跨环境总览（B11） | **接口预留**，同上 |
| 视频观看 | 当前不列入：浏览器端延迟与解码行为不可控，现场观看由控制台负责 |
| 登录第二因子 | 未设计。管理端有固件下发与用户管理权限，若对外网开放应补充 |
| 未使用的 `EmptyState` / `PermissionGate` 在部分页面 | 已定义未全部接入，属骨架期正常状态 |
