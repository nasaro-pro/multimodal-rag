@echo off
rem Multimodal RAG setup for Windows - double-click this file.
rem It runs setup.ps1 without changing your PowerShell execution policy.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
