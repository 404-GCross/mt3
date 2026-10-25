"""WebUI：在浏览器里用 MT3 转谱（本机或局域网）。

- ``jobs``    任务队列（单 worker 串行，模型按需加载）
- ``server``  FastAPI 应用与命令行入口（``app.py --web``）
- ``selftest`` 不需要模型的接口自检

入口在 ``gui/app.py``：``mt3-transcriber --web --lan``。
"""
