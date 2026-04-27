param(
    [string]$InputDxf = "D:\OneDrive\Radna površina\codex\komercijalni-sw\radni_folder\converted_r2000\house.dxf",
    [string]$OutputDxf = "C:\Users\Vladica\Documents\Codex\2026-04-18-cao\house_electrical.dxf"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$culture = [Globalization.CultureInfo]::InvariantCulture

function Read-DxfPairs {
    param([string]$Path)

    $pairs = New-Object System.Collections.Generic.List[object]
    $reader = [System.IO.File]::OpenText($Path)
    try {
        while (($code = $reader.ReadLine()) -ne $null) {
            $value = $reader.ReadLine()
            if ($null -eq $value) { break }
            $pairs.Add([pscustomobject]@{ Code = $code; Value = $value })
        }
    }
    finally {
        $reader.Close()
    }
    return $pairs
}

function Get-LayerBounds {
    param(
        [System.Collections.Generic.List[object]]$Pairs,
        [string]$LayerName
    )

    $bounds = [pscustomobject]@{
        MinX = [double]::PositiveInfinity
        MinY = [double]::PositiveInfinity
        MaxX = [double]::NegativeInfinity
        MaxY = [double]::NegativeInfinity
        Count = 0
    }

    $entityLayer = "0"
    $xs = New-Object System.Collections.Generic.List[double]
    $ys = New-Object System.Collections.Generic.List[double]

    function Flush-Entity {
        if ($entityLayer -ne $LayerName -or $xs.Count -eq 0 -or $ys.Count -eq 0) {
            return
        }

        foreach ($x in $xs) {
            if ($x -lt $bounds.MinX) { $bounds.MinX = $x }
            if ($x -gt $bounds.MaxX) { $bounds.MaxX = $x }
        }
        foreach ($y in $ys) {
            if ($y -lt $bounds.MinY) { $bounds.MinY = $y }
            if ($y -gt $bounds.MaxY) { $bounds.MaxY = $y }
        }
        $bounds.Count++
    }

    foreach ($pair in $Pairs) {
        $code = $pair.Code.Trim()
        $value = $pair.Value.Trim()

        if ($code -eq "0") {
            Flush-Entity
            $entityLayer = "0"
            $xs.Clear()
            $ys.Clear()
            continue
        }

        if ($code -eq "8") {
            $entityLayer = $value
            continue
        }

        if ($code -match "^1[0-9]$") {
            $number = 0.0
            if ([double]::TryParse($value, [Globalization.NumberStyles]::Float, $culture, [ref]$number)) {
                $xs.Add($number)
            }
            continue
        }

        if ($code -match "^2[0-9]$") {
            $number = 0.0
            if ([double]::TryParse($value, [Globalization.NumberStyles]::Float, $culture, [ref]$number)) {
                $ys.Add($number)
            }
        }
    }

    Flush-Entity

    if ($bounds.Count -eq 0) {
        throw "Layer '$LayerName' has no measurable geometry."
    }

    return $bounds
}

function Format-DxfNumber {
    param([double]$Value)
    return $Value.ToString("0.###", $culture)
}

function Dxf-Line {
    param([string]$Layer, [double]$X1, [double]$Y1, [double]$X2, [double]$Y2)
    return @(
        "  0", "LINE",
        "  8", $Layer,
        " 10", (Format-DxfNumber $X1),
        " 20", (Format-DxfNumber $Y1),
        " 30", "0.0",
        " 11", (Format-DxfNumber $X2),
        " 21", (Format-DxfNumber $Y2),
        " 31", "0.0"
    )
}

function Dxf-Circle {
    param([string]$Layer, [double]$X, [double]$Y, [double]$Radius)
    return @(
        "  0", "CIRCLE",
        "  8", $Layer,
        " 10", (Format-DxfNumber $X),
        " 20", (Format-DxfNumber $Y),
        " 30", "0.0",
        " 40", (Format-DxfNumber $Radius)
    )
}

function Dxf-Text {
    param([string]$Layer, [double]$X, [double]$Y, [double]$Height, [string]$Text)
    return @(
        "  0", "TEXT",
        "  8", $Layer,
        " 10", (Format-DxfNumber $X),
        " 20", (Format-DxfNumber $Y),
        " 30", "0.0",
        " 40", (Format-DxfNumber $Height),
        "  1", $Text,
        " 50", "0.0"
    )
}

function Dxf-Route {
    param([string]$Layer, [double[]]$Points)

    $entity = New-Object System.Collections.Generic.List[string]
    foreach ($line in @("  0", "LWPOLYLINE", "  8", $Layer, " 90", (($Points.Count / 2).ToString()), " 70", "0")) {
        $entity.Add([string]$line)
    }

    for ($i = 0; $i -lt $Points.Count; $i += 2) {
        $entity.Add(" 10")
        $entity.Add((Format-DxfNumber $Points[$i]))
        $entity.Add(" 20")
        $entity.Add((Format-DxfNumber $Points[$i + 1]))
    }

    return $entity.ToArray()
}

function Add-DxfLines {
    param([System.Collections.Generic.List[string]]$Target, [object[]]$Lines)
    foreach ($line in $Lines) {
        $Target.Add([string]$line)
    }
}

function Add-Panel {
    param([System.Collections.Generic.List[string]]$Entities, [double]$X, [double]$Y, [double]$Size, [string]$Label)

    Add-DxfLines $Entities (Dxf-Line "EL_PANEL" ($X - $Size) ($Y - $Size) ($X + $Size) ($Y - $Size))
    Add-DxfLines $Entities (Dxf-Line "EL_PANEL" ($X + $Size) ($Y - $Size) ($X + $Size) ($Y + $Size))
    Add-DxfLines $Entities (Dxf-Line "EL_PANEL" ($X + $Size) ($Y + $Size) ($X - $Size) ($Y + $Size))
    Add-DxfLines $Entities (Dxf-Line "EL_PANEL" ($X - $Size) ($Y + $Size) ($X - $Size) ($Y - $Size))
    Add-DxfLines $Entities (Dxf-Line "EL_PANEL" ($X - $Size) ($Y - $Size) ($X + $Size) ($Y + $Size))
    Add-DxfLines $Entities (Dxf-Line "EL_PANEL" ($X - $Size) ($Y + $Size) ($X + $Size) ($Y - $Size))
    Add-DxfLines $Entities (Dxf-Text "EL_TEXT" ($X + $Size + 120) ($Y - ($Size / 3)) 250 $Label)
}

function Add-Light {
    param([System.Collections.Generic.List[string]]$Entities, [double]$X, [double]$Y, [double]$Radius, [string]$Label)

    Add-DxfLines $Entities (Dxf-Circle "EL_LIGHT" $X $Y $Radius)
    Add-DxfLines $Entities (Dxf-Line "EL_LIGHT" ($X - ($Radius * 0.7)) ($Y - ($Radius * 0.7)) ($X + ($Radius * 0.7)) ($Y + ($Radius * 0.7)))
    Add-DxfLines $Entities (Dxf-Line "EL_LIGHT" ($X - ($Radius * 0.7)) ($Y + ($Radius * 0.7)) ($X + ($Radius * 0.7)) ($Y - ($Radius * 0.7)))
    Add-DxfLines $Entities (Dxf-Text "EL_TEXT" ($X + $Radius + 80) ($Y - ($Radius / 2)) 220 $Label)
}

function Add-Socket {
    param([System.Collections.Generic.List[string]]$Entities, [double]$X, [double]$Y, [double]$Radius, [string]$Label)

    Add-DxfLines $Entities (Dxf-Circle "EL_SOCKET" $X $Y $Radius)
    Add-DxfLines $Entities (Dxf-Line "EL_SOCKET" ($X - ($Radius * 0.55)) $Y ($X + ($Radius * 0.55)) $Y)
    Add-DxfLines $Entities (Dxf-Line "EL_SOCKET" ($X - ($Radius * 0.25)) ($Y - ($Radius * 0.5)) ($X - ($Radius * 0.25)) ($Y + ($Radius * 0.5)))
    Add-DxfLines $Entities (Dxf-Line "EL_SOCKET" ($X + ($Radius * 0.25)) ($Y - ($Radius * 0.5)) ($X + ($Radius * 0.25)) ($Y + ($Radius * 0.5)))
    Add-DxfLines $Entities (Dxf-Text "EL_TEXT" ($X + $Radius + 80) ($Y - ($Radius / 2)) 190 $Label)
}

function Add-Switch {
    param([System.Collections.Generic.List[string]]$Entities, [double]$X, [double]$Y, [double]$Radius, [string]$Label)

    Add-DxfLines $Entities (Dxf-Circle "EL_SWITCH" $X $Y $Radius)
    Add-DxfLines $Entities (Dxf-Line "EL_SWITCH" ($X - ($Radius * 0.5)) ($Y - ($Radius * 0.2)) ($X + ($Radius * 0.45)) ($Y + ($Radius * 0.5)))
    Add-DxfLines $Entities (Dxf-Text "EL_TEXT" ($X + $Radius + 70) ($Y - ($Radius / 2)) 190 $Label)
}

function Add-OrthoRoute {
    param([System.Collections.Generic.List[string]]$Entities, [string]$Layer, [double]$X1, [double]$Y1, [double]$X2, [double]$Y2)

    $midX = $X2
    Add-DxfLines $Entities (Dxf-Route $Layer @($X1, $Y1, $midX, $Y1, $midX, $Y2))
}

if (-not (Test-Path -LiteralPath $InputDxf)) {
    throw "Input DXF not found: $InputDxf"
}

$pairs = Read-DxfPairs -Path $InputDxf
$wall = Get-LayerBounds -Pairs $pairs -LayerName "wall"

$width = $wall.MaxX - $wall.MinX
$height = $wall.MaxY - $wall.MinY

$panel = [pscustomobject]@{
    X = $wall.MinX + ($width * 0.08)
    Y = $wall.MinY + ($height * 0.14)
    Label = "RT"
}

$lights = @(
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.22); Y = $wall.MinY + ($height * 0.28); Label = "L1" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.48); Y = $wall.MinY + ($height * 0.28); Label = "L2" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.74); Y = $wall.MinY + ($height * 0.28); Label = "L3" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.26); Y = $wall.MinY + ($height * 0.66); Label = "L4" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.55); Y = $wall.MinY + ($height * 0.69); Label = "L5" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.82); Y = $wall.MinY + ($height * 0.72); Label = "L6" }
)

