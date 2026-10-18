// 通讯录小改题库 · 基础工程（validators 模块）
// 本文件声明日期、手机号、生日提醒相关的校验与计算函数。
// 学生做小改任务时通常修改 validators.cpp 中的函数实现，
// 不应修改本头文件中的函数签名。
#pragma once

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
