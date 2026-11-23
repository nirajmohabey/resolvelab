"""Bounded owned subprocess execution, including Windows launcher descendants."""
import os
import signal
import subprocess


def terminate_owned_tree(child):
    if child.poll() is not None:
        return
    if os.name == "nt":
        # This PID is the exact Popen child we created, never a name search.
        subprocess.run(["taskkill.exe","/PID",str(child.pid),"/T","/F"],capture_output=True,timeout=10,check=False)
    else:
        try: os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError: pass
    if child.poll() is None:child.kill()
    child.wait(timeout=10)


def run_bounded(args, *, cwd, env, timeout):
    options = {"creationflags":subprocess.CREATE_NEW_PROCESS_GROUP} if os.name=="nt" else {"start_new_session":True}
    child = subprocess.Popen(args,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,**options)
    try:
        stdout,stderr=child.communicate(timeout=timeout)
        return subprocess.CompletedProcess(args,child.returncode,stdout,stderr)
    except subprocess.TimeoutExpired:
        terminate_owned_tree(child)
        child.communicate(timeout=10)
        raise
