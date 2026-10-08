param(
  [Parameter(Mandatory = $false)]
  [string]$ProjectRoot
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $ProjectRoot = Split-Path -Parent $scriptDir
}
$root = (Resolve-Path -LiteralPath $ProjectRoot).Path.TrimEnd([char[]]@([char]92, [char]47))
$python = [System.IO.Path]::GetFullPath((Join-Path $root '.venv\Scripts\python.exe'))

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
  Write-Output '未找到项目虚拟环境；没有停止任何进程。'
} else {
  $stopped = 0
  $candidates = Get-CimInstance Win32_Process -Filter "Name='python.exe'"
  foreach ($target in $candidates) {
    if (-not $target.ExecutablePath -or
        [System.IO.Path]::GetFullPath($target.ExecutablePath) -ine $python) {
      continue
    }
    $commandLine = [string]$target.CommandLine
    if ($commandLine.IndexOf('uvicorn app.main:app', [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
      $label = 'API'
    } elseif ($commandLine.IndexOf('app.worker', [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
      $label = 'Worker'
    } else {
      continue
    }
    Stop-Process -Id $target.ProcessId -Force
    Write-Output "已停止 $label (pid $($target.ProcessId))"
    $stopped++
  }
  if ($stopped -eq 0) {
    Write-Output '没有正在运行的内容工作台服务。'
  } else {
    Write-Output '数据仍在 storage\ 下，不会丢。重新启动：start.bat'
  }
}
