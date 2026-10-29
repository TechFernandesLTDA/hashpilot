param([long]$ClientHandle,[string]$OutputPath)
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class CaptureWindow {
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern IntPtr GetParent(IntPtr h);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
 [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
 public struct RECT {public int left,top,right,bottom;}
}
"@
[void][CaptureWindow]::SetProcessDPIAware()
$handle=[CaptureWindow]::GetParent([IntPtr]$ClientHandle)
$rect=New-Object CaptureWindow+RECT
if (-not [CaptureWindow]::GetWindowRect($handle,[ref]$rect)) { throw 'GetWindowRect failed' }
$bitmap=New-Object Drawing.Bitmap ($rect.right-$rect.left),($rect.bottom-$rect.top)
$graphics=[Drawing.Graphics]::FromImage($bitmap)
$dc=$graphics.GetHdc()
try { if (-not [CaptureWindow]::PrintWindow($handle,$dc,2)) { throw 'PrintWindow failed' } }
finally { $graphics.ReleaseHdc($dc) }
$bitmap.Save($OutputPath,[Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose(); $bitmap.Dispose()
