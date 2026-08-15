#define MyAppName "ERP Workbench"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "ERP Workbench"
#define MyAppExeName "ERPWorkbench.exe"

[Setup]
AppId={{C8F4A27D-5A5D-4F65-BDF7-A1B2C3D4E5F6}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\ERP Workbench
DefaultGroupName=ERP Workbench
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\release
OutputBaseFilename=ERP_Workbench_1.0_Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=ERP Workbench 1.0
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\ERPWorkbench\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ERP Workbench"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\ERP Workbench"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch ERP Workbench"; Flags: nowait postinstall skipifsilent
