# 手机号校验试测样例

- `phone_correct.cpp` / `phone_correct.zip`：手机号校验符合 T1 规则。
- `phone_faulty.cpp` / `phone_faulty.zip`：故意保留“只检查长度与首位”的缺陷，`1380013800a` 会被错误接受。

这些文件由仓库的 `contactbook_base@1.0.0` 与 T1 correct/faulty 变体组合而成。
单文件版合并了头文件与业务实现，没有 `main()`；LLM 需要生成调用真实 `ContactBook::addContact` 的测试驱动。
ZIP 版保留原来的 4 个模块文件。两者都不是仅返回预置答案的模拟程序。

在 WebAssembly 试验页面选择“手机号校验”后上传其中一个文件。正确版预期 4 项 MATCH；缺陷版预期 3 项 MATCH、非数字手机号用例 MISMATCH。
其他题目的业务函数仍保留基准工程原有实现，这些样例仅用于 T1 验证。
