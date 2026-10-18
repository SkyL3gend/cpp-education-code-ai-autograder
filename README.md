# 面向 C++ 程序设计教学的代码小改自动测评与 AI 诊断系统（CLI + 本地 Web）

本仓库是《双模式测评系统架构与实施方案》v0.2 的第一步实现：按任务书
“第 1—2 周交付能运行 5 道小改题的 CLI 原型”的要求，完成**模式一（标准测评）**
的完整闭环——导入提交、构建受测工程、在受限后端中执行预配置用例、用可信断言
比对观测、生成可追溯的结构化 JSON 报告，并用参照版本完成校准。

当前版本（0.5.0）在 CLI 与本地 Web 之上实现了**模式二（LLM 适配）**：
学生提交一个结构未知的完整程序（单个 `.cpp`、多文件目录或 ZIP），模型阅读任务契约与
源码后，输出一份声明式的**目标判断清单**（`TargetVerifierBundle`：怎么编译、用什么
输入序列驱动、由哪个可信提取器观测、每个断言绑定到哪个观察字段），宿主对清单做静态
校验与试跑，冻结后才跑正式用例，判定仍完全由确定性引擎完成。模型的输出**不能**
携带预期值或结论，也不参与评分。当前用真实模型验证了 T1（原版 `FAIL`、修复版
`PASS`）；T2—T5 的清单生成链路已通，但尚未用真实模型逐题校准。

`0.4.1` 及以前完成了模式一（标准测评）的完整闭环：导入提交、构建受测工程、在受限
后端中执行预配置用例、用可信断言比对观测、生成可追溯的结构化 JSON 报告，并用参照
版本完成校准；同一套内核还提供**基准程序实测**（观察型自检）与**单文件适配**
（提交物与基准工程无交集时的观察型兜底）。

0.4.0 的界面改进：进入站点先选**基准模式 / LLM 模式**（当时 LLM 模式置灰，
0.5.0 起开放）；页面支持**中英双语**切换，结论徽章显示为
`Accepted · 通过` / `Rejected · 未通过`；新增**批量测评**：一次上传多个程序，
勾选一道或多道题，生成“程序 × 题目”矩阵并逐格给出结论。

0.4.1 的两点变化：

- **完整程序可以直接测评**：提交物与基准工程没有交集时不再报“没有匹配基准工程”，
  而是退化为**单文件适配（观察型）**——把提交物当作独立程序编译（支持一个文件夹里
  多文件互相依赖），用与基准程序实测相同的菜单脚本观察该题的一条规则，生成正式
  报告并明确标注“观察型、不是完整评分”。
- **批量测评不再只收 ZIP**：每个上传物（ZIP 或单个 `.cpp`/`.h`/`.hpp`）都落成
  `programs/<id>/` 独立文件夹（`source/` 放被测代码、`verifier/` 预留给该程序
  专属的判定代码），因此多文件程序与单文件程序可以混在同一批里。

0.5.0 的模式二（LLM 适配）：

- **首页的 LLM 模式已开放**：选择一道题、上传程序、在页面填入模型 API Key，
  即可让模型为这份程序生成目标判断清单；Key 只用于本次请求，不写库、不落盘。
- **清单是声明式的**：模型只能选择“标准输入序列 / 函数驱动、可信提取器、断言绑定、
  迁移矩阵”，不能写预期值、不能改断言、不能引入宿主命令或网络访问；宿主逐条静态
  校验，不合格就把问题回喂模型，最多重问 2 轮。
- **先试跑、后冻结**：清单通过校验后先编译并用探针脚本试运行，观测不到事实就给
  模型有界修正机会（只许改适配层）；仍失败则记 `ADAPTATION_FAILED`，明确不算
  学生程序的错。
- **判定仍由确定性引擎完成**：正式用例、断言、聚合与证据和模式一完全相同；
  报告标注 `mode=LLM`、`validation_level=CHECKED`，并附迁移矩阵与覆盖缺口。

## 1. 已实现的功能

- **题库（5 道小改题）**：以简化的通讯录工程为初始程序，每题包含功能要求、
  初始程序、参考正确实现、已知缺陷实现、4—6 个覆盖正常与边界的用例、断言定义、
  参照版本清单与校验和。
