"""Bounded trusted programs verify live output and disk termination."""
import shutil
from pathlib import Path
import pytest
from cpp_assessor.tasks.loader import load_task
from cpp_assessor.domain.models import BuildPlan
from cpp_assessor.domain.enums import CompileStatus,Termination
from cpp_assessor.sandbox.base import CompileRequest,RunRequest
from cpp_assessor.sandbox.local import LocalJobBackend

pytestmark=pytest.mark.skipif(shutil.which("g++") is None,reason="trusted compiler required")

@pytest.mark.parametrize("disk",[False,True])
def test_live_monitor_stops_before_wall_deadline(tmp_path,disk):
    backend=LocalJobBackend()
    task=load_task("t1_phone_validation@1.0.0")
    limits=task.document.limits.model_copy(update={"case_wall_ms":5000,"output_bytes":3072,"workdir_bytes":64*1024*1024})
    source=tmp_path/"known_monitor_fixture.cpp"
    body='std::ofstream out("large.bin",std::ios::binary); for(int i=0;i<100;i++){out<<std::string(32768,\'x\');out.flush();Sleep(25);}' if disk else 'for(int i=0;i<100;i++){std::cout<<std::string(512,\'x\')<<std::flush;std::cerr<<std::string(512,\'x\')<<std::flush;Sleep(25);}'
    source.write_text('#include <windows.h>\n#include <iostream>\n#include <fstream>\n#include <string>\nint main(){'+body+'}',encoding='utf-8')
    plan=BuildPlan(schema_version="1.0",build_plan_id="resource_fixture",role="ORIGINAL",subject_snapshot_hash="0"*64,toolchain_id="test",source_units=[],include_dirs=[],defines=[],libraries=[],entrypoint="main",artifact_name="subject.exe",build_hash="0"*64)
    exe=tmp_path/"subject.exe"
    compiled=backend.compile(CompileRequest(build_plan=plan,compiler=Path(shutil.which("g++")),source_units=[source],include_dirs=[],flags=["-std=c++17"],output=exe,work_dir=tmp_path,limits=limits))
    assert compiled.status is CompileStatus.SUCCESS, compiled.stderr
    work=tmp_path/"run";work.mkdir();stdin=work/"stdin.txt";stdin.write_text("")
    runtime=limits.model_copy(update={"workdir_bytes":32768}) if disk else limits
    result=backend.run(RunRequest(executable=exe,argv=[],stdin_path=stdin,stdout_path=work/"stdout.txt",stderr_path=work/"stderr.txt",work_dir=work,limits=runtime))
    assert result.termination is (Termination.DISK_LIMIT if disk else Termination.OUTPUT_LIMIT)
    assert result.wall_ms < 4000
