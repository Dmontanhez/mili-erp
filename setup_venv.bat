@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "PROJECT_DIR=C:\Ecommerce"
set "VENV_NAME=venv"
set "PYTHON_EXE=python"

echo ===========================================
echo  Script de Setup - MILI ERP
echo ===========================================
echo.

:: Verificar se o Python está instalado
%PYTHON_EXE% --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python não foi encontrado no PATH.
    echo [ERRO] Por favor, instale o Python e adicione-o ao PATH do sistema.
    pause
    exit /b 1
)

for /f "tokens=*" %%a in ('%PYTHON_EXE% --version') do (
    echo [OK] Python encontrado: %%a
)

:: Criar o diretório do projeto se não existir
if not exist "%PROJECT_DIR%" (
    echo [INFO] Criando diretório do projeto: %PROJECT_DIR%
    mkdir "%PROJECT_DIR%"
    if errorlevel 1 (
        echo [ERRO] Não foi possível criar o diretório %PROJECT_DIR%.
        pause
        exit /b 1
    )
)

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [ERRO] Não foi possível acessar o diretório %PROJECT_DIR%.
    pause
    exit /b 1
)

echo [INFO] Diretório do projeto: %CD%

:: Verificar se o ambiente virtual já existe
if exist "%VENV_NAME%\Scripts\activate.bat" (
    echo [AVISO] O ambiente virtual "%VENV_NAME%" já existe.
) else (
    echo [INFO] Criando ambiente virtual "%VENV_NAME%"...
    %PYTHON_EXE% -m venv "%VENV_NAME%"
    if errorlevel 1 (
        echo [ERRO] Não foi possível criar o ambiente virtual.
        pause
        exit /b 1
    )
    echo [OK] Ambiente virtual criado com sucesso.
)

:: Ativar o ambiente virtual
echo [INFO] Ativando ambiente virtual...
call "%VENV_NAME%\Scripts\activate.bat"
if errorlevel 1 (
    echo [ERRO] Não foi possível ativar o ambiente virtual.
    pause
    exit /b 1
)
echo [OK] Ambiente virtual ativado.

:: Atualizar o pip
echo [INFO] Atualizando o pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERRO] Não foi possível atualizar o pip.
    pause
    exit /b 1
)
echo [OK] Pip atualizado com sucesso.

:: Instalar dependências
echo [INFO] Instalando dependências do projeto MILI ERP...
python -m pip install fastapi uvicorn[standard] httpx psycopg2-binary python-multipart pydantic
if errorlevel 1 (
    echo [ERRO] Não foi possível instalar as dependências.
    pause
    exit /b 1
)
echo [OK] Dependências instaladas com sucesso.

echo.
echo ===========================================
echo  Setup concluído com sucesso!
echo  Ambiente virtual: %PROJECT_DIR%\%VENV_NAME%
echo  Para ativar manualmente, execute:
echo  %PROJECT_DIR%\%VENV_NAME%\Scripts\activate.bat
echo ===========================================

pause
endlocal