- **自动测评**：编译学生提交 → 受资源限制地逐个执行用例 → 通过标准输入输出协议
  取得结构化观测 → 按断言白名单（`EQUALS` / `MULTISET_EQUALS` / `UNCHANGED`）
  严格比较 → 汇总为 `PASS` / `FAIL` / `INCONCLUSIVE` / `NOT_ASSESSED`。
- **报告与证据**：每次运行生成 `objective_report.json`（含覆盖情况、用例与断言计数、
  判定理由、执行后端与校验等级），并保留原始 stdout/stderr、观测、编译日志、
  构建计划、校验记录，可用 `replay` 命令从证据复现判定。
- **本地 Web 界面**：浏览器里选题、上传提交（目录 / 单文件 / ZIP）、查看等待与
  结果页、下载 JSON/CSV、取消运行；后端用单线程队列串行执行，与 CLI 共用同一套
  运行器、判定与证据。
- **模式选择与双语界面**：首页先选“基准模式 / LLM 模式”（0.4.0 起 LLM 模式入口存在，
  0.5.0 起两种模式都可进入）；
  所有页面支持中文 / English 切换（`?lang=en` 或导航上的语言按钮），**只翻译展示层**，
  报告里的枚举代码不变；结论徽章同时显示英文术语与中文解释。
- **批量测评**：一次上传最多 10 个程序（ZIP 或单个源文件）、勾选最多 5 道题，
  串行跑“程序 × 题目”矩阵，逐格链接到单次运行的完整报告；每个程序独立成
  `programs/<id>/` 文件夹（`source/` + 预留的 `verifier/`），批次状态可恢复、可取消。
- **模式二（LLM 适配，v0.5.0）**：对结构未知的完整程序，模型阅读任务契约与源码后
  生成声明式判断清单（编译方式、输入序列、提取器、断言绑定、迁移矩阵）；宿主做
  静态校验 → 编译试跑 → 有界修正 → 冻结 → 正式执行，判定仍由断言引擎给出。
  页面填 Key 即可使用，也支持 `assessor run --llm` 与环境变量；模型不接触预期值，
  不参与评分。
- **基准程序实测（观察型）**：对随附的参考程序做真实编译与脚本化运行，
  用可信提取器从原始输出中判定 5 条检查（菜单可启动、手机号必须为 11 位数字、
  当天生日提醒、姓名检索不区分大小写、日期合法性），给出 `PASS` / `DEFECT` /
  `NOT_OBSERVED` 与证据文件；**不参与五题评分**，也不产生 `grade_eligible`。
- **校准**：`tools/calibrate.py` 对每题运行“正确实现”和“已知缺陷实现”，
  校验正确实现通过、缺陷实现被指定用例识别，并写入 SQLite 与 JSON 记录。
- **执行后端**：本地 Win32 Job Object 受限后端（CPU/内存/进程数/墙钟/进程树终止）
  与模拟后端（`mock`，不执行任何代码，仅用于验证状态机与报告链路）。
- **能力门禁**：后端缺少 CPU、内存、时间、进程数、网络隔离、进程树终止、环境清理
  中的任一必要能力时，**默认拒绝执行真实提交**（符合实施方案 §9.3）。

## 2. 环境与依赖

| 组件 | 版本要求 | 本机实测 |
| --- | --- | --- |
| Python | ≥ 3.11 | 3.12.10 |
| pydantic | ≥ 2.6 | 2.14.1 |
| jsonschema | ≥ 4.20 | 4.26.0 |
| fastapi | ≥ 0.110 | 0.143.5 |
| uvicorn | ≥ 0.27 | 0.54.0 |
| jinja2 | ≥ 3.1 | 3.1.6 |
| pytest（开发） | ≥ 8 | 9.1.1 |
| httpx（开发，Web 测试） | ≥ 0.27 | 已安装 |
| g++ | 支持 C++17 | 13.2.0（MinGW-W64 x86_64-ucrt-posix-seh） |
| 操作系统 | Windows 10/11 x64 | Windows（中文路径已验证） |

说明：

- 编译器通过 `PATH` 查找 `g++`；找不到时运行会在校验阶段失败并给出
  `COMPILER_MISSING`，不会退回“在宿主直接运行”的路径。
- 数据默认写入仓库根目录的 `data/`，可用环境变量 `ASSESSOR_HOME` 改到别处；
  题库目录默认 `task_library/`，可用 `ASSESSOR_TASK_LIBRARY` 覆盖。

