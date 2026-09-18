Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
here = fso.GetParentFolderName(WScript.ScriptFullName)
rc = sh.Run("cmd /c """ & here & "\kick.cmd""", 0, True)
WScript.Quit rc
