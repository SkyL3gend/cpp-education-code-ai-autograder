# 手机号校验 WebAssembly 验证记录

日期：2026-10-22。平台：当前 Windows 家庭版 x64 机器。本次范围为可信示例程序的可行性验证。

## 已实现的链路

Web 上传 `.cpp` 或 ZIP → 提交快照 → 真实模型生成与独立审查测试驱动 → 契约及源码引用校验 → 原始编译单元检查 → clang 编译为 WASIp1 → Binaryen 转换异常指令 → Wasmtime 试跑 → 冻结计划 → 4 个正式用例 → 确定性断言比较 → 网页、JSON、CSV 结果。

没有本机学生 EXE 执行回退。`subject.wasm` 是真实 WebAssembly；模拟后端没有参与这些结果。

## 首版（v1）实测结果

| 输入 | 真实运行记录 | 比较结果 | 活动耗时 |
|---|---|---|---|
| 正确版单文件 `phone_correct.cpp` | `run_20261022_102546_c4c89d` | 4 MATCH、0 MISMATCH；8 次用例断言比较符合 | 72 秒，含模型和适配修复 |
| 缺陷版 ZIP `phone_faulty.zip` | `run_20261022_102830_8e7f97` | 3 MATCH、1 MISMATCH | 39 秒，含模型和一次适配声明修复 |

缺陷版在 `c_non_digit_11` 中，`created` 预期 false、实际 true；联系人列表额外写入号码 `1380013800a` 的联系人。`a_created` 与 `a_contacts` 同时不符合预期。
时间从 RUN_STARTED 至 RUN_FINISHED，不含排队等待；模型服务响应及适配重试会导致变化，以上不是固定性能承诺。

性能采样使用已完成的正确版驱动，不再次请求模型：

- 同一组源码重新编译 3 次（包含封闭 VFS 建立、clang 和异常指令转换）：6941、7675、7053 ms，中位数 **7.053 秒**。
- 10 次独立 worker 运行（每次重新启动与 JIT）：588—702 ms，中位数 **600.5 ms**。
- 该次含 C++ 标准库和完整驱动的 WASM 模块：2,758,094 字节。
- 便携 EXE 在新解压目录、PATH 仅保留 System32 时，HTTP 就绪约 **1.696 秒**；ZIP 解压约 **1.933 秒**。
- 当前打包测量约 **77 MiB ZIP / 192 MiB 解压文件**，内含 Python、Web 依赖、Wasmtime、裁剪后的 WASI SDK 和 Binaryen；最终精确字节数与 SHA-256 见 `data/wasm-pilot/package-metrics.json`。

独立解压验证补充：将 PATH 限定为 System32，清除 Python、编译器、题库和模型安装路径后，使用便携 EXE 重放上述真实模型已生成、冻结并校验的判断程序。

| 原始输入 | 便携 EXE 重放运行 | 结果 | 编译及执行耗时（不调用模型） |
|---|---|---|---|
| 正确版单文件 | `run_20261022_172517_436c77` | COMPLETED，4 MATCH | 11.94 秒 |
| 缺陷版 ZIP | `run_20261022_172528_9eda08` | COMPLETED，3 MATCH、1 MISMATCH | 9.92 秒 |

该测试再次使用内置 clang、Binaryen、Wasmtime 编译并实际执行，并非直接展示旧观测。记录见 `data/wasm-pilot/portable-replay.json`，新报告保存在独立解压目录。

**首版新模型请求的限制**：补齐便携 Python 的公共证书策略文件后，HTTPS 已正常连接；当时独立包新上传请求返回 HTTP 402 / Insufficient Balance。因此首版没有完成包内新的成功模型生成。2026-10-22 23:03 只读复查服务状态，`is_available=true`，后续在线验收结果单独记录。旧错误保存在 `data/wasm-pilot/portable-final.log`，没有关闭证书校验或使用伪造模型响应。

测量证据保存在 `data/wasm-pilot/benchmark.json`、`web/runs/<run_id>/objective_report.json`、`events.jsonl` 以及 `portable-smoke.json`。运行数据、模型 Key、用户配置均不进入发行包。

## 首版限制与评分资格

