# vim: ts=4:sw=4:expandtab

# BleachBit
# Copyright (C) 2008-2015 Andrew Ziem
# http://bleachbit.sourceforge.net
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.


"""
Functionality specific to Microsoft Windows

The Windows Registry terminology can be confusing. Take for example
the reference
* HKCU\\Software\\BleachBit
* CurrentVersion

These are the terms:
* 'HKCU' is an abbreviation for the hive HKEY_CURRENT_USER.
* 'HKCU\Software\BleachBit' is the key name.
* 'Software' is a sub-key of HCKU.
* 'BleachBit' is a sub-key of 'Software.'
* 'CurrentVersion' is the value name.
* '0.5.1' is the value data.


"""


import glob
import os
import platform
import re
import sys
import traceback


if 'win32' == sys.platform:
    import _winreg
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32gui
    import win32process

    from ctypes import windll, c_ulong, c_buffer, byref, sizeof
    from win32com.shell import shell, shellcon

    psapi = windll.psapi
    kernel = windll.kernel32

import Command
import Common
import FileUtilities
import General


def browse_file(_, title):
    """Ask the user to select a single file.  Return full path"""
    try:
        ret = win32gui.GetOpenFileNameW(None,
                                        Flags=win32con.OFN_EXPLORER
                                        | win32con.OFN_FILEMUSTEXIST
                                        | win32con.OFN_HIDEREADONLY,
                                        Title=title)
    except pywintypes.error, e:
        if 0 == e.winerror:
            print 'debug: browse_file(): user cancelled'
        else:
            traceback.print_exc()
        return None
    return ret[0]


def browse_files(_, title):
    """Ask the user to select files.  Return full paths"""
    try:
        ret = win32gui.GetOpenFileNameW(None,
                                        Flags=win32con.OFN_ALLOWMULTISELECT
                                        | win32con.OFN_EXPLORER
                                        | win32con.OFN_FILEMUSTEXIST
                                        | win32con.OFN_HIDEREADONLY,
                                        Title=title)
    except pywintypes.error, e:
        if 0 == e.winerror:
            print 'debug: browse_files(): user cancelled'
        else:
            traceback.print_exc()
        return None
    _split = ret[0].split('\x00')
    if 1 == len(_split):
        # only one filename
        return _split
    pathnames = []
    dirname = _split[0]
    for fname in _split[1:]:
        pathnames.append(os.path.join(dirname, fname))
    return pathnames


def browse_folder(hwnd, title):
    """Ask the user to select a folder.  Return full path."""
    pidl = shell.SHBrowseForFolder(hwnd, None, title)[0]
    if None == pidl:
        # user cancelled
        return None
    fullpath = shell.SHGetPathFromIDList(pidl)
    return fullpath


def csidl_to_environ(varname, csidl):
    """Define an environment variable from a CSIDL for use in CleanerML and Winapp2.ini"""
    try:
        sppath = shell.SHGetSpecialFolderPath(None, csidl)
        set_environ(varname, sppath)
    except:
        traceback.print_exc()
        print 'ERROR: setting environment variable "%s": %s ' % (
            varname, str(sys.exc_info()[1]))


def delete_locked_file(pathname):
    """Delete a file that is currently in use"""
    if os.path.exists(pathname):
        win32api.MoveFileEx(
            pathname, None, win32con.MOVEFILE_DELAY_UNTIL_REBOOT)


def delete_registry_value(key, value_name, really_delete):
    """Delete named value under the registry key.
    Return boolean indicating whether reference found and
    successful.  If really_delete is False (meaning preview),
    just check whether the value exists."""
    (hive, sub_key) = split_registry_key(key)
    if really_delete:
        try:
            hkey = _winreg.OpenKey(hive, sub_key, 0, _winreg.KEY_SET_VALUE)
            _winreg.DeleteValue(hkey, value_name)
        except WindowsError, e:
            if e.winerror == 2:
                # 2 = 'file not found' means value does not exist
                return False
            raise
        else:
            return True
    try:
        hkey = _winreg.OpenKey(hive, sub_key)
        _winreg.QueryValueEx(hkey, value_name)
    except WindowsError, e:
        if e.winerror == 2:
            return False
        raise
    else:
        return True
    raise RuntimeError('Unknown error in delete_registry_value')


