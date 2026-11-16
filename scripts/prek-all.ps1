# Run every prek hook on all files git does not ignore, tracked or not.
# (`prek run --all-files` only sees files that git already tracks.)
Push-Location (Join-Path $PSScriptRoot '..')
try {
    prek run @args --files (git ls-files --cached --others --exclude-standard)
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