## 3. 快速开始

```powershell
# 1) 安装（可编辑模式，同时安装 pytest）
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# 2) 查看题库与后端能力
python assessor.py task list
python assessor.py sandbox capabilities --backend local

# 3) 用参照版本跑一次标准测评（本机后端未过门禁，需显式放行）
python assessor.py run --task t1_phone_validation@1.0.0 --variant correct --allow-unattested-backend

# 4) 导出报告
python assessor.py report <run_id> --format json --out report.json
python assessor.py report <run_id> --format csv      # 默认写到 data/exports/<run_id>.csv

# 5) 从证据复现判定
python assessor.py replay <run_id>

# 6) 启动本地 Web 界面（默认 http://127.0.0.1:8765）
python assessor.py web
#    本机后端未过门禁时，允许在 Web 里显式放行：
python assessor.py web --allow-unattested-backend

# 7) 基准程序实测（观察型，不参与五题评分）
python assessor.py selfcheck

# 8) 模式二：让模型为完整程序生成判断清单（需要 API Key）
$env:ASSESSOR_LLM_API_KEY = "sk-..."      # 或只写进 %USERPROFILE%\.cpp-assessor\llm.env
python assessor.py run --task t1_phone_validation@1.0.0 `
  --submission "参考基准程序\通讯录_v6最终版.cpp" `
  --llm --backend local --allow-unattested-backend

# 9) 回归测试
python -m pytest
python tools\calibrate.py
```

### 使用本地 Web 界面

```powershell
python assessor.py web                      # 打开 http://127.0.0.1:8765
python assessor.py web --allow-unattested-backend   # 本地 Job Object 后端下允许真实运行
python assessor.py web --backend mock --no-open     # 演示 Web 流程（不执行代码）
```

| 页面 / 接口 | 作用 |
| --- | --- |
| `GET /` | 模式选择：基准模式 / LLM 模式（均已开放） |
| `GET /llm` | LLM 模式首页：API Key 说明、题目列表、使用边界 |
| `GET /llm/tasks/<task_ref>` | 填 Key、上传程序，让模型生成目标判断清单 |
| `POST /llm/runs` | 执行一次模式二测评（Key 只用于本次请求） |
| `GET /standard` | 题库列表、最近运行、当前后端能力与门禁状态 |
| `GET /batch` / `POST /batch` | 批量测评：多程序（ZIP 或单文件）× 多题矩阵，串行 |
| `GET /batch/<batch_id>` | 矩阵结果页，每格链接到单次运行；可取消 |
| `GET /api/batches/<batch_id>` | 批次状态 JSON（供页面轮询） |
| `GET /tasks/<task_ref>` | 功能要求、规则、用例清单；下载基准工程、选择参照版本或上传提交 |
| `GET /tasks/<task_ref>/base.zip` | 下载可修改的基准工程（学生小改的起点） |
| `POST /runs` | 创建运行（`variant=correct/faulty` 或上传目录/ZIP/单文件） |
| `GET /runs/<run_id>` | 等待页 / 结果页（结论、计数、逐用例断言明细、证据路径） |
| `GET /api/runs/<run_id>` | 运行状态 JSON（供页面轮询） |
| `POST /runs/<run_id>/cancel` | 写入取消标记 |
| `GET /runs/<run_id>/report.json` / `report.csv` | 下载报告（CSV 带 BOM、防公式注入） |
| `GET /reference` / `POST /reference` | 基准程序实测（观察型，不参与评分） |
| `GET /healthz` | 健康检查 |

语言切换：导航右侧的 `English / 中文` 按钮，或任意页面加 `?lang=en`；选择会记在
Cookie 里，**只影响界面文案**，不影响报告数据。

Web 端只负责“选题—提交—查看—导出”，判定完全复用 CLI 的运行器与断言引擎；
运行在进程内单线程队列中串行执行（与实施方案“第一版单工作进程串行队列”一致）。
界面默认只监听 `127.0.0.1`，且**没有登录与鉴权**，不要直接暴露到公网。

> 提交对象首选任务页下载的**基准工程**（`contactbook_base`），此时按任务定义逐用例
> 评分。仓库里的《参考基准程序/通讯录_v6最终版.cpp》也可以直接提交：它与基准工程
> 结构不同，会走**单文件适配（观察型）**，只核对一条可观察规则并标注
> “不是完整评分”（`INCONCLUSIVE` 或按观察结果给 `PASS`/`FAIL`，`grade_eligible`
> 始终为 `false`）。

