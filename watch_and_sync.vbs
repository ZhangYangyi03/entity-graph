Set ws = CreateObject("Wscript.Shell")
base = "C:\Users\china\Desktop\" & Chr(39033) & Chr(30446) & "_" & Chr(24320) & Chr(21457) & "\entity-graph"
ws.CurrentDirectory = base
ws.Run "pythonw.exe """ & base & "\sync_clean_to_github.py"" --watch", 0, False
