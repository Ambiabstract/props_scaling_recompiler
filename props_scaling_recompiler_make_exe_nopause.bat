pyinstaller --onefile --name=props_scaling_recompiler --icon=props_scaling_recompiler_icon_v1.ico props_scaling_recompiler.py

set source="dist\props_scaling_recompiler.exe"
set destination="C:\Program Files (x86)\Steam\steamapps\common\Half-Life 2\bin"

xcopy /y %source% %destination%

rem pause