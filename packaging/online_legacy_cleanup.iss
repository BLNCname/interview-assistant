[InstallDelete]
; Exact obsolete DLLs from the v0.1.1 inventory, absent from v0.1.3.
; Inno executes these entries only after PrepareToInstall succeeds.
; Keep current PySide6 subdirectories and all unlisted files intact.
Type: files; Name: "{app}\_internal\dbgcore.dll"
Type: files; Name: "{app}\_internal\dbghelp.dll"
Type: files; Name: "{app}\_internal\MSVCP140_1.dll"
Type: files; Name: "{app}\_internal\MSVCP140_2.dll"
Type: files; Name: "{app}\_internal\Qt6Core.dll"
Type: files; Name: "{app}\_internal\Qt6Gui.dll"
Type: files; Name: "{app}\_internal\Qt6Network.dll"
Type: files; Name: "{app}\_internal\Qt6OpenGL.dll"
Type: files; Name: "{app}\_internal\Qt6Positioning.dll"
Type: files; Name: "{app}\_internal\Qt6Qml.dll"
Type: files; Name: "{app}\_internal\Qt6QmlMeta.dll"
Type: files; Name: "{app}\_internal\Qt6QmlModels.dll"
Type: files; Name: "{app}\_internal\Qt6QmlWorkerScript.dll"
Type: files; Name: "{app}\_internal\Qt6Quick.dll"
Type: files; Name: "{app}\_internal\Qt6WebChannel.dll"
Type: files; Name: "{app}\_internal\Qt6WebEngineCore.dll"
