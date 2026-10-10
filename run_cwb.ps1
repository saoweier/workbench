# run_cwb.ps1 - 单实例启动器：保证全网段 0.0.0.0:9195 只跑 1 个 API + 1 个 Worker
# 背景：底层 harness 每次后台启动会拉起 2 份相同进程，用全局 Mutex 去重。
$ErrorActionPreference = 'Stop'

$ROOT   = "C:\Users\Administrator\Desktop\cloud\view\workbench"
$PY     = Join-Path $ROOT ".venv\Scripts\python.exe"
$MUTEX  = "Global\cwb_stack_9195"

# ---- JobObject：父进程退出时，子进程（api/worker）一并被回收 ----
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Job {
  [DllImport("kernel32.dll")] public static extern IntPtr CreateJobObject(IntPtr a, string n);
  [DllImport("kernel32.dll")] public static extern bool AssignProcessToJobObject(IntPtr j, IntPtr p);
  [DllImport("kernel32.dll")] public static extern bool SetInformationJobObject(IntPtr j, int c, IntPtr i, int l);
  [DllImport("kernel32.dll")] public static extern bool CloseHandle(IntPtr h);
  [DllImport("kernel32.dll")] public static extern bool TerminateJobObject(IntPtr j, uint e);
  [DllImport("kernel32.dll")] public static extern bool IsProcessInJob(IntPtr p, IntPtr j, out bool r);
}
"@
$job = [Job]::CreateJobObject([IntPtr]::Zero, $null)
# JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
$lim = 0x2000
$ptr = [System.Runtime.InteropServices.Marshal]::AllocHGlobal(4)
[System.Runtime.InteropServices.Marshal]::WriteInt32($ptr, $lim)
[Job]::SetInformationJobObject($job, 2, $ptr, 4) | Out-Null
[System.Runtime.InteropServices.Marshal]::FreeHGlobal($ptr)

# ---- 全局 Mutex 去重 ----
try {
    $mtx = New-Object System.Threading.Mutex($false, $MUTEX)
    $owned = $false
    try { $owned = $mtx.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $owned = $true }  # 上一个持有者已死，可接管
    if (-not $owned) {
        Write-Output "$(Get-Date) [launcher] 已有实例持有互斥锁，本重复进程退出。"
        exit 0
    }
} catch {
    Write-Output "$(Get-Date) [launcher] 互斥锁创建失败：$_ ，直接退出避免重复。"
    exit 1
}

Write-Output "$(Get-Date) [launcher] 已获取互斥锁，启动工作台 (v1.4.4, 0.0.0.0:9195)..."

$env:PYTHONPATH = "backend"
$env:PYTHONUTF8 = "1"
$env:CWB_INSTANCE_ID = "manual-9195"

$children = @()
function Start-Child($argsArr) {
    $p = Start-Process -FilePath $PY -ArgumentList $argsArr -WorkingDirectory $ROOT -PassThru -WindowStyle Hidden
    # 把子进程加入 JobObject，父退出即回收
    [Job]::AssignProcessToJobObject($job, $p.Handle) | Out-Null
    $script:children += $p
    return $p
}

function Stop-All {
    foreach ($c in $script:children) {
        try { if (-not $c.HasExited) { Stop-Process -Id $c.Id -Force -ErrorAction SilentlyContinue } } catch {}
    }
}

try {
    $api    = Start-Child @("-m","uvicorn","app.main:app","--host","0.0.0.0","--port","9195")
    $worker = Start-Child @("-m","app.worker","--interval","2","--worker-id","manual-9195")
    Write-Output "$(Get-Date) [launcher] api pid=$($api.Id) | worker pid=$($worker.Id)"

    while ($true) {
        Start-Sleep -Seconds 5
        if ($api.HasExited)    { Write-Output "$(Get-Date) [launcher] api 退出，重启";    $api    = Start-Child @("-m","uvicorn","app.main:app","--host","0.0.0.0","--port","9195") }
        if ($worker.HasExited) { Write-Output "$(Get-Date) [launcher] worker 退出，重启"; $worker = Start-Child @("-m","app.worker","--interval","2","--worker-id","manual-9195") }
    }
} finally {
    Write-Output "$(Get-Date) [launcher] 正在关闭子进程..."
    Stop-All
    try { [Job]::TerminateJobObject($job, 0) } catch {}
    try { if ($owned) { $mtx.ReleaseMutex() } } catch {}
    try { [Job]::CloseHandle($job) } catch {}
}
