"""Known C++ test targets with a new protocol/output layout, no live model calls."""
from pathlib import Path
from cpp_assessor.tasks.loader import load_task
from tests.verifier_fixtures import attach_evidence

LIB = r'''
#pragma once
#include "json_lite.h"
#include <cctype>
#include <algorithm>
#include <cstdio>
using jsonlite::Value;
inline Value createEntry(const Value& input) {
    Value records = *input.find("initial_contacts");
    int next=1;
    for(auto& row: records.arrValue) {
        next=std::max(next,row.intOr("id",0)+1);
        const Value& b=*row.find("birthday");
        char date[32]; std::snprintf(date,32,"%04d-%02d-%02d",b.intOr("year",0),b.intOr("month",0),b.intOr("day",0));
        row["birthday"]=Value::of(std::string(date));
    }
    const Value& c=*input.find("candidate");
    std::string phone=c.stringOr("phone","");
    bool ok=phone.size()==11 && phone[0]=='1';
    for(unsigned char ch:phone) if(ch<'0'||ch>'9') ok=false;
    if(ok){
        Value row=c; row["id"]=Value::of(next);
        const Value& b=*c.find("birthday");
        char date[32]; std::snprintf(date,32,"%04d-%02d-%02d",b.intOr("year",0),b.intOr("month",0),b.intOr("day",0));
        row["birthday"]=Value::of(std::string(date)); records.push(row);
    }
    Value actual=Value::object(); actual["created"]=Value::of(ok); actual["contacts_after"]=records;
    return actual;
}
'''
MAIN = r'''
#include <iostream>
#include <sstream>
#include "include/alien.hpp"
int main(){ std::ostringstream in; in<<std::cin.rdbuf(); auto req=jsonlite::parse(in.str());
std::cout<<"ALIEN_LAYOUT:"<<jsonlite::dump(createEntry(req))<<std::endl; }
'''
DRIVER = r'''
#include <iostream>
#include <sstream>
#include "alien.hpp"
int main(){ std::ostringstream in; in<<std::cin.rdbuf(); auto req=jsonlite::parse(in.str());
auto actual=createEntry(*req.find("input")); req["protocol_version"]=Value::of(req.stringOr("protocol_version","")); req["status"]=Value::of(std::string("OK"));
req.objValue.erase("input"); req["actual"]=actual; std::cout<<jsonlite::dump(req)<<std::endl; }
'''
OBSERVER = r'''
#include <iostream>
#include <sstream>
#include "json_lite.h"
int main(){ std::ostringstream in; in<<std::cin.rdbuf(); auto outer=jsonlite::parse(in.str());
auto req=*outer.find("request"); std::string raw=outer.stringOr("stdout","");
auto pos=raw.find("ALIEN_LAYOUT:"); if(pos==std::string::npos) return 2;
auto actual=jsonlite::parse(raw.substr(pos+13)); req.objValue.erase("input");
req["status"]=jsonlite::Value::of(std::string("OK")); req["actual"]=actual;
std::cout<<jsonlite::dump(req)<<std::endl; }
'''


def create_target(root: Path):
    task = load_task("t1_phone_validation@1.0.0")
    (root / "include").mkdir(parents=True)
    (root / "include/alien.hpp").write_text(LIB, encoding="utf-8")
    (root / "alien_lib.cpp").write_text('#include "alien.hpp"\n', encoding="utf-8")
    (root / "main.cpp").write_text(MAIN, encoding="utf-8")
    (root / "demo.cpp").write_text("int main(){return 0;}\n", encoding="utf-8")
    (root / "json_lite.h").write_bytes((task.harness_dir / "json_lite.h").read_bytes())
    return root


def bundle(root: Path, *, function=False):
    task = load_task("t1_phone_validation@1.0.0")
    payload = {
        "task_id": task.task_id, "task_version": task.version,
        "target": {"sources": ["alien_lib.cpp"] if function else ["main.cpp"], "entry_files": ["main.cpp"], "excluded_files": ["main.cpp", "demo.cpp"] if function else ["demo.cpp"], "include_dirs": ["include"]},
        "build": {"compiler_flags": ["-std=c++17", "-O2"]},
        "driver": {"kind": "function_driver", "driver_source": DRIVER} if function else {"kind": "stdin_script", "session": ["{{__input_json__}}"]},
        "observer": {"kind": "json_stdout", "extractor_id": "json_stdout_v1"} if function else {"kind": "generated_observer", "observer_source": OBSERVER},
        "assertion_bindings": [{"assertion_id": a.assertion_id, "actual_key": a.actual_observation_key} for a in task.method.assertions],
        "migration_matrix": [{"partition_id": p.partition_id, "rule_ids": p.rule_ids, "driver_step": 0, "observation": "Actual target records emitted by createEntry", "assertion_ids": [a.assertion_id for a in task.method.assertions], "status": "covered"} for p in task.method.input_partitions],
    }
    return attach_evidence(payload, task, {"include/alien.hpp": LIB}, "include/alien.hpp", "createEntry")
