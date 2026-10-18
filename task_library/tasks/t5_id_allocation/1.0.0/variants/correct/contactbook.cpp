// 通讯录小改题库 · 基础工程（通讯录业务实现）
//
// 说明：这里保留一份"能编译、能跑、但不完全符合需求"的实现，
// 作为小改任务的初始版本。请按每道任务的功能要求修改对应函数。
#include "contactbook.h"

#include <cstdio>

#include "validators.h"

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
    int nextId = 1;
    for (std::size_t i = 0; i < contacts_.size(); ++i) {
        if (contacts_[i].id >= nextId) {
            nextId = contacts_[i].id + 1;
        }
    }
    Contact c;
    c.id = nextId;
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
