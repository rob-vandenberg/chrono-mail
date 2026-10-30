; --- Version ------------------------------------------------------------
; chrono-mail.iss 4.2.435
; --- Version history ----------------------------------------------------
; v4.2.435: the setup is named chrono-mail_<version>_x64_setup.exe
; v4.2.419: an update from Chrono Mail runs this setup silently; it starts Chrono Mail again when done, as the person (D-293)
; v4.2.402: the uninstaller removes the start at login of the person who uninstalls (D-284)
; v0.5.127: Initial: the Windows installer (Inno Setup 6), made by packaging\build_windows.ps1
;
; packaging\build_windows.ps1 passes Version (from pyproject.toml) and Source (the program folder Nuitka made).
; The AppId must never change: Windows recognises updates and the uninstaller by it.

#ifndef Version
  #error Version is not given: run packaging\build_windows.ps1
#endif
#ifndef Source
  #error Source is not given: run packaging\build_windows.ps1
#endif

[Setup]
AppId={{0546C20D-4EDF-4188-8267-7F98AC8B7196}
AppName=Chrono Mail
AppVersion={#Version}
AppVerName=Chrono Mail {#Version}
AppPublisher=Rob Vandenberg
AppPublisherURL=https://github.com/rob-vandenberg/chrono-mail
AppSupportURL=https://github.com/rob-vandenberg/chrono-mail/issues
DefaultDirName={autopf}\Chrono Mail
DefaultGroupName=Chrono Mail
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputBaseFilename=chrono-mail_{#Version}_x64_setup
SetupIconFile=..\src\chrono_mail\ui\resources\chrono-mail.ico
UninstallDisplayIcon={app}\chrono-mail.exe
UninstallDisplayName=Chrono Mail
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
; Installs for all users (asks for administrator rights); the person may choose to install for themselves only.
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Chrono Mail"; Filename: "{app}\chrono-mail.exe"; AppUserModelID: "chrono-mail.ChronoMail"
Name: "{autodesktop}\Chrono Mail"; Filename: "{app}\chrono-mail.exe"; Tasks: desktopicon; AppUserModelID: "chrono-mail.ChronoMail"

[Run]
Filename: "{app}\chrono-mail.exe"; Description: "{cm:LaunchProgram,Chrono Mail}"; Flags: nowait postinstall skipifsilent
; An update started by Chrono Mail itself (Check for updates, D-293) runs silently and opens the program again; never elevated.
Filename: "{app}\chrono-mail.exe"; Flags: nowait runasoriginaluser; Check: WizardSilent

[Code]
{ The start at login is the person's own entry in their Run list (Settings > General, D-284); it goes with the program. }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Run', 'Chrono Mail');
    RegDeleteValue(HKEY_CURRENT_USER, 'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run', 'Chrono Mail');
  end;
end;