def delete_registry_key(parent_key, really_delete):
    """Delete registry key including any values and sub-keys.
    Return boolean whether found and success.  If really
    delete is False (meaning preview), just check whether
    the key exists."""
    parent_key = str(parent_key)  # Unicode to byte string
    (hive, parent_sub_key) = split_registry_key(parent_key)
    hkey = None
    try:
        hkey = _winreg.OpenKey(hive, parent_sub_key)
    except WindowsError, e:
        if e.winerror == 2:
            # 2 = 'file not found' happens when key does not exist
            return False
    if not really_delete:
        return True
    if not hkey:
        # key not found
        return False
    keys_size = _winreg.QueryInfoKey(hkey)[0]
    child_keys = []
    for i in range(keys_size):
        child_keys.append(parent_key + '\\' + _winreg.EnumKey(hkey, i))
    for child_key in child_keys:
        delete_registry_key(child_key, True)
    _winreg.DeleteKey(hive, parent_sub_key)
    return True


def delete_updates():
    """Returns commands for deleting Windows Updates files"""
    windir = os.path.expandvars('$windir')
    dirs = glob.glob(os.path.join(windir, '$NtUninstallKB*'))
    dirs += [os.path.expandvars('$windir\\SoftwareDistribution\\Download')]
    dirs += [os.path.expandvars('$windir\\ie7updates')]
    dirs += [os.path.expandvars('$windir\\ie8updates')]
    if not dirs:
        # if nothing to delete, then also do not restart service
        return

    import win32serviceutil
    wu_running = win32serviceutil.QueryServiceStatus('wuauserv')[1] == 4

    args = ['net', 'stop', 'wuauserv']

    def wu_service():
        General.run_external(args)
        return 0
    if wu_running:
        yield Command.Function(None, wu_service, " ".join(args))

    for path1 in dirs:
        for path2 in FileUtilities.children_in_directory(path1, True):
            yield Command.Delete(path2)
        if os.path.exists(path1):
            yield Command.Delete(path1)

    args = ['net', 'start', 'wuauserv']
    if wu_running:
        yield Command.Function(None, wu_service, " ".join(args))


def detect_registry_key(parent_key):
    """Detect whether registry key exists"""
    parent_key = str(parent_key)  # Unicode to byte string
    (hive, parent_sub_key) = split_registry_key(parent_key)
    hkey = None
    try:
        hkey = _winreg.OpenKey(hive, parent_sub_key)
    except WindowsError, e:
        if e.winerror == 2:
            # 2 = 'file not found' happens when key does not exist
            return False
    if not hkey:
        # key not found
        return False
    return True


def elevate_privileges():
    """On Windows Vista and later, try to get administrator
    privileges.  If successful, return True (so original process
    can exit).  If failed or not applicable, return False."""

    if platform.version() <= '5.2':
        # Earlier than Vista
        return False

    if shell.IsUserAnAdmin():
        print 'debug: already an admin (UAC not required)'
        return False

    if hasattr(sys, 'frozen'):
        # running frozen in py2exe
        exe = unicode(sys.executable, sys.getfilesystemencoding())
        py = "--gui --no-uac"
    else:
        # __file__ is absolute path to bleachbit/Windows.py
        pydir = os.path.dirname(unicode(__file__, sys.getfilesystemencoding()))
        pyfile = os.path.join(pydir, 'GUI.py')
        # If the Python file is on a network drive, do not offer the UAC because
        # the administrator may not have privileges and user will not be
        # prompted.
        if len(pyfile) > 0 and path_on_network(pyfile):
            print "debug: skipping UAC because '%s' is on network" % pyfile
            return False
        py = '"%s" --gui --no-uac' % pyfile
        exe = sys.executable

    # add any command line parameters such as --debug-log
    py = "%s %s" % (py, ' '.join(sys.argv[1:]))

    print 'debug: exe=', exe, ' parameters=', py

    rc = None
    try:
        rc = shell.ShellExecuteEx(lpVerb='runas',
                                  lpFile=exe,
                                  lpParameters=py,
                                  nShow=win32con.SW_SHOW)
    except pywintypes.error, e:
        if 1223 == e.winerror:
            print 'debug: user denied the UAC dialog'
            return False
        raise

    print 'debug: ShellExecuteEx=', rc

    if isinstance(rc, dict):
        return True

    return False


def empty_recycle_bin(path, really_delete):
    """Empty the recycle bin or preview its size.

    If the recycle bin is empty, it is not emptied again to avoid an error.

    Keyword arguments:
    path          -- A drive, folder or None.  None refers to all recycle bins.
    really_delete -- If True, then delete.  If False, then just preview.
    """
    (bytes_used, num_files) = shell.SHQueryRecycleBin(path)
    if really_delete and num_files > 0:
        # Trying to delete an empty Recycle Bin on Vista/7 causes a
        # 'catastrophic failure'
        flags = shellcon.SHERB_NOSOUND | shellcon.SHERB_NOCONFIRMATION | shellcon.SHERB_NOPROGRESSUI
        shell.SHEmptyRecycleBin(None, path, flags)
    return bytes_used


