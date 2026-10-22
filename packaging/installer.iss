; Installer Windows pentru Music Manager (Inno Setup 6)
#define AppVer GetEnv("APP_VERSION")
#if AppVer == ""
  #define AppVer "0.0.0"
#endif

[Setup]
AppId={{6C1F7E3A-9B2D-4C8E-A1F0-5D3B2E7C9A41}
AppName=Music Manager
AppVersion={#AppVer}
AppPublisher=George Mocanu
DefaultDirName={localappdata}\Programs\Music Manager
DefaultGroupName=Music Manager
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=MusicManager-Setup
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\MusicManager.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Pune o scurtătură pe Desktop"; GroupDescription: "Scurtături:"

[Files]
Source: "..\dist\MusicManager\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Music Manager"; Filename: "{app}\MusicManager.exe"
Name: "{userdesktop}\Music Manager"; Filename: "{app}\MusicManager.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MusicManager.exe"; Description: "Pornește Music Manager"; Flags: nowait postinstall skipifsilent
