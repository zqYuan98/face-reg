param([string[]]$MavenArguments = @('test'))
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$javaRuntime = if ($env:JAVA_HOME) { $env:JAVA_HOME } else { 'C:/Program Files/Android/Android Studio/jbr' }
$mavenCommand = Get-Command mvn -ErrorAction SilentlyContinue
$mavenExecutable = if ($mavenCommand) { $mavenCommand.Source } else { 'C:/Program Files/JetBrains/IntelliJ IDEA 2025.3.3/plugins/maven/lib/maven3/bin/mvn.cmd' }
if (!(Test-Path -LiteralPath "$javaRuntime/bin/java.exe") -or !(Test-Path -LiteralPath $mavenExecutable)) {
    throw 'Set JAVA_HOME and install Maven (mvn on PATH) before running backend tests.'
}
$env:JAVA_HOME = $javaRuntime
& $mavenExecutable --batch-mode --no-transfer-progress -f "$projectRoot/pom.xml" @MavenArguments
if ($LASTEXITCODE -ne 0) { throw "Backend verification failed with exit code $LASTEXITCODE" }
