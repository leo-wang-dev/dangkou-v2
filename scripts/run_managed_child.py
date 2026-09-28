"""Run a tenant role in this process; parent pipe EOF terminates the role.

Only the read end is inherited. Parent crash closes its write end even on SIGKILL.
The runtime ownership flock is also inherited so a new group cannot overlap.
"""
import argparse
import os
from pathlib import Path
import runpy
import signal
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def watch_parent(fd):
    try:
        while os.read(fd,1):pass
    finally:
        os.close(fd)
        os.kill(os.getpid(),signal.SIGTERM)
        time.sleep(8)
        os._exit(1)  # A role stuck during graceful shutdown cannot retain ownership forever.


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent-fd',type=int,required=True)
    choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--script');choice.add_argument('--module')
    args,rest=parser.parse_known_args()
    threading.Thread(target=watch_parent,args=(args.parent_fd,),daemon=True).start()
    sys.argv=[args.script or args.module,*rest]
    if args.script:runpy.run_path(args.script,run_name='__main__')
    else:runpy.run_module(args.module,run_name='__main__')


if __name__=='__main__':main()
