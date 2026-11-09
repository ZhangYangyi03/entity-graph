import os, subprocess, sys
base = os.path.join("C:/Users/china/Desktop", chr(39033)+chr(30446)+"_"+chr(24320)+chr(21457), "entity-graph")
os.chdir(base)
pyw = sys.executable.replace("python.exe", "pythonw.exe")
# DETACHED + BREAKAWAY so the watcher is not reaped when the launching job closes
flags = 0x00000008 | 0x00000200 | 0x01000000 | 0x08000000
with open(os.devnull, "wb") as null:
    subprocess.Popen([pyw, "sync_clean_to_github.py", "--watch"], cwd=base,
                     stdin=null, stdout=null, stderr=null,
                     creationflags=flags, close_fds=True)
