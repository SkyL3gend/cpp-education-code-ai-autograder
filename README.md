# 面向 C++ 程序设计教学的代码小改自动测评与 AI 诊断系统

当前版本：0.6.0，2026-10-24 更新。面向教师和学生的本地 Web 测评工具，当前主入口为 LLM 适配测评；CLI 与 Web 共用任务包、运行器、判定引擎及证据存储。

先阅读 [使用手册](docs/使用手册.md)。已有变更见 [开发日志及 10 月 24 日续记](docs/开发日志_2026-10-22.md)，T1 实际结果与性能见 [手机号 WebAssembly 验证记录](docs/reviews/wasm-phone-pilot.md)，T2 接入范围与本轮结果见 [生日提醒验证记录](docs/reviews/wasm-birthday-pilot.md)。设计依据见 [双模式方案](企划书/双模式测评系统架构与实施方案.md)，早期问题与修复见 [2026-10-20 修复日志](docs/修复日志_2026-10-20.md)。历史审查记录用于说明当时状态，不代表当前实现。

## 先完成一次测评

已有 `cpp-assessor-wasm-pilot.zip` 便携包时：

1. 解压到自己可写的目录，双击 `START.cmd`。
2. 打开 `http://127.0.0.1:8766/`，选择“手机号校验”或“生日提醒”；仅含 T1 的旧便携包需先更新。
3. 配置自己的模型 API Key，上传 `.cpp` 或多文件工程 ZIP。随包提供正确/缺陷样例。
4. 查看实际输入、预期与观测差异，下载 JSON/CSV。

便携包内含 Python、裁剪后的 WASI 编译工具链和 Wasmtime，无需另装 Python、C++ 编译器、Docker 或 WSL。LLM 需要网络和使用者自己的 Key；包内不含密钥。当前仅在 Windows x64 本机完成验收。

可下载的二进制以 [GitHub Releases](https://github.com/SkyL3gend/cpp-education-code-ai-autograder/releases) 实际附件为准。当前已完成本地便携构建与验证；公开分发前仍需核对第三方运行库的再分发条件。GitHub 自动生成的 Source code ZIP 和 `git clone` 只提供源码，不包含这些运行工具。

T1 已从独立解压包实际上传正确程序，完成全新真实 LLM 生成与审查：4 项必测和 3 项补充检查均符合预期，含模型与适配修复共 93.89 秒。缺陷 ZIP 得到 3 项符合、1 项不符；仅漏查号码首位的样例被补充规则检出。结果是程序真实运行观测，仍不作为正式成绩。

T2 正确单文件和缺陷 ZIP 均已从独立解压包完成全新真实 LLM 测评，PATH 仅保留 System32。正确版 `run_20261023_000550_e17838`：6 项正式与 3 项学生补充均符合，5 次成功模型调用，136.75 秒。缺陷版 `run_20261024_004748_243a3b`：正式 3 项符合、3 项不符，补充 1 项符合、2 项不符，2 次成功模型调用，79.62 秒；两次均通过 18 次独立夹具回归，仍无正式评分资格。

## 测评如何工作

Web 普通入口仅提供 **LLM 适配测评**。基准工程及标准模式保留为任务包开发、参考版本校准和回归测试接口。

标准模式将符合基准接口的提交与基准工程合成，不匹配接口的独立程序返回 `INVALID_INPUT`，不会悄悄改成一条菜单测试。

**LLM 适配模式**针对结构不同的程序，流程为：

1. 保存源码快照；检查隔离能力、时间与调用预算。
2. 向模型提供完整基准测试方法、真实判断程序及协议辅助代码、输入输出模板、目标源码和可选修改前后差异。宿主保留用例期望值。
3. 模型生成 `TargetVerifierBundle`：基准方法学习记录、目标构建清单、驱动、观测方式、目标源码引用、断言绑定、分区迁移矩阵。
4. 宿主校验引用、字段、覆盖与构建参数；模型独立审查生成物。失败有界修正，仍不可实现则结束并说明原因。
5. 分别检查原始编译单元和生成判断程序；使用独立探针检查协议与观测 Schema。未知输出布局可以生成独立 C++ 观测器，无需先添加宿主正则。
6. T1 和 T2 的固定工程适配共用任务 profile 与独立正确/缺陷夹具回归；T1 执行 14 次，T2 执行 18 次。校验后冻结计划、源码、判断程序、正式用例和环境哈希。宿主用固定断言比较真实观测。
7. 当前学生程序另执行各题 3 项补充规则，网页单独展示。T1 检查错误首位、空号码、全角数字；T2 检查跨年窗口、闰年二月边界、同日生日与重复姓名。正式计数及 JSON/CSV 按任务保留，T1 为 4 项、T2 为 6 项，不混入补充检查。
8. 保存原始输入输出、编译日志、逐例观测、覆盖、错误归因和最终报告。缺失观测记 `NOT_COMPARED`，不从输入或期望值补齐。

模型能生成驱动和观测代码，但不能修改正确性标准、自授可信等级或直接决定评分。T1/T2 固定工程的独立回归只覆盖明确范围，不能证明任意 C++ 适配正确，因此新适配保持 `CHECKED`，汇总结论为 `INCONCLUSIVE`、`grade_eligible=false`。权威目标校准和人工签审工作流尚未交付。

## 当前运行边界

| 后端 | 用途 | 任意上传代码 | 正式评分资格 |
| --- | --- | --- | --- |
| `mock` | 状态机、页面与报告演示；不会运行 C++ | 不执行 | 无 |
| `local` | Win32 Job Object 资源限制；开发与可信样例验证 | 拒绝执行 | 无 |
| `wasm_pilot` | T1/T2 共用本机试验：AppContainer 隔离编译，WASI 只开放标准输入输出 | 仅明确支持的 T1/T2 1.0.0、显式试验开关 | 无 |
| Windows Sandbox / VM | 方案中的其他隔离后端 | 尚未实现 | 尚不可用 |

`local` 不提供网络和宿主文件系统隔离。`--allow-unattested-backend` **只放行明确选择的内置可信参考版本**；普通上传、LLM 上传、批量上传及 `/reference` 自检上传不能通过这个开关绕过门禁。源码编译和生成的驱动/观测器同样需要隔离。

`wasm_pilot` 是显式本机试验路径，只开放 `t1_phone_validation@1.0.0` 和 `t2_birthday_today@1.0.0`，未知任务或版本拒绝运行。原生编译工具强制使用零能力 Windows AppContainer，输入与 SDK 只读，输出使用专用目录；隔离不可用即拒绝，不回退执行学生本机 EXE。WASI 客体没有宿主目录、网络或命令执行接口。它尚未完成生产安全验收，不应部署为公开上传服务。

输出总量按 stdout + stderr 实时监测；工作目录采用轮询监测并终止进程树。这不是虚拟机磁盘硬配额，可能有短时超量，不能替代真实隔离。

## 从源码安装与开发

以下面向开发者；已拿到便携包的使用者无需执行。源码启动需要 Windows、Python 3.11+。T1/T2 共用的 WASM 路径使用 WASI SDK；MinGW `g++` 仅用于旧本机参考版本及部分开发测试。

准备 WASM 路径：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev,wasm]" pyinstaller
.venv\Scripts\python.exe tools/prepare_wasi_pilot.py
.venv\Scripts\python.exe assessor.py web --backend wasm_pilot --allow-unattested-backend --port 8766
```

准备阶段会下载官方固定版本 SDK 和 Binaryen 并验证 SHA-256；应用运行时不会自动下载工具。开发自检：

```powershell
python -m pip install -e ".[dev]"
python assessor.py task list
python assessor.py sandbox capabilities --backend local
```

运行内置可信样例并验证完整链路：

```powershell
# 先用正确/缺陷参考版本建立当前任务哈希与环境的校准证据
python tools/calibrate.py
python assessor.py run --task t1_phone_validation@1.0.0 --variant correct --allow-unattested-backend
python assessor.py run --task t1_phone_validation@1.0.0 --variant faulty --allow-unattested-backend

