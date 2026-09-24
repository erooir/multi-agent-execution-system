import os

# 测试进程默认关闭启动期 MCP 自动发现，避免 TestClient 反复拉起子进程；
# 自动发现路径由 test_external_tools.py 用桩单独覆盖。
os.environ.setdefault("WORKBENCH_MCP_AUTODISCOVERY", "off")
