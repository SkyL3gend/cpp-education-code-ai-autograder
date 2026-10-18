# 参照判断程序（模式一）

本目录的 `assertions.json` 是测试方法中断言定义的副本，用于固定“判断程序”的内容哈希。
实际驱动来自 `task_library/shared/harness/test_entry.cpp`，它的哈希随任务包哈希一起固定。
模式二会在此目录加入 LLM 生成的适配器与观测器；本原型只实现模式一。