Wasmtime worker 只连接标准输入输出；未开放目录、环境变量、宿主函数或继承的网络 socket。设置燃料预算、线性内存上限，并由 Windows Job Object 补充墙钟、内存、进程树及输出限制。燃料不是毫秒计时器。

实际测试覆盖：正常运行、死循环被终止、宿主 `system` 导入失败、无预开放主机目录、内存增长受限、拒绝本机二进制、输出超限立即终止、真实 C++ 编译执行、编译时不能 include 指定范围外的文件。相关 Web、契约、门禁等首轮回归共 61 项通过；增加本机同源保护及 worker 分类后，相关 14 项测试通过；既有重放回归 2 项通过。上述测试集合有重叠，不将数量相加作为唯一测试数。

**首版编译阶段是未隔离的宿主原生 clang/Binaryen 进程。** 固定参数、封闭 VFS 和资源限制不能替代 OS 级编译器隔离。后续版本已改为强制 AppContainer，验收范围与实测记录在下方单独列出，不能将首版性能或验证结果视为新版数据。

判断程序保持 CHECKED。后续版本增加有限范围的独立夹具回归，仍未获得权威目标校准/人工签审。页面保留真实 MATCH/MISMATCH 和具体差异，总体仍为 INCONCLUSIVE、`grade_eligible=false`，不能用作正式成绩。

## 便携使用

下载/复制 `dist/cpp-assessor-wasm-pilot.zip`，解压到可写目录，双击 `START.cmd`。访问 `http://127.0.0.1:8766/`，在手机号校验题上传 `examples/wasm_phone` 中的样例。
本机已占用 8766 时，运行：

```powershell
.\cpp-assessor-wasm-pilot.exe web --backend wasm_pilot --allow-unattested-backend --port 8767
```

模型服务需要联网和使用者自己的 API Key。包内附带本地执行依赖，应用运行时不自动下载工具。当前只验证当前机器上独立解压使用；未完成干净 Windows 镜像、其他架构、杀毒软件兼容性和完整再分发许可验收。特别是 app-local VC++ DLL 在公开发布前仍需核对再分发条件。

服务仅监听回环地址，并拒绝外部 Host、非同源浏览器提交，避免其他网页直接触发本机试验。此处没有实现多用户登录、公开服务或完整 CSRF token 会话系统。

## 维护者重建

```powershell
python -m pip install -e ".[dev,wasm]" pyinstaller
python tools/prepare_wasi_pilot.py
python tools/build_wasm_portable.py
```

维护者准备阶段需要下载；源码 `git clone` 本身不包含二进制依赖。WASI SDK 和 Binaryen 从官方固定版本下载，并验证预期 SHA-256。

版本：WASI SDK 34（LLVM 23.1.0-rc3）、Binaryen 133、Wasmtime Python 49.0.0。SDK 默认生成旧异常指令，因此用 `wasm-opt --enable-exception-handling --emit-exnref` 转换，保留 C++ 异常行为。