### 基准程序实测（观察型）

```powershell
python assessor.py selfcheck                              # 默认测 参考基准程序/通讯录_v6最终版.cpp
python assessor.py selfcheck --source path\to\other.cpp   # 测别的单文件 C++ 程序
python assessor.py selfcheck --json                       # 只输出 JSON 摘要
```

界面入口：`GET /reference`（页面左侧导航“基准程序实测”），提交后可下载
`report.json` 与逐检查的原始 stdout/stderr。报告落在 `data/selfchecks/<selfcheck_id>/`。

它做的事情是：用与五道题**相同的**编译/受限执行/工件基础设施，真实运行程序，
再用预设脚本驱动菜单，从原始输出中提取可观察事实并按规则判定：

| 检查 | 观察 | 参考程序当前结论 |
| --- | --- | --- |
| `smoke_menu` | 程序能启动并打印主菜单 | `PASS` |
| `phone_digits` | 11 位但含非数字的手机号是否被拒绝 | `DEFECT`（接受了 `1380013800a`） |
| `birthday_today` | 当天生日是否出现在“未来 N 天”提醒里 | `PASS` |
| `name_search_case` | 姓名检索是否不区分大小写并返回全部匹配 | `DEFECT`（`an` 只返回 `annabel`） |
| `date_validation` | `1900-02-29`、`2023-04-31` 是否被拒绝 | `PASS` |

`DEFECT` 表示“程序的实际行为与规则不符”，是该程序自身的行为结论；`NOT_OBSERVED`
表示脚本没能驱动到可判断的位置（不会当成通过）。**这套实测不产生 PASS/FAIL 成绩，
也不写入五题题库的校准记录**——它解决的是“想看看参考程序自己哪里不对”，而不是
“给学生打分”。

### 直接提交完整程序（单文件适配，观察型）

把与基准工程无关的完整程序（例如《参考基准程序/通讯录_v6最终版.cpp》，或一个
内部互相依赖的多文件工程目录/ZIP）直接交给某道题时，系统不再拒绝，而是：

1. 把提交物当作独立程序，**全部 C++ 编译单元一起**编译（不链接测试驱动）；
2. 用该题对应的菜单脚本驱动程序，从原始输出提取一条可观察事实；
3. 用 `EQUALS` 比较“事实是否符合规则”，生成正式报告，但标注
   **“单文件适配（观察型）：…结论不是完整评分”**，`grade_eligible=false`。

```powershell
python assessor.py run --task t1_phone_validation@1.0.0 `
  --submission "参考基准程序\通讯录_v6最终版.cpp" `
  --backend local --allow-unattested-backend
```

参考程序在五道题上的观察结果示例：T1 手机号规则 `FAIL`（存下了 `1380013800a`）、
T2 当天生日 `PASS`、T3 日期合法性 `PASS`、T4 姓名检索 `FAIL`（区分大小写）、
T5 编号分配 `PASS`（先删除再新增，编号不复用）。程序无法启动或观察不到脚本要求的
菜单事实时给出 `INCONCLUSIVE`，并在用例备注里写明“没有观察到可核对的事实”。

### 模式二：LLM 适配（v0.5.0）

模式一要求提交物能对上基准工程的文件/函数签名；模式二面向**结构未知的完整程序**：
程序有自己的 `main`、菜单、数据结构和文件划分。做法不是让模型直接打分，而是让模型
把“这一题该怎么测这份程序”写成一份**声明式清单**，再由宿主的确定性引擎执行与判定。

```powershell
# Web：首页选“LLM 模式” → 选题 → 填 API Key → 上传程序 → 查看结果
python assessor.py web --allow-unattested-backend

# CLI：Key 从环境变量或仓库外的 %USERPROFILE%\.cpp-assessor\llm.env 读取
python assessor.py run --task t1_phone_validation@1.0.0 `
  --submission "参考基准程序\通讯录_v6最终版.cpp" `
  --llm --backend local --allow-unattested-backend
```

流水线：

1. **ANALYZE**：宿主把题目契约（规则、输入/输出 Schema、用例 ID、断言定义与
   `actual_observation_key`、分区、编译选项）、可用提取器清单和程序源码（总限
   120k 字符）交给模型；**不给**任何用例的期望值或答案。
