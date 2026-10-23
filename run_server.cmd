@echo off
cd /d "D:\Users\china\Desktop\项目_开发\entity-graph"
set PYTHONPATH=%~dp0
set ENTITYGRAPH_HOST=127.0.0.1
set ENTITYGRAPH_PORT=8087
C:\Users\china\miniconda3\python.exe -u -m entitygraph.server >> server.runtime.log 2>&1
