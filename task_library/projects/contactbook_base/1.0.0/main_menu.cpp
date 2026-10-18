// 通讯录小改题库 · 基础工程（交互式控制台入口）
//
// 这是给学生手工体验和自测用的控制台程序，不属于自动测评的构建对象。
// 自动测评只编译 contactbook.cpp / validators.cpp，
// 由题库提供的 test_entry.cpp 作为测试入口。
#include <cstdlib>
#include <iostream>
#include <string>

#ifdef _WIN32
#include <windows.h>
#endif

#include "contactbook.h"
#include "validators.h"

namespace {

void seedDemoData(cb::ContactBook& book) {
    book.seed(1, "张伟", "13800138000", 1995, 10, 18);
    book.seed(2, "李娜", "13900139000", 1990, 3, 15);
    book.seed(3, "王强", "13700137000", 2000, 2, 29);
}

void printContacts(const std::vector<cb::Contact>& list) {
    if (list.empty()) {
        std::cout << "  （没有记录）\n";
        return;
    }
    for (std::size_t i = 0; i < list.size(); ++i) {
        const cb::Contact& c = list[i];
        std::cout << "  #" << c.id << " " << c.name << "  手机:" << c.phone
                  << "  生日:" << c.birthdayText() << "\n";
    }
}

int readInt(const std::string& prompt) {
    std::cout << prompt;
    std::string line;
    if (!std::getline(std::cin, line)) {
        return -1;
    }
    try {
        return std::stoi(line);
    } catch (...) {
        return -1;
    }
}

std::string readLine(const std::string& prompt) {
    std::cout << prompt;
    std::string line;
    std::getline(std::cin, line);
    return line;
}

void doAdd(cb::ContactBook& book) {
    std::string name = readLine("姓名: ");
    std::string phone = readLine("手机号: ");
    int year = readInt("出生年: ");
    int month = readInt("出生月: ");
    int day = readInt("出生日: ");
    if (book.addContact(name, phone, year, month, day)) {
        std::cout << "添加成功。\n";
    } else {
        std::cout << "添加失败：姓名、手机号或生日不符合要求。\n";
    }
}

void doSearch(const cb::ContactBook& book) {
    std::string keyword = readLine("姓名关键字: ");
    std::vector<cb::Contact> found = book.searchByName(keyword);
    std::cout << "共找到 " << found.size() << " 条:\n";
    printContacts(found);
}

void doRemind(const cb::ContactBook& book) {
    int year = readInt("今天（年）: ");
    int month = readInt("今天（月）: ");
    int day = readInt("今天（日）: ");
    int daysAhead = readInt("提醒未来多少天内（含今天）: ");
    std::vector<std::string> names = book.birthdayReminders(year, month, day, daysAhead);
    std::cout << "需要提醒 " << names.size() << " 人:\n";
    for (std::size_t i = 0; i < names.size(); ++i) {
        std::cout << "  " << names[i] << "\n";
    }
}

void doRemove(cb::ContactBook& book) {
    int id = readInt("要删除的编号: ");
    if (book.removeById(id)) {
        std::cout << "已删除。\n";
    } else {
        std::cout << "编号不存在。\n";
    }
}

}  // namespace

int main() {
#ifdef _WIN32
    SetConsoleCP(65001);
    SetConsoleOutputCP(65001);
#endif
    std::cout << "==== 通讯录（初始版本）====\n";
    cb::ContactBook book;
    seedDemoData(book);

    while (true) {
        std::cout << "\n1 列表  2 添加  3 检索  4 生日提醒  5 删除  0 退出\n> ";
        std::string line;
        if (!std::getline(std::cin, line)) {
            break;
        }
        int choice = -1;
        try {
            choice = std::stoi(line);
        } catch (...) {
            choice = -1;
        }
        if (choice == 0) {
            break;
        } else if (choice == 1) {
            printContacts(book.contacts());
        } else if (choice == 2) {
            doAdd(book);
        } else if (choice == 3) {
            doSearch(book);
        } else if (choice == 4) {
            doRemind(book);
        } else if (choice == 5) {
            doRemove(book);
        }
    }
    std::cout << "再见。\n";
    return 0;
}
