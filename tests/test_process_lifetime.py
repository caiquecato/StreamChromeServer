import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"src"))
from process_guard import attach_to_server

class ProcessLifetimeTests(unittest.TestCase):
    def test_ffmpeg_children_cannot_outlive_the_server(self):
        directory=str(Path(__file__).resolve().parents[1]/'src')
        script=f'import sys,subprocess,time;sys.path.insert(0,{directory!r});from process_guard import attach_to_server;p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print(str(p.pid)+":"+str(attach_to_server(p)),flush=True);time.sleep(60)'
        self.assert_child_stops_with_parent(script)

    def test_managed_analysis_cannot_outlive_server(self):
        directory=str(Path(__file__).resolve().parents[1]/'src')
        child='import os,time;print(str(os.getpid())+":True",flush=True);time.sleep(60)'
        script=f'import sys;sys.path.insert(0,{directory!r});from process_guard import run_managed;run_managed([sys.executable,"-c",{child!r}])'
        self.assert_child_stops_with_parent(script)

    def assert_child_stops_with_parent(self,script):
        parent=subprocess.Popen([sys.executable,'-c',script],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,creationflags=subprocess.CREATE_NO_WINDOW)
        handle=None
        child=None
        try:
            line=parent.stdout.readline().strip()
            self.assertTrue(line, 'O processo de teste nao iniciou.')
            pid,attached=line.split(':');child=int(pid)
            self.assertEqual(attached,'True')
            kernel=ctypes.windll.kernel32
            kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
            kernel.OpenProcess.restype=wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            kernel.WaitForSingleObject.restype=wintypes.DWORD
            handle=kernel.OpenProcess(0x100000,False,child)
            self.assertTrue(handle)
            parent.terminate();parent.wait(timeout=5)
            self.assertEqual(kernel.WaitForSingleObject(handle,5000),0,'O filho continuou ativo depois do servidor encerrar.')
        finally:
            if parent.poll() is None:parent.kill();parent.wait()
            if child is not None:
                subprocess.run(['taskkill','/PID',str(child),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            if handle:
                ctypes.windll.kernel32.CloseHandle.argtypes=[wintypes.HANDLE]
                ctypes.windll.kernel32.CloseHandle(handle)
            parent.stdout.close();parent.stderr.close()