$switches = @(
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.14); Y = $wall.MinY + ($height * 0.22); Label = "P1"; Target = 0 },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.40); Y = $wall.MinY + ($height * 0.22); Label = "P2"; Target = 1 },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.66); Y = $wall.MinY + ($height * 0.22); Label = "P3"; Target = 2 },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.18); Y = $wall.MinY + ($height * 0.58); Label = "P4"; Target = 3 },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.47); Y = $wall.MinY + ($height * 0.60); Label = "P5"; Target = 4 },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.72); Y = $wall.MinY + ($height * 0.62); Label = "P6"; Target = 5 }
)

$sockets = @(
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.17); Y = $wall.MinY + ($height * 0.18); Label = "U1" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.31); Y = $wall.MinY + ($height * 0.20); Label = "U2" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.50); Y = $wall.MinY + ($height * 0.18); Label = "U3" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.70); Y = $wall.MinY + ($height * 0.20); Label = "U4" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.86); Y = $wall.MinY + ($height * 0.24); Label = "U5" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.16); Y = $wall.MinY + ($height * 0.76); Label = "U6" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.35); Y = $wall.MinY + ($height * 0.82); Label = "U7" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.58); Y = $wall.MinY + ($height * 0.82); Label = "U8" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.78); Y = $wall.MinY + ($height * 0.84); Label = "U9" },
    [pscustomobject]@{ X = $wall.MinX + ($width * 0.91); Y = $wall.MinY + ($height * 0.70); Label = "U10" }
)