2. **GENERATE**：模型输出 `TargetVerifierBundle`——目标文件与编码、编译选项、
   驱动方式（`stdin_script` 会话或 `function_driver` 源码）、观测提取器及其参数、
   每条断言的绑定、分区到驱动步骤的迁移矩阵、覆盖缺口。
3. **VALIDATE**：宿主静态校验：任务/版本匹配、源文件存在、占位符必须落在输入
   Schema 内、会话可按下标逐用例覆盖、提取器已注册、所有必填断言都绑定且不能改
   `actual_key`、迁移矩阵覆盖全部输入分区、清单里不得出现 `expected`/`answer`/
   `verdict` 等字段。不合格把问题回喂模型，最多重问 2 轮。
4. **PROBE / REPAIR**：编译清单选定的源文件，用首个用例试跑；协议不通或提取器
   观测不到事实时，把失败原因回喂模型做**有界修正**（只许改适配层，不许改判据），
   最多 2 轮；仍失败记 `ADAPTATION_FAILED`。
5. **FREEZE → EXECUTE → REPORT**：冻结清单后写正式请求、逐用例执行、用同一套
   断言/覆盖/聚合引擎判定，并在 `objective_report.json` 里记录
   `mode=LLM`、`validation_level=CHECKED`、迁移矩阵与覆盖缺口。

模型的输出**不能**携带预期值、不能改断言、不能引入网络/宿主命令/读取文件，
也不参与 `assessment_status` 或 `grade_eligible` 的计算——它只决定“怎么观察”，
“是否通过”由断言引擎按任务包里的规则判定。

当前用真实模型（DeepSeek）验证过 T1：参考程序原版 `FAIL`（接受非数字手机号）、
补上数字校验的修复版 `PASS`，4 个用例全部执行、证据齐全；T2—T5 的清单生成链路
已打通，但尚未用真实模型逐题校准（见“已知限制”）。

**Key 处理**：页面填写的 Key 只放在本次请求的内存里；CLI 从
`ASSESSOR_LLM_API_KEY` 或 `%USERPROFILE%\.cpp-assessor\llm.env` 读取。仓库里没有
任何 Key，日志只记录模型名、耗时、token 数与调用状态（Authorization 已脱敏）。

### 关于“不计入正式成绩”

当前本地后端是 **Windows Job Object**：它能限制 CPU 时间、内存、进程数、墙钟时间，
能终止整棵进程树，但**不提供网络隔离与文件系统隔离**。按实施方案 §9.3 的能力门禁，
它不足以承载真实学生提交的正式评分。因此本机运行的报告一律：

- `grade_eligible = false`
- `grade_eligible_reasons` 含 `EXECUTION_BACKEND_NOT_ATTESTED`
- 结论仍会给出明确的 `PASS` / `FAIL`，但界面与文档只把它视为**开发验证结论**。

接入 Windows Sandbox 或固定虚拟机后端并通过门禁后，同一套流程可产出
`grade_eligible = true` 的报告。

## 4. 题库

| 任务 ID | 规则（学生要修的小改题） | 操作 | 用例数 | 缺陷版本被识别 |
| --- | --- | --- | --- | --- |
| `t1_phone_validation` | 手机号必须是 11 位数字且以 1 开头 | `create_contact` | 4 | `c_non_digit_11` |
| `t2_birthday_today` | 当天生日必须出现在提醒结果里 | `birthday_reminders` | 6 | `c_today_exact`、`c_today_window`、`c_leap_day` |
| `t3_date_validation` | 生日必须是真实日期，年份 1900—2100 | `create_contact` | 6 | `c_invalid_1900_02_29`、`c_invalid_year_low`、`c_invalid_year_high` |
| `t4_name_search` | 姓名检索不区分大小写且返回全部匹配 | `search_contact` | 5 | `c_case_insensitive`、`c_upper_keyword` |
| `t5_id_allocation` | 删除联系人后新增，编号不得重复 | `id_allocation` | 5 | `c_delete_then_add`、`c_max_seed`、`c_multi_add` |

每个任务都有 `variants/correct/`（只改必要文件的参考实现）与 `variants/faulty/`
（初始缺陷版本），`reference_suite.json` 声明两者期望的判定；`tools/calibrate.py`
会实际编译运行并比对，只有“正确实现 PASS、缺陷实现 FAIL 且命中指定用例”才算通过。
当前 5 题全部校准通过。

