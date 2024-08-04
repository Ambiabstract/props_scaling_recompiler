pyinstaller --onefile --name=props_scaling_recompiler --icon=props_scaling_recompiler_icon_v1.ico props_scaling_recompiler.py

rem set source="dist\props_scaling_recompiler.exe"
rem set destination="C:\Program Files (x86)\Steam\steamapps\common\Half-Life 2\bin"
rem 
rem xcopy /y %source% %destination%

pause