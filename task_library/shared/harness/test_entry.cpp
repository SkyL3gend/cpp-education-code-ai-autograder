// 通讯录小改题库 · 测试入口（测评宿主注入，不属于学生提交内容）
//
// 协议（由题库 IOTemplate 声明）：
//   标准输入：一行 JSON 请求信封
//     {"protocol_version":"1.0","run_id":"...","attempt_id":"...",
//      "case_id":"...","operation_id":"...","input":{...}}
//   标准输出：最后一行 JSON 观测信封
//     {"protocol_version":"1.0","run_id":"...","attempt_id":"...","case_id":"...",
//      "operation_id":"...","status":"OK|INVALID|OBSERVATION_UNAVAILABLE|ADAPTER_ERROR",
//      "actual":{...},"error":"..."}
//
// status 语义：
//   OK                   观测已产生
//   INVALID              请求信封本身非法（宿主侧问题）
//   OBSERVATION_UNAVAILABLE  被测业务调用抛出异常，无法产生观测（学生代码问题）
//   ADAPTER_ERROR        驱动自身故障（unknown operation_id 等）
// 观测只来自 ContactBook 的真实状态，不包含任何 PASS/FAIL 结论。
#include <cctype>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "contactbook.h"
#include "json_lite.h"

namespace {

using jsonlite::Value;

Value contactToJson(const cb::Contact& c) {
    Value item = Value::object();
    item["id"] = Value::of(c.id);
    item["name"] = Value::of(c.name);
    item["phone"] = Value::of(c.phone);
    item["birthday"] = Value::of(c.birthdayText());
    return item;
}

Value bookToJson(const cb::ContactBook& book) {
    Value list = Value::array();
    const std::vector<cb::Contact>& contacts = book.contacts();
    for (std::size_t i = 0; i < contacts.size(); ++i) {
        list.push(contactToJson(contacts[i]));
    }
    return list;
}

void seedFromInput(cb::ContactBook& book, const Value& input) {
    const Value* list = input.find("initial_contacts");
    if (list == nullptr || !list->isArray()) {
        return;
    }
    int fallbackId = 0;
    for (std::size_t i = 0; i < list->arrValue.size(); ++i) {
        const Value& item = list->arrValue[i];
        ++fallbackId;
        int id = item.intOr("id", fallbackId);
        std::string name = item.stringOr("name", "");
        std::string phone = item.stringOr("phone", "");
        int year = 1990;
        int month = 1;
        int day = 1;
        const Value* birthday = item.find("birthday");
        if (birthday != nullptr && birthday->isObject()) {
            year = birthday->intOr("year", 1990);
            month = birthday->intOr("month", 1);
            day = birthday->intOr("day", 1);
        }
        book.seed(id, name, phone, year, month, day);
    }
}

void readBirthday(const Value& holder, const char* key, int* year, int* month, int* day) {
    const Value* birthday = holder.find(key);
    if (birthday == nullptr || !birthday->isObject()) {
        return;
    }
    *year = birthday->intOr("year", *year);
    *month = birthday->intOr("month", *month);
    *day = birthday->intOr("day", *day);
}

// ---------------------------------------------------------------------------
// operation: create_contact
//   input:  {initial_contacts[], candidate{name,phone,birthday{year,month,day}}}
//   actual: {created: bool, contacts_after: [...]}
// ---------------------------------------------------------------------------
Value opCreateContact(const Value& input) {
    cb::ContactBook book;
    seedFromInput(book, input);

    bool created = false;
    int year = 1990;
    int month = 1;
    int day = 1;
    std::string name;
    std::string phone;
    const Value* candidate = input.find("candidate");
    if (candidate != nullptr && candidate->isObject()) {
        name = candidate->stringOr("name", "");
        phone = candidate->stringOr("phone", "");
        readBirthday(*candidate, "birthday", &year, &month, &day);
        created = book.addContact(name, phone, year, month, day);
    }

    Value actual = Value::object();
    actual["created"] = Value::of(created);
    actual["contacts_after"] = bookToJson(book);
    return actual;
}

// ---------------------------------------------------------------------------
// operation: birthday_reminders
//   input:  {initial_contacts[], today{year,month,day}, days_ahead: int}
//   actual: {names: [string], contacts_after: [...]}
// ---------------------------------------------------------------------------
Value opBirthdayReminders(const Value& input) {
    cb::ContactBook book;
    seedFromInput(book, input);

    int year = 2000;
    int month = 1;
    int day = 1;
    readBirthday(input, "today", &year, &month, &day);
    int daysAhead = input.intOr("days_ahead", 0);

    std::vector<std::string> names = book.birthdayReminders(year, month, day, daysAhead);

    Value nameList = Value::array();
    for (std::size_t i = 0; i < names.size(); ++i) {
        nameList.push(Value::of(names[i]));
    }

    Value actual = Value::object();
    actual["names"] = nameList;
    actual["contacts_after"] = bookToJson(book);
    return actual;
}

// ---------------------------------------------------------------------------
// operation: search_contact
//   input:  {initial_contacts[], keyword: string}
//   actual: {matches: [...], contacts_after: [...]}
// ---------------------------------------------------------------------------
Value opSearchContact(const Value& input) {
    cb::ContactBook book;
    seedFromInput(book, input);

    std::string keyword = input.stringOr("keyword", "");
    std::vector<cb::Contact> found = book.searchByName(keyword);

    Value matches = Value::array();
    for (std::size_t i = 0; i < found.size(); ++i) {
        matches.push(contactToJson(found[i]));
    }

    Value actual = Value::object();
    actual["matches"] = matches;
    actual["contacts_after"] = bookToJson(book);
    return actual;
}

// ---------------------------------------------------------------------------
// operation: id_allocation
//   input:  {initial_contacts[], ops:[{kind:"add",...}|{kind:"delete",id}]}
//   actual: {id_list: [int], contacts_after: [...]}
// ---------------------------------------------------------------------------
Value opIdAllocation(const Value& input) {
    cb::ContactBook book;
    seedFromInput(book, input);

    const Value* ops = input.find("ops");
    if (ops != nullptr && ops->isArray()) {
        for (std::size_t i = 0; i < ops->arrValue.size(); ++i) {
            const Value& op = ops->arrValue[i];
            std::string kind = op.stringOr("kind", "");
            if (kind == "add") {
                int year = 1990;
                int month = 1;
                int day = 1;
                readBirthday(op, "birthday", &year, &month, &day);
                book.addContact(op.stringOr("name", ""), op.stringOr("phone", ""), year, month, day);
            } else if (kind == "delete") {
                book.removeById(op.intOr("id", -1));
            }
        }
    }

    Value idList = Value::array();
    const std::vector<cb::Contact>& contacts = book.contacts();
    for (std::size_t i = 0; i < contacts.size(); ++i) {
        idList.push(Value::of(contacts[i].id));
    }

    Value actual = Value::object();
    actual["id_list"] = idList;
    actual["contacts_after"] = bookToJson(book);
    return actual;
}

// ---------------------------------------------------------------------------
// operation: snapshot_contacts（用于动作前状态观测）
//   input:  {initial_contacts[]}
//   actual: {contacts_after: [...]}
// ---------------------------------------------------------------------------
Value opSnapshotContacts(const Value& input) {
    cb::ContactBook book;
    seedFromInput(book, input);
    Value actual = Value::object();
    actual["contacts_after"] = bookToJson(book);
    return actual;
}

void writeEnvelope(const std::string& protocolVersion, const std::string& runId,
                   const std::string& attemptId, const std::string& caseId,
                   const std::string& operationId, const std::string& status,
                   const Value* actual, const std::string& error) {
    Value envelope = Value::object();
    envelope["protocol_version"] = Value::of(protocolVersion);
    envelope["run_id"] = Value::of(runId);
    envelope["attempt_id"] = Value::of(attemptId);
    envelope["case_id"] = Value::of(caseId);
    envelope["operation_id"] = Value::of(operationId);
    envelope["status"] = Value::of(status);
    envelope["actual"] = (actual != nullptr) ? *actual : Value::object();
    if (!error.empty()) {
        envelope["error"] = Value::of(error);
    }
    std::cout << jsonlite::dump(envelope) << "\n";
}

}  // namespace

