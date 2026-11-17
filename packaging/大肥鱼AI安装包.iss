; 大肥鱼AI · 安装程序（Inno Setup 6）
; 程序装到 %LOCALAPPDATA%\Programs\大肥鱼AI（不需要管理员、不碰 C 盘系统目录）
; 数据放 %APPDATA%\大肥鱼AI\data，卸载默认保留

#define AppName "大肥鱼AI"
#define AppVersion "1.0.0"
#define AppPublisher "Childe"
#define AppExe "大肥鱼AI.exe"

[Setup]
AppId={{8F3C2A61-5B7D-4E29-9C84-2D6A0F1B7E55}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=E:\
OutputBaseFilename=大肥鱼AI安装包
SetupIconFile=E:\dfy_build\app\ds图片\avatar.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableWelcomePage=no
CloseApplications=yes
; 老版本还在跑时提示关掉，避免文件占用
CloseApplicationsFilter=*.exe,*.dll
RestartApplications=no

[Languages]
Name: "chinesesimplified"; MessagesFile: "E:\dfy_build\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务："; Flags: checkedonce
Name: "autostart"; Description: "开机自动启动（托盘常驻）"; GroupDescription: "附加任务："; Flags: unchecked

[Files]
Source: "E:\dfy_build\app\dist\大肥鱼AI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExe}"; WorkingDir: "{app}"; Tasks: autostart

[Run]
; 装完直接启动（不勾选也能从桌面点开）
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 只清程序目录里的临时产物，用户数据和聊天记录保留在 %APPDATA%\大肥鱼AI
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir: String;
begin
  if CurStep = ssPostInstall then
  begin
    DataDir := ExpandConstant('{userappdata}\大肥鱼AI\data');
    if not DirExists(DataDir) then
      ForceDirectories(DataDir);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    // 静默卸载（/SILENT、/VERYSILENT）一律保留数据，绝不替用户做主删记录
    if UninstallSilent then
      exit;
    // 数据保留：只在用户明确同意时才删
    if MsgBox('是否同时删除聊天记录、记忆和配置？' + #13#10 +
              '选择"否"则保留，重装后可以继续用原来的记录。',
              mbConfirmation, MB_YESNO) = IDYES then
    begin
      DelTree(ExpandConstant('{userappdata}\大肥鱼AI'), True, True, True);
    end;
  end;
end;
