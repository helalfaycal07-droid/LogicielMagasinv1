#define AppName "LogicielMagasin"
#define AppVersion "0.3.3"
#define AppPublisher "LogicielMagasin"
#define AppExeName "LogicielMagasin.exe"

[Setup]
AppId={{E03B5A01-8D4B-46B8-BB52-B4108D46E29A}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
OutputDir=dist
OutputBaseFilename=LogicielMagasin-Setup-{#AppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SetupMutex={{BB32BB4B-1D91-46B8-BB52-B4108D46E29A}
CloseAfterInstall=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer une icône sur le Bureau"; GroupDescription: "Raccourcis supplémentaires :"; Flags: unchecked

[Files]
Source: "dist\LogicielMagasin\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "LICENCE.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist
Source: "README.txt"; DestDir: "{app}"; Flags: ignoreversion skipifsourcedoesntexist

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userprograms}\{#AppName}\Uninstall"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Lancer {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
Type: dirifempty; Name: "{localappdata}\Programs\{#AppName}"
Type: dirifempty; Name: "{userappdata}\{#AppName}"

[Registry]
Root: "HKCU"; SubKey: "Software\{#AppName}"; ValueType: string; ValueName: "InstalledVersion"; ValueData: "{#AppVersion}"
Root: "HKCU"; SubKey: "Software\{#AppName}"; ValueType: string; ValueName: "InstallDir"; ValueData: "{app}"

[Code]
function InitializeWizard(): String;
begin
    Result := '{#AppName} v{#AppVersion}';
end;

function IsFirstInstall(): Boolean;
var
    ResultKey: String;
begin
    Result := False;
    if RegQueryStringValue('HKCU', 'Software\' + '{#AppName}', 'InstalledVersion', ResultKey) then
        Result := (ResultKey <> '{#AppVersion}')
    else
        Result := True;
end;