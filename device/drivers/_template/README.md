# 驱动骨架模板（`device/drivers/_template/`）

**复制到 `device/drivers/<型号>/` 再填写。** 它存在的唯一目的是防止模板腐烂：
一个没人构建的骨架，几个月后必然编不过，而到那时它已经成了所有人的抄写来源。

## 为什么不在主构建里

`device/CMakeLists.txt` **不** `add_subdirectory` 本目录。理由：

- 模板里的占位型号不是真实硬件，把它编进设备端产物没有意义；
- 更关键的是，**能被主构建编过的模板不等于能被复制后的模板编过**——
  占位型号一旦漏改，主构建照样通过，而复制出来的驱动是坏的。

因此 CI 有一个独立的 `template` job：把本目录复制到临时目录、替换占位型号、
在**复制后的副本**上配置与构建。腐烂会在 CI 里红，而不是在半年后。

## 占位型号：`_model_`

三个大小写变体，复制后全部替换：

| 占位 | 变体 | 出现位置 |
| --- | --- | --- |
| `_model_` | 小写 | 文件名、库名后缀、JSON 键、Python 模块名 |
| `_Model_` | PascalCase | 类名 `_Model_Driver` |
| `_MODEL_` | 大写 | 宏名 `_MODEL_OD_VERIFIED` |

用带下划线的形式是为了**可机检**：替换后仍有残留就说明漏改，CI 里一句 `grep -rn '_model_' .` 即可发现。
普通占位词（如 `MODEL`）没有这个性质——它可能恰好是某个真实标识符的一部分。

## 复制后要改哪几处

1. **目录名**：`device/drivers/<型号>/`，用型号不用中文别名（同 `docs/硬件手册/` 的约定）。
2. **三处占位**：全目录替换（`grep -rn '_model_\|_Model_\|_MODEL_' <新目录>` 必须无输出）。
3. **`CMakeLists.txt`**：`KM3_DRV_MODEL` 改成真实型号。
4. **`driver/od.json`**：按 `docs/硬件手册/<型号>/协议/对象字典.md` 填真实索引与子索引。
   **只填手册里查到的值**，查不到就保持 `unverified` 且 `cite` 为 `null`。
5. **`driver/od_generated.hpp`**：由 `tools/hwdriver/gen-od.py` 从 `od.json` 生成，**不要手改**。
6. **`docs/硬件手册/<型号>/README.md`**：本模板不生成它。
   全部 OD 条目核对完毕后，把 `od.json` 里的 `status` 改成 `verified` 并补上 `cite`，
   重新生成 `od_generated.hpp`，`_MODEL_OD_VERIFIED` 才会翻成 `1`。
7. **`CONTRACT.md`**：只保留两节。**反馈能力不写在这里**——
   它只在 `docs/硬件手册/<型号>/README.md` 声明一次。同一事实只能有一个家。
8. **`tools/_model_.py`**：接上真实的单位、通道与安全限位。

## 为什么 `od_generated.hpp` 默认是毒的

`_MODEL_OD_VERIFIED` 默认为 `0`，此时文件里是一个 `#error`。
**编不过是有意设计**：对象字典里的索引、子索引、标度因子在没有手册时全是猜的，
而猜错的代价是「电机按错误的方向/距离动」这类现场最难反查的故障。
把「未核对」变成编译期错误，好过让它变成一个能跑但会动错的行为。

旁路开关 `KM3_ALLOW_UNVERIFIED_OD=1` **只允许用于 CI 与未接电机的回环验证**，
代码里留下了 `unverified` 标记与运行时拒绝路径与之配套。

## 依赖白名单

驱动**只**链架构层，绝不链兄弟驱动：

| 依赖 | 作用 |
| --- | --- |
| `km3_core` | 实时原语、运行时类型、时间源 |
| `km3_hal` | 硬件抽象（`ICanDevice` 等接口 + 模拟设备） |
| `km3_canopen` | CANopen 协议栈（SDO / PDO / NMT） |
| `km3_driver_api` | 驱动对外接口（上层只认它，不认具体型号） |

其中 `km3_canopen` 与 `km3_driver_api` **当前尚未定义**，因此 `CMakeLists.txt` 里
存在的就链、缺失的记名（`-DKM3_DRV_STRICT_DEPS=ON` 时缺失即为配置错误）。
理由：直接 `target_link_libraries` 一个不存在的目标，会退化成 `-lkm3_canopen`
并在**链接期**报错，错误发生在离根因很远的地方。

**禁止链兄弟驱动**：一旦 `km3_drv_a` 链了 `km3_drv_b`，两者就无法分别测试、
分别版本化，且换用一个型号会连带改动另一个的依赖闭包。型号之间要协同，
走 `km3_driver_api` 或上层编排。

## 复制后的构建与测试

```sh
MODEL=yz100                                  # 小写，用作目录名 / 库名后缀 / 模块名
PASCAL=Yz100                                 # PascalCase，用作类名前缀
UPPER=YZ100                                  # 大写，用作宏名前缀

DEST=device/drivers/$MODEL
cp -r device/drivers/_template "$DEST"

# 三次替换，**顺序不可颠倒**：_MODEL_ 是大写变体，先替换它会吃掉 _Model_ 的
# 前半段（两者都以 _ 开头、以 Model 续），结果把 PascalCase 变成残缺标识符。
grep -rl '_MODEL_' "$DEST" | xargs -r sed -i "s/_MODEL_/${UPPER}_/g"
grep -rl '_Model_' "$DEST" | xargs -r sed -i "s/_Model_/${PASCAL}/g"
grep -rl '_model_' "$DEST" | xargs -r sed -i "s/_model_/${MODEL}/g"
# 文件名也要改（sed 不改名）
for f in $(find "$DEST" -depth \( -name '*_model_*' -o -name '*_Model_*' \)); do
  mv "$f" "$(echo "$f" | sed "s/_model_/${MODEL}/g; s/_Model_/${PASCAL}/g")"
done

# 残留即漏改
grep -rn '_model_\|_Model_\|_MODEL_' "$DEST" && echo "占位未替换干净" && exit 1

# 构建：驱动依赖 km3_core / km3_hal，必须与仓库一起构建。
# 副本已落在 device/drivers/<型号>/。
cmake -S device -B build-tmpl -DKM3_ALLOW_UNVERIFIED_OD=1
cmake --build build-tmpl -j"$(nproc)"
ctest --test-dir build-tmpl -R "$MODEL" --output-on-failure

# Python 工具
python3 -m py_compile "$DEST"/tools/*.py
```

## 目录内容

| 路径 | 内容 |
| --- | --- |
| `CMakeLists.txt` | 库目标 `km3_drv_<型号>`；依赖白名单见文件内注释 |
| `CONTRACT.md` | 「阻塞项」与「RT 可达性」两节 |
| `driver/od.json` | 对象字典来源文件，每条带 `status` 与 `cite` |
| `driver/od_generated.hpp` | 生成物，首行有 DO NOT EDIT 标记 |
| `driver/_model__driver.{hpp,cpp}` | 驱动接口与管理器 |
| `tests/test__model__.cpp` | 用 `device/tests/test_harness.hpp` 的零依赖框架 |
| `tools/_model_.py` | 每硬件一个 TUI 工具（复用 `tools/hwtui`） |
