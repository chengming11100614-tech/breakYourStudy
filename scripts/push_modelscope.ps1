# Push current branch to ModelScope Git (non-interactive). Requires MODELSCOPE_TOKEN in the environment.
# Example (PowerShell): $env:MODELSCOPE_TOKEN = '<token>'; .\scripts\push_modelscope.ps1
param(
    [string]$RemoteName = "modelscope",
    [string]$Branch = "main",
    # 魔搭创空间默认克隆 master 时：-ToBranch master（本地仍用 main 开发）
    [string]$ToBranch = ""
)
$ErrorActionPreference = "Stop"
if (-not $env:MODELSCOPE_TOKEN) {
    Write-Error "Set environment variable MODELSCOPE_TOKEN to your ModelScope access token (https://www.modelscope.cn/my/myaccesstoken)."
}
$url = git remote get-url $RemoteName 2>$null
if (-not $url) {
    Write-Error "Git remote '$RemoteName' not found. Add it first, e.g. git remote add modelscope https://www.modelscope.cn/studios/<user>/<repo>.git"
}
if ($url -notmatch '^https?://') {
    Write-Error "Remote URL must be http(s). Current: $url"
}
$authUrl = $url -replace '^https://', "https://oauth2:$($env:MODELSCOPE_TOKEN)@" -replace '^http://', "http://oauth2:$($env:MODELSCOPE_TOKEN)@"
$dest = if ($ToBranch) { $ToBranch } else { $Branch }
git push $authUrl "refs/heads/${Branch}:refs/heads/${dest}"
