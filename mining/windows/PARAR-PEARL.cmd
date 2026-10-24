@echo off
powershell.exe -NoProfile -Command "Start-Process powershell.exe -Verb RunAs -WindowStyle Hidden -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File C:\Users\renan\AppData\Local\Mining\Pearl\Parar-Pearl.ps1'"
