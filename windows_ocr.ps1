param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding

Add-Type -AssemblyName System.Runtime.WindowsRuntime

function Await-WinRT {
    param(
        [Parameter(Mandatory = $true)]
        $Operation,
        [Parameter(Mandatory = $true)]
        [Type]$ResultType
    )

    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.IsGenericMethodDefinition -and
            $_.GetGenericArguments().Count -eq 1 -and
            $_.GetParameters().Count -eq 1
        } |
        Select-Object -First 1

    $generic = $method.MakeGenericMethod(@($ResultType))
    $task = $generic.Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}

function Await-Action {
    param(
        [Parameter(Mandatory = $true)]
        $Operation
    )

    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and
            -not $_.IsGenericMethodDefinition -and
            $_.GetParameters().Count -eq 1
        } |
        Select-Object -First 1

    $task = $method.Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult() | Out-Null
}

$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.InMemoryRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Data.Pdf.PdfDocument, Windows.Data.Pdf, ContentType = WindowsRuntime]

function Get-OcrTextFromBitmap {
    param(
        [Parameter(Mandatory = $true)]
        [Windows.Graphics.Imaging.SoftwareBitmap]$Bitmap
    )

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    $result = Await-WinRT ($engine.RecognizeAsync($Bitmap)) ([Windows.Media.Ocr.OcrResult])
    return $result.Text
}

function Get-OcrTextFromImage {
    param(
        [Parameter(Mandatory = $true)]
        [Windows.Storage.StorageFile]$File
    )

    $stream = Await-WinRT ($File.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    return Get-OcrTextFromBitmap -Bitmap $bitmap
}

function Get-OcrTextFromPdf {
    param(
        [Parameter(Mandatory = $true)]
        [Windows.Storage.StorageFile]$File
    )

    $pdf = Await-WinRT ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($File)) ([Windows.Data.Pdf.PdfDocument])
    $parts = New-Object System.Collections.Generic.List[string]

    for ($i = 0; $i -lt $pdf.PageCount; $i++) {
        $page = $pdf.GetPage($i)
        try {
            $stream = New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
            Await-Action ($page.RenderToStreamAsync($stream))
            $decoder = Await-WinRT ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
            $bitmap = Await-WinRT ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
            $parts.Add((Get-OcrTextFromBitmap -Bitmap $bitmap))
        }
        finally {
            if ($page) { $page.Dispose() }
        }
    }

    return ($parts -join [Environment]::NewLine + [Environment]::NewLine)
}

$resolvedPath = (Resolve-Path -LiteralPath $Path).Path
$file = Await-WinRT ([Windows.Storage.StorageFile]::GetFileFromPathAsync($resolvedPath)) ([Windows.Storage.StorageFile])
$extension = [System.IO.Path]::GetExtension($resolvedPath).ToLowerInvariant()

if ($extension -eq ".pdf") {
    $text = Get-OcrTextFromPdf -File $file
} else {
    $text = Get-OcrTextFromImage -File $file
}

Write-Output $text
