# 生日提醒试测样例

- `birthday_correct.cpp` / `birthday_correct.zip`：当天生日返回 0，按提醒窗口筛选联系人。
- `birthday_faulty.cpp` / `birthday_faulty.zip`：故意保留 `birth <= today` 的缺陷，把当天生日推到明年，漏掉当天的提醒。

文件由仓库的 `contactbook_base@1.0.0` 与 T2 correct/faulty 变体组合而成，未改写业务实现。单文件版依次合并 `validators.h`、`contactbook.h`、`validators.cpp`、`contactbook.cpp`，仅移除本地头文件包含与 `#pragma once`；没有 `main()`。ZIP 版保留这 4 个平级模块的原始内容。LLM 需要生成调用真实 `ContactBook::birthdayReminders` 的测试驱动。

使用支持 T2 的新版 `wasm_pilot` Web 接口，选择“生日提醒”后上传任一 `.cpp` 或 `.zip`。旧版仅支持手机号题的便携包需要更新。在线生成判断程序需要可用的模型配置。

本题检查：当天生日应返回 0 并出现；未来生日恰好在窗口内应出现，超出窗口不出现；已过生日按下一次生日计算；负数窗口返回空列表；提醒操作保留完整通讯录内容。正式用例也包含闰年 2 月 29 日。

根据题库规则，正确版应符合 6 项正式用例；缺陷版应在“当天生日”“当天与未来窗口”“闰日当天”这 3 项出现差异。这是预期行为，实际结果以本次测评报告为准。

其他题目的函数仍保留基础工程实现，这些文件仅用于 T2 验证。非闰年 2 月 29 日与超长提醒窗口的产品规则尚需单独明确，不把这些样例当成完整日期库。