# 旧本机后端的开发页面；不开放普通源码上传执行
python assessor.py web --allow-unattested-backend

# 不执行任何 C++ 的页面演示
python assessor.py web --backend mock
```

题库目前包含 5 道题、共 26 个用例：手机号校验、生日提醒、日期校验、姓名检索、编号分配。校准成功后，标准模式可显示参考正确版本 `PASS`、缺陷版本 `FAIL`；本机后端仍明确记录 `grade_eligible=false`。未有有效校准记录时只给试测结论，不把全 MATCH 当成正式通过。

LLM 客户端支持 OpenAI 兼容的 JSON 对话接口，默认 DeepSeek。配置变量为 `ASSESSOR_LLM_API_KEY`、`ASSESSOR_LLM_BASE_URL`、`ASSESSOR_LLM_MODEL`；Web 可填写 Key。调用日志仅保存耗时、用量和内容摘要，不保存完整提示词或 API Key。真实供应商已完成独立便携包上传联调；开发单元测试中的模型替身仅用于可重复回归。

## 状态与报告

| 字段 | 含义 |
| --- | --- |
| `execution_status` | 系统是否完成构建、适配、执行；与学生是否正确分开 |
| `assessment_status` | `PASS` / `FAIL` / `INCONCLUSIVE` / `NOT_ASSESSED` |
| `validation_level` | 无验证 `UNVERIFIED`；试跑检查 `CHECKED`；证据校准 `CALIBRATED`；人工签审 `REVIEWED` |
| `comparison` | 逐例 `MATCH` / `MISMATCH` / `NOT_COMPARED` |
| `grade_eligible` | 所有规则、发布、验证、覆盖与隔离门禁均满足后才可能为 true |
| `fault_owner` | 学生源码、生成适配、基础设施或未知；不能确定时不判学生失败 |

新适配即使逐例 MATCH，仍可能是 `INCONCLUSIVE`。未验证生成驱动的崩溃、编译错误或观测缺失，不能自动解释为学生程序有错。

```powershell
python assessor.py status <run_id> --json
python assessor.py cancel <run_id>
python assessor.py report <run_id> --format json --out report.json
python assessor.py report <run_id> --format csv --out report.csv
python assessor.py replay <run_id>
python assessor.py rejudge <run_id>
```

`replay` 检查当前任务、环境及冻结工件哈希，实际重新执行并创建新的 run_id，不调用 LLM。仍需满足隔离门禁。旧版记录没有工件清单时拒绝称为精确重放。`rejudge` 只使用已有观测重算断言，不执行学生程序。

运行数据默认在 `data/`，可用 `ASSESSOR_HOME` 指定。CLI 与 Web 统一使用 `app.sqlite3`；打开数据库时会保留并迁移旧 `assessor.db` 的运行索引。

```text
data/
  app.sqlite3
  calibrations/                        # 绑定包哈希和环境的校准记录
  runs/<run_id>/
    objective_report.json
    plans/                             # 清单、审查、冻结计划、工件摘要
    generated/                         # 目标副本、驱动、观测器、构建产物
    evidence/compile/                  # 原始/生成代码的编译证据
    evidence/probe/                    # 独立探针
    evidence/cases/<case_id>/          # 逐例原始证据与 observation.json
    evidence/target_calibration/      # 正确/缺陷独立夹具执行证据
    evidence/target_challenges/       # 当前学生程序的三项补充检查
    evidence/revisions/               # 修正前的计划和失败证据
