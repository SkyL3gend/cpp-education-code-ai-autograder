# 通讯录基础工程（contactbook_base 1.0.0）

这是小改题库使用的**简化版通讯录程序**，由参考程序
（`参考基准程序/通讯录_v6最终版.cpp`）简化而来：保留"联系人编号、姓名、手机号、
生日、按姓名检索、生日提醒、增删"这些核心业务，去掉菜单里的分页、排序、
统计、导入导出等与本阶段小改任务无关的部分。

## 文件说明

| 文件 | 作用 | 自动测评时是否编译 |
| --- | --- | --- |
| `contactbook.h` | `Contact` / `ContactBook` 类型与接口声明 | 会被包含 |
| `contactbook.cpp` | 通讯录业务实现（新增、删除、检索、生日提醒） | 是 |
| `validators.h` | 手机号、日期、生日计算函数声明 | 会被包含 |
| `validators.cpp` | 校验与日期计算实现 | 是 |
| `main_menu.cpp` | 交互式控制台入口（手工体验用） | **否**（测评构建按 `exclude_globs` 排除） |

## 注意

- 请**不要修改头文件中的函数签名和类型定义**，否则自动测评的测试入口无法编译。
- `contactbook.cpp` / `validators.cpp` 的实现可以自由修改。
- 编译方式（与自动测评一致）：

```
g++ -std=c++17 -O2 -I. contactbook.cpp validators.cpp main_menu.cpp -o contactbook.exe
```
