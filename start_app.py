import subprocess
import time
import os

# 启动后端服务
print("启动后端服务...")
backend_proc = subprocess.Popen(
    ["python", "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8001"],
    cwd="d:\\shopkeeper-agent-main\\interview-simulator\\backend",
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

# 等待后端启动
time.sleep(3)

# 启动前端服务
print("启动前端服务...")
frontend_proc = subprocess.Popen(
    ["python", "-m", "http.server", "5173"],
    cwd="d:\\shopkeeper-agent-main\\interview-simulator\\frontend",
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

print("服务启动完成！")
print("后端 API: http://127.0.0.1:8001")
print("前端页面: http://127.0.0.1:5173")

# 等待用户输入来停止服务
input("按 Enter 键停止服务...")

# 停止服务
backend_proc.terminate()
frontend_proc.terminate()
print("服务已停止")