$entities = New-Object System.Collections.Generic.List[string]
Add-DxfLines $entities @("  0", "COMMENT", "  1", "Electrical installation overlay generated by Add-ElectricalPlan.ps1")
Add-DxfLines $entities (Dxf-Text "EL_TEXT" $wall.MinX ($wall.MaxY + 1400) 380 "ELEKTRO INSTALACIJA - AUTOMATSKI NACRT")
Add-DxfLines $entities (Dxf-Text "EL_TEXT" $wall.MinX ($wall.MaxY + 950) 240 "RT razvodna tabla | L rasveta | P prekidac | U uticnica")

Add-Panel $entities $panel.X $panel.Y 290 $panel.Label

foreach ($light in $lights) {
    Add-Light $entities $light.X $light.Y 220 $light.Label
    Add-OrthoRoute $entities "EL_CABLE_LIGHT" $panel.X $panel.Y $light.X $light.Y
}

foreach ($switch in $switches) {
    $targetLight = $lights[$switch.Target]
    Add-Switch $entities $switch.X $switch.Y 170 $switch.Label
    Add-OrthoRoute $entities "EL_CABLE_SWITCH" $switch.X $switch.Y $targetLight.X $targetLight.Y
}

foreach ($socket in $sockets) {
    Add-Socket $entities $socket.X $socket.Y 170 $socket.Label
    Add-OrthoRoute $entities "EL_CABLE_SOCKET" $panel.X $panel.Y $socket.X $socket.Y
}

