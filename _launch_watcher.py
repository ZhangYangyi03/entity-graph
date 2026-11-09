import os, subprocess, sys
base = os.path.join("C:/Users/china/Desktop", chr(39033)+chr(30446)+"_"+chr(24320)+chr(21457), "entity-graph")
os.chdir(base)
pyw = sys.executable.replace("python.exe", "pythonw.exe")
subprocess.Popen([pyw, "sync_clean_to_github.py", "--watch"], cwd=base, creationflags=0x08000000, close_fds=True)