```

任务发布必须通过静态检查和当前正确/缺陷参考版本校准：

```powershell
python assessor.py task validate task_library/tasks/t1_phone_validation/1.0.0
python tools/calibrate.py --task t1_phone_validation
python assessor.py task publish task_library/tasks/t1_phone_validation/1.0.0
```

发布写入 `task_library/publications/<task_id>/<version>.json`。已封存版本的内容发生变化会拒绝加载，应创建新版本。历史随附的 PUBLISHED 标记不能代替校准证据。

## 验证与后续工作

```powershell
python -m pytest
```

2026-10-24 最终完整套件为 **337 passed、0 failed、1 warning，146.78 秒**。警告是 Starlette/httpx 弃用提示。T2 两次真实测评和 T1 回放的网页、JSON/CSV 已核对，导出分别保留 6、6、4 项正式用例；非同源 POST 返回 403。

回归覆盖真实 C++ 编译、原值观测、错误归因、适配完整性、未知输出布局观测器、调用预算、取消、实时资源监测、校准发布、重放以及 CLI/Web 共用链路。测试仅执行仓库内可信参考程序和明确编写的测试夹具，不会让任意上传程序在宿主运行。

仍需完成：干净 Windows 机器兼容性与正式隔离验收、磁盘硬配额、目标判断程序权威校准/签审工作流、其他题目的真实 WASM 适配、后续 AI 诊断与题库扩充。公开二进制分发还需完成第三方许可核查。当前不能声称已满足任意学生工程的完整自动验收。

## WebAssembly 手机号与生日提醒试验

已接入 `wasm_pilot` 后端，T1 手机号校验与 T2 生日提醒共用执行和校准框架，支持上传单文件 C++ 或 ZIP、真实模型生成驱动、WASI 执行和网页结果；不回退执行学生本机 EXE。适用范围仅为这两道题的 `1.0.0` 版本。T2 正确单文件与缺陷多文件 ZIP 均已完成真实测评。

| 任务 | 样例目录 | 正式 / 学生补充检查 | 独立正确与缺陷夹具执行 |
| --- | --- | --- | --- |
| T1 手机号校验 | `examples/wasm_phone/` | 4 / 3 | `2 × (4 + 3) = 14` 次 |
| T2 生日提醒 | `examples/wasm_birthday/` | 6 / 3 | `2 × (6 + 3) = 18` 次 |

T2 正确样例实际得到 6 项正式符合；缺陷 ZIP 实际得到 3 项符合、3 项不符，定位为漏掉当天生日。名单比较保留重复姓名次数，通讯录比较保留原始编号和内容；缺陷版 6 项通讯录保留断言均符合。

本地便携试验包为 `dist/cpp-assessor-wasm-pilot.zip`，约 77 MiB，解压后双击 `START.cmd`。内含 Python、运行时和裁剪后的编译工具链；模型服务仍需使用者自己的 Key 和网络。源码 clone 不包含这些二进制依赖。

维护者准备和构建：

```powershell
python -m pip install -e ".[dev,wasm]" pyinstaller
python tools/prepare_wasi_pilot.py
python tools/build_wasm_portable.py
```

源码启动：

```powershell
python assessor.py web --backend wasm_pilot --allow-unattested-backend --port 8766
```

WASI 客体仅开放标准输入输出；编译工具强制使用 Windows 自带的 AppContainer 与专用输入输出目录，隔离不可用即拒绝运行。当前仍为本机试验，尚未完成公开不可信上传服务的安全验收或正式评分资格。T1 完整结果、测量方法和边界见 [手机号验证记录](docs/reviews/wasm-phone-pilot.md)；T2 范围及实测进度见 [生日提醒验证记录](docs/reviews/wasm-birthday-pilot.md)。