参考：[WASI SDK](https://github.com/WebAssembly/wasi-sdk/tree/wasi-sdk-34)、[Binaryen 转换实现](https://github.com/WebAssembly/binaryen/blob/version_133/src/tools/wasm-opt.cpp)、[Wasmtime 安全边界](https://docs.wasmtime.dev/security.html)。

## 新版隔离与补充检查（v3）

编译策略摘要为 `wasi-p1-stdio-appcontainer-compiler-v3`。原生 clang 前端、链接器和 Binaryen 均强制在零能力 Windows LPAC/AppContainer 中运行；没有非隔离回退。编译输入复制到独立只读目录，SDK 只读，仅专用输出目录可写。宿主逐项代理 clang 的工具计划，每一个工具仍在同样的隔离令牌内启动，不允许执行工具链之外的程序。无需安装 Docker、WSL 或虚拟机。

真实隔离探针确认宿主私有文件读取、源码写入、网络连接均被拒绝，指定输出写入成功。新加入日志限量读取、完整可写目录的轮询预算、拒绝跟随 reparse/junction、进程树退出确认。磁盘限制属于轮询，不是硬磁盘配额。

异常退出恢复使用 guest 无权读取或修改的宿主 journal，核对当前用户、SDK、派生 SID、固定目录、PID 与创建时间。只回收已死亡宿主留下的精确匹配盘符、权限和 profile，不递归删除旧临时文件。固定 `icacls` helper 也受 kill-on-close Job 约束。真实宿主生命周期、遗留资源恢复与失败清理共 28 项测试通过；资源边界 18 项通过。记录分别在 `data/recovery-host-20261022-3`、`data/resource-finish-unit-2`。

每次固定工程测评增加两层独立检查：同一判断程序对正确/已知缺陷夹具执行 14 次回归；再对当前学生程序测试错误首位、空号码、全角数字 3 项补充规则。网页明确分开显示补充结果，正式四项计数与导出保持原值。补充结果绑定冻结的运行、任务、bundle、环境及 WASM 字节 hash，不从当前环境推断历史结果；相关 Web 回归 22 项通过。

以下使用早先真实模型保存的判断程序，在当前隔离环境重新编译执行，没有再次请求模型，也没有沿用旧观测：

| 输入 | 新运行 | 正式四项 | 学生补充三项 | 全活动耗时 |
|---|---|---|---|---|
| 正确单文件 | `run_20261022_225737_c2dbc5` | 4 符合 | 3 符合 | 91.89 秒 |
| 缺陷 ZIP | `run_20261022_225948_c0dea1` | 3 符合、1 不符 | 3 符合 | 107.27 秒 |
| 仅漏查首位的自编样例 | `run_20261022_230224_667c9b` | 4 符合 | 2 符合、1 不符 | 86.17 秒 |

三次均通过 14 项独立夹具回归。最后一行说明补充检查实际发现了原四项未覆盖的缺陷：号码 `23800138000` 被错误接受。原始样例和旧运行均未修改。证据位于 `data/wasm-pilot/final-validation/runs/`。

新版性能采样 `data/wasm-pilot/benchmark-isolated.json`：同组源码完整编译三次为 19.189、19.204、19.584 秒，中位数 **19.204 秒**；10 次独立 WASM worker 含启动/JIT 为 610–666 ms，中位数 **623 ms**。模块 2,758,094 字节。完整活动额外包含原始单元编译、探针、独立夹具编译及 14 次回归，不能将单次编译速度当作整次测评耗时；在线模型时间另计。

AppContainer 改善了编译阶段的隔离边界，但本机试验不等于生产安全认证；编译器/运行时漏洞、干净 Windows 镜像兼容性和其他工程形态仍未完成验收。判断程序有限回归不代替人工签审或权威校准，继续保持 `CHECKED`、`grade_eligible=false`。

### 独立便携包与新在线 LLM 验收

新版 ZIP 在新目录解压，清除 Python、编译器及项目环境变量，PATH 只保留 System32 后，便携 EXE 再次实际编译执行缺陷 ZIP 与首位缺陷样例：新运行 `run_20261022_230850_743b39`（67.230 秒）与 `run_20261022_230956_a5cd22`（52.779 秒）。两者正式计数与补充差异均复现，独立夹具校准通过。网页、JSON/CSV 导出及非同源 POST 403 检查通过。解压采样 8.946 秒，Web 就绪 1.122 秒，详细证据为 `data/wasm-pilot/portable-isolated-replay.json`。

账户恢复可用后，从同一独立便携目录的 Web 实际上传 `phone_correct.cpp`，完成全新真实模型生成与独立审查、一次适配修复、隔离编译、14 次夹具回归、4 次正式用例和 3 次学生补充规则检查。运行 `run_20261022_231131_6e35d0`：执行完成、4/4 正式符合、3/3 补充符合；记录 5 次成功模型调用，非旧 bundle 重放。全活动 93.89 秒，包含模型时间；该次服务就绪 634 ms。证据为 `data/wasm-pilot/portable-smoke-isolated.json` 与独立目录中的 `data/logs/llm_calls.jsonl`、报告和事件记录。

这补齐了首版因余额不足未完成的“独立包新上传 → 新模型生成 → 实测 → 结果展示”验收。结果页另在窄侧栏实看，补充检查先展示号码与是否允许新增，完整输入/状态折叠到详情。
