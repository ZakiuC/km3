# tsconfig 取舍说明

本文件解释 `tsconfig.json` 中几个**刻意**的取舍。配置会漂移，理由不会。

## 为什么不开 `exactOptionalPropertyTypes`

它本意是好的：区分"这个可选属性不存在"与"这个可选属性存在且为 undefined"。

**但它与 Vue 生态的组件类型声明不兼容。** 开启后，模板里任何
`<el-button size="small">` 都会报：

```
TS2322: Type 'string' is not assignable to type
  '{ readonly type: PropType<...>; readonly required: false;
     readonly validator: ...; __epPropKey: true }'
```

期望类型是 Element Plus 的**运行时 prop 声明对象**，而不是解析后的属性类型。
这是已知的旗标与第三方组件库 prop 类型冲突，不是我们的用法有错。

**代价评估**：它真正保护我们的地方是请求层（`RequestInit` 的 `body`/`signal`），
那里我们已经用条件赋值写对了；而它带来的噪音是模板里几十处无法干净修复的报错。
因此关掉，并在请求层保留显式判空的写法。

## 为什么保留 `noUncheckedIndexedAccess`

它抓到了真实缺陷：`const [from, to] = range.value`（`Date[]`）会得到
`Date | undefined`，而日期范围控件确实可能被用户清空。开启后该分支必须显式处理。

这类"索引可能不存在"的情况在本工程里很常见（分页项、环境列表首项），
且修复方式干净（判空并提前返回），因此保留。

## 为什么不 relax `noUnusedLocals` / `noUnusedParameters`

未使用的绑定在骨架期很常见（先铺接口、后接线），但保留它们能让"忘了接线"
在 CI 就暴露，而不是等到运行时页面缺一块。已因此抓到两处（`LogsView` 的 `env`、
`UsersView` 的 `reload`）。

## `auto-imports.d.ts` / `components.d.ts` 为什么要提交

`unplugin-auto-import` 与 `unplugin-vue-components` 的产物，声明了 `ElMessage` 等全局符号。
CI 顺序是 **typecheck → test → build**，若这两个文件不提交，
typecheck 阶段还没有它们，会得到一片 `TS2304: Cannot find name 'ElMessage'`。
它们是类型声明而非构建产物，因此不列入 `.gitignore`。