## 5. 命令参考

| 命令 | 作用 |
| --- | --- |
| `assessor task validate <dir>` | 静态校验任务包（必填文件、Schema、引用、覆盖、参照版本、哈希） |
| `assessor task publish <dir>` | 校验通过后把 `publication_status` 置为 `PUBLISHED` |
| `assessor task list [--json]` | 列出题库与用例数量 |
| `assessor sandbox capabilities [--backend local\|mock]` | 打印后端能力清单（门禁依据） |
| `assessor run --task <id@version> (--submission <path> \| --variant <id>) [--target-before <path>] [--backend local\|mock] [--llm] [--allow-draft] [--allow-unattested-backend] [--json]` | 执行一次测评；`--llm` 走模式二（模型生成判断清单） |
| `assessor status <run_id> [--json]` | 查看工作流状态、判定与最近事件 |
| `assessor cancel <run_id>` | 写入取消标记；运行器在阶段边界检查并取消 |
| `assessor report <run_id> --format json\|csv [--out <path>]` | 导出报告；CSV 带 BOM 且做了公式注入防护 |
| `assessor replay <run_id>` | 用保存的观测重新执行断言，与报告逐条比对 |
| `assessor web [--host] [--port] [--backend local\|mock] [--allow-unattested-backend] [--allow-draft] [--no-open]` | 启动本地 Web 界面 |
| `assessor selfcheck [--source <file.cpp>] [--backend local\|mock] [--json] [--strict]` | 基准程序实测（观察型，不参与评分）；`--strict` 时有缺陷返回 1 |

`run` 的退出码：`0` 通过、`1` 失败、`2` 未测评、`3` 不确定、`4` 基础设施错误、
`130` 取消。**退出码不区分是否具备正式评分资格**，资格请看报告里的 `grade_eligible`。
`selfcheck` 的退出码：`0` 正常完成（无论是否有缺陷）、`1` 指定 `--strict` 且存在缺陷、
`2` 编译失败。

## 6. 一次运行的产出

```
data/
  app.sqlite3                      # 运行记录、事件、工件索引、校准记录
  submissions/<submission_id>/     # 只读快照 + manifest.json（路径、大小、sha256、编码）
  runs/<run_id>/
    objective_report.json          # 结构化报告（判定唯一依据）
    plans/build_plan.json          # 构建计划（源单元、包含目录、入口、哈希）
    plans/test_plan.json           # 测试计划（用例、断言引用、哈希）
    plans/validation.json          # 校验记录（九项检查与校验等级）
    plans/requests/<case>.json     # 发给被测程序的请求信封（不含期望值）
    generated/{subject,harness}/   # 本次构建实际使用的源码副本
    evidence/compile/              # 编译命令结果、stdout/stderr
    evidence/cases/<case>/         # 请求、观测、原始 stdout/stderr、工作目录
data/exports/<run_id>.csv          # 按需导出
data/calibrations/<task>.json      # 校准记录
data/selfchecks/<selfcheck_id>/    # 基准程序实测：source/ build/ cases/<检查>/ report.json
```

运行目录中的 `objective_report.json` 是判定的权威来源；`assessor replay` 只读
`evidence/`，不重新执行学生程序。

## 7. 目录结构

```
src/cpp_assessor/
  domain/       # 枚举、数据模型（pydantic）、JSON Pointer、规范化、错误类型
  intake/       # 提交导入、路径策略、快照与哈希
  tasks/        # 任务包加载、静态校验、发布、提交合成
  sandbox/      # 后端接口、能力门禁、Win32 Job Object 后端、模拟后端
  orchestrator/ # 协议信封、标准测评运行器（状态机、预算、取消）、模式二编排器
  judge/        # 断言引擎、覆盖计算、判定聚合、可信提取器注册表
  planning/     # 模式二的目标判断清单模型与静态校验
  llm/          # 模型提供方客户端（OpenAI 兼容、JSON 输出、调用日志）
  storage/      # SQLite、工件存储
  selfcheck/    # 基准程序实测：检查定义、提取与判定、受限执行、报告
  reporting/    # JSON/CSV 导出
  interfaces/   # CLI 与本地 Web（FastAPI + Jinja2 模板 + 静态资源）
skills/
  cpp-microchange-judge-author/SKILL.md  # 给模型的任务说明与输出契约（可移植包）
task_library/
  projects/contactbook_base/1.0.0/   # 学生修改的初始工程
  shared/harness/                    # 标准输入输出测试驱动
  tasks/<task_id>/1.0.0/             # 题目、模板、用例、参照版本、断言定义
tests/          # pytest 单元测试与集成测试
tools/          # 任务包生成器、校准脚本
docs/           # 使用手册、设计说明、实验报告
```