$legendX = $wall.MaxX + 900
$legendY = $wall.MaxY - 600
Add-DxfLines $entities (Dxf-Text "EL_TEXT" $legendX $legendY 300 "LEGENDA")
Add-Panel $entities $legendX ($legendY - 520) 170 "RT"
Add-Light $entities $legendX ($legendY - 1050) 150 "L"
Add-Switch $entities $legendX ($legendY - 1520) 130 "P"
Add-Socket $entities $legendX ($legendY - 1970) 130 "U"
Add-DxfLines $entities (Dxf-Line "EL_CABLE_LIGHT" ($legendX - 170) ($legendY - 2400) ($legendX + 500) ($legendY - 2400))
Add-DxfLines $entities (Dxf-Text "EL_TEXT" ($legendX + 620) ($legendY - 2500) 190 "kabl rasvete")
Add-DxfLines $entities (Dxf-Line "EL_CABLE_SOCKET" ($legendX - 170) ($legendY - 2750) ($legendX + 500) ($legendY - 2750))
Add-DxfLines $entities (Dxf-Text "EL_TEXT" ($legendX + 620) ($legendY - 2850) 190 "kabl uticnica")

$content = [System.IO.File]::ReadAllLines($InputDxf)
$entitiesStart = -1
for ($i = 0; $i -lt ($content.Length - 1); $i++) {
    if ($content[$i].Trim() -eq "2" -and $content[$i + 1].Trim() -eq "ENTITIES") {
        $entitiesStart = $i
        break
    }
}

if ($entitiesStart -lt 0) {
    throw "Could not find ENTITIES section."
}

$insertIndex = -1
for ($i = $entitiesStart + 2; $i -lt ($content.Length - 1); $i++) {
    if ($content[$i].Trim() -eq "0" -and $content[$i + 1].Trim() -eq "ENDSEC") {
        $insertIndex = $i
        break
    }
}

if ($insertIndex -lt 0) {
    throw "Could not find ENTITIES ENDSEC insertion point."
}

$output = New-Object System.Collections.Generic.List[string]
for ($i = 0; $i -lt $insertIndex; $i++) {
    $output.Add($content[$i])
}
Add-DxfLines $output $entities.ToArray()
for ($i = $insertIndex; $i -lt $content.Length; $i++) {
    $output.Add($content[$i])
}

[System.IO.File]::WriteAllLines($OutputDxf, $output, [System.Text.Encoding]::ASCII)
Write-Host "Created $OutputDxf"
Write-Host ("Wall bounds: min=({0:N0},{1:N0}) max=({2:N0},{3:N0})" -f $wall.MinX, $wall.MinY, $wall.MaxX, $wall.MaxY)
Write-Host ("Added: {0} lights, {1} switches, {2} sockets" -f $lights.Count, $switches.Count, $sockets.Count)
