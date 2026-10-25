# Recria os bitmaps do cabeçalho a partir do PNG oficial, sem dependência em runtime.
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$assets=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\assets\brand'))
$source=Join-Path $assets 'logo-horizontal-escuro.png'
$image=[Drawing.Image]::FromFile($source)
try {
 $crop=[Drawing.Rectangle]::new(240,94,1120,332)
 foreach($width in @(1120,180,240,360)){
  $height=[int][Math]::Round($width*332.0/1120)
  $bitmap=[Drawing.Bitmap]::new($width,$height,[Drawing.Imaging.PixelFormat]::Format32bppArgb)
  $graphics=[Drawing.Graphics]::FromImage($bitmap)
  $attributes=[Drawing.Imaging.ImageAttributes]::new()
  try {
   $graphics.Clear([Drawing.Color]::Transparent)
   $graphics.CompositingQuality=[Drawing.Drawing2D.CompositingQuality]::HighQuality
   $graphics.InterpolationMode=[Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
   $graphics.SmoothingMode=[Drawing.Drawing2D.SmoothingMode]::HighQuality
   $graphics.PixelOffsetMode=[Drawing.Drawing2D.PixelOffsetMode]::HighQuality
   $attributes.SetWrapMode([Drawing.Drawing2D.WrapMode]::TileFlipXY)
   $graphics.DrawImage($image,[Drawing.Rectangle]::new(0,0,$width,$height),$crop.X,$crop.Y,$crop.Width,$crop.Height,[Drawing.GraphicsUnit]::Pixel,$attributes)
   $name=if($width -eq 1120){'logo-header-source.png'}else{'logo-header-'+$width+'.png'}
   $bitmap.Save((Join-Path $assets $name),[Drawing.Imaging.ImageFormat]::Png)
  } finally {$attributes.Dispose();$graphics.Dispose();$bitmap.Dispose()}
 }
} finally {$image.Dispose()}
