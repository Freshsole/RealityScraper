#include <windows.h>
#include <stdio.h>
#include <string.h>

static void dirname_of(char *path) {
    char *slash = strrchr(path, '\\');
    if (!slash) slash = strrchr(path, '/');
    if (slash) *slash = '\0';
}

int main(void) {
    char exe_path[MAX_PATH];
    char python[MAX_PATH];
    char script[MAX_PATH];
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;
    char cmd[MAX_PATH * 3];
    DWORD exit_code = 1;

    if (!GetModuleFileNameA(NULL, exe_path, MAX_PATH)) {
        fprintf(stderr, "Nelze najit umisteni SrealityMonitor.exe\n");
        return 1;
    }
    dirname_of(exe_path);
    SetCurrentDirectoryA(exe_path);

    snprintf(python, MAX_PATH, "%s\\python\\python.exe", exe_path);
    snprintf(script, MAX_PATH, "%s\\run_app.py", exe_path);
    snprintf(cmd, sizeof(cmd), "\"%s\" \"%s\"", python, script);

    memset(&si, 0, sizeof(si));
    si.cb = sizeof(si);
    memset(&pi, 0, sizeof(pi));

    if (!CreateProcessA(python, cmd, NULL, NULL, FALSE, 0, NULL, exe_path, &si, &pi)) {
        fprintf(stderr, "Nepodarilo se spustit Python. Zkontroluj slozku python vedle tohoto .exe\n");
        return 1;
    }
    WaitForSingleObject(pi.hProcess, INFINITE);
    GetExitCodeProcess(pi.hProcess, &exit_code);
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return (int)exit_code;
}
