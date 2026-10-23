// 通讯录小改题库 · 基础工程（validators 模块）
// 本文件声明日期、手机号、生日提醒相关的校验与计算函数。
// 学生做小改任务时通常修改 validators.cpp 中的函数实现，
// 不应修改本头文件中的函数签名。


#include <string>

namespace cb {

// 判断给定年份是否为闰年
bool isLeapYear(int year);

// 返回给定年份某月的天数；月份越界时返回 0
int daysInMonth(int year, int month);

// 返回 year-month-day 是当年的第几天（1 月 1 日为 1）
int dayOfYear(int year, int month, int day);

// 判断手机号是否合法
bool isValidPhone(const std::string& phone);

// 判断生日是否为真实存在的日期
bool isValidDate(int year, int month, int day);

// 计算距离下一次生日还有多少天：今天生日返回 0，明天生日返回 1
int daysUntilBirthday(int todayYear, int todayMonth, int todayDay,
                      int birthMonth, int birthDay);

}  // namespace cb

// 通讯录小改题库 · 基础工程（通讯录数据结构与业务）
// 学生做小改任务时通常修改 contactbook.cpp 中的函数实现，
// 不应修改本头文件中的类型定义与函数签名。


#include <string>
#include <vector>

namespace cb {

struct Contact {
    int id = 0;
    std::string name;
    std::string phone;
    int birthYear = 0;
    int birthMonth = 0;
    int birthDay = 0;

    // 以 "YYYY-MM-DD" 形式返回生日
    std::string birthdayText() const;
};

class ContactBook {
public:
    // 放入一条已经存在的联系人记录（读档、导入时使用），不会做校验
    void seed(int id, const std::string& name, const std::string& phone,
              int birthYear, int birthMonth, int birthDay);

    // 新增联系人：姓名非空、手机号合法、生日合法时才写入，成功返回 true
    bool addContact(const std::string& name, const std::string& phone,
                    int birthYear, int birthMonth, int birthDay);

    // 按编号删除联系人，删除成功返回 true
    bool removeById(int id);

    const std::vector<Contact>& contacts() const { return contacts_; }

    // 按姓名关键字检索（关键字为空时返回空列表）
    std::vector<Contact> searchByName(const std::string& keyword) const;

    // 返回未来 daysAhead 天内（含今天）过生日的联系人姓名
    std::vector<std::string> birthdayReminders(int todayYear, int todayMonth,
                                               int todayDay, int daysAhead) const;

private:
    std::vector<Contact> contacts_;
};

}  // namespace cb

// 通讯录小改题库 · 基础工程（validators 模块实现）
//
// 说明：这里保留一份"能编译、能跑、但不完全符合需求"的实现，
// 作为小改任务的初始版本。请按每道任务的功能要求修改对应函数。


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
    if (month < 1 || month > 12) {
        return false;
    }
    if (day < 1) {
        return false;
    }
    if (month == 2) {
        return day <= 29;
    }
    return day <= daysInMonth(year, month);
}

// 任务：今天是生日时返回 0；生日已过则算到明年。
int daysUntilBirthday(int todayYear, int todayMonth, int todayDay,
                      int birthMonth, int birthDay) {
    int today = dayOfYear(todayYear, todayMonth, todayDay);
    int birth = dayOfYear(todayYear, birthMonth, birthDay);
    if (birth < today) {
        birth += isLeapYear(todayYear) ? 366 : 365;
    }
    return birth - today;
}

}  // namespace cb

// 通讯录小改题库 · 基础工程（通讯录业务实现）
//
// 说明：这里保留一份"能编译、能跑、但不完全符合需求"的实现，
// 作为小改任务的初始版本。请按每道任务的功能要求修改对应函数。


#include <cstdio>



namespace cb {

std::string Contact::birthdayText() const {
    char buffer[16] = {0};
    std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02d", birthYear, birthMonth, birthDay);
    return std::string(buffer);
}

void ContactBook::seed(int id, const std::string& name, const std::string& phone,
                       int birthYear, int birthMonth, int birthDay) {
    Contact c;
    c.id = id;
    c.name = name;
    c.phone = phone;
    c.birthYear = birthYear;
    c.birthMonth = birthMonth;
    c.birthDay = birthDay;
    contacts_.push_back(c);
}

bool ContactBook::addContact(const std::string& name, const std::string& phone,
                             int birthYear, int birthMonth, int birthDay) {
    if (name.empty()) {
        return false;
    }
    if (!isValidPhone(phone)) {
        return false;
    }
    if (!isValidDate(birthYear, birthMonth, birthDay)) {
        return false;
    }
    Contact c;
    c.id = static_cast<int>(contacts_.size()) + 1;
    c.name = name;
    c.phone = phone;
    c.birthYear = birthYear;
    c.birthMonth = birthMonth;
    c.birthDay = birthDay;
    contacts_.push_back(c);
    return true;
}

bool ContactBook::removeById(int id) {
    for (std::size_t i = 0; i < contacts_.size(); ++i) {
        if (contacts_[i].id == id) {
            contacts_.erase(contacts_.begin() + static_cast<long>(i));
            return true;
        }
    }
    return false;
}

std::vector<Contact> ContactBook::searchByName(const std::string& keyword) const {
    std::vector<Contact> result;
    if (keyword.empty()) {
        return result;
    }
    for (std::size_t i = 0; i < contacts_.size(); ++i) {
        if (contacts_[i].name.find(keyword) != std::string::npos) {
            result.push_back(contacts_[i]);
            break;
        }
    }
    return result;
}

std::vector<std::string> ContactBook::birthdayReminders(int todayYear, int todayMonth,
                                                        int todayDay, int daysAhead) const {
    std::vector<std::string> result;
    for (std::size_t i = 0; i < contacts_.size(); ++i) {
        int left = daysUntilBirthday(todayYear, todayMonth, todayDay,
                                     contacts_[i].birthMonth, contacts_[i].birthDay);
        if (left <= daysAhead) {
            result.push_back(contacts_[i].name);
        }
    }
    return result;
}

}  // namespace cb
