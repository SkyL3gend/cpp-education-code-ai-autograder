// 通讯录小改题库 · 基础工程（validators 模块实现）
//
// 说明：这里保留一份"能编译、能跑、但不完全符合需求"的实现，
// 作为小改任务的初始版本。请按每道任务的功能要求修改对应函数。
#include "validators.h"

namespace cb {

bool isLeapYear(int year) {
    return (year % 4 == 0 && year % 100 != 0) || (year % 400 == 0);
}

int daysInMonth(int year, int month) {
    static const int table[13] = {0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    if (month < 1 || month > 12) {
        return 0;
    }
    if (month == 2 && isLeapYear(year)) {
        return 29;
    }
    return table[month];
}

int dayOfYear(int year, int month, int day) {
    int total = 0;
    for (int m = 1; m < month; ++m) {
        total += daysInMonth(year, m);
    }
    return total + day;
}

// 任务：手机号必须是 11 位数字且以 1 开头。
bool isValidPhone(const std::string& phone) {
    if (phone.size() != 11) {
        return false;
    }
    return phone[0] == '1';
}

// 任务：生日必须是真实存在的日期，且年份限制在 1900—2100。
bool isValidDate(int year, int month, int day) {
    if (year < 1900 || year > 2100) {
        return false;
    }
    if (month < 1 || month > 12) {
        return false;
    }
    if (day < 1 || day > daysInMonth(year, month)) {
        return false;
    }
    return true;
}

// 任务：今天是生日时返回 0；生日已过则算到明年。
int daysUntilBirthday(int todayYear, int todayMonth, int todayDay,
                      int birthMonth, int birthDay) {
    int today = dayOfYear(todayYear, todayMonth, todayDay);
    int birth = dayOfYear(todayYear, birthMonth, birthDay);
    if (birth <= today) {
        birth += isLeapYear(todayYear) ? 366 : 365;
    }
    return birth - today;
}

}  // namespace cb
