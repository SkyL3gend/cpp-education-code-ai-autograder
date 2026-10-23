# 姓名检索试测样例

- `search_correct.cpp` / `search_correct.zip`：按姓名子串检索，不区分 ASCII 字母大小写，返回全部匹配联系人。
- `search_faulty.cpp` / `search_faulty.zip`：故意保留区分大小写、找到第一项就停止的缺陷。

文件由 `contactbook_base@1.0.0` 与 T4 correct/faulty 变体组合而成。本题替换的是 **`contactbook.cpp`**；`validators.cpp` 来自基础工程。单文件版依次合并 `validators.h`、`contactbook.h`、`validators.cpp`、`contactbook.cpp`，仅移除本地头文件包含和 `#pragma once`，没有 `main()`；正确版额外的 `<cctype>` 保持原样。ZIP 版保留这 4 个平级文件的原始字节，CRC、安全路径和来源内容已核对。

在支持 T4 的新版 `wasm_pilot` Web 服务中选择“姓名检索”，上传任一 `.cpp` 或 `.zip`。LLM 应生成调用真实 `ContactBook::searchByName` 的测试驱动，观察完整 `matches` 联系人和 `contacts_after`，保留每条原始 ID、姓名、电话、生日；不能仅输出姓名或重新编号。

已在独立解压包完成全新真实 LLM 测评：正确单文件五项正式与八项补充全部符合；缺陷 ZIP 正式 3 项符合、2 项不符，补充 3 项符合、5 项不符，两次均通过 26 次独立夹具回归。关键字 `an` 只返回 `annabel`、漏 `Anna Liu`，`LIU` 错误返回空列表；中文关键字、空关键字和无匹配三项符合，五项通讯录保留断言均符合。两次各十三份正式/补充观测都已核对完整 Contact 与原 ID、电话、生日和通讯录原状态。

运行分别为 `run_20261024_020331_52fc33`、`run_20261024_020454_7b8002`。试验结果仍为 `CHECKED`、`grade_eligible=false`，不能直接登记正式成绩。

其他题目的函数仍保留基础工程实现，这些文件仅用于 T4 验证。旧版只支持前三题的便携包需要更新；在线生成需要可用的模型配置。试验结果不能直接作为正式成绩。
