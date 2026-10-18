// 通讯录小改题库 · 基础工程（通讯录数据结构与业务）
// 学生做小改任务时通常修改 contactbook.cpp 中的函数实现，
// 不应修改本头文件中的类型定义与函数签名。
#pragma once

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
