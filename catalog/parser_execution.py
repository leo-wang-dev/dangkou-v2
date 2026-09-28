"""Host-wide actual-work ownership, independent of tenant document lease timestamps.

Never unlink lock files: flock ownership outlives leases and is inherited by child
commands. Durable child markers prevent releasing uncertain orphan work.
"""
from contextvars import ContextVar
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import uuid

_current = ContextVar('parser_execution', default=None)
LABEL = 'com.dangkou.parser-execution'


def state_dir():
    value = os.environ.get('CATALOG_PARSER_STATE_DIR','')
    if not value or not os.path.isabs(value):
        raise RuntimeError('CATALOG_PARSER_STATE_DIR must be one shared absolute host path')
    path = Path(value)
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    return path


def _lock(path):
    handle = open(path,'a+')
    try:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def _write(path, data):
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex)
    with open(temporary,'w') as handle:
        json.dump(data,handle)
        handle.flush();os.fsync(handle.fileno())
    os.replace(temporary,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


def reconcile(marker, *, completed=False):
    """Only remove a uniquely labelled owned container. Daemon ambiguity fails closed.

    A marker without a container after supervisor loss is uncertain (dispatch may
    not have finished), so only the original completed caller can clear absence.
    """
    if not marker:return True
    if marker.get('kind') != 'docker':return False
    environment={k:os.environ[k] for k in ('PATH','HOME','DOCKER_HOST','DOCKER_CONTEXT') if k in os.environ}
    def run(args):
        return subprocess.run([marker['docker'],*args],capture_output=True,text=True,timeout=15,env=environment)
    try:
        listing=run(['ps','-aq','--no-trunc','--filter','name=^/'+marker['name']+'$'])
        if listing.returncode:return False
        identities=(listing.stdout or '').strip().splitlines()
        if not identities:return completed
        if len(identities)!=1:return False
        inspected=run(['inspect',identities[0]])
        if inspected.returncode:return False
        objects=json.loads(inspected.stdout)
        if len(objects)!=1:return False
        obj=objects[0]
        if obj.get('Name')!='/'+marker['name'] or obj.get('Config',{}).get('Labels',{}).get(LABEL)!=marker['owner']:
            return False
        removed=run(['rm','--force',identities[0]])
        if removed.returncode:return False
        checked=run(['ps','-aq','--no-trunc','--filter','name=^/'+marker['name']+'$'])
        return checked.returncode==0 and not (checked.stdout or '').strip()
    except (OSError,subprocess.SubprocessError,ValueError,KeyError,TypeError):
        return False


class Slot:
    def __init__(self, lock, document_lock, metadata, data):
        self.lock,self.document_lock,self.metadata,self.data=lock,document_lock,metadata,data

    @property
    def fds(self):return (self.lock.fileno(),self.document_lock.fileno())

    def child(self,marker):
        self.data['child']=marker;_write(self.metadata,self.data)

    def clear_child(self, *, completed=False):
        if not reconcile(self.data.get('child'),completed=completed):
            raise RuntimeError('Parser child termination is uncertain; execution slot retained')
        self.child(None)

    def close(self):
        # A failed cleanup marker deliberately survives unlock. Future admission
        # reconciles it before reusing the physical slot, or stays blocked.
        self.lock.close();self.document_lock.close()

    @contextmanager
    def activate(self):
        token=_current.set(self)
        try:yield self
        finally:_current.reset(token)


def acquire(document):
    root=state_dir()
    key=hashlib.sha256(document.encode()).hexdigest()
    doc_lock=_lock(root/('document-'+key+'.lock'))
    if doc_lock is None:return None
    limit=max(1,int(os.environ.get('CATALOG_IMPORT_WORKERS','1')))
    # All services must use one configured host capacity, including during restart.
    guard=open(root/'authority.lock','a+')
    fcntl.flock(guard,fcntl.LOCK_EX)
    try:
        capacity=root/'capacity.json'
        if not capacity.exists():_write(capacity,{'limit':limit})
        if json.loads(capacity.read_text())['limit']!=limit:
            raise RuntimeError('Parser host capacity mismatch; stop all workers before reconfiguration')
        # Check this document's uncertain orphan in every slot before choosing a
        # free one; with capacity>1 an earlier empty slot must not bypass it.
        for number in range(limit):
            metadata=root/f'slot-{number}.json'
            previous=json.loads(metadata.read_text()) if metadata.exists() else {}
            if previous.get('document')!=document or not previous.get('child'):continue
            lock=_lock(root/f'slot-{number}.lock')
            if lock is None:
                doc_lock.close();return None
            try:
                if not reconcile(previous['child']):
                    doc_lock.close();return None
                previous['child']=None;_write(metadata,previous)
            finally:lock.close()
        for number in range(limit):
            lock=_lock(root/f'slot-{number}.lock')
            if lock is None:continue
            metadata=root/f'slot-{number}.json'
            try:
                previous=json.loads(metadata.read_text()) if metadata.exists() else {}
                if not reconcile(previous.get('child')):
                    lock.close()
                    if previous.get('document')==document:
                        doc_lock.close();return None
                    continue
                data={'document':document,'owner':uuid.uuid4().hex,'child':None}
                _write(metadata,data)
                return Slot(lock,doc_lock,metadata,data)
            except BaseException:
                lock.close();raise
        doc_lock.close()
        return None
    except BaseException:
        doc_lock.close();raise
    finally:guard.close()


def current():return _current.get()
