@echo off
pushd "%~dp0"
call npm run desktop:build
popd
