"""Durable non-secret journal for the single fixed-demo replacement."""
import json
import os
from pathlib import Path


class DemoRecoveryJournal:
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory/'reseed-recovery.json'

    def read(self):
        if not self.path.exists():
            return None
        state = json.loads(self.path.read_text('utf-8'))
        if state.get('phase') not in {'stopping','mutating'} or not isinstance(state.get('original_id'),str) or not state['original_id']:
            raise ValueError('Invalid demo recovery journal')
        if state['phase']=='mutating' and (Path(state.get('archive','')).name!=state.get('archive') or not state['archive'].endswith('.tar.gz.enc')):
            raise ValueError('Invalid demo recovery archive')
        return state

    def _sync_directory(self):
        if os.name != 'nt':
            fd=os.open(self.directory,os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def write(self, phase, original_id, archive=None):
        self.directory.mkdir(parents=True,exist_ok=True)
        temporary=self.directory/'reseed-recovery.json.tmp'
        with temporary.open('w',encoding='utf-8') as handle:
            os.chmod(temporary,0o600)
            json.dump({'phase':phase,'original_id':original_id,'archive':archive},handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary,self.path)
        self._sync_directory()

    def clear(self):
        self.path.unlink(missing_ok=True)
        self._sync_directory()
