function Get-PlatformProcesses {
    param([int]$ProcessId, [int]$ParentProcessId)
    if (([bool]$ProcessId) -eq ([bool]$ParentProcessId)) { throw 'Specify a process or parent PID.' }
    $filter = if ($ProcessId) { "ProcessId = $ProcessId" } else { "ParentProcessId = $ParentProcessId AND Name = 'python.exe'" }
    try {
        $rows = @(Get-CimInstance Win32_Process -Filter $filter -Property ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine -ErrorAction Stop)
        if (-not ($rows | Where-Object { -not $_.CommandLine })) { $rows; return }
    } catch { Write-Verbose 'CIM unavailable; reading Windows process information directly.' }
    if (-not ('Hangzhi.ProcessQuery' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
namespace Hangzhi {
    public static class ProcessQuery {
        [DllImport("ntdll.dll")]
        static extern int NtQueryInformationProcess(IntPtr process, int kind, IntPtr buffer, int size, out int needed);
        static IntPtr Query(Process p, int kind, int size) {
            IntPtr buffer = Marshal.AllocHGlobal(size);
            int needed;
            int status = NtQueryInformationProcess(p.Handle, kind, buffer, size, out needed);
            if (status == 0) return buffer;
            Marshal.FreeHGlobal(buffer);
            throw new Win32Exception("Cannot verify process " + p.Id + " (status " + status + ").");
        }
        public static long Parent(Process p) {
            IntPtr b = Query(p, 0, 6 * IntPtr.Size);
            try { return Marshal.ReadIntPtr(b, 5 * IntPtr.Size).ToInt64(); }
            finally { Marshal.FreeHGlobal(b); }
        }
        public static string Arguments(Process p) {
            // ProcessCommandLineInformation returns a UNICODE_STRING followed by the UTF-16 text.
            IntPtr b = Query(p, 60, 131072);
            try {
                int chars = ((ushort)Marshal.ReadInt16(b)) / 2;
                return Marshal.PtrToStringUni(Marshal.ReadIntPtr(b, IntPtr.Size), chars);
            } finally { Marshal.FreeHGlobal(b); }
        }
    }
}
'@
    }
    $items = if ($ProcessId) { Get-Process -Id $ProcessId -ErrorAction SilentlyContinue } else { Get-Process -Name python -ErrorAction SilentlyContinue }
    foreach ($item in $items) {
        try {
            $parentId = [Hangzhi.ProcessQuery]::Parent($item)
            if ($ParentProcessId -and $parentId -ne $ParentProcessId) { continue }
            [pscustomobject]@{
                ProcessId = $item.Id; ParentProcessId = $parentId; Name = $item.ProcessName + '.exe'
                ExecutablePath = $item.Path; CommandLine = [Hangzhi.ProcessQuery]::Arguments($item)
            }
        } catch {
            if (-not $item.HasExited) { throw }
        }
    }
}
