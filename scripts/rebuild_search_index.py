"""Independent index repair loop; notifications and Telegram polling never wait on embeddings."""
import fcntl
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from catalog import config, db, search
from catalog.storage import LocalStorage
from catalog.templates import TEMPLATES


def main():
    conn=db.connect();db.init_db(conn)
    try:
        while True:
            for category in TEMPLATES:
                search.reindex(conn,LocalStorage(config.IMG_DIR),category)
            time.sleep(30)
    finally:conn.close()


if __name__=='__main__':
    Path(config.DB_PATH).resolve().parent.mkdir(parents=True,exist_ok=True)
    with open(config.DB_PATH+'.index.lock','w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:main()
        except KeyboardInterrupt:pass
