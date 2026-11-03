import io
import os
import zipfile

# 测试进程默认关闭启动期 MCP 自动发现，避免 TestClient 反复拉起子进程；
# 自动发现路径由 test_external_tools.py 用桩单独覆盖。
os.environ.setdefault("WORKBENCH_MCP_AUTODISCOVERY", "off")


def skill_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "external-skill/SKILL.md",
            "---\nname: external-skill\ndescription: Synthetic external skill\n"
            "metadata:\n  version: 1.2.0\nallowed-tools:\n  - list_sources\n---\n"
            "Use list_sources before drawing conclusions.\n",
        )
        archive.writestr("external-skill/references/guide.md", "Synthetic reference")
    return buffer.getvalue()