def get_autostart_path():
    """Return the path of the BleachBit shortcut in the user's startup folder"""
    try:
        startupdir = shell.SHGetSpecialFolderPath(None, shellcon.CSIDL_STARTUP)
    except:
        # example of failure
        # http://bleachbit.sourceforge.net/forum/error-windows-7-x64-bleachbit-091
        traceback.print_exc()
        msg = 'Error finding user startup folder: %s ' % (
            str(sys.exc_info()[1]))
        import GuiBasic
        GuiBasic.message_dialog(None, msg)
        # as a fallback, guess
        # Windows XP: C:\Documents and Settings\(username)\Start Menu\Programs\Startup
        # Windows 7:
        # C:\Users\(username)\AppData\Roaming\Microsoft\Windows\Start
        # Menu\Programs\Startup
        startupdir = os.path.expandvars(
            '$USERPROFILE\\Start Menu\\Programs\\Startup')
        if not os.path.exists(startupdir):
            startupdir = os.path.expandvars(
                '$APPDATA\\Roaming\\Microsoft\\Windows\\Start Menu\\Programs\\Startup')
    return os.path.join(startupdir, 'bleachbit.lnk')


def get_fixed_drives():
    """Yield each fixed drive"""
    for drive in win32api.GetLogicalDriveStrings().split('\x00'):
        if win32file.GetDriveType(drive) == win32file.DRIVE_FIXED:
            yield drive


def get_known_folder_path(folder_name):
    """Return the path of a folder by its Folder ID

    Requires Windows Vista, Server 2008, or later

    Based on the code Michael Kropat (mkropat) from
    <https://gist.github.com/mkropat/7550097>
    licensed  under the GNU GPL"""
    import ctypes
    from ctypes import wintypes
    from uuid import UUID

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8)
        ]

        def __init__(self, uuid_):
            ctypes.Structure.__init__(self)
            self.Data1, self.Data2, self.Data3, self.Data4[
                0], self.Data4[1], rest = uuid_.fields
            for i in range(2, 8):
                self.Data4[i] = rest >> (8 - i - 1) * 8 & 0xff

    class FOLDERID:
        LocalAppDataLow = UUID(
            '{A520A1A4-1780-4FF6-BD18-167343C5AF16}')

    class UserHandle:
        current = wintypes.HANDLE(0)

    _CoTaskMemFree = windll.ole32.CoTaskMemFree
    _CoTaskMemFree.restype = None
    _CoTaskMemFree.argtypes = [ctypes.c_void_p]

    try:
        _SHGetKnownFolderPath = windll.shell32.SHGetKnownFolderPath
    except AttributeError:
        # Not supported on Windows XP
        return None
    _SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID), wintypes.DWORD, wintypes.HANDLE, ctypes.POINTER(
            ctypes.c_wchar_p)
    ]

    class PathNotFoundException(Exception):
        pass

    folderid = getattr(FOLDERID, folder_name)
    fid = GUID(folderid)
    pPath = ctypes.c_wchar_p()
    S_OK = 0
    if _SHGetKnownFolderPath(ctypes.byref(fid), 0, UserHandle.current, ctypes.byref(pPath)) != S_OK:
        raise PathNotFoundException()
    path = pPath.value
    _CoTaskMemFree(pPath)
    return path


def get_recycle_bin():
    """Yield a list of files in the recycle bin"""
    pidl = shell.SHGetSpecialFolderLocation(0, shellcon.CSIDL_BITBUCKET)
    desktop = shell.SHGetDesktopFolder()
    h = desktop.BindToObject(pidl, None, shell.IID_IShellFolder)
    for item in h:
        path = h.GetDisplayNameOf(item, shellcon.SHGDN_FORPARSING)
        if os.path.isdir(path):
            for child in FileUtilities.children_in_directory(path, True):
                yield child
            yield path
        else:
            yield path


def is_process_running(name):
    """Return boolean whether process (like firefox.exe) is running"""

    if os.getenv('PROGRAMFILES(X86)'):
        # 64-bit Windows detected
        return is_process_running_wmic(name)
    else:
        # 32-bit Windows detected
        return is_process_running_win32(name)


def is_process_running_win32(name):
    """Return boolean whether process (like firefox.exe) is running

    Does not work on 64-bit Windows

    Originally by Eric Koome
    license GPL
    http://code.activestate.com/recipes/305279/
    """

    hModule = c_ulong()
    count = c_ulong()
    modname = c_buffer(30)
    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010

    for pid in win32process.EnumProcesses():

        # Get handle to the process based on PID
        hProcess = kernel.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_VM_READ,
            False, pid)
        if hProcess:
            psapi.EnumProcessModules(
                hProcess, byref(hModule), sizeof(hModule), byref(count))
            psapi.GetModuleBaseNameA(
                hProcess, hModule.value, modname, sizeof(modname))
            clean_modname = "".join(
                [i for i in modname if i != '\x00']).lower()

            # Clean up
            for i in range(modname._length_):
                modname[i] = '\x00'

            kernel.CloseHandle(hProcess)

            if len(clean_modname) > 0 and '?' != clean_modname:
                # Filter out non-ASCII characters which we don't need
                # and which may cause display warnings
                clean_modname2 = re.sub(r'[^a-z\.]', '_', clean_modname.lower())
                if clean_modname2 == name.lower():
                    return True

    return False