int main() {
    std::ios::sync_with_stdio(false);

    std::ostringstream buffer;
    buffer << std::cin.rdbuf();
    const std::string requestText = buffer.str();

    Value request;
    try {
        request = jsonlite::parse(requestText);
    } catch (const std::exception& e) {
        writeEnvelope("", "", "", "", "", "INVALID", nullptr, std::string("request parse error: ") + e.what());
        return 0;
    }

    if (!request.isObject()) {
        writeEnvelope("", "", "", "", "", "INVALID", nullptr, "request must be a JSON object");
        return 0;
    }

    const std::string protocolVersion = request.stringOr("protocol_version", "");
    const std::string runId = request.stringOr("run_id", "");
    const std::string attemptId = request.stringOr("attempt_id", "");
    const std::string caseId = request.stringOr("case_id", "");
    const std::string operationId = request.stringOr("operation_id", "");

    if (protocolVersion != "1.0") {
        writeEnvelope(protocolVersion, runId, attemptId, caseId, operationId, "INVALID", nullptr,
                      "unsupported protocol_version");
        return 0;
    }

    const Value* input = request.find("input");
    if (input == nullptr || !input->isObject()) {
        writeEnvelope(protocolVersion, runId, attemptId, caseId, operationId, "INVALID", nullptr,
                      "missing input object");
        return 0;
    }

    Value actual;
    try {
        if (operationId == "create_contact") {
            actual = opCreateContact(*input);
        } else if (operationId == "birthday_reminders") {
            actual = opBirthdayReminders(*input);
        } else if (operationId == "search_contact") {
            actual = opSearchContact(*input);
        } else if (operationId == "id_allocation") {
            actual = opIdAllocation(*input);
        } else if (operationId == "snapshot_contacts") {
            actual = opSnapshotContacts(*input);
        } else {
            writeEnvelope(protocolVersion, runId, attemptId, caseId, operationId, "ADAPTER_ERROR", nullptr,
                          "unknown operation_id");
            return 0;
        }
    } catch (const std::exception& e) {
        writeEnvelope(protocolVersion, runId, attemptId, caseId, operationId, "OBSERVATION_UNAVAILABLE",
                      nullptr, std::string("business call threw: ") + e.what());
        return 0;
    } catch (...) {
        writeEnvelope(protocolVersion, runId, attemptId, caseId, operationId, "OBSERVATION_UNAVAILABLE",
                      nullptr, "business call threw: unknown exception");
        return 0;
    }

    writeEnvelope(protocolVersion, runId, attemptId, caseId, operationId, "OK", &actual, "");
    return 0;
}