## 8. 安全与边界

- 被测程序只在沙箱后端中执行，**没有宿主直跑后备路径**；请求文件中不含期望值。
- 提交导入拒绝绝对路径、`..`、盘符、UNC、ADS、设备名、大小写冲突路径，
  限制文件数与解压总量，逐块读取后落盘（不使用 `extractall`）。
- 编译命令以参数数组调用，不经过 shell；编译选项由任务包正向声明。
- 观测协议分两段：驱动程序只输出原始观测，宿主信任的解析器提取 `actual`，
  比较在宿主侧完成；协议中不含 PASS/FAIL 字段。
- 学生崩溃/超时/超内存归因为 `SUBMISSION`，报告为 `FAIL`（`SUBJECT_CRASH_OR_LIMIT`），
  不会因“没有观测”而误判为“不确定”。

## 9. 已知限制（下一步）

1. **隔离等级不足**：本地 Job Object 后端无法阻断网络与文件系统访问，按门禁
   `grade_eligible` 恒为 `false`；需接入 Windows Sandbox 或固定虚拟机后端。
2. **只实现模式一与模式二的“客观测评”部分**：LLM 诊断报告（任务 3 要求的
   错误定位/修复建议）尚未实现；模式二已能让模型生成目标判断清单并跑通 T1，
   但 T2—T5 尚未用真实模型逐题校准。Web 界面覆盖任务书任务 4 的
   “选题 / 提交 / 查看结果 / 导出”闭环。
3. **Web 端无鉴权、无多用户隔离**：只监听本机回环地址；上传文件保存在
   `data/web_uploads/`（批量为 `data/batches/<id>/uploads/`），不会自动清理；
   任务书要求的班级、成绩管理不在本阶段范围。
4. **批量测评是串行的**：一批最多 10 个程序 × 5 道题（50 个运行），复用同一个
   单工作线程；批次状态写在 `data/batches/<batch_id>/batch.json`。
5. **题量为 5 题**：任务书要求最终 ≥10 题、每题 ≥4 用例；当前 26 个用例覆盖
   正常与边界，第 3—4 周扩展到 10 题 40 用例。
6. **驱动方式**：模式一固定用标准输入输出驱动（`STDIN_SCRIPT`）；模式二可让模型
   选择会话驱动或生成函数级驱动（`FUNCTION_DRIVER`），但后者还没有真实题目的
   校准样例，属于实验能力。
7. **模式二依赖模型输出**：清单生成不是确定性的，换了模型/提示词可能得到不同的
   会话或观测方案；宿主只能保证“不合格的清单不执行、失败归因为适配失败”，
   不能保证模型一定读得懂任意程序。T1 已用真实模型验证，T2—T5 待逐题校准。
8. **模板中的动作前观测与 `UNCHANGED` 断言**已实现并有单元测试，但五个任务包
   暂未使用；后续题库会补充“操作前后不变”的用例。
9. **提交匹配**：学生提交按相对路径覆盖基准工程，找不到时用唯一文件名匹配；
   生产环境应加入更严格的工程结构校验与人工复核入口。
10. **基准程序实测是观察型的**：它固定测量 5 条检查、依赖程序菜单文本作为锚点，
    结论是“该程序的行为观察”，不是完整评分；更换程序时锚点可能需要调整，
    驱动不到的位置会明确记为 `NOT_OBSERVED` 而不是猜。

## 10. 文档

- [使用手册](docs/使用手册.md)：安装、命令、任务包制作、报告解读、故障排查。
- [设计说明](docs/设计说明.md)：模块职责、数据契约、协议、状态机、判定规则、
  与实施方案 v0.2 的对应关系及本原型的简化点。
- [实验报告](docs/实验报告.md)：环境、方法、五题校准数据、异常场景验证与结论。