def is_process_running_wmic(name):
    """Return boolean whether process (like firefox.exe) is running

    Works on Windows XP Professional but not on XP Home
    """

    clean_name = re.sub(r'[^A-Za-z\.]', '_', name).lower()
    args = ['wmic', 'path', 'win32_process', 'where', "caption='%s'" %
            clean_name, 'get', 'Caption']
    (_, stdout, _) = General.run_external(args)
    return stdout.lower().find(clean_name) > -1


def move_to_recycle_bin(path):
    """Move 'path' into recycle bin"""
    shell.SHFileOperation(
        (0, shellcon.FO_DELETE, path, None, shellcon.FOF_ALLOWUNDO | shellcon.FOF_NOCONFIRMATION))


def path_on_network(path):
    """Check whether 'path' is on a network drive"""
    if len(os.path.splitunc(path)[0]) > 0:
        return True
    drive = os.path.splitdrive(path)[0] + '\\'
    return win32file.GetDriveType(drive) == win32file.DRIVE_REMOTE


def shell_change_notify():
    """Notify the Windows shell of update.

    Used in windows_explorer.xml."""
    shell.SHChangeNotify(shellcon.SHCNE_ASSOCCHANGED, shellcon.SHCNF_IDLIST,
                         None, None)
    return 0


def set_environ(varname, path):
    """Define an environment variable for use in CleanerML and Winapp2.ini"""
    if not path:
        # Such as LocalAppDataLow on XP
        return
    if os.environ.has_key(varname):
        # Do not redefine the environment variable when it already exists
        return
    try:
        if not os.path.exists(path):
            raise RuntimeError(
                'Variable %s points to a non-existent path %s' % (varname, path))
        os.environ[varname] = path
    except:
        import traceback
        traceback.print_exc()
        print 'ERROR: setting environment variable "%s": %s ' % (
            varname, str(sys.exc_info()[1]))


def setup_environment():
    """Define any extra environment variables for use in CleanerML and Winapp2.ini"""
    csidl_to_environ('commonappdata', shellcon.CSIDL_COMMON_APPDATA)
    csidl_to_environ('documents', shellcon.CSIDL_PERSONAL)
    # Windows XP does not define localappdata, but Windows Vista and 7 do
    csidl_to_environ('localappdata', shellcon.CSIDL_LOCAL_APPDATA)
    csidl_to_environ('music', shellcon.CSIDL_MYMUSIC)
    csidl_to_environ('pictures', shellcon.CSIDL_MYPICTURES)
    csidl_to_environ('video', shellcon.CSIDL_MYVIDEO)
    # LocalLowAppData does not have a CSIDL for use with
    # SHGetSpecialFolderPath. Instead, it is identified using
    # SHGetKnownFolderPath in Windows Vista and later
    path = get_known_folder_path('LocalAppDataLow')
    set_environ('LocalAppDataLow', path)


def split_registry_key(full_key):
    r"""Given a key like HKLM\Software split into tuple (hive, key).
    Used internally."""
    assert len(full_key) >= 6
    [k1, k2] = full_key.split("\\", 1)
    hive_map = {
        'HKCR': _winreg.HKEY_CLASSES_ROOT,
        'HKCU': _winreg.HKEY_CURRENT_USER,
        'HKLM': _winreg.HKEY_LOCAL_MACHINE,
        'HKU': _winreg.HKEY_USERS}
    if k1 not in hive_map:
        raise RuntimeError("Invalid Windows registry hive '%s'" % k1)
    return (hive_map[k1], k2)


def start_with_computer(enabled):
    """If enabled, create shortcut to start application with computer.
    If disabled, then delete the shortcut."""
    autostart_path = get_autostart_path()
    if not enabled:
        if os.path.lexists(autostart_path):
            FileUtilities.delete(autostart_path)
        return
    if os.path.lexists(autostart_path):
        return
    import win32com.client
    wscript_shell = win32com.client.Dispatch('WScript.Shell')
    shortcut = wscript_shell.CreateShortCut(autostart_path)
    shortcut.TargetPath = os.path.join(
        Common.bleachbit_exe_path, 'bleachbit.exe')
    shortcut.save()


def start_with_computer_check():
    """Return boolean whether BleachBit will start with the computer"""
    return os.path.lexists(get_autostart_path